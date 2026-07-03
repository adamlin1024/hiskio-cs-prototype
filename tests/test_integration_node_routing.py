"""整合驗證：真的節點 → 真的 call_fast/call_role → registry（只把最底層換成假 provider）。

證明「命名遷移（call_haiku→call_fast）」後，節點仍能正常穿過新的 LLM 層，且不打真 API。
"""
from core import model_config
from core.llm_providers import LLMResponse


class _FakeProvider:
    name = "fake"

    def __init__(self, text):
        self._text = text

    def complete(self, **kwargs):
        return LLMResponse(text=self._text, model="fake-model", provider="fake")


class _FakeRegistry:
    def __init__(self, text):
        self._p = _FakeProvider(text)

    def provider_for_role(self, role):
        return self._p, "fake-model"

    def pricing(self, model):
        return None


def test_entry_classifier_routes_through_new_layer(monkeypatch):
    monkeypatch.setattr(model_config, "_default_registry", _FakeRegistry("greeting"))
    from nodes import entry_classifier

    state = {
        "phase": "start",
        "chat_history": [],
        "intent_state": {"consecutive_unclear_count": 0},
    }
    assert entry_classifier.classify(state, "你好") == "greeting"
