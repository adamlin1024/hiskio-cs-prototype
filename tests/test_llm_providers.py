"""Provider 層測試：用「注入的假 client」驗證中立請求→各家 SDK 的翻譯與回應解析。

不需要真的 anthropic / openai 套件連線，也不打真 API（不花錢）。
"""
from core.llm_providers import (
    AnthropicNativeProvider,
    LLMResponse,
    OpenAICompatProvider,
)


# ── 假的 Anthropic client ─────────────────────────────────────────
class _Blk:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _Usage:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _AnthResp:
    def __init__(self, text, usage):
        self.content = [_Blk(text)]
        self.usage = usage


class _AnthMessages:
    def __init__(self, outer):
        self.outer = outer

    def create(self, **kwargs):
        self.outer.last_kwargs = kwargs
        return _AnthResp(
            "哈囉",
            _Usage(
                input_tokens=10,
                output_tokens=5,
                cache_read_input_tokens=3,
                cache_creation_input_tokens=2,
            ),
        )


class FakeAnthropicClient:
    def __init__(self):
        self.messages = _AnthMessages(self)
        self.last_kwargs = None


# ── 假的 OpenAI 相容 client ────────────────────────────────────────
class _Msg:
    def __init__(self, content):
        self.content = content


class _Choice:
    def __init__(self, content):
        self.message = _Msg(content)


class _OADetails:
    def __init__(self, reasoning_tokens):
        self.reasoning_tokens = reasoning_tokens


class _OAUsage:
    def __init__(self, prompt_tokens, completion_tokens, cost=None, reasoning_tokens=None):
        self.prompt_tokens = prompt_tokens
        self.completion_tokens = completion_tokens
        if cost is not None:
            self.cost = cost
        if reasoning_tokens is not None:
            self.completion_tokens_details = _OADetails(reasoning_tokens)


class _OAResp:
    def __init__(self, content, usage):
        self.choices = [_Choice(content)]
        self.usage = usage


class _OAComps:
    def __init__(self, outer):
        self.outer = outer

    def create(self, **kwargs):
        self.outer.last_kwargs = kwargs
        return _OAResp("回你", _OAUsage(12, 7, cost=self.outer.cost,
                                        reasoning_tokens=self.outer.reasoning_tokens))


class _OAChat:
    def __init__(self, outer):
        self.completions = _OAComps(outer)


class FakeOpenAIClient:
    def __init__(self, cost=None, reasoning_tokens=None):
        self.chat = _OAChat(self)
        self.last_kwargs = None
        self.cost = cost
        self.reasoning_tokens = reasoning_tokens


# ── Anthropic provider ────────────────────────────────────────────
def test_anthropic_provider_returns_text_and_usage():
    fake = FakeAnthropicClient()
    p = AnthropicNativeProvider(client=fake, name="anthropic")
    r = p.complete(model="claude-x", prompt="hi", max_tokens=100, temperature=0.0)
    assert isinstance(r, LLMResponse)
    assert r.text == "哈囉"
    assert r.input_tokens == 10 and r.output_tokens == 5
    assert r.cache_read_tokens == 3 and r.cache_create_tokens == 2
    assert r.model == "claude-x" and r.provider == "anthropic"
    assert fake.last_kwargs["model"] == "claude-x"
    assert fake.last_kwargs["messages"] == [{"role": "user", "content": "hi"}]
    assert fake.last_kwargs["max_tokens"] == 100


def test_anthropic_provider_caches_system_when_requested():
    fake = FakeAnthropicClient()
    p = AnthropicNativeProvider(client=fake, name="anthropic")
    p.complete(model="m", prompt="hi", max_tokens=10, temperature=0.0,
               system="RULES", cache_system=True)
    sys = fake.last_kwargs["system"]
    assert isinstance(sys, list)
    assert sys[0]["type"] == "text"
    assert sys[0]["text"] == "RULES"
    assert sys[0]["cache_control"] == {"type": "ephemeral"}


def test_anthropic_provider_plain_system_without_cache():
    fake = FakeAnthropicClient()
    p = AnthropicNativeProvider(client=fake, name="anthropic")
    p.complete(model="m", prompt="hi", max_tokens=10, temperature=0.0,
               system="RULES", cache_system=False)
    assert fake.last_kwargs["system"] == "RULES"


# ── OpenAI 相容 provider（OpenRouter 等）────────────────────────────
def test_openai_compat_returns_text_and_maps_usage():
    fake = FakeOpenAIClient()
    p = OpenAICompatProvider(client=fake, name="openrouter", base_url="http://x")
    r = p.complete(model="openai/gpt-x", prompt="hi", max_tokens=50,
                   temperature=0.2, system="SYS")
    assert r.text == "回你"
    assert r.input_tokens == 12 and r.output_tokens == 7
    assert r.model == "openai/gpt-x" and r.provider == "openrouter"
    msgs = fake.last_kwargs["messages"]
    assert msgs == [
        {"role": "system", "content": "SYS"},
        {"role": "user", "content": "hi"},
    ]


def test_openai_compat_reads_cost_from_response():
    fake = FakeOpenAIClient(cost=0.0012)
    p = OpenAICompatProvider(client=fake, name="openrouter", base_url="http://x")
    r = p.complete(model="m", prompt="hi", max_tokens=10, temperature=0)
    assert r.cost_usd == 0.0012


def test_openai_compat_no_system_message_when_system_none():
    fake = FakeOpenAIClient()
    p = OpenAICompatProvider(client=fake, name="openrouter", base_url="http://x")
    p.complete(model="m", prompt="hi", max_tokens=10, temperature=0)
    assert fake.last_kwargs["messages"] == [{"role": "user", "content": "hi"}]


# ── 關思考(reasoning_enabled)與思考偵測警報(規格 §5.1 / §14-3)────────
def test_openai_compat_passes_reasoning_disabled():
    """role 設 reasoning_enabled=false → 轉譯為 OpenRouter reasoning.enabled=false。"""
    fake = FakeOpenAIClient()
    p = OpenAICompatProvider(client=fake, name="openrouter", base_url="http://x")
    p.complete(model="m", prompt="hi", max_tokens=10, temperature=0,
               reasoning_enabled=False)
    assert fake.last_kwargs["extra_body"]["reasoning"] == {"enabled": False}


def test_openai_compat_omits_reasoning_key_when_not_set():
    """沒設 reasoning_enabled(None)＝不帶參數,維持現況行為。"""
    fake = FakeOpenAIClient()
    p = OpenAICompatProvider(client=fake, name="openrouter", base_url="http://x")
    p.complete(model="m", prompt="hi", max_tokens=10, temperature=0)
    assert "reasoning" not in fake.last_kwargs.get("extra_body", {})


def test_openai_compat_surfaces_reasoning_tokens():
    """回應含思考 token 數 → 透出到 LLMResponse(供監控/驗收斷言)。"""
    fake = FakeOpenAIClient(reasoning_tokens=42)
    p = OpenAICompatProvider(client=fake, name="openrouter", base_url="http://x")
    r = p.complete(model="m", prompt="hi", max_tokens=10, temperature=0)
    assert r.reasoning_tokens == 42


def test_openai_compat_warns_when_reasoning_sneaks_back(caplog):
    """關了思考、供應端卻回思考 token → 記警告(供應端無視參數的警報)。"""
    import logging
    fake = FakeOpenAIClient(reasoning_tokens=99)
    p = OpenAICompatProvider(client=fake, name="openrouter", base_url="http://x")
    with caplog.at_level(logging.WARNING):
        r = p.complete(model="m", prompt="hi", max_tokens=10, temperature=0,
                       reasoning_enabled=False)
    assert r.reasoning_tokens == 99
    assert any("思考" in rec.message for rec in caplog.records)


def test_anthropic_provider_ignores_reasoning_flag():
    """Anthropic 原廠不吃這參數 → 優雅忽略、不外洩到 SDK kwargs。"""
    fake = FakeAnthropicClient()
    p = AnthropicNativeProvider(client=fake, name="anthropic")
    r = p.complete(model="m", prompt="hi", max_tokens=10, temperature=0,
                   reasoning_enabled=False)
    assert r.text == "哈囉"
    assert "reasoning" not in fake.last_kwargs
    assert "reasoning_enabled" not in fake.last_kwargs
