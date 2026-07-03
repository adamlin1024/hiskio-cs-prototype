"""對抗式鑑檢後的修正回歸測試。

- F1/HIGH：設定/等級解析失敗時，request 端要優雅退化回 fallback（不可 500）。
- M1/MED ：OpenRouter 要主動要求回傳費用（extra_body usage.include）。
- M2/MED ：usage buffer 要有上限（長時間執行不可記憶體無限成長）。
- M3/MED ：成本統計要標示是否完整（有模型無法計價時）。
"""
from collections import deque

from core import llm_client
from core.llm_providers import LLMResponse, OpenAICompatProvider


# ── F1：解析失敗要退化回 fallback ────────────────────────────────
class _BadRegistry:
    def provider_for_role(self, role):
        raise KeyError(f"unknown role {role}")

    def pricing(self, model):
        return None


def test_call_role_returns_fallback_when_resolution_fails():
    out = llm_client.call_role("reasoning", "hi", max_tokens=10, temperature=0,
                               fallback="FB", registry=_BadRegistry())
    assert out == "FB"


# ── M2：usage buffer 有上限 ──────────────────────────────────────
def test_usage_log_is_bounded():
    assert isinstance(llm_client._usage_log, deque)
    assert llm_client._usage_log.maxlen is not None


# ── M3：成本完整性旗標 ───────────────────────────────────────────
class _Prov:
    name = "p"

    def __init__(self, resp):
        self._r = resp

    def complete(self, **k):
        return self._r


class _Reg:
    def __init__(self, resp, model, pricing=None):
        self._p = _Prov(resp)
        self._m = model
        self._pr = pricing or {}

    def provider_for_role(self, role):
        return self._p, self._m

    def pricing(self, model):
        return self._pr.get(model)


def test_usage_summary_flags_incomplete_cost():
    reg = _Reg(LLMResponse(text="x", input_tokens=100, output_tokens=0,
                           model="mystery", provider="p", cost_usd=None), "mystery")
    llm_client.reset_usage()
    llm_client.call_role("fast", "hi", max_tokens=10, temperature=0, registry=reg)
    s = llm_client.get_usage_summary(registry=reg)
    assert s["cost_complete"] is False
    assert "mystery" in s["unknown_models"]


def test_usage_summary_complete_when_all_costed():
    reg = _Reg(LLMResponse(text="x", input_tokens=100, output_tokens=0,
                           model="m", provider="p", cost_usd=0.01), "m")
    llm_client.reset_usage()
    llm_client.call_role("fast", "hi", max_tokens=10, temperature=0, registry=reg)
    s = llm_client.get_usage_summary(registry=reg)
    assert s["cost_complete"] is True
    assert s["unknown_models"] == []


# ── M1：OpenRouter 要求回傳費用 ──────────────────────────────────
class _OAUsage:
    prompt_tokens = 1
    completion_tokens = 1


class _Msg:
    content = "ok"


class _Choice:
    message = _Msg()


class _OAResp:
    choices = [_Choice()]
    usage = _OAUsage()


class _FakeOAClient:
    def __init__(self):
        self.last_kwargs = None
        outer = self

        class _Comps:
            def create(self, **kwargs):
                outer.last_kwargs = kwargs
                return _OAResp()

        class _Chat:
            completions = _Comps()

        self.chat = _Chat()


def test_openai_compat_requests_cost_inclusion_when_enabled():
    fake = _FakeOAClient()
    p = OpenAICompatProvider(client=fake, name="openrouter", base_url="http://x",
                             cost_from_response=True)
    p.complete(model="m", prompt="hi", max_tokens=10, temperature=0)
    assert fake.last_kwargs.get("extra_body") == {"usage": {"include": True}}


def test_openai_compat_no_cost_inclusion_when_disabled():
    fake = _FakeOAClient()
    p = OpenAICompatProvider(client=fake, name="x", base_url="http://x",
                             cost_from_response=False)
    p.complete(model="m", prompt="hi", max_tokens=10, temperature=0)
    assert "extra_body" not in fake.last_kwargs
