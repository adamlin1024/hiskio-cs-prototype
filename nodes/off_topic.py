"""節點 6：離題處理（Haiku）。

回應的語氣會根據第幾次離題漸強。off_topic_count 在 orchestrator 累加，
這裡只負責生成回應字串。
"""
from __future__ import annotations

import logging

from core.llm_client import call_haiku, load_prompt

logger = logging.getLogger(__name__)

_PROMPT = load_prompt("off_topic")


def respond(state: dict, user_message: str) -> str:
    """生成離題回應。off_topic_count 由呼叫端先 +1 再傳入 state。"""
    issue = state["issue_context"]
    original_issue = issue.get("summary") or "（用戶尚未描述任何客服問題）"
    off_topic_count = state["service_limits"]["off_topic_count"]

    prompt = _PROMPT.format(
        user_message=user_message,
        original_issue=original_issue,
        off_topic_count=off_topic_count,
    )

    fallback = "這個問題不在 HiSKIO 客服範圍內喔，如果有課程或帳號相關的問題我都可以協助您。"
    return call_haiku(prompt, max_tokens=200, temperature=0.6, fallback=fallback)
