"""greeting 分支處理：根據用戶身分動態問候（Haiku）。"""
from __future__ import annotations

import logging

from core.llm_client import call_writer, load_prompt

logger = logging.getLogger(__name__)

_PROMPT = load_prompt("greeting_handler")


def respond(state: dict, user_message: str) -> str:
    user = state["user_info"]
    prompt = _PROMPT.format(
        is_logged_in="是" if user["is_logged_in"] else "否",
        user_name_or_default=user.get("user_name") or "（訪客）",
        is_returning_customer="是" if user.get("purchase_history") else "否",
        user_message=user_message,
    )
    fallback = "您好！請問有什麼需要協助的嗎？例如影片播放、退款、帳號相關的問題我都可以幫忙。"
    return call_writer(prompt, max_tokens=120, temperature=0.5, fallback=fallback)
