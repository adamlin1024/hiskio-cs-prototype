"""節點 8：背景評估（Haiku）。

每輪在 AI 回應後跑一次，提煉結構化資訊回寫 State：
- issue_context（category / sub_category / summary / user_emotion）
- service_limits.low_confidence_count（ai_confidence < 0.4 時 +1）
- service_limits.unresolved_count（user_satisfied_with_answer 為 False 時 +1）
- ticket_state.user_decision（user_explicitly_wants_ticket 為 True 時設 'accepted'）
- intent_log 中 current_intent 標 confirmed_resolved（user_confirmed_resolution=True 時）

解析失敗時 log 並跳過更新，不影響主流程回應。
"""
from __future__ import annotations

import logging

from core.llm_client import call_haiku, load_prompt
from core.text_utils import extract_json_object

logger = logging.getLogger(__name__)

_PROMPT = load_prompt("evaluator")


def evaluate(state: dict, user_message: str, ai_response: str) -> None:
    """跑評估並就地更新 state。"""
    prompt = _PROMPT.format(
        user_message=user_message,
        ai_response=ai_response,
        previous_category=state["issue_context"].get("category") or "未分類",
        turn_count=state["turn_count"],
    )
    raw = call_haiku(prompt, max_tokens=300, temperature=0.0, fallback="")
    result = extract_json_object(raw)
    if not result:
        logger.warning("evaluator 解析失敗，原始輸出：%r", raw)
        return

    issue = state["issue_context"]
    if "issue_category" in result:
        issue["category"] = result["issue_category"]
    if "issue_sub_category" in result:
        issue["sub_category"] = result["issue_sub_category"]
    if "issue_summary" in result:
        issue["summary"] = result["issue_summary"]
    if "user_emotion" in result:
        issue["user_emotion"] = result["user_emotion"]

    sl = state["service_limits"]
    confidence = result.get("ai_confidence_in_answer")
    if isinstance(confidence, (int, float)) and confidence < 0.4:
        sl["low_confidence_count"] += 1

    if result.get("user_satisfied_with_answer") is False:
        sl["unresolved_count"] += 1

    # v6 註：user_explicitly_wants_ticket 不再寫入 user_decision。
    # 在主管模式下，建單意願由主管統一判斷，evaluator 只填知識性欄位。

    # v4.1：偵測到用戶確認解決 → 把 current_intent 標 confirmed_resolved
    if result.get("user_confirmed_resolution") is True:
        current = state["intent_state"].get("current_intent")
        if current:
            for item in state["intent_state"].get("intent_log", []):
                if item["text"] == current and item["status"] != "confirmed_resolved":
                    item["status"] = "confirmed_resolved"
                    logger.info("intent %r 標記為 confirmed_resolved", current)
                    break
