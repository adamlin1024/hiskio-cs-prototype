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

# v6.2 Token 統計（簡易記憶體 buffer，給 benchmark 與 dashboard 用）
# 每筆：{"model", "input", "output", "cache_read", "cache_create"}
_usage_log: list[dict] = []


def reset_usage() -> None:
    _usage_log.clear()


def get_usage_summary() -> dict:
    """回傳分模型統計 + 估算成本。"""
    # 公定價（USD per million token）
    PRICING = {
        "sonnet": {"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_create": 3.75},
        "haiku":  {"input": 0.80, "output": 4.0, "cache_read": 0.08, "cache_create": 1.0},
    }
    summary: dict = {"calls": len(_usage_log), "by_model": {}, "total_usd": 0.0}
    for entry in _usage_log:
        m = entry["model"]
        family = "sonnet" if "sonnet" in m else ("haiku" if "haiku" in m else "other")
        bucket = summary["by_model"].setdefault(m, {
            "calls": 0, "input": 0, "output": 0, "cache_read": 0, "cache_create": 0, "usd": 0.0,
        })
        bucket["calls"] += 1
        bucket["input"] += entry["input"]
        bucket["output"] += entry["output"]
        bucket["cache_read"] += entry["cache_read"]
        bucket["cache_create"] += entry["cache_create"]
        if family in PRICING:
            p = PRICING[family]
            cost = (
                entry["input"] * p["input"]
                + entry["output"] * p["output"]
                + entry["cache_read"] * p["cache_read"]
                + entry["cache_create"] * p["cache_create"]
            ) / 1_000_000
            bucket["usd"] += cost
            summary["total_usd"] += cost
    return summary


def _get_client() -> Anthropic:
    global _client
    if _client is None:
        api_key = (os.getenv("ANTHROPIC_API_KEY") or "").strip()
        if not api_key:
            raise RuntimeError("ANTHROPIC_API_KEY 未設定，請檢查 .env")
        _client = Anthropic(api_key=api_key)
    return _client


def _model(env_key: str, default: str) -> str:
    """讀環境變數的 model ID，自動 strip 前後空白避免設定時誤打字導致 404。"""
    value = (os.getenv(env_key) or "").strip()
    return value or default


def call_claude(
    *,
    model: str,
    prompt: str,
    max_tokens: int,
    temperature: float = 0.0,
    system: str | list | None = None,
    cache_system: bool = False,
    fallback: str = "",
) -> str:
    """呼叫 Claude，失敗時回傳 fallback 字串並 log 錯誤。

    system 可以是字串或結構化 list（含 cache_control）。
    cache_system=True 時自動把 system 包成可快取的結構化 block。
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
            if isinstance(system, str) and cache_system:
                # 字串 system + 要求快取 → 包成結構化 block
                kwargs["system"] = [{
                    "type": "text",
                    "text": system,
                    "cache_control": {"type": "ephemeral"},
                }]
            else:
                kwargs["system"] = system
        resp = client.messages.create(**kwargs)
        # v6.2：記錄 token 用量到 _usage_log
        usage = getattr(resp, "usage", None)
        if usage is not None:
            _usage_log.append({
                "model": model,
                "input": getattr(usage, "input_tokens", 0) or 0,
                "output": getattr(usage, "output_tokens", 0) or 0,
                "cache_read": getattr(usage, "cache_read_input_tokens", 0) or 0,
                "cache_create": getattr(usage, "cache_creation_input_tokens", 0) or 0,
            })
        text_blocks = [b.text for b in resp.content if getattr(b, "type", None) == "text"]
        return "".join(text_blocks).strip()
    except APIError as e:
        logger.error("Claude API 錯誤 (model=%s): %s", model, e)
        return fallback
    except Exception as e:
        logger.exception("呼叫 Claude 發生未預期錯誤 (model=%s): %s", model, e)
        return fallback


def call_sonnet(
    prompt: str,
    *,
    max_tokens: int = 600,
    temperature: float = 0.6,
    system: str | list | None = None,
    cache_system: bool = False,
    **kwargs,
) -> str:
    return call_claude(
        model=_model("MODEL_SONNET", "claude-sonnet-4-6"),
        prompt=prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system,
        cache_system=cache_system,
        **kwargs,
    )


def call_haiku(
    prompt: str,
    *,
    max_tokens: int = 200,
    temperature: float = 0.0,
    system: str | list | None = None,
    cache_system: bool = False,
    **kwargs,
) -> str:
    return call_claude(
        model=_model("MODEL_HAIKU", "claude-haiku-4-5-20251001"),
        prompt=prompt,
        max_tokens=max_tokens,
        temperature=temperature,
        system=system,
        cache_system=cache_system,
        **kwargs,
    )


_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


def load_prompt(name: str) -> str:
    """從 prompts/{name}.txt 讀取模板字串。節點呼叫時用 .format(...) 帶參數。"""
    path = _PROMPT_DIR / f"{name}.txt"
    return path.read_text(encoding="utf-8")
