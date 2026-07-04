"""收尾流程（留言給真人）行為測試。

釘死兩件事，避免以後又跑掉：
1. 使用者拒絕留言後，「連續講不清」計數要歸零（給喘息空間）。
2. 拒絕過一次後，這次 session 不再自動強逼——就算又講不清到門檻，也只澄清、不再轉。

全部用假的 clarify_message，走不到 LLM，不連線、不花錢。
"""
import os
import tempfile

os.environ.setdefault(
    "RUNTIME_CONFIG_PATH", os.path.join(tempfile.gettempdir(), "hibot_escal_rc.json")
)

import pytest  # noqa: E402

from core import orchestrator, runtime_config  # noqa: E402
from core import state as state_mod  # noqa: E402
from nodes import ticket_handler  # noqa: E402


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(state_mod, "DB_PATH", str(tmp_path / "t.db"))
    state_mod.init_db()
    runtime_config.reset()
    yield
    runtime_config.reset()


def _fresh_state():
    s = state_mod.new_state()
    state_mod.save_state(s)
    return s


def test_unclear_reaching_threshold_forces_when_not_declined(db):
    """沒拒絕過：連續講不清到門檻 → 仍會強制轉真人（保住既有行為）。"""
    s = _fresh_state()
    s["intent_state"]["consecutive_unclear_count"] = 2  # 門檻 3 的前一步
    res = orchestrator._execute_uncertainty(
        s, "還是不懂", {"clarify_message": "能再多說一點嗎"}, "sid"
    )
    assert s["phase"] == "等待轉真人確認"
    assert res["response_type"] == "force_escalation"


def test_decline_resets_unclear_count_and_marks_declined(db):
    """拒絕留言 → 計數歸零、標記 declined、回到對話中。"""
    s = _fresh_state()
    s["phase"] = "等待轉真人確認"
    s["ticket_state"]["ticket_suggested"] = True
    s["intent_state"]["consecutive_unclear_count"] = 3
    ticket_handler.handle_decline(s)
    assert s["intent_state"]["consecutive_unclear_count"] == 0
    assert s["ticket_state"]["user_decision"] == "declined"
    assert s["phase"] == "對話中"


def test_after_decline_unclear_does_not_reforce(db):
    """拒絕過後：又講不清到門檻，也不再自動強逼，只澄清。"""
    s = _fresh_state()
    s["ticket_state"]["user_decision"] = "declined"
    s["intent_state"]["consecutive_unclear_count"] = 2
    res = orchestrator._execute_uncertainty(
        s, "還是不懂", {"clarify_message": "能再多說一點嗎"}, "sid"
    )
    assert s["phase"] == "對話中"  # 沒被轉去等待轉真人確認
    assert res["response_type"] == "clarification"


def test_after_decline_clarify_does_not_reforce(db):
    """clarify 這條路一樣：拒絕過後不再自動強逼。"""
    s = _fresh_state()
    s["ticket_state"]["user_decision"] = "declined"
    s["intent_state"]["consecutive_unclear_count"] = 2
    res = orchestrator._execute_clarify(
        s, "嗯嗯", {"clarify_message": "想問哪方面呢"}, "sid"
    )
    assert s["phase"] == "對話中"
    assert res["response_type"] == "clarification"
