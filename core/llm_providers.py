"""模型供應商抽象層。

把「中立請求」翻成各家 SDK 的呼叫，回傳「中立回應」LLMResponse。
- provider 的 complete() 只負責「呼叫 + 解析」；錯誤處理與 fallback 由上層門面(llm_client)負責。
- client 可注入（測試用假 client）；未注入時延遲(lazy)建立真 client，
  所以跑測試不需要真的安裝 openai、也不會連線。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)


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
    # 思考(reasoning)token 數：供監控與「關思考卻偷思考」警報用（規格 §14-3）。
    reasoning_tokens: int = 0


class LLMProvider:
    name: str = "base"

    def complete(self, *, model: str, prompt: str, max_tokens: int,
                 temperature: float, system: str | None = None,
                 cache_system: bool = False,
                 reasoning_enabled: bool | None = None,
                 images: list[str] | None = None) -> LLMResponse:
        raise NotImplementedError


class AnthropicNativeProvider(LLMProvider):
    """直連 Anthropic 原廠（保留 prompt caching 折扣）。"""

    def __init__(self, *, api_key: str | None = None, client: Any = None,
                 name: str = "anthropic", timeout: float | None = None):
        self.name = name
        self._api_key = api_key
        self._client = client
        self._timeout = timeout

    def _get_client(self):
        if self._client is None:
            from anthropic import Anthropic
            self._client = Anthropic(api_key=self._api_key)
        return self._client

    def complete(self, *, model, prompt, max_tokens, temperature,
                 system=None, cache_system=False, reasoning_enabled=None, images=None):
        # reasoning_enabled 是 OpenAI 相容端點(OpenRouter)專用參數;原廠 SDK 不吃 → 優雅忽略。
        if images:
            # 讀圖走 openai_compat(vision role);原廠路徑目前不支援 → 記警告、退純文字(不炸)。
            logger.warning("AnthropicNative 未支援 images 參數,已忽略 %d 張圖", len(images))
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
        if self._timeout is not None and self._timeout > 0:
            kwargs["timeout"] = self._timeout
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
                 cost_from_response: bool = True, timeout: float | None = None):
        self.name = name
        self._base_url = base_url
        self._api_key = api_key
        self._client = client
        self._cost_from_response = cost_from_response
        self._timeout = timeout

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI
            self._client = OpenAI(base_url=self._base_url, api_key=self._api_key)
        return self._client

    def complete(self, *, model, prompt, max_tokens, temperature,
                 system=None, cache_system=False, reasoning_enabled=None, images=None):
        client = self._get_client()
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        if images:
            # 視覺輸入(讀圖員):文字+圖片組成 content 陣列(OpenAI 相容格式;圖=data URI 或公開網址)
            content: list[dict] = [{"type": "text", "text": prompt}]
            content.extend({"type": "image_url", "image_url": {"url": u}} for u in images)
            messages.append({"role": "user", "content": content})
        else:
            messages.append({"role": "user", "content": prompt})
        create_kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
        extra_body: dict[str, Any] = {}
        if self._cost_from_response:
            # OpenRouter 需明確要求(usage.include)才會回傳實際費用；否則 usage.cost 不存在。
            extra_body["usage"] = {"include": True}
        if reasoning_enabled is not None:
            # 混合思考模型(DeepSeek V4 等)的思考開關。關思考=2026-07-04 事故根治(規格 §1.1/§5.1)。
            extra_body["reasoning"] = {"enabled": bool(reasoning_enabled)}
        if extra_body:
            create_kwargs["extra_body"] = extra_body
        if self._timeout is not None and self._timeout > 0:
            create_kwargs["timeout"] = self._timeout
        resp = client.chat.completions.create(**create_kwargs)
        choice = resp.choices[0]
        text = (getattr(choice.message, "content", "") or "").strip()
        usage = getattr(resp, "usage", None)
        cost = None
        if self._cost_from_response and usage is not None:
            cost = getattr(usage, "cost", None)
        details = getattr(usage, "completion_tokens_details", None)
        reasoning_tokens = (getattr(details, "reasoning_tokens", 0) or 0) if details else 0
        if reasoning_enabled is False and reasoning_tokens > 0:
            # 供應端無視關思考參數的警報(規格 §14-3):思考偷跑=延遲/成本/截斷風險回歸。
            logger.warning(
                "已要求關閉思考,但供應端回報思考 token=%d(model=%s)——"
                "OpenRouter 路由可能無視 reasoning 參數,請檢查供應商路由或鎖定供應商。",
                reasoning_tokens, model,
            )
        return LLMResponse(
            text=text,
            input_tokens=getattr(usage, "prompt_tokens", 0) or 0,
            output_tokens=getattr(usage, "completion_tokens", 0) or 0,
            model=model,
            provider=self.name,
            cost_usd=cost,
            reasoning_tokens=reasoning_tokens,
        )
