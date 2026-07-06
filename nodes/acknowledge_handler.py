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


def _next_pending(intent_log: list[dict], current: str | None) -> str | None:
    """下一個待辦=最早出現、status=pending、且不是當前主題的意圖。

    由程式判定、不交給模型掃清單——實測小模型會把已解決的又端出來(2026-07-06)。
    """
    candidates = [
        i for i in intent_log
        if i.get("status") == "pending" and i.get("text") != current
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda i: i.get("first_turn", 0))["text"]


def respond(state: dict, user_message: str) -> str:
    """生成 acknowledge 回應。

    「要不要推進、推進到哪一題」由程式算好(_next_pending),模型只負責措辭。
    失敗時 fallback 為通用回應，避免卡住主流程。
    """
    intent_state = state.get("intent_state", {})
    current = intent_state.get("current_intent") or "（無）"
    intent_log = intent_state.get("intent_log", [])
    nxt = _next_pending(intent_log, intent_state.get("current_intent"))

    prompt = _PROMPT.format(
        user_message=user_message,
        current_intent=current,
        next_pending=nxt or "（無）",
    )

    fallback = "不客氣！還有其他需要協助的地方嗎？"
    return call_writer(
        prompt,
        max_tokens=200,
        temperature=0.5,  # 比 0 高一點讓語氣自然，但不要太發散
        fallback=fallback,
    )
