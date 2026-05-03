"""流程編排（v5 分類器流水線版）。

主流程結構：
    handle_user_message
       │
       ├─ phase guards（特殊 phase 直接交給對應 handler，可 fall through）
       │     等待用戶選擇意圖 → _try_select_or_fall_through
       │     等待工單確認 → ticket_handler.decide → Y/N 直接 return；U fall through
       │     等待 Email → ticket_handler.handle_email_input
       │     已結束 → _ended_session
       │
       ├─ 服務上限攔截（已達上限且未拒絕過）→ _suggest_ticket_due_to_limit
       │
       ├─ entry_classifier 分類（greeting / unclear / off_topic / customer_service）
       │
       └─ _dispatch_classification → _finalize_turn

四個分支處理函式（_handle_*）回傳 (ai_response, response_type, increment_turn)，
共用 _finalize_turn 統一收尾（append_message、evaluator、turn_count、save）。
"""
from __future__ import annotations

import logging

from core.state import append_message, load_state, now_iso, save_state
from nodes import (
    clarification_handler,
    cs_response,
    entry_classifier,
    evaluator,
    faq_matcher,
    faq_responder,
    greeting_handler,
    intent_clarity,
    intent_selector,
    kb_indexer,
    no_kb_handler,
    off_topic,
    ticket_handler,
)

logger = logging.getLogger(__name__)

FAQ_CONFIDENCE_THRESHOLD = 0.7
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

_LIMIT_REASON_MESSAGES = {
    "turn_max": "這次對話的輪數已經達到上限，為了讓客服能更完整地跟進您的問題",
    "off_topic_max": "我們的對話似乎多次偏離主題",
    "low_confidence_max": "看起來這個情況有點複雜，AI 可能無法完整協助",
    "unresolved_max": "看起來我前面的回應沒能解決您的問題",
}


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

    # 2. 服務上限攔截
    if _should_suggest_ticket_now(state):
        return _suggest_ticket_due_to_limit(state, session_id)

    # 3. 入口分類 + 分派
    classification = entry_classifier.classify(state, user_message)
    state["intent_state"]["input_classification"] = classification
    logger.info("session=%s entry_classifier=%s", session_id, classification)

    return _dispatch_and_finalize(state, user_message, classification, session_id)


def _try_handle_phase(state: dict, user_message: str, session_id: str) -> dict | None:
    """特殊 phase 處理；回傳 None 代表「繼續走正常流程」（fall through）。"""
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
        # U：重置 phase，fall through 到正常流程
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
        return None  # fall through

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
        ai_response, response_type = _run_faq_then_rag(state, user_message, selected_text, session_id)

    return _finalize_turn(
        state, user_message, ai_response, response_type,
        increment_turn=True, classification="customer_service", session_id=session_id,
    )


def _dispatch_and_finalize(
    state: dict, user_message: str, classification: str, session_id: str
) -> dict:
    """根據 entry_classifier 結果分派到對應處理函式，再走 _finalize_turn 收尾。"""
    if classification == "greeting":
        ai_response, response_type, increment_turn = _handle_greeting(state, user_message)
    elif classification == "unclear":
        ai_response, response_type, increment_turn = _handle_unclear(state, user_message)
    elif classification == "off_topic":
        state["intent_state"]["consecutive_unclear_count"] = 0
        ai_response, response_type = _handle_off_topic(state, user_message)
        increment_turn = True
    else:  # customer_service
        state["intent_state"]["consecutive_unclear_count"] = 0
        ai_response, response_type = _handle_service_intent(state, user_message, session_id)
        increment_turn = True

    return _finalize_turn(
        state, user_message, ai_response, response_type,
        increment_turn=increment_turn, classification=classification, session_id=session_id,
    )


def _finalize_turn(
    state: dict,
    user_message: str,
    ai_response: str,
    response_type: str,
    *,
    increment_turn: bool,
    classification: str,
    session_id: str,
) -> dict:
    """共用收尾：append、evaluator、status 更新、turn_count、save。"""
    append_message(state, "assistant", ai_response, response_type=response_type)

    # 只有 customer_service 路徑且仍在對話中才跑評估
    if classification == "customer_service" and state["phase"] == "對話中":
        evaluator.evaluate(state, user_message, ai_response)
        if response_type != "intent_selection":
            _mark_current_answered(state)

    if increment_turn:
        state["turn_count"] += 1
    state["updated_at"] = now_iso()

    check_and_update_limits(state)
    save_state(state)

    return _build_response(state, ai_response, response_type)


def _build_response(state: dict, ai_response: str, response_type: str) -> dict:
    """組 API 回應 dict。"""
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
# 各分類分支
# ────────────────────────────────────────────────────────────────────


def _handle_greeting(state: dict, user_message: str) -> tuple[str, str, bool]:
    """greeting：Haiku 動態回應，不增加 turn_count，重置 unclear count。

    一個 session 累計超過 max_greeting_count 次後永久轉灰框硬擋（不再重置）。
    """
    intent = state["intent_state"]
    intent["consecutive_unclear_count"] = 0
    intent["greeting_count"] += 1

    if intent["greeting_count"] > intent["max_greeting_count"]:
        return GREETING_BLOCKED_MSG, "greeting_blocked", False

    ai_response = greeting_handler.respond(state, user_message)
    return ai_response, "greeting", False


def _handle_unclear(state: dict, user_message: str) -> tuple[str, str, bool]:
    """unclear：累加 count，第 3 次強制建單，否則呼叫釐清節點。"""
    intent = state["intent_state"]
    intent["consecutive_unclear_count"] += 1

    if intent["consecutive_unclear_count"] >= MAX_UNCLEAR_BEFORE_FORCE_TICKET:
        state["ticket_state"]["ticket_suggested"] = True
        state["phase"] = "等待工單確認"
        return FORCE_ESCALATION_MSG, "force_escalation", True

    ai_response = clarification_handler.respond(state, user_message)
    return ai_response, "clarification", True


def _handle_off_topic(state: dict, user_message: str) -> tuple[str, str]:
    """off_topic：達上限後不再呼叫 LLM，回固定灰框訊息。"""
    sl = state["service_limits"]
    if sl["off_topic_count"] >= sl["max_off_topic_count"]:
        return OFF_TOPIC_BLOCKED_MSG, "off_topic_blocked"

    sl["off_topic_count"] += 1
    ai_response = off_topic.respond(state, user_message)
    return ai_response, "off_topic"


def _handle_service_intent(
    state: dict, user_message: str, session_id: str
) -> tuple[str, str]:
    """customer_service（v5.1）：

    根據 intent_clarity 的 role + needs_user_selection 決定路由：
    - 用指稱詞 → 取 intent_log 對應項
    - needs_user_selection=True → 列選項（多個 primary 且用戶沒排序）
    - 否則 → 取第一個 primary 意圖直接處理（context 純脈絡不處理）
    """
    clarity_result = intent_clarity.analyze(state, user_message)
    intent = state["intent_state"]
    intent["intent_clarity"] = (
        "parallel_multiple" if clarity_result["needs_user_selection"]
        else "simple"
    )
    logger.info(
        "session=%s intent_clarity detected=%r needs_selection=%s referenced=%r",
        session_id,
        clarity_result["detected_intents"],
        clarity_result["needs_user_selection"],
        clarity_result["referenced_intent_index"],
    )

    # case 1：用指稱詞 → 用 intent_log 對應項
    if clarity_result["referenced_intent_index"] is not None:
        idx = clarity_result["referenced_intent_index"]
        log_item = intent["intent_log"][idx]
        target_text = log_item["text"]
        _switch_current_intent(state, target_text)
        if not log_item.get("in_scope", True):
            return _route_off_topic_with_count(state, target_text)
        return _run_faq_then_rag(state, user_message, target_text, session_id)

    # 把這次偵測到的意圖都記進 intent_log（含 role）
    detected = clarity_result["detected_intents"]
    for det in detected:
        _ensure_in_intent_log(
            state,
            det["text"],
            in_scope=det.get("in_scope", True),
            role=det.get("role", "primary"),
        )

    # case 2：needs_user_selection → 列選項
    if clarity_result["needs_user_selection"]:
        ai_response = intent_selector.respond(state, user_message)
        intent["awaiting_selection"] = True
        state["phase"] = "等待用戶選擇意圖"
        return ai_response, "intent_selection"

    # case 3：直接處理 primary（context 跳過）
    primary_intents = [d for d in detected if d.get("role", "primary") == "primary"]
    if primary_intents:
        chosen_det = primary_intents[0]
    elif detected:
        chosen_det = detected[0]
    else:
        chosen_det = {"text": user_message, "in_scope": True}

    chosen = chosen_det["text"]
    chosen_in_scope = chosen_det.get("in_scope", True)
    _switch_current_intent(state, chosen)
    if not chosen_in_scope:
        return _route_off_topic_with_count(state, chosen)
    return _run_faq_then_rag(state, user_message, chosen, session_id)


def _route_off_topic_with_count(state: dict, intent_text: str) -> tuple[str, str]:
    """選到非業務意圖 → 走 off_topic_handler 流程，並累加 off_topic_count。"""
    sl = state["service_limits"]
    if sl["off_topic_count"] >= sl["max_off_topic_count"]:
        return OFF_TOPIC_BLOCKED_MSG, "off_topic_blocked"
    sl["off_topic_count"] += 1
    ai_response = off_topic.respond(state, intent_text)
    return ai_response, "off_topic"


def _run_faq_then_rag(
    state: dict, user_message: str, effective_message: str, session_id: str
) -> tuple[str, str]:
    """FAQ 命中 → faq_responder；FAQ 沒命中 → kb_indexer → cs_response（KB 空 → no_kb_handler）。"""
    faq_result = faq_matcher.match(effective_message)
    state["faq_context"]["matched_faq_id"] = faq_result["matched_id"]
    state["faq_context"]["match_confidence"] = faq_result["confidence"]
    logger.info(
        "session=%s FAQ id=%s confidence=%.2f",
        session_id, faq_result["matched_id"], faq_result["confidence"],
    )

    if faq_result["matched_id"] and faq_result["confidence"] >= FAQ_CONFIDENCE_THRESHOLD:
        faq_data = faq_matcher.load_faq_by_id(faq_result["matched_id"])
        ai_response = faq_responder.respond(state, faq_data, effective_message)
        state["faq_context"]["answer_strategy"] = "faq_template"
        state["kb_context"]["indexed_articles"] = []
        state["kb_context"]["articles_used_in_response"] = []
        return ai_response, "faq"

    # FAQ 沒命中 → RAG
    kb_ids = kb_indexer.index_articles(state, effective_message)
    state["kb_context"]["indexed_articles"] = kb_ids

    if not kb_ids:
        # KB 完全空 → 承認不知道 + 建議建單
        ai_response = no_kb_handler.respond(state, effective_message)
        state["escalation_signals"]["no_kb_match"] = True
        state["ticket_state"]["ticket_suggested"] = True
        state["phase"] = "等待工單確認"
        state["faq_context"]["answer_strategy"] = "no_kb_match"
        state["kb_context"]["articles_used_in_response"] = []
        return ai_response, "no_kb_match"

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
        return ai_response, "ticket_flow"

    return ai_response, "rag"


# ────────────────────────────────────────────────────────────────────
# intent_log 管理
# ────────────────────────────────────────────────────────────────────


def _ensure_in_intent_log(
    state: dict, intent_text: str, in_scope: bool = True, role: str = "primary"
) -> None:
    """把意圖加進 intent_log（已存在則不重複加）。預設 status=pending、role=primary。"""
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
    """AI 給完答案後：current_intent 從 in_progress 標為 answered（保守，等用戶確認再 resolved）。"""
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
# 服務上限與工單建議
# ────────────────────────────────────────────────────────────────────


def _should_suggest_ticket_now(state: dict) -> bool:
    """是否該主動建議建單：已達上限 + 還沒建議過 + 用戶之前沒拒絕過。"""
    return (
        state["service_limits"]["limit_reached"]
        and not state["ticket_state"]["ticket_suggested"]
        and state["ticket_state"]["user_decision"] != "declined"
    )


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


def _suggest_ticket_due_to_limit(state: dict, session_id: str) -> dict:
    """達服務上限的固定建單訊息。"""
    reason = state["service_limits"]["limit_reached_reason"]
    prefix = _LIMIT_REASON_MESSAGES.get(reason, "為了讓您獲得更完整的協助")
    ai_response = (
        f"{prefix}，建議改為由人工客服跟進處理。\n"
        "請問需要為您建立服務工單嗎？回覆「好」或「不用」。"
    )
    state["ticket_state"]["ticket_suggested"] = True
    state["phase"] = "等待工單確認"

    append_message(state, "assistant", ai_response, response_type="ticket_flow")
    state["turn_count"] += 1
    state["updated_at"] = now_iso()
    save_state(state)
    logger.info("session=%s 達服務上限 reason=%s 建議建單", session_id, reason)

    return _build_response(state, ai_response, "ticket_flow")


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
    """前端「建立工單」按鈕觸發，跳過「好/不用」直接進入收 email / 建單。"""
    state = load_state(session_id)
    if state is None:
        raise ValueError(f"找不到 session: {session_id}")
    return ticket_handler.initiate(state)
