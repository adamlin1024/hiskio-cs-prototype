"""流程編排（v6 主管模式）。

新架構（取代 v5 的「分類器流水線」）：

    handle_user_message
       │
       ├─ phase guards（特殊 phase 直接交給對應 handler，可 fall through）
       │     等待用戶選擇意圖 → _try_select_or_fall_through
       │     等待工單確認 → ticket_handler.decide → Y/N 直接 return；U fall through
       │     等待 Email → ticket_handler.handle_email_input
       │     已結束 → _ended_session
       │
       ├─ greeting fast-path（regex 快速擋純問候，省一次 Sonnet）
       │
       ├─ Manager（Sonnet 統一決策）→ 回 action + payload
       │
       └─ Action Executor（按 action 派給對應節點）→ _finalize_turn 收尾

Manager 統一接管原本散在 entry_classifier、intent_clarity、faq_matcher、kb_indexer
四個節點的「決策職責」。faq_responder、cs_response、no_kb_handler、off_topic、
ticket_handler 等執行者節點維持不變，只是改由 manager 指派。
"""
from __future__ import annotations

import logging
import re

from core import pipeline, runtime_config
from core.state import append_message, load_state, now_iso, save_state
from nodes import (
    acknowledge_handler,
    clarification_handler,
    cs_response,
    evaluator,
    faq_matcher,
    faq_responder,
    greeting_handler,
    intent_selector,
    kb_indexer,
    manager,
    no_kb_handler,
    off_topic,
    ticket_handler,
)

logger = logging.getLogger(__name__)

MAX_UNCLEAR_BEFORE_FORCE_TICKET = 3
OFF_TOPIC_BLOCKED_MSG = "對話僅處理 HiSKIO 服務相關問題，如無客服需求請關閉視窗。"
GREETING_BLOCKED_MSG = (
    "如果您有客服問題（影片、退款、帳號等），請直接描述問題；"
    "若沒有客服需求，可以關閉視窗結束對話。"
)
FORCE_ESCALATION_MSG = (
    "看起來這個問題比較複雜，建議由人工客服協助處理會更有效率。\n"
    "我為您建立工單，客服團隊會主動聯繫您。"
)
DEFAULT_OUT_OF_SCOPE_MSG = (
    "這個問題不在 HiSKIO 客服範圍內喔，"
    "如果您有課程、影片、帳號或付款相關的問題，我都可以協助。"
)
DEFAULT_UNCERTAINTY_MSG = "抱歉，我不太確定您想問的內容，能否再多描述一下您遇到的狀況？"

_LIMIT_REASON_MESSAGES = {
    "turn_max": "這次對話的輪數已經達到上限，為了讓客服能更完整地跟進您的問題",
    "off_topic_max": "我們的對話似乎多次偏離主題",
    "low_confidence_max": "看起來這個情況有點複雜，AI 可能無法完整協助",
    "unresolved_max": "看起來我前面的回應沒能解決您的問題",
}

# Greeting fast-path：純招呼用 regex 攔截，不勞煩 Sonnet 主管
_GREETING_RE = re.compile(
    r"^\s*(你好|您好|哈囉|哈嘍|嗨|嘿|在嗎|在不在|hello|hi|hey|早安?|午安|晚安)"
    r"[\s。.!?！？~～]*$",
    re.IGNORECASE,
)


# ────────────────────────────────────────────────────────────────────
# 主流程
# ────────────────────────────────────────────────────────────────────


def handle_user_message(session_id: str, user_message: str) -> dict:
    """主流程入口。"""
    state = load_state(session_id)
    if state is None:
        raise ValueError(f"找不到 session: {session_id}")

    append_message(state, "user", user_message)

    # 1. 特殊 phase 攔截
    phase_result = _try_handle_phase(state, user_message, session_id)
    if phase_result is not None:
        return phase_result

    # 2. greeting fast-path（純問候不勞煩 Sonnet）
    if _GREETING_RE.match(user_message.strip()):
        return _handle_greeting_fast_path(state, user_message, session_id)

    # 3. v7：流水線預判（給主管當參考）
    hint = pipeline.run(state, user_message)

    # 4. Manager 統一決策（吃流水線 hint，仍保有 override 權）
    decision = manager.decide(state, user_message, hint=hint)
    state["intent_state"]["input_classification"] = decision["recommended_action"]
    logger.info(
        "session=%s manager action=%s reason=%r summary=%r",
        session_id,
        decision["recommended_action"],
        decision.get("reason", ""),
        decision.get("user_intent_summary", ""),
    )

    # 把 manager 偵測到的新意圖記進 intent_log
    for det in decision["new_intents_to_log"]:
        _ensure_in_intent_log(
            state, det["text"],
            in_scope=det["in_scope"], role=det["role"],
        )

    # 4. 執行對應 action
    return _execute_action(state, user_message, decision, session_id)


def _try_handle_phase(state: dict, user_message: str, session_id: str) -> dict | None:
    """特殊 phase 處理；回傳 None 代表 fall through。"""
    phase = state["phase"]

    if phase == "等待用戶選擇意圖":
        return _try_select_or_fall_through(state, user_message, session_id)

    if phase == "等待工單確認":
        decision = ticket_handler.decide(user_message)
        logger.info("session=%s 工單確認 decision=%s", session_id, decision)
        if decision == "Y":
            return ticket_handler.handle_accept(state)
        if decision == "N":
            return ticket_handler.handle_decline(state)
        # U：重置 phase，fall through 到 manager
        state["phase"] = "對話中"
        state["ticket_state"]["ticket_suggested"] = False
        return None

    if phase == "等待 Email":
        return ticket_handler.handle_email_input(state, user_message)

    if phase == "已結束":
        return _ended_session(state)

    return None


def _try_select_or_fall_through(
    state: dict, user_message: str, session_id: str
) -> dict | None:
    """phase=等待用戶選擇意圖：是真的在選 → 處理；不是 → 退出 phase fall through。"""
    intent_log = state["intent_state"].get("intent_log") or []
    selected_idx = intent_selector.parse_selection(state, user_message)

    if selected_idx is None or not (0 <= selected_idx < len(intent_log)):
        logger.info("session=%s 不是在選，退出 intent_selection", session_id)
        state["phase"] = "對話中"
        state["intent_state"]["awaiting_selection"] = False
        return None  # fall through 到 manager

    selected_item = intent_log[selected_idx]
    selected_text = selected_item["text"]
    in_scope = selected_item.get("in_scope", True)
    logger.info(
        "session=%s 選 intent_log[%d]=%r (in_scope=%s)",
        session_id, selected_idx, selected_text, in_scope,
    )
    state["phase"] = "對話中"
    state["intent_state"]["awaiting_selection"] = False
    _switch_current_intent(state, selected_text)

    if not in_scope:
        ai_response, response_type = _route_off_topic_with_count(state, selected_text)
    else:
        # 用戶選的意圖，重新讓 manager 決定該怎麼回（FAQ/KB 由主管挑）
        # 這裡簡化：直接走老的 FAQ→KB 流程（保留執行者）
        ai_response, response_type = _faq_then_rag_executor(
            state, selected_text, faq_id=None, kb_ids=None, session_id=session_id,
        )

    return _finalize_turn(
        state, user_message, ai_response, response_type,
        increment_turn=True, did_customer_service=True, session_id=session_id,
    )


def _handle_greeting_fast_path(state: dict, user_message: str, session_id: str) -> dict:
    """純問候 fast-path：regex 攔下、不打 Sonnet，但仍走 greeting_count 邏輯。"""
    state["intent_state"]["input_classification"] = "greeting"
    intent = state["intent_state"]
    intent["greeting_count"] += 1

    if intent["greeting_count"] > intent["max_greeting_count"]:
        ai_response = GREETING_BLOCKED_MSG
        response_type = "greeting_blocked"
    else:
        ai_response = greeting_handler.respond(state, user_message)
        response_type = "greeting"

    logger.info("session=%s greeting fast-path count=%d", session_id, intent["greeting_count"])

    return _finalize_turn(
        state, user_message, ai_response, response_type,
        increment_turn=False,  # greeting 不增 turn_count
        did_customer_service=False, session_id=session_id,
    )


# ────────────────────────────────────────────────────────────────────
# Action Executor（按 manager 的 recommended_action 派任務）
# ────────────────────────────────────────────────────────────────────


def _execute_action(
    state: dict, user_message: str, decision: dict, session_id: str
) -> dict:
    """根據 manager 決策執行對應動作。"""
    action = decision["recommended_action"]

    if action == "greeting":
        return _handle_greeting_fast_path(state, user_message, session_id)

    if action == "clarify":
        return _execute_clarify(state, user_message, decision, session_id)

    if action == "answer_with_faq":
        return _execute_answer_with_faq(state, user_message, decision, session_id)

    if action == "answer_with_kb":
        return _execute_answer_with_kb(state, user_message, decision, session_id)

    if action == "acknowledge_out_of_scope":
        return _execute_out_of_scope(state, user_message, decision, session_id)

    if action == "acknowledge_uncertainty":
        return _execute_uncertainty(state, user_message, decision, session_id)

    if action == "acknowledge_confirmation":
        return _execute_acknowledge_confirmation(state, user_message, decision, session_id)

    if action == "suggest_ticket":
        return _execute_suggest_ticket(state, user_message, decision, session_id)

    if action == "list_pending_intents":
        return _execute_list_pending(state, user_message, decision, session_id)

    if action == "continue_intent":
        return _execute_continue_intent(state, user_message, decision, session_id)

    if action == "force_escalation":
        return _execute_force_escalation(state, user_message, session_id)

    # 不該走到這裡（manager 已校驗），保險 fallback
    logger.warning("未知 action=%r，fallback 為 acknowledge_uncertainty", action)
    return _execute_uncertainty(state, user_message, decision, session_id)


def _execute_clarify(state, user_message, decision, session_id) -> dict:
    """連續 unclear 達上限 → 強制建單；否則用 manager 給的 clarify_message 或自己生。"""
    intent = state["intent_state"]
    intent["consecutive_unclear_count"] += 1

    if intent["consecutive_unclear_count"] >= runtime_config.get_threshold("max_unclear", MAX_UNCLEAR_BEFORE_FORCE_TICKET):
        return _execute_force_escalation(state, user_message, session_id)

    msg = decision.get("clarify_message") or clarification_handler.respond(state, user_message)
    return _finalize_turn(
        state, user_message, msg, "clarification",
        increment_turn=True, did_customer_service=False, session_id=session_id,
    )


def _execute_answer_with_faq(state, user_message, decision, session_id) -> dict:
    """用 manager 指定的 FAQ id 走 faq_responder。"""
    state["intent_state"]["consecutive_unclear_count"] = 0
    target_text = _resolve_target_intent_text(state, decision)
    if target_text:
        _switch_current_intent(state, target_text)

    faq_id = decision.get("faq_id")
    faq_data = faq_matcher.load_faq_by_id(faq_id) if faq_id else None
    if not faq_data:
        # manager 給的 faq_id 無效，降級成 KB
        logger.warning("manager 給的 faq_id=%r 無效，降級走 KB", faq_id)
        return _execute_answer_with_kb(state, user_message, decision, session_id)

    state["faq_context"]["matched_faq_id"] = faq_id
    state["faq_context"]["match_confidence"] = 1.0  # manager 已確認
    state["faq_context"]["answer_strategy"] = "faq_template"
    state["kb_context"]["indexed_articles"] = []
    state["kb_context"]["articles_used_in_response"] = []

    ai_response = faq_responder.respond(state, faq_data, user_message)
    return _finalize_turn(
        state, user_message, ai_response, "faq",
        increment_turn=True, did_customer_service=True, session_id=session_id,
    )


def _execute_answer_with_kb(state, user_message, decision, session_id) -> dict:
    """用 manager 指定的 KB 文章走 cs_response。"""
    state["intent_state"]["consecutive_unclear_count"] = 0
    target_text = _resolve_target_intent_text(state, decision)
    if target_text:
        _switch_current_intent(state, target_text)

    return _faq_then_rag_executor(
        state,
        effective_message=user_message,
        faq_id=None,
        kb_ids=decision.get("kb_article_ids") or None,
        session_id=session_id,
    )


def _execute_out_of_scope(state, user_message, decision, session_id) -> dict:
    """非業務範圍 → 走 off_topic 流程，累加 off_topic_count。"""
    state["intent_state"]["consecutive_unclear_count"] = 0
    target_text = _resolve_target_intent_text(state, decision) or user_message
    _switch_current_intent(state, target_text)

    ai_response, response_type = _route_off_topic_with_count(state, target_text)
    return _finalize_turn(
        state, user_message, ai_response, response_type,
        increment_turn=True, did_customer_service=False, session_id=session_id,
    )


def _execute_uncertainty(state, user_message, decision, session_id) -> dict:
    """誠實說我聽不懂，請用戶澄清。"""
    state["intent_state"]["consecutive_unclear_count"] += 1
    if state["intent_state"]["consecutive_unclear_count"] >= runtime_config.get_threshold("max_unclear", MAX_UNCLEAR_BEFORE_FORCE_TICKET):
        return _execute_force_escalation(state, user_message, session_id)

    msg = decision.get("clarify_message") or DEFAULT_UNCERTAINTY_MSG
    return _finalize_turn(
        state, user_message, msg, "clarification",
        increment_turn=True, did_customer_service=False, session_id=session_id,
    )


def _execute_acknowledge_confirmation(state, user_message, decision, session_id) -> dict:
    """v7.2：用戶說『謝謝/我知道了/OK』等確認語 → Haiku handler 生回應、推進 intent_log。

    流程：
    - 先把 current_intent 標 confirmed_resolved（讓 handler 看到正確的 intent_log）
    - 呼叫 acknowledge_handler.respond()（Haiku）讀 intent_log 自動推進到下一個 pending
    - 重置 consecutive_unclear_count
    - 主管的 reason_to_user 不再使用（v7.1 後改由 Haiku 寫，主管只寫 debug 短理由）
    """
    state["intent_state"]["consecutive_unclear_count"] = 0

    # 先把 current_intent 標 confirmed_resolved
    intent = state["intent_state"]
    current = intent.get("current_intent")
    if current:
        for item in intent.get("intent_log", []):
            if item["text"] == current and item["status"] != "confirmed_resolved":
                item["status"] = "confirmed_resolved"
                logger.info("intent %r 標記為 confirmed_resolved（acknowledge）", current)
                break

    # Haiku handler 生回應（讀更新後的 intent_log 自動推進到下一個 pending）
    msg = acknowledge_handler.respond(state, user_message)
    return _finalize_turn(
        state, user_message, msg, "acknowledge",
        increment_turn=True, did_customer_service=False, session_id=session_id,
    )


def _execute_suggest_ticket(state, user_message, decision, session_id) -> dict:
    """主動建議建單。"""
    state["intent_state"]["consecutive_unclear_count"] = 0
    target_text = _resolve_target_intent_text(state, decision)
    if target_text:
        _switch_current_intent(state, target_text)

    reason = decision.get("reason_to_user") or "這個問題建議由人工客服協助處理會更有效率"
    ai_response = (
        f"{reason}\n請問需要為您建立服務工單嗎？回覆「好」或「不用」。"
    )
    state["ticket_state"]["ticket_suggested"] = True
    state["phase"] = "等待工單確認"
    state["escalation_signals"]["no_kb_match"] = state["escalation_signals"].get("no_kb_match", False)
    return _finalize_turn(
        state, user_message, ai_response, "ticket_flow",
        increment_turn=True, did_customer_service=True, session_id=session_id,
    )


def _execute_list_pending(state, user_message, decision, session_id) -> dict:
    """用戶用指稱詞 → 列出 pending/in_progress 意圖讓他選。"""
    state["intent_state"]["consecutive_unclear_count"] = 0
    state["intent_state"]["awaiting_selection"] = True
    state["phase"] = "等待用戶選擇意圖"
    ai_response = intent_selector.respond(state, user_message)
    return _finalize_turn(
        state, user_message, ai_response, "intent_selection",
        increment_turn=True, did_customer_service=False, session_id=session_id,
    )


def _execute_continue_intent(state, user_message, decision, session_id) -> dict:
    """用戶在補充 current_intent → 維持當前 intent 走 FAQ/KB。"""
    state["intent_state"]["consecutive_unclear_count"] = 0
    target_text = _resolve_target_intent_text(state, decision) or state["intent_state"].get("current_intent")
    if target_text:
        _switch_current_intent(state, target_text)
    return _faq_then_rag_executor(
        state,
        effective_message=target_text or user_message,
        faq_id=decision.get("faq_id"),
        kb_ids=decision.get("kb_article_ids") or None,
        session_id=session_id,
    )


def _execute_force_escalation(state, user_message, session_id) -> dict:
    """unclear 連續達上限 → 強制建單，不再嘗試溝通。"""
    state["ticket_state"]["ticket_suggested"] = True
    state["phase"] = "等待工單確認"
    return _finalize_turn(
        state, user_message, FORCE_ESCALATION_MSG, "force_escalation",
        increment_turn=True, did_customer_service=False, session_id=session_id,
    )


# ────────────────────────────────────────────────────────────────────
# 共用執行邏輯
# ────────────────────────────────────────────────────────────────────


def _resolve_target_intent_text(state: dict, decision: dict) -> str | None:
    """從 decision 推斷目標意圖文字（matched_intent_in_log_index / target_intent_index 對應）。"""
    intent_log = state["intent_state"].get("intent_log") or []
    for key in ("matched_intent_in_log_index", "target_intent_index"):
        idx = decision.get(key)
        if idx is not None and 0 <= idx < len(intent_log):
            return intent_log[idx]["text"]
    # 退而求其次：取剛偵測到的第一個 primary 意圖
    for det in decision.get("new_intents_to_log", []):
        if det.get("role") == "primary":
            return det["text"]
    return None


def _faq_then_rag_executor(
    state: dict, effective_message: str,
    faq_id: str | None, kb_ids: list[str] | None,
    session_id: str,
) -> tuple[str, str] | dict:
    """共用：FAQ → KB → cs_response 執行邏輯。

    - 若 faq_id 有值且有效，直接用 faq_responder
    - 否則用 manager 給的 kb_ids；若沒有再 fallback 跑 kb_indexer
    - kb_ids 仍為空 → no_kb_handler 建單建議

    回傳 (ai_response, response_type)，但會自己呼叫 _finalize_turn 完成（接 _try_select 才是直接 return tuple）
    """
    if faq_id:
        faq_data = faq_matcher.load_faq_by_id(faq_id)
        if faq_data:
            state["faq_context"]["matched_faq_id"] = faq_id
            state["faq_context"]["match_confidence"] = 1.0
            state["faq_context"]["answer_strategy"] = "faq_template"
            state["kb_context"]["indexed_articles"] = []
            state["kb_context"]["articles_used_in_response"] = []
            ai_response = faq_responder.respond(state, faq_data, effective_message)
            return _maybe_finalize(state, effective_message, ai_response, "faq", session_id)

    # KB 走查
    if not kb_ids:
        # manager 沒給就 fallback 跑 kb_indexer
        kb_ids = kb_indexer.index_articles(state, effective_message)
    state["kb_context"]["indexed_articles"] = kb_ids

    if not kb_ids:
        # KB 完全空 → 承認不知道
        ai_response = no_kb_handler.respond(state, effective_message)
        state["escalation_signals"]["no_kb_match"] = True
        state["ticket_state"]["ticket_suggested"] = True
        state["phase"] = "等待工單確認"
        state["faq_context"]["answer_strategy"] = "no_kb_match"
        state["kb_context"]["articles_used_in_response"] = []
        return _maybe_finalize(state, effective_message, ai_response, "no_kb_match", session_id)

    articles = []
    for kid in kb_ids:
        art = kb_indexer.load_kb_article(kid)
        if art:
            articles.append(art)

    ai_response = cs_response.respond(state, articles, effective_message)
    state["kb_context"]["articles_used_in_response"] = [a["id"] for a in articles]
    state["faq_context"]["answer_strategy"] = "rag"

    if ai_response.startswith("[SUGGEST_TICKET]"):
        ai_response = ai_response.replace("[SUGGEST_TICKET]", "", 1).strip()
        state["ticket_state"]["ticket_suggested"] = True
        state["phase"] = "等待工單確認"
        return _maybe_finalize(state, effective_message, ai_response, "ticket_flow", session_id)

    return _maybe_finalize(state, effective_message, ai_response, "rag", session_id)


def _maybe_finalize(state, user_message, ai_response, response_type, session_id):
    """從 _faq_then_rag_executor 出口呼叫，呼叫 _finalize_turn 收尾。"""
    return _finalize_turn(
        state, user_message, ai_response, response_type,
        increment_turn=True, did_customer_service=True, session_id=session_id,
    )


def _route_off_topic_with_count(state: dict, intent_text: str) -> tuple[str, str]:
    """走 off_topic_handler 流程，並累加 off_topic_count。"""
    sl = state["service_limits"]
    if sl["off_topic_count"] >= sl["max_off_topic_count"]:
        return OFF_TOPIC_BLOCKED_MSG, "off_topic_blocked"
    sl["off_topic_count"] += 1
    ai_response = off_topic.respond(state, intent_text)
    return ai_response, "off_topic"


# ────────────────────────────────────────────────────────────────────
# 收尾（_finalize_turn）
# ────────────────────────────────────────────────────────────────────


def _finalize_turn(
    state: dict,
    user_message: str,
    ai_response: str,
    response_type: str,
    *,
    increment_turn: bool,
    did_customer_service: bool,
    session_id: str,
) -> dict:
    """共用收尾：append、evaluator、status 更新、turn_count、save。"""
    append_message(state, "assistant", ai_response, response_type=response_type)

    # 只有真的處理客服問題才跑 evaluator（避免污染 issue_context）
    if did_customer_service and state["phase"] == "對話中":
        evaluator.evaluate(state, user_message, ai_response)
        # v6 移除：evaluator 後台靜默推進建單的邏輯。
        # 在主管模式下，用戶若真要建單，下一輪主管會看 chat_history 判斷並選 suggest_ticket。
        # evaluator 只負責「填知識性欄位」（情緒、分類、解決確認），不該動 ticket_state.phase。
        if response_type != "intent_selection":
            _mark_current_answered(state)

    if increment_turn:
        state["turn_count"] += 1
    state["updated_at"] = now_iso()

    check_and_update_limits(state)
    save_state(state)

    return _build_response(state, ai_response, response_type)


def _build_response(state: dict, ai_response: str, response_type: str) -> dict:
    return {
        "ai_response": ai_response,
        "response_type": response_type,
        "show_ticket_button": (
            state["ticket_state"]["ticket_suggested"]
            and not state["ticket_state"]["ticket_id"]
        ),
        "ticket_id": state["ticket_state"].get("ticket_id"),
        "state": state,
    }


# ────────────────────────────────────────────────────────────────────
# intent_log 管理
# ────────────────────────────────────────────────────────────────────


def _ensure_in_intent_log(
    state: dict, intent_text: str, in_scope: bool = True, role: str = "primary"
) -> None:
    """把意圖加進 intent_log（已存在則不重複加，但補齊缺欄位）。"""
    if not intent_text:
        return
    intent_text = intent_text.strip()
    log = state["intent_state"].setdefault("intent_log", [])
    for item in log:
        if item["text"] == intent_text:
            if "in_scope" not in item:
                item["in_scope"] = in_scope
            if "role" not in item:
                item["role"] = role
            return
    log.append({
        "text": intent_text,
        "status": "pending",
        "in_scope": in_scope,
        "role": role,
        "first_turn": state["turn_count"],
    })
    logger.info("intent_log 新增 %r (role=%s, in_scope=%s)", intent_text, role, in_scope)


def _switch_current_intent(state: dict, intent_text: str) -> None:
    """切換 current_intent：舊的 in_progress 退到 answered、新的設 in_progress。"""
    if not intent_text:
        return
    intent_text = intent_text.strip()
    intent = state["intent_state"]
    if intent.get("current_intent") == intent_text:
        return

    log = intent.setdefault("intent_log", [])
    for item in log:
        if item["status"] == "in_progress":
            item["status"] = "answered"
            logger.info("intent %r in_progress → answered", item["text"])

    found = next((item for item in log if item["text"] == intent_text), None)
    if found is None:
        log.append({
            "text": intent_text,
            "status": "in_progress",
            "in_scope": True,
            "role": "primary",
            "first_turn": state["turn_count"],
        })
        logger.info("intent_log 新增 %r 並設為 in_progress", intent_text)
    else:
        found["status"] = "in_progress"
        logger.info("intent %r 設為 in_progress", intent_text)

    intent["current_intent"] = intent_text


def _mark_current_answered(state: dict) -> None:
    """AI 給完答案後：current_intent 從 in_progress 標為 answered。"""
    intent = state["intent_state"]
    current = intent.get("current_intent")
    if not current:
        return
    for item in intent.get("intent_log", []):
        if item["text"] == current and item["status"] == "in_progress":
            item["status"] = "answered"
            logger.info("intent %r in_progress → answered", current)
            break


# ────────────────────────────────────────────────────────────────────
# 服務上限
# ────────────────────────────────────────────────────────────────────


def check_and_update_limits(state: dict) -> None:
    """每輪結束後檢查 service_limits 是否達上限。"""
    sl = state["service_limits"]
    if sl["limit_reached"]:
        return

    if state["turn_count"] >= sl["max_turns_per_session"]:
        sl["limit_reached"] = True
        sl["limit_reached_reason"] = "turn_max"
    elif sl["off_topic_count"] >= sl["max_off_topic_count"]:
        sl["limit_reached"] = True
        sl["limit_reached_reason"] = "off_topic_max"
    elif sl["low_confidence_count"] >= sl["max_low_confidence_count"]:
        sl["limit_reached"] = True
        sl["limit_reached_reason"] = "low_confidence_max"
    elif sl["unresolved_count"] >= sl["max_unresolved_count"]:
        sl["limit_reached"] = True
        sl["limit_reached_reason"] = "unresolved_max"


def _ended_session(state: dict) -> dict:
    """phase=已結束 時的固定回應。"""
    ticket_id = state["ticket_state"].get("ticket_id")
    msg = (
        f"您的工單已建立（#{ticket_id}），請耐心等候回覆。\n"
        "若您還想再問新問題，請按右上方「新對話」按鈕重新開始。"
        if ticket_id
        else "本次對話已結束，若有新問題請按右上方「新對話」按鈕。"
    )
    append_message(state, "assistant", msg, response_type="session_ended")
    state["turn_count"] += 1
    state["updated_at"] = now_iso()
    save_state(state)
    return _build_response(state, msg, "session_ended")


# ────────────────────────────────────────────────────────────────────
# 對外 API（給 app.py 用）
# ────────────────────────────────────────────────────────────────────


def initiate_ticket_from_button(session_id: str) -> dict:
    """前端「建立工單」按鈕觸發，跳過 yes/no 直接進入收 email / 建單。"""
    state = load_state(session_id)
    if state is None:
        raise ValueError(f"找不到 session: {session_id}")
    return ticket_handler.initiate(state)
