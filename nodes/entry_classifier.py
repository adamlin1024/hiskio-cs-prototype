"""入口分類節點（v4，取代 router）。

Haiku 一個 call 同時判斷：
- greeting：純打招呼
- unclear：訊息模糊／亂碼
- off_topic：跟 HiSKIO 服務無關
- customer_service：真的是客服問題

非預期回傳 fallback 為 customer_service（寧可走完整流程，不要誤擋）。
"""
from __future__ import annotations

import logging

from core.llm_client import call_haiku, load_prompt
from core.text_utils import format_recent_history

logger = logging.getLogger(__name__)

_PROMPT = load_prompt("entry_classifier")
_VALID = {"greeting", "unclear", "off_topic", "customer_service"}


def classify(state: dict, user_message: str) -> str:
    """回傳 greeting / unclear / off_topic / customer_service。"""
    prompt = _PROMPT.format(
        phase=state["phase"],
        recent_history=format_recent_history(state["chat_history"]),
        consecutive_unclear_count=state["intent_state"]["consecutive_unclear_count"],
        user_message=user_message,
    )
    raw = call_haiku(prompt, max_tokens=10, temperature=0.0, fallback="customer_service")
    if not raw:
        return "customer_service"
    token = raw.strip().split()[0].lower().strip(".,!?'\"")
    if token in _VALID:
        return token
    logger.warning("entry_classifier 回傳非預期值 %r，fallback=customer_service", raw)
    return "customer_service"
