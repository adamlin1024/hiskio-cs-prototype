"""一顆腦流程行為測試(規格 §3/§4/§14)。

釘死:①每日配額守衛(超額=固定話術+提議轉真人,絕不打 LLM)②「好吧」誤結案修正
(user_satisfied 閘門)③幽靈 phase 防呆 ④離題=固定話術不花 LLM ⑤配額跨日重置。
"""
import os
import tempfile

os.environ.setdefault(
    "RUNTIME_CONFIG_PATH", os.path.join(tempfile.gettempdir(), "hibot_onebrain_rc.json")
)

import pytest  # noqa: E402

from core import orchestrator, runtime_config  # noqa: E402
from core import state as state_mod  # noqa: E402
from nodes import acknowledge_handler, brain  # noqa: E402


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(state_mod, "DB_PATH", str(tmp_path / "t.db"))
    state_mod.init_db()
    runtime_config.reset()
    yield
    runtime_config.reset()


def _fresh_state(**overrides):
    s = state_mod.new_state()
    for k, v in overrides.items():
        s[k] = v
    state_mod.save_state(s)
    return s


def _canned_decision(action="clarify", **extra):
    d = {
        "recommended_action": action,
        "faq_id": None,
        "kb_article_ids": [],
        "clarify_message": "想問哪方面呢？",
        "reason_to_user": None,
        "user_satisfied": False,
        "issue": {"category": None, "summary": None, "user_emotion": None},
        "new_intents_to_log": [],
        "target_intent_index": None,
        "reason": "test",
    }
    d.update(extra)
    return d


# ── 每日配額(規格 §14-8)────────────────────────────────────────


def test_daily_quota_blocks_without_llm(db, monkeypatch):
    """超額:固定話術+進兩段式轉真人,分診腦絕不能被呼叫。"""
    from datetime import date

    def _boom(*a, **k):
        raise AssertionError("配額滿了不該呼叫分診腦")

    monkeypatch.setattr(brain, "decide", _boom)
    s = _fresh_state()
    s["service_limits"]["daily_date"] = date.today().isoformat()
    s["service_limits"]["daily_count"] = 30
    state_mod.save_state(s)

    res = orchestrator.handle_user_message(s["session_id"], "再問一題")
    assert res["response_type"] == "daily_limit"
    reloaded = state_mod.load_state(s["session_id"])
    assert reloaded["phase"] == "等待轉真人確認"
    assert reloaded["ticket_state"]["handoff_reason"] == "daily_limit"


def test_daily_quota_resets_on_new_day(db, monkeypatch):
    """跨日自動歸零:昨天刷爆,今天照常服務。"""
    monkeypatch.setattr(brain, "decide", lambda st, msg: _canned_decision())
    s = _fresh_state()
    s["service_limits"]["daily_date"] = "2000-01-01"
    s["service_limits"]["daily_count"] = 999
    state_mod.save_state(s)

    res = orchestrator.handle_user_message(s["session_id"], "退費怎麼辦")
    assert res["response_type"] == "clarification"  # 正常走到分診腦
    reloaded = state_mod.load_state(s["session_id"])
    assert reloaded["service_limits"]["daily_count"] == 1


def test_daily_quota_threshold_injectable(db, monkeypatch):
    """後台可注入 max_daily_messages 門檻(runtime_config 白名單)。"""
    from datetime import date
    runtime_config.set_overlay({"thresholds": {"max_daily_messages": 2}})
    monkeypatch.setattr(brain, "decide", lambda st, msg: _canned_decision())
    s = _fresh_state()
    s["service_limits"]["daily_date"] = date.today().isoformat()
    s["service_limits"]["daily_count"] = 2
    state_mod.save_state(s)
    res = orchestrator.handle_user_message(s["session_id"], "hi 這是第三句")
    assert res["response_type"] == "daily_limit"


# ── 「好吧」誤結案修正(規格 §4)──────────────────────────────────


def _state_with_open_intent(db_unused=None):
    s = state_mod.new_state()
    s["intent_state"]["current_intent"] = "退費問題"
    s["intent_state"]["intent_log"] = [
        {"text": "退費問題", "status": "answered", "role": "primary",
         "in_scope": True, "first_turn": 0},
    ]
    state_mod.save_state(s)
    return s


def test_passive_ok_does_not_resolve_intent(db, monkeypatch):
    """「好吧」(user_satisfied=False)→ 溫和回應,但問題不標已解決。"""
    monkeypatch.setattr(acknowledge_handler, "respond", lambda st, msg: "好的，有需要再叫我")
    s = _state_with_open_intent()
    orchestrator._execute_acknowledge_confirmation(
        s, "好吧", _canned_decision("acknowledge_confirmation", user_satisfied=False), "sid",
    )
    assert s["intent_state"]["intent_log"][0]["status"] != "confirmed_resolved"


def test_explicit_thanks_resolves_intent(db, monkeypatch):
    """「謝謝,解決了」(user_satisfied=True)→ 標 confirmed_resolved。"""
    monkeypatch.setattr(acknowledge_handler, "respond", lambda st, msg: "太好了！")
    s = _state_with_open_intent()
    orchestrator._execute_acknowledge_confirmation(
        s, "謝謝解決了", _canned_decision("acknowledge_confirmation", user_satisfied=True), "sid",
    )
    assert s["intent_state"]["intent_log"][0]["status"] == "confirmed_resolved"


# ── 幽靈 phase 防呆(規格 §14-5)──────────────────────────────────


def test_retired_phase_is_normalized(db, monkeypatch):
    monkeypatch.setattr(brain, "decide", lambda st, msg: _canned_decision())
    s = _fresh_state(phase="等待用戶選擇意圖")  # v7 已退役的 phase
    res = orchestrator.handle_user_message(s["session_id"], "我要退費")
    assert res["response_type"] == "clarification"  # 沒卡死,正常走分診腦
    reloaded = state_mod.load_state(s["session_id"])
    assert reloaded["phase"] in ("對話中", "等待轉真人確認")


# ── 離題:固定話術、不花 LLM ─────────────────────────────────────


def test_out_of_scope_uses_canned_reply_and_counts(db, monkeypatch):
    monkeypatch.setattr(
        brain, "decide", lambda st, msg: _canned_decision("acknowledge_out_of_scope"))
    s = _fresh_state()
    res = orchestrator.handle_user_message(s["session_id"], "推薦台北咖啡廳")
    assert res["response_type"] == "off_topic"
    assert res["ai_response"] == orchestrator.DEFAULT_OUT_OF_SCOPE_MSG
    reloaded = state_mod.load_state(s["session_id"])
    assert reloaded["service_limits"]["off_topic_count"] == 1


def test_out_of_scope_blocked_after_limit(db, monkeypatch):
    monkeypatch.setattr(
        brain, "decide", lambda st, msg: _canned_decision("acknowledge_out_of_scope"))
    s = _fresh_state()
    s["service_limits"]["off_topic_count"] = 3  # 已達預設上限
    state_mod.save_state(s)
    res = orchestrator.handle_user_message(s["session_id"], "再聊個天氣")
    assert res["response_type"] == "off_topic_blocked"
    assert res["ai_response"] == orchestrator.OFF_TOPIC_BLOCKED_MSG


# ── 引導下一題:由程式判定,不交給模型掃清單(2026-07-06 實測修正)────


def test_next_pending_picks_earliest_pending_only():
    log = [
        {"text": "退費", "status": "confirmed_resolved", "first_turn": 0},
        {"text": "發票", "status": "pending", "first_turn": 2},
        {"text": "抵用券", "status": "pending", "first_turn": 1},
    ]
    assert acknowledge_handler._next_pending(log, "退費") == "抵用券"  # 最早的 pending


def test_next_pending_none_when_all_resolved():
    """全部解決 → 無待辦(模型不得再把已解決的端出來——由程式保證)。"""
    log = [
        {"text": "退費", "status": "confirmed_resolved", "first_turn": 0},
        {"text": "發票", "status": "answered", "first_turn": 1},
    ]
    assert acknowledge_handler._next_pending(log, "發票") is None


# ── 轉真人精確原因(Adam 拍板:交接資料要最完整)────────────────────


def test_suggest_ticket_uses_brain_handoff_reason(db, monkeypatch):
    """腦標 no_kb_match → 交接原因照標(真人看到「知識庫沒有對應資料」=補 KB 訊號)。"""
    monkeypatch.setattr(brain, "decide", lambda st, msg: _canned_decision(
        "suggest_ticket", reason_to_user="這部分沒有資料", handoff_reason="no_kb_match"))
    s = _fresh_state()
    res = orchestrator.handle_user_message(s["session_id"], "有 Java 代報名嗎")
    assert res["response_type"] == "handoff_offer"
    reloaded = state_mod.load_state(s["session_id"])
    assert reloaded["ticket_state"]["handoff_reason"] == "no_kb_match"


def test_suggest_ticket_defaults_needs_human(db, monkeypatch):
    monkeypatch.setattr(brain, "decide", lambda st, msg: _canned_decision(
        "suggest_ticket", reason_to_user="要查訂單"))
    s = _fresh_state()
    orchestrator.handle_user_message(s["session_id"], "查我的訂單")
    reloaded = state_mod.load_state(s["session_id"])
    assert reloaded["ticket_state"]["handoff_reason"] == "needs_human"


# ── 決定單 issue → 交接摘要原料(同源)──────────────────────────────


def test_brain_issue_updates_issue_context(db, monkeypatch):
    monkeypatch.setattr(brain, "decide", lambda st, msg: _canned_decision(
        issue={"category": "退款", "summary": "想退組合包其中一堂", "user_emotion": "焦急"}))
    s = _fresh_state()
    orchestrator.handle_user_message(s["session_id"], "組合包退一堂可以嗎")
    reloaded = state_mod.load_state(s["session_id"])
    assert reloaded["issue_context"]["category"] == "退款"
    assert reloaded["issue_context"]["user_emotion"] == "焦急"
