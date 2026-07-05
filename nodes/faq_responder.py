"""節點 3：FAQ 回應（混合模式，Haiku）。

核心步驟由程式組成編號清單後塞入 prompt，LLM 只能潤飾開場與結尾。
這是「FAQ 不會幻覺」的核心保護機制。
"""
from __future__ import annotations

import logging

from core.llm_client import call_writer, load_prompt

logger = logging.getLogger(__name__)

_PROMPT = load_prompt("faq_responder")


def _format_core_steps(steps: list[str]) -> str:
    return "\n".join(f"{i + 1}. {s}" for i, s in enumerate(steps))


def respond(state: dict, faq_data: dict, user_message: str) -> str:
    """根據命中的 FAQ 生成回應。"""
    user = state["user_info"]
    issue = state["issue_context"]

    prompt = _PROMPT.format(
        is_logged_in="是" if user["is_logged_in"] else "否",
        is_returning_customer="是" if user.get("purchase_history") else "否",
        user_emotion=issue.get("user_emotion", "中性"),
        faq_category=faq_data.get("category", ""),
        core_steps_formatted=_format_core_steps(faq_data.get("core_steps", [])),
        fallback_message=faq_data.get("fallback_message", ""),
        user_message=user_message,
    )

    fallback = (
        "了解，您可以嘗試以下步驟：\n"
        + _format_core_steps(faq_data.get("core_steps", []))
        + "\n\n"
        + faq_data.get("fallback_message", "")
    )
    return call_writer(prompt, max_tokens=400, temperature=0.5, fallback=fallback)
