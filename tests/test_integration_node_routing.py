"""整合驗證：分診腦 → 真的 call_triage → registry（只把最底層換成假 provider）。

證明一顆腦改版後,決定單能正常穿過新的 LLM 層,且不打真 API。
"""
import json

from core import model_config
from core.llm_providers import LLMResponse
from core.state import new_state


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

    def params_for_role(self, role):
        return {}

    def pricing(self, model):
        return None


def test_brain_decision_routes_through_new_layer(monkeypatch):
    decision = {
        "recommended_action": "clarify",
        "faq_id": None,
        "kb_article_ids": [],
        "clarify_message": "想問哪方面呢？",
        "reason_to_user": None,
        "user_satisfied": False,
        "issue": {"category": "其他", "summary": "訊息模糊", "user_emotion": "中性"},
        "new_intents_to_log": [],
        "target_intent_index": None,
        "reason": "太短",
    }
    monkeypatch.setattr(
        model_config, "_default_registry", _FakeRegistry(json.dumps(decision, ensure_ascii=False))
    )
    from nodes import brain

    state = new_state()
    out = brain.decide(state, "嗯")
    assert out["recommended_action"] == "clarify"
    assert out["clarify_message"] == "想問哪方面呢？"
    assert out["user_satisfied"] is False
    assert out["issue"]["summary"] == "訊息模糊"
