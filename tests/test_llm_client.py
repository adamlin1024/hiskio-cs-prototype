"""門面測試：call_role/call_reasoning/call_fast + 用量/成本統計。

用注入的 FakeRegistry/FakeProvider，不打真 API。
"""
from core import llm_client
from core.llm_providers import LLMResponse


class FakeProvider:
    def __init__(self, response=None, exc=None):
        self.name = "fakeprov"
        self._response = response
        self._exc = exc
        self.last_call = None

    def complete(self, **kwargs):
        self.last_call = kwargs
        if self._exc:
            raise self._exc
        return self._response


class FakeRegistry:
    def __init__(self, provider, model="fake-model"):
        self._p = provider
        self._m = model
        self.pricing_table: dict[str, dict] = {}

    def provider_for_role(self, role):
        return self._p, self._m

    def pricing(self, model):
        return self.pricing_table.get(model)


def test_call_role_returns_text_on_success():
    prov = FakeProvider(response=LLMResponse(
        text="你好", input_tokens=4, output_tokens=2,
        model="fake-model", provider="fakeprov"))
    reg = FakeRegistry(prov)
    out = llm_client.call_role("reasoning", "hi", max_tokens=50,
                               temperature=0.1, registry=reg)
    assert out == "你好"
    assert prov.last_call["model"] == "fake-model"
    assert prov.last_call["prompt"] == "hi"
    assert prov.last_call["max_tokens"] == 50


def test_call_role_returns_fallback_on_provider_error():
    prov = FakeProvider(exc=RuntimeError("boom"))
    reg = FakeRegistry(prov)
    out = llm_client.call_role("fast", "hi", max_tokens=10, temperature=0,
                               fallback="FB", registry=reg)
    assert out == "FB"


def test_call_role_logs_usage_with_reported_cost():
    prov = FakeProvider(response=LLMResponse(
        text="x", input_tokens=10, output_tokens=5,
        model="fake-model", provider="fakeprov", cost_usd=0.01))
    reg = FakeRegistry(prov)
    llm_client.reset_usage()
    llm_client.call_role("reasoning", "hi", max_tokens=10, temperature=0, registry=reg)
    summary = llm_client.get_usage_summary(registry=reg)
    assert summary["calls"] == 1
    assert summary["by_model"]["fake-model"]["input"] == 10
    assert abs(summary["total_usd"] - 0.01) < 1e-9


def test_usage_summary_falls_back_to_pricing_table_when_no_cost():
    prov = FakeProvider(response=LLMResponse(
        text="x", input_tokens=1_000_000, output_tokens=0,
        model="priced-model", provider="fakeprov", cost_usd=None))
    reg = FakeRegistry(prov, model="priced-model")
    reg.pricing_table["priced-model"] = {
        "input": 3.0, "output": 15.0, "cache_read": 0, "cache_create": 0}
    llm_client.reset_usage()
    llm_client.call_role("reasoning", "hi", max_tokens=10, temperature=0, registry=reg)
    summary = llm_client.get_usage_summary(registry=reg)
    assert abs(summary["by_model"]["priced-model"]["usd"] - 3.0) < 1e-9


def test_usage_summary_marks_cost_unknown_when_no_cost_no_pricing():
    prov = FakeProvider(response=LLMResponse(
        text="x", input_tokens=100, output_tokens=50,
        model="mystery-model", provider="fakeprov", cost_usd=None))
    reg = FakeRegistry(prov, model="mystery-model")  # 沒價目表
    llm_client.reset_usage()
    llm_client.call_role("fast", "hi", max_tokens=10, temperature=0, registry=reg)
    summary = llm_client.get_usage_summary(registry=reg)
    assert summary["by_model"]["mystery-model"]["cost_known"] is False


def test_call_reasoning_and_fast_use_expected_defaults():
    prov = FakeProvider(response=LLMResponse(
        text="ok", model="fake-model", provider="fakeprov"))
    reg = FakeRegistry(prov)
    assert llm_client.call_reasoning("hi", registry=reg) == "ok"
    assert prov.last_call["max_tokens"] == 600
    assert prov.last_call["temperature"] == 0.6
    llm_client.call_fast("hi", registry=reg)
    assert prov.last_call["max_tokens"] == 200
    assert prov.last_call["temperature"] == 0.0


def test_load_prompt_still_works():
    # 既有 prompts/ 模板應照舊可讀
    text = llm_client.load_prompt("entry_classifier")
    assert isinstance(text, str) and len(text) > 0
