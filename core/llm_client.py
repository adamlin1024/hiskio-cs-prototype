"""Anthropic Claude API 封裝。

只暴露兩個函式：call_sonnet / call_haiku。失敗時記錄 log 並回傳 fallback。
規格規定主對話用 Sonnet、輕量任務用 Haiku；模型 ID 從環境變數讀取。
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from anthropic import Anthropic, APIError

logger = logging.getLogger(__name__)

_client: Anthropic | None = None


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY 未設定，請檢查 .env")
        _client = Anthropic(api_key=api_key)
    return _client


def _model(env_key: str, default: str) -> str:
    return os.getenv(env_key, default)


def call_claude(
    *,
    model: str,
    prompt: str,
    max_tokens: int,
    temperature: float = 0.0,
    system: str | None = None,
    fallback: str = "",
) -> str:
    """呼叫 Claude，失敗時回傳 fallback 字串並 log 錯誤。

    雛形階段全部走 single-turn（messages 只有一則 user），把所有上下文塞進 prompt 字串。
    """
    try:
        client = _get_client()
        kwargs: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "messages": [{"role": "user", "content": prompt}],
        }
        if system:
            kwargs["system"] = system
        resp = client.messages.create(**kwargs)
        text_blocks = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
        return "".join(text_blocks).strip()
    except APIError as e:
        logger.error("Claude API 錯誤 (model=%s): %s", model, e)
        return fallback
    except Exception as e:
        logger.exception("呼叫 Claude 發生未預期錯誤 (model=%s): %s", model, e)
        return fallback


def call_sonnet(prompt: str, *, max_tokens: int = 600, temperature: float = 0.6, **kwargs) -> str:
    return call_claude(
        model=_model("MODEL_SONNET", "claude-sonnet-4-6"),
        prompt=prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        **kwargs,
    )


def call_haiku(prompt: str, *, max_tokens: int = 200, temperature: float = 0.0, **kwargs) -> str:
    return call_claude(
        model=_model("MODEL_HAIKU", "claude-haiku-4-5-20251001"),
        prompt=prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        **kwargs,
    )


_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


def load_prompt(name: str) -> str:
    """從 prompts/{name}.txt 讀取模板字串。節點呼叫時用 .format(...) 帶參數。"""
    path = _PROMPT_DIR / f"{name}.txt"
    return path.read_text(encoding="utf-8")
