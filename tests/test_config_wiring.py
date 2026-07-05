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

    def fake_call_writer(prompt, **kw):
        captured["system"] = kw.get("system")
        return "ok"

    monkeypatch.setattr(cs_response, "call_writer", fake_call_writer)
    st = state_mod.new_state()

    # 沒注入 ＝ 檔案預設人設 + 守則(永遠附加)
    runtime_config.reset()
    cs_response.respond(st, [], "hi")
    assert captured["system"].startswith(cs_response._SYSTEM_TPL.rstrip())
    assert "防捏造鐵則" in captured["system"]

    # 注入後 ＝ 覆寫人設 + 守則(仍在)
    runtime_config.set_overlay({"prompts": {"cs_response_system": "你是溫柔客服"}})
    cs_response.respond(st, [], "hi")
    assert captured["system"].startswith("你是溫柔客服")


def test_persona_injection_cannot_wipe_guard(monkeypatch):
    """2026-07-06 live 抓到的漏洞回歸:後台注入一行簡短人設,
    防捏造鐵則/禁粗體/SUGGEST_TICKET 規則必須仍然生效(不可被整份蓋掉)。"""
    captured = {}

    def fake_call_writer(prompt, **kw):
        captured["system"] = kw.get("system")
        return "ok"

    monkeypatch.setattr(cs_response, "call_writer", fake_call_writer)
    runtime_config.set_overlay({"prompts": {"cs_response_system": "你是 HiSKIO 的親切線上客服。"}})
    cs_response.respond(state_mod.new_state(), [], "hi")
    sys_prompt = captured["system"]
    assert "防捏造鐵則" in sys_prompt
    assert "不要用粗體" in sys_prompt
    assert "[SUGGEST_TICKET]" in sys_prompt
