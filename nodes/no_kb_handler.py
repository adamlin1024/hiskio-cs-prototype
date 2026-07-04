"""KB 索引空陣列時的處理（v4 Phase D，Haiku）。

當 kb_indexer 完全沒挑到任何文章時呼叫，承認知識庫沒對應資訊 + 提議轉真人。
省去讓 Sonnet 硬答 + 提升一致性。
"""
from __future__ import annotations

import logging

from core.llm_client import call_fast, load_prompt

logger = logging.getLogger(__name__)

_PROMPT = load_prompt("no_kb_handler")


def respond(state: dict, user_message: str) -> str:
    user = state["user_info"]
    is_logged_in_text = (
        f"已登入會員（{user.get('user_name') or user.get('user_id')}）"
        if user.get("is_logged_in")
        else "訪客"
    )
    prompt = _PROMPT.format(
        user_message=user_message,
        is_logged_in_text=is_logged_in_text,
    )
    fallback = (
        "這個問題我們的知識庫目前沒有對應資訊，要不要幫您轉真人客服協助？"
        "回覆「好」或「不用」。"
    )
    return call_fast(prompt, max_tokens=200, temperature=0.4, fallback=fallback)
