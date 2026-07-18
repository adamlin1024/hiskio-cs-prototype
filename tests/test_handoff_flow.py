"""轉真人交接（handoff）行為測試。

釘死轉真人改版後的核心行為：
1. 同意轉真人 → 回應帶 handoff.requested=True + 摘要，設 handed_off，不建工單、不問 email、不進死路。
2. 安撫話：沒注入用內建預設；HiSupport 注入 handoff_message 就用注入的（確保單機＝正式一致）。
3. 閉環：已交接後再打字 → 維持已交接、不再重問，handoff.requested 持續 True，且不打 LLM。
"""
import os
import tempfile

os.environ.setdefault(
    "RUNTIME_CONFIG_PATH", os.path.join(tempfile.gettempdir(), "hibot_handoff_rc.json")
)

import pytest  # noqa: E402

from core import orchestrator, runtime_config  # noqa: E402
from core import state as state_mod  # noqa: E402


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(state_mod, "DB_PATH", str(tmp_path / "t.db"))
    state_mod.init_db()
    runtime_config.reset()
    yield
    runtime_config.reset()


def _offered_state():
    """已進入『等待轉真人確認』的 state（offer 已發出、原因已記）。"""
    s = state_mod.new_state()
    s["phase"] = "等待轉真人確認"
    s["ticket_state"]["ticket_suggested"] = True
    s["ticket_state"]["handoff_reason"] = "unclear_limit"
    state_mod.save_state(s)
    return s


def test_accept_emits_handoff_signal_and_no_ticket(db):
    s = _offered_state()
    res = orchestrator._execute_handoff(s, "好", "sid")
    assert res["handoff"]["requested"] is True
    assert res["handoff"]["summary"]                 # 有摘要給真人看
    assert s["ticket_state"]["handed_off"] is True
    assert res["ticket_id"] is None                  # 不再建工單
    assert s["phase"] == "對話中"                     # 不進死路
    assert "編號" not in res["ai_response"]           # 不提工單編號


def test_accept_uses_default_message_when_not_injected(db):
    s = _offered_state()
    res = orchestrator._execute_handoff(s, "好", "sid")
    assert res["ai_response"] == orchestrator.DEFAULT_HANDOFF_MSG


def test_accept_uses_injected_handoff_message(db):
    runtime_config.set_overlay({"messages": {"handoff_message": "專人馬上來"}})
    s = _offered_state()
    res = orchestrator._execute_handoff(s, "好", "sid")
    assert res["ai_response"] == "專人馬上來"


def test_offer_then_accept_full_path(db, monkeypatch):
    """提議轉真人 → 用戶回「好」→ 走完整 phase 攔截 → 交接（mock 掉 LLM 判斷）。"""
    from nodes import ticket_handler
    monkeypatch.setattr(ticket_handler, "decide", lambda msg: "Y")
    s = _offered_state()
    res = orchestrator.handle_user_message(s["session_id"], "好")
    assert res["response_type"] == "handoff"
    assert res["handoff"]["requested"] is True
    assert res["handoff"]["summary"]
    reloaded = state_mod.load_state(s["session_id"])
    assert reloaded["ticket_state"]["handed_off"] is True


def test_offer_then_decline_full_path(db, monkeypatch):
    """提議轉真人 → 用戶回「不用」→ 回對話、不交接、不再強逼。"""
    from nodes import ticket_handler
    monkeypatch.setattr(ticket_handler, "decide", lambda msg: "N")
    s = _offered_state()
    res = orchestrator.handle_user_message(s["session_id"], "不用")
    assert res["response_type"] == "handoff_declined"
    assert res["handoff"]["requested"] is False
    reloaded = state_mod.load_state(s["session_id"])
    assert reloaded["ticket_state"]["handed_off"] is False
    assert reloaded["ticket_state"]["user_decision"] == "declined"
    assert reloaded["phase"] == "對話中"


def test_decide_clear_yes_no_uses_rules_not_llm(db, monkeypatch):
    """『等待轉真人確認』下，明確的好/不用要用規則判定、完全不呼叫 LLM。

    釘死實測 bug：使用者回「好」時，快速模型偶爾判成 U → 漏接掉回主管 →
    被誤判成 acknowledge（回「不客氣」）。規則判定杜絕這個漏接。
    """
    from nodes import ticket_handler

    def _boom(*a, **k):
        raise AssertionError("明確的好/不用不該呼叫 LLM")

    monkeypatch.setattr(ticket_handler, "call_triage", _boom)
    for yes in ["好", "好的", "好啊", "可以", "要", "麻煩你", "OK", "是",
                "同意", "認同", "當然", "沒問題", "需要", "行"]:
        assert ticket_handler.decide(yes) == "Y", yes
    for no in ["不用", "不要", "不需要", "算了", "先不用", "不行", "不可以"]:
        assert ticket_handler.decide(no) == "N", no


def test_confirm_phase_with_image_skips_vision_and_decides_once(db, monkeypatch):
    """對抗健檢 2026-07-18：等待轉真人確認 + 附圖 + 回「好」→
    圖不讀（不讓圖描述汙染 yes/no 判斷、害「好」漏接）、且 decide 同輪只算一次（dedup）。"""
    from nodes import ticket_handler, vision

    calls = {"decide": 0, "vision": 0}

    def _decide(msg):
        calls["decide"] += 1
        return "Y"

    def _vision(urls):
        calls["vision"] += 1
        return "（圖描述）"

    monkeypatch.setattr(ticket_handler, "decide", _decide)
    monkeypatch.setattr(vision, "describe_images", _vision)

    s = _offered_state()
    res = orchestrator.handle_user_message(
        s["session_id"], "好", image_urls=["http://8.8.8.8/x.png"]
    )
    assert res["response_type"] == "handoff"
    assert res["handoff"]["requested"] is True
    assert calls["vision"] == 0   # 確認 yes/no 時不讀圖
    assert calls["decide"] == 1   # 同輪只判一次（不再重複呼叫）


def test_reason_is_none_when_not_handed_off(db):
    """未交接（requested=False）時，訊號不該夾帶殘留 reason／summary（衛生）。"""
    s = _offered_state()  # 提議中、尚未同意 → requested 應為 False
    h = state_mod.build_handoff(s)
    assert h["requested"] is False
    assert h["reason"] is None
    assert h["summary"] is None


def test_after_handoff_holds_and_keeps_signal(db):
    s = _offered_state()
    orchestrator._execute_handoff(s, "好", "sid")
    # 已交接後再打字：走 holding，不重問、訊號持續
    res = orchestrator.handle_user_message(s["session_id"], "那我還想問退費")
    assert res["handoff"]["requested"] is True
    reloaded = state_mod.load_state(s["session_id"])
    assert reloaded["ticket_state"]["handed_off"] is True
    assert reloaded["phase"] != "等待轉真人確認"      # 沒有重新進入確認
