"""意圖明確度判斷節點（v5.1：加 role + needs_user_selection）。

只在 entry_classifier 回傳 customer_service 後才呼叫。

新輸出：
- detected_intents: 物件陣列 [{text, role, in_scope}]
    role 為 primary | secondary | context
- user_specified_priority: 用戶有沒有自己明示優先順序
- needs_user_selection: 系統要不要叫用戶選
- referenced_intent_index: 用戶用指稱詞時，指向 intent_log 中對應項目的索引
"""
from __future__ import annotations

import logging

from core.llm_client import call_haiku, load_prompt
from core.text_utils import extract_json_object, format_recent_history

logger = logging.getLogger(__name__)

_PROMPT = load_prompt("intent_clarity")
_VALID_ROLES = {"primary", "secondary", "context"}


def _format_intent_log(intent_log: list[dict]) -> str:
    if not intent_log:
        return "（無）"
    lines = []
    for i, item in enumerate(intent_log):
        role = item.get("role", "primary")
        lines.append(f"  [{i}] {item['text']}（status={item['status']}, role={role}）")
    return "\n".join(lines)


def analyze(state: dict, user_message: str) -> dict:
    """回傳結構：
    {
        "detected_intents": [{"text": str, "role": str, "in_scope": bool}, ...],
        "user_specified_priority": bool,
        "needs_user_selection": bool,
        "referenced_intent_index": int | None,
    }
    """
    intent_log = state["intent_state"].get("intent_log") or []
    prompt = _PROMPT.format(
        user_message=user_message,
        recent_history=format_recent_history(state["chat_history"]),
        intent_log_str=_format_intent_log(intent_log),
    )
    raw = call_haiku(prompt, max_tokens=400, temperature=0.0, fallback="")
    parsed = extract_json_object(raw)

    fallback = {
        "detected_intents": [{"text": user_message, "role": "primary", "in_scope": True}],
        "user_specified_priority": False,
        "needs_user_selection": False,
        "referenced_intent_index": None,
    }
    if parsed is None:
        logger.warning("intent_clarity 解析失敗，fallback。raw=%r", raw[:200])
        return fallback

    # 解析 detected_intents（容錯：dict 或 str 都接）
    detected = parsed.get("detected_intents", [])
    if not isinstance(detected, list):
        detected = []
    seen: set[str] = set()
    cleaned: list[dict] = []
    for item in detected:
        if isinstance(item, dict):
            text = str(item.get("text", "")).strip()
            role = str(item.get("role", "primary")).lower().strip()
            if role not in _VALID_ROLES:
                role = "primary"
            in_scope = bool(item.get("in_scope", True))
        elif item:
            text = str(item).strip()
            role = "primary"
            in_scope = True
        else:
            continue
        if text and text not in seen:
            seen.add(text)
            cleaned.append({"text": text, "role": role, "in_scope": in_scope})

    # 解析其他欄位
    user_specified = bool(parsed.get("user_specified_priority", False))

    # 推論 needs_user_selection（即使 LLM 給的也用我們自己推一次保險）
    primary_count = sum(1 for it in cleaned if it["role"] == "primary")
    if primary_count >= 2 and not user_specified:
        needs_selection = True
    else:
        needs_selection = False
    # LLM 若明確說 false，尊重它（避免單意圖被硬塞列選項）
    llm_needs = parsed.get("needs_user_selection")
    if llm_needs is False:
        needs_selection = False

    ref_idx = parsed.get("referenced_intent_index")
    if ref_idx is not None:
        try:
            ref_idx = int(ref_idx)
            if ref_idx < 0 or ref_idx >= len(intent_log):
                ref_idx = None
        except (TypeError, ValueError):
            ref_idx = None

    return {
        "detected_intents": cleaned,
        "user_specified_priority": user_specified,
        "needs_user_selection": needs_selection,
        "referenced_intent_index": ref_idx,
    }
