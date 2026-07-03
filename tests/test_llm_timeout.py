"""逾時測試：provider 的 complete() 會把 timeout 帶進底層 create()，避免模型卡死時整支請求跟著卡。

用最小假 client（記錄傳給 create 的 kwargs）驗證，不連線、不花錢。
"""
from core.llm_providers import AnthropicNativeProvider, OpenAICompatProvider


# ── 最小假 Anthropic client ──────────────────────────────────────
class _RecAnthMessages:
    def __init__(self, outer):
        self.outer = outer

    def create(self, **kw):
        self.outer.last_kwargs = kw

        class _U:
            input_tokens = output_tokens = 1
            cache_read_input_tokens = cache_creation_input_tokens = 0

        class _B:
            type = "text"
            text = "x"

        class _R:
            content = [_B()]
            usage = _U()

        return _R()


class _FakeAnth:
    def __init__(self):
        self.messages = _RecAnthMessages(self)
        self.last_kwargs = None


# ── 最小假 OpenAI 相容 client ─────────────────────────────────────
class _RecComps:
    def __init__(self, outer):
        self.outer = outer

    def create(self, **kw):
        self.outer.last_kwargs = kw

        class _M:
            content = "x"

        class _C:
            message = _M()

        class _U:
            prompt_tokens = completion_tokens = 1

        class _R:
            choices = [_C()]
            usage = _U()

        return _R()


class _FakeOAChat:
    def __init__(self, outer):
        self.completions = _RecComps(outer)


class _FakeOA:
    def __init__(self):
        self.last_kwargs = None
        self.chat = _FakeOAChat(self)


def test_anthropic_passes_timeout():
    fake = _FakeAnth()
    p = AnthropicNativeProvider(client=fake, name="anthropic", timeout=30.0)
    p.complete(model="m", prompt="hi", max_tokens=10, temperature=0.0)
    assert fake.last_kwargs["timeout"] == 30.0


def test_openai_passes_timeout():
    fake = _FakeOA()
    p = OpenAICompatProvider(client=fake, name="openrouter", base_url="http://x", timeout=30.0)
    p.complete(model="m", prompt="hi", max_tokens=10, temperature=0.0)
    assert fake.last_kwargs["timeout"] == 30.0


def test_no_timeout_key_when_not_set():
    """沒指定 timeout 時，不塞 timeout（保持與現行呼叫一致，不改行為）。"""
    fake = _FakeAnth()
    p = AnthropicNativeProvider(client=fake, name="anthropic")
    p.complete(model="m", prompt="hi", max_tokens=10, temperature=0.0)
    assert "timeout" not in fake.last_kwargs


def test_zero_timeout_not_passed():
    """timeout=0（誤設）不塞給 SDK，避免「0 秒逾時＝每次都失敗」。"""
    fake = _FakeAnth()
    p = AnthropicNativeProvider(client=fake, name="anthropic", timeout=0)
    p.complete(model="m", prompt="hi", max_tokens=10, temperature=0.0)
    assert "timeout" not in fake.last_kwargs


def test_build_provider_survives_bad_timeout_env(monkeypatch):
    """LLM_TIMEOUT_SECONDS 填非數字時，build_provider 不該炸（不然整台服務開不了機）。"""
    from core import model_config
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "60s")
    cfg = {"providers": {"anthropic": {"type": "anthropic", "api_key_env": "NOPE"}}, "roles": {}}
    p = model_config.build_provider("anthropic", cfg)
    assert p is not None
