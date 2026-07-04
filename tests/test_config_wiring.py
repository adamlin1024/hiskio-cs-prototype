"""設定注入『接到使用點』的驗證：新 session 門檻、cs_response 人設；預設＝現況。"""
import os
import tempfile

os.environ["RUNTIME_CONFIG_PATH"] = os.path.join(tempfile.gettempdir(), "hibot_wiring_rc.json")

from core import runtime_config  # noqa: E402
from core import state as state_mod  # noqa: E402
from nodes import cs_response  # noqa: E402


def teardown_function():
    runtime_config.reset()


def test_new_state_default_thresholds():
    runtime_config.reset()
    s = state_mod.new_state()
    assert s["service_limits"]["max_off_topic_count"] == 3


def test_new_state_honors_threshold_override():
    runtime_config.reset()
    runtime_config.set_overlay({"thresholds": {"max_off_topic_count": 9}})
    s = state_mod.new_state()
    assert s["service_limits"]["max_off_topic_count"] == 9


def test_cs_response_persona_default_then_override(monkeypatch):
    captured = {}

    def fake_call_reasoning(prompt, **kw):
        captured["system"] = kw.get("system")
        return "ok"

    monkeypatch.setattr(cs_response, "call_reasoning", fake_call_reasoning)
    st = state_mod.new_state()

    # 沒注入 ＝ 檔案預設人設
    runtime_config.reset()
    cs_response.respond(st, [], "hi")
    assert captured["system"] == cs_response._SYSTEM_TPL

    # 注入後 ＝ 用覆寫人設
    runtime_config.set_overlay({"prompts": {"cs_response_system": "你是溫柔客服"}})
    cs_response.respond(st, [], "hi")
    assert captured["system"] == "你是溫柔客服"
