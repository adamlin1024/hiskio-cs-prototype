"""LLM 對外門面（模型無關）。

節點只喊「等級」(role)，不指定廠牌／型號：
- call_reasoning(...) → 聰明檔（主對話、推理、工單摘要）
- call_fast(...)      → 快省檔（路由、分類、抽取、判斷離題 …）
- call_role(role,...) → 通用，之後要加第 3 級直接用這個

背後用哪個供應商的哪個模型，由 config/models.toml 決定（見 core/model_config）。
本層負責：解析等級 → 呼叫 provider → 記錄用量 → 出錯回 fallback。
"""
from __future__ import annotations

import logging
from pathlib import Path

from core.model_config import get_registry

logger = logging.getLogger(__name__)

# 用量統計（記憶體 buffer，給 benchmark 與後台 dashboard 用）
# 每筆：{provider, model, role, input, output, cache_read, cache_create, cost_usd}
_usage_log: list[dict] = []


def reset_usage() -> None:
    _usage_log.clear()


def get_usage_summary(*, registry=None) -> dict:
    """分模型統計 + 成本。

    成本來源優先序：①供應商回傳的實際費用 ②設定檔價目表 ③都沒有 → 標 cost_known=False。
    """
    reg = registry or get_registry()
    summary: dict = {"calls": len(_usage_log), "by_model": {}, "total_usd": 0.0}
    for e in _usage_log:
        m = e["model"]
        bucket = summary["by_model"].setdefault(m, {
            "calls": 0, "input": 0, "output": 0, "cache_read": 0,
            "cache_create": 0, "usd": 0.0, "cost_known": True,
        })
        bucket["calls"] += 1
        bucket["input"] += e["input"]
        bucket["output"] += e["output"]
        bucket["cache_read"] += e["cache_read"]
        bucket["cache_create"] += e["cache_create"]

        cost = e.get("cost_usd")
        if cost is None:
            pricing = reg.pricing(m)
            if pricing:
                cost = (
                    e["input"] * pricing.get("input", 0)
                    + e["output"] * pricing.get("output", 0)
                    + e["cache_read"] * pricing.get("cache_read", 0)
                    + e["cache_create"] * pricing.get("cache_create", 0)
                ) / 1_000_000
        if cost is None:
            bucket["cost_known"] = False
        else:
            bucket["usd"] += cost
            summary["total_usd"] += cost
    return summary


def call_role(
    role: str,
    prompt: str,
    *,
    max_tokens: int,
    temperature: float = 0.0,
    system: str | None = None,
    cache_system: bool = False,
    fallback: str = "",
    registry=None,
) -> str:
    """依「等級」呼叫模型，失敗時記 log 並回傳 fallback 字串。"""
    reg = registry or get_registry()
    # 等級/設定解析在 try 外：設定寫錯要明顯報錯，不被 fallback 吃掉。
    provider, model = reg.provider_for_role(role)
    try:
        resp = provider.complete(
            model=model,
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            cache_system=cache_system,
        )
    except Exception as e:  # 只吃「呼叫當下」的錯（API 逾時、額度…）→ 優雅退化
        logger.exception("呼叫模型失敗 (role=%s, provider=%s, model=%s): %s",
                         role, provider.name, model, e)
        return fallback

    _usage_log.append({
        "provider": resp.provider,
        "model": resp.model,
        "role": role,
        "input": resp.input_tokens,
        "output": resp.output_tokens,
        "cache_read": resp.cache_read_tokens,
        "cache_create": resp.cache_create_tokens,
        "cost_usd": resp.cost_usd,
    })
    return resp.text


def call_reasoning(
    prompt: str,
    *,
    max_tokens: int = 600,
    temperature: float = 0.6,
    system: str | None = None,
    cache_system: bool = False,
    fallback: str = "",
    registry=None,
) -> str:
    """聰明檔：主對話、推理、工單摘要。"""
    return call_role(
        "reasoning", prompt,
        max_tokens=max_tokens, temperature=temperature,
        system=system, cache_system=cache_system,
        fallback=fallback, registry=registry,
    )


def call_fast(
    prompt: str,
    *,
    max_tokens: int = 200,
    temperature: float = 0.0,
    system: str | None = None,
    cache_system: bool = False,
    fallback: str = "",
    registry=None,
) -> str:
    """快省檔：路由、分類、抽取、判斷離題等輕量任務。"""
    return call_role(
        "fast", prompt,
        max_tokens=max_tokens, temperature=temperature,
        system=system, cache_system=cache_system,
        fallback=fallback, registry=registry,
    )


_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


def load_prompt(name: str) -> str:
    """從 prompts/{name}.txt 讀取模板字串。節點呼叫時用 .format(...) 帶參數。"""
    path = _PROMPT_DIR / f"{name}.txt"
    return path.read_text(encoding="utf-8")
