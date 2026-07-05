"""acknowledge handler（v7.2，Haiku）。

主管選 acknowledge_confirmation action 時呼叫，產出禮貌回應 + 主動推進到下一個 pending intent。

主管 Sonnet 負責「判定這句是 confirmation」的決策，這個 handler 負責**寫回應**。
拆開的好處：Haiku 寫制式回應比 Sonnet 便宜，主管 output 也縮短（reason_to_user 只寫 debug 短理由）。
"""
from __future__ import annotations

import logging

from core.llm_client import call_writer, load_prompt

logger = logging.getLogger(__name__)

_PROMPT = load_prompt("acknowledge_handler")


def _format_intent_log(intent_log: list[dict]) -> str:
    if not intent_log:
        return "（無）"
    lines = []
    for i, item in enumerate(intent_log):
        role = item.get("role", "primary")
        in_scope = item.get("in_scope", True)
        lines.append(
            f"  [{i}] {item['text']}"
            f"（status={item['status']}, role={role}, in_scope={in_scope}, "
            f"first_turn={item.get('first_turn', '?')}）"
        )
    return "\n".join(lines)


def respond(state: dict, user_message: str) -> str:
    """生成 acknowledge 回應。

    讀 intent_log + current_intent，由 Haiku 產出帶推進語的禮貌回應。
    失敗時 fallback 為通用回應，避免卡住主流程。
    """
    intent_state = state.get("intent_state", {})
    current = intent_state.get("current_intent") or "（無）"
    intent_log = intent_state.get("intent_log", [])

    prompt = _PROMPT.format(
        user_message=user_message,
        current_intent=current,
        intent_log_str=_format_intent_log(intent_log),
    )

    fallback = "不客氣！還有其他需要協助的地方嗎？"
    return call_writer(
        prompt,
        max_tokens=200,
        temperature=0.5,  # 比 0 高一點讓語氣自然，但不要太發散
        fallback=fallback,
    )
