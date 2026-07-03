"""模型供應商抽象層。

把「中立請求」翻成各家 SDK 的呼叫，回傳「中立回應」LLMResponse。
- provider 的 complete() 只負責「呼叫 + 解析」；錯誤處理與 fallback 由上層門面(llm_client)負責。
- client 可注入（測試用假 client）；未注入時延遲(lazy)建立真 client，
  所以跑測試不需要真的安裝 openai、也不會連線。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class LLMResponse:
    """provider-neutral 回應。cost_usd=None 代表供應商沒回傳費用（改由上層查價目表）。"""
    text: str
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_create_tokens: int = 0
    model: str = ""
    provider: str = ""
    cost_usd: float | None = None


class LLMProvider:
    name: str = "base"

    def complete(self, *, model: str, prompt: str, max_tokens: int,
                 temperature: float, system: str | None = None,
                 cache_system: bool = False) -> LLMResponse:
        raise NotImplementedError


class AnthropicNativeProvider(LLMProvider):
    """直連 Anthropic 原廠（保留 prompt caching 折扣）。"""

    def __init__(self, *, api_key: str | None = None, client: Any = None,
                 name: str = "anthropic"):
        self.name = name
        self._api_key = api_key
        self._client = client

    def _get_client(self):
        if self._client is None:
            from anthropic import Anthropic
            self._client = Anthropic(api_key=self._api_key)
        return self._client

    def complete(self, *, model, prompt, max_tokens, temperature,
                 system=None, cache_system=False):
        client = self._get_client()
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            if cache_system:
                kwargs["system"] = [{
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }]
            else:
                kwargs["system"] = system
        resp = client.messages.create(**kwargs)
        text = "".join(
            b.text for b in resp.content if getattr(b, "type", None) == "text"
        ).strip()
        usage = getattr(resp, "usage", None)
        return LLMResponse(
            text=text,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            cache_read_tokens=getattr(usage, "cache_read_input_tokens", 0) or 0,
            cache_create_tokens=getattr(usage, "cache_creation_input_tokens", 0) or 0,
            model=model,
            provider=self.name,
        )


class OpenAICompatProvider(LLMProvider):
    """OpenAI 相容端點：OpenRouter（預設）、OpenAI 官方、或自架相容服務。

    快取折扣(cache_system)在此忽略（優雅退化，不報錯）。
    """

    def __init__(self, *, base_url: str, api_key: str | None = None,
                 client: Any = None, name: str = "openrouter",
                 cost_from_response: bool = True):
        self.name = name
        self._base_url = base_url
        self._api_key = api_key
        self._client = client
        self._cost_from_response = cost_from_response

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(base_url=self._base_url, api_key=self._api_key)
        return self._client

    def complete(self, *, model, prompt, max_tokens, temperature,
                 system=None, cache_system=False):
        client = self._get_client()
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        resp = client.chat.completions.create(
            model=model,
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
        )
        choice = resp.choices[0]
        text = (getattr(choice.message, "content", "") or "").strip()
        usage = getattr(resp, "usage", None)
        cost = None
        if self._cost_from_response and usage is not None:
            cost = getattr(usage, "cost", None)
        return LLMResponse(
            text=text,
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
            model=model,
            provider=self.name,
            cost_usd=cost,
        )
