"""unclear 分支的釐清節點。

第 1 次與第 2 次釐清各用一個 prompt（Haiku），
第 3 次由 orchestrator 直接觸發建單流程，不會呼叫這裡。
"""
from __future__ import annotations

import logging

from core.llm_client import call_fast, load_prompt

logger = logging.getLogger(__name__)

_PROMPT_FIRST = load_prompt("clarification_first")
_PROMPT_SECOND = load_prompt("clarification_second")


def respond(state: dict, user_message: str) -> str:
    """根據 consecutive_unclear_count 選用對應 prompt。"""
    count = state["intent_state"]["consecutive_unclear_count"]
    if count <= 1:
        prompt = _PROMPT_FIRST.format(user_message=user_message)
        fallback = "不好意思，能再多描述一些您遇到的狀況嗎？例如是影片播放、付款、帳號相關的問題嗎？"
    else:
        prompt = _PROMPT_SECOND.format(user_message=user_message)
        fallback = (
            "了解可能不太知道怎麼描述，請從以下類別選一個：\n"
            "1. 課程觀看問題（影片、字幕、進度）\n"
            "2. 帳務問題（退款、發票、付款）\n"
            "3. 帳號問題（登入、密碼、Email）\n"
            "4. 其他\n"
            "請回覆數字或簡單說明。"
        )
    return call_fast(prompt, max_tokens=300, temperature=0.4, fallback=fallback)
