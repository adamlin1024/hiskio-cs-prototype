"""LLM 對外門面（模型無關）。

節點只喊「等級」(role)，不指定廠牌／型號（一顆腦改版,規格 design-one-brain-2026-07-06 §5）：
- call_triage(...) → 分診檔（分診腦決策、好/不用語意判斷、問候）
- call_writer(...) → 寫手檔（KB 寫手、FAQ 潤飾、確認回應）
- call_role(role,...) → 通用，之後要加新等級直接用這個
- call_reasoning / call_fast → **過渡別名**（reasoning→triage、fast→writer），
  P1/P2 呼叫點遷移完成後移除。

背後用哪個供應商的哪個模型，由 config/models.toml 決定（見 core/model_config）；
role 層參數（如 reasoning_enabled=false 關思考）一路傳到 provider。
本層負責：解析等級 → 呼叫 provider → 記錄用量 → 出錯回 fallback。
"""
from __future__ import annotations

import logging
from collections import deque
from pathlib import Path

from core.model_config import get_registry

logger = logging.getLogger(__name__)

# 用量統計（記憶體 buffer，給 benchmark 與後台 dashboard 用）
# 每筆：{provider, model, role, input, output, cache_read, cache_create, cost_usd}
# 有上限：避免長時間執行記憶體無限成長（超過上限自動丟最舊的）。
_USAGE_LOG_MAXLEN = 50000
_usage_log: "deque[dict]" = deque(maxlen=_USAGE_LOG_MAXLEN)


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

    # 標示總金額是否完整：有任何模型算不出價 → total_usd 是低估值，需明確告知消費端。
    unknown = [m for m, b in summary["by_model"].items() if not b["cost_known"]]
    summary["unknown_models"] = unknown
    summary["cost_complete"] = len(unknown) == 0
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
    # 全程納入防護：設定/等級解析與呼叫都可能出錯，對外請求一律優雅退化回 fallback。
    # 設定寫錯的「大聲報錯」改由 app 啟動時 validate_model_config 負責，
    # 在開機階段就擋下壞設定，不讓它流到線上請求（見 app.py startup）。
    try:
        reg = registry or get_registry()
        provider, model = reg.provider_for_role(role)
        # role 層呼叫參數(如 reasoning_enabled);registry 不支援時視為無參數(向後相容)。
        params = reg.params_for_role(role) if hasattr(reg, "params_for_role") else {}
        resp = provider.complete(
            model=model,
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            system=system,
            cache_system=cache_system,
            **params,
        )
    except Exception as e:
        logger.exception("呼叫模型失敗 (role=%s): %s", role, e)
        return fallback

    _usage_log.append({
        "provider": resp.provider,
        "model": resp.model,
        "role": role,
        "input": resp.input_tokens,
        "output": resp.output_tokens,
        "cache_read": resp.cache_read_tokens,
        "cache_create": resp.cache_create_tokens,
        "reasoning": getattr(resp, "reasoning_tokens", 0),
        "cost_usd": resp.cost_usd,
    })
    return resp.text


def call_triage(
    prompt: str,
    *,
    max_tokens: int = 600,
    temperature: float = 0.0,
    system: str | None = None,
    cache_system: bool = False,
    fallback: str = "",
    registry=None,
) -> str:
    """分診檔：分診腦決策、好/不用語意判斷、問候。決策要穩 → 預設溫度 0。"""
    return call_role(
        "triage", prompt,
        max_tokens=max_tokens, temperature=temperature,
        system=system, cache_system=cache_system,
        fallback=fallback, registry=registry,
    )


def call_writer(
    prompt: str,
    *,
    max_tokens: int = 600,
    temperature: float = 0.6,
    system: str | None = None,
    cache_system: bool = False,
    fallback: str = "",
    registry=None,
) -> str:
    """寫手檔：KB 寫手、FAQ 潤飾、確認回應。寫字要自然 → 預設溫度 0.6。"""
    return call_role(
        "writer", prompt,
        max_tokens=max_tokens, temperature=temperature,
        system=system, cache_system=cache_system,
        fallback=fallback, registry=registry,
    )


# ── 過渡別名(P1/P2 呼叫點遷移完成後移除)────────────────────────────
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
    """【過渡別名】舊聰明檔 → 分診檔(triage)。新程式請用 call_triage/call_writer。"""
    return call_role(
        "triage", prompt,
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
    """【過渡別名】舊快省檔 → 寫手檔(writer)。新程式請用 call_triage/call_writer。"""
    return call_role(
        "writer", prompt,
        max_tokens=max_tokens, temperature=temperature,
        system=system, cache_system=cache_system,
        fallback=fallback, registry=registry,
    )


_PROMPT_DIR = Path(__file__).resolve().parent.parent / "prompts"


def load_prompt(name: str) -> str:
    """從 prompts/{name}.txt 讀取模板字串。節點呼叫時用 .format(...) 帶參數。"""
    path = _PROMPT_DIR / f"{name}.txt"
    return path.read_text(encoding="utf-8")
