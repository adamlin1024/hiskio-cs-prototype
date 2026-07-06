"""流程編排(v8 一顆腦,規格 data/design-one-brain-2026-07-06.md §3)。

取代 v7 的「流水線四站+主管」:

    handle_user_message
       │
       ├─ 程式守衛(零 LLM)
       │     handed_off guard(已交接真人 → holding,閉環退場)
       │     phase=等待轉真人確認 → ticket_handler.decide(規則優先、語意備援)
       │     greeting fast-path(regex 擋純問候)
       │     每日訊息配額(規格 §14-8:超額 → 固定話術+提議轉真人,不打 LLM)
       │
       ├─ 分診腦(nodes/brain.py,一次呼叫)→ 決定單
       │
       └─ Action Executor(照決定單派工)→ _finalize_turn 收尾

裁掉的站(規格 §6):entry_classifier/intent_clarity/faq_matcher 比對/kb_indexer 挑文/
evaluator/intent_selector/clarification_handler/no_kb_handler/off_topic/pipeline。
issue_context(情緒/分類/摘要)改由分診腦每輪順手輸出(同源,不再事後另猜)。
"""
from __future__ import annotations

import logging
import re
from datetime import date

from core import runtime_config
from core.state import append_message, build_handoff, load_state, now_iso, save_state
from nodes import (
    acknowledge_handler,
    brain,
    cs_response,
    faq_matcher,
    faq_responder,
    greeting_handler,
    kb_indexer,
    ticket_handler,
)

logger = logging.getLogger(__name__)

MAX_UNCLEAR_BEFORE_FORCE_TICKET = 3
DEFAULT_MAX_DAILY_MESSAGES = 30  # 每日訊息配額預設(規格 §14-8;後台可注入 max_daily_messages)
OFF_TOPIC_BLOCKED_MSG = "對話僅處理 HiSKIO 服務相關問題，如無客服需求請關閉視窗。"
GREETING_BLOCKED_MSG = (
    "如果您有客服問題（影片、退款、帳號等），請直接描述問題；"
    "若沒有客服需求，可以關閉視窗結束對話。"
)
FORCE_ESCALATION_MSG = (
    "看起來這個問題我這邊比較難處理，交給真人客服會更快。\n"
    "要幫您轉真人嗎？（回覆「好」或「不用」）"
)
DEFAULT_OUT_OF_SCOPE_MSG = (
    "這個問題不在 HiSKIO 客服範圍內喔，"
    "如果您有課程、影片、帳號或付款相關的問題，我都可以協助。"
)
DEFAULT_UNCERTAINTY_MSG = "抱歉，我不太確定您想問的內容，能否再多描述一下您遇到的狀況？"
DAILY_LIMIT_MSG = (
    "今天的訊息量比較多，為了確保您的問題被完整處理，"
    "我幫您轉給真人客服好嗎？（回覆「好」或「不用」）"
)

# 轉真人安撫話「內建預設」：HiSupport 沒推 handoff_message 時用這句（純單機／未接時）。
# 依約定，這句要跟 HiSupport 後台「期待管理訊息」的預設一致，確保單機＝正式體驗。
DEFAULT_HANDOFF_MSG = "好的，我們會將您的訊息轉達給真人客服，並於正常上班日回覆您。"
# 已交接後、單機被繼續輸入時的固定 holding（正式環境 HiSupport 已切真人、不會再送進來）
HANDED_OFF_HOLDING_MSG = "您的訊息我已經轉給真人客服，請稍候他們的回覆。"

# Greeting fast-path：純招呼用 regex 攔截,不勞煩分診腦
_GREETING_RE = re.compile(
    r"^\s*(你好|您好|哈囉|哈嘍|嗨|嘿|在嗎|在不在|hello|hi|hey|早安?|午安|晚安)"
    r"[\s。.!?！？~～]*$",
    re.IGNORECASE,
)

# v8 現役 phase(規格 §7);其餘(如已退役的「等待用戶選擇意圖」)一律當「對話中」
_KNOWN_PHASES = {"對話中", "等待轉真人確認"}


# ────────────────────────────────────────────────────────────────────
# 主流程
# ────────────────────────────────────────────────────────────────────


def handle_user_message(session_id: str, user_message: str) -> dict:
    """主流程入口。"""
    state = load_state(session_id)
    if state is None:
        raise ValueError(f"找不到 session: {session_id}")

    append_message(state, "user", user_message)

    # 0. 已交接真人 → HiBot 退場（閉環）。
    if state["ticket_state"].get("handed_off"):
        return _handed_off_holding(state, user_message, session_id)

    # 0.5 幽靈 phase 防呆(規格 §14-5):退役/未知的 phase 一律當「對話中」
    if state.get("phase") not in _KNOWN_PHASES:
        state["phase"] = "對話中"

    # 1. 特殊 phase 攔截（等待轉真人確認）
    phase_result = _try_handle_phase(state, user_message, session_id)
    if phase_result is not None:
        return phase_result

    # 2. greeting fast-path（純問候不勞煩分診腦、不吃每日配額）
    if _GREETING_RE.match(user_message.strip()):
        return _handle_greeting_fast_path(state, user_message, session_id)

    # 3. 每日訊息配額（規格 §14-8:超額=固定話術+提議轉真人,不打 LLM）
    quota_result = _check_daily_quota(state, user_message, session_id)
    if quota_result is not None:
        return quota_result

    # 4. 分診腦（一次呼叫,直接看原件）→ 決定單
    decision = brain.decide(state, user_message)
    logger.info(
        "session=%s brain action=%s reason=%r",
        session_id, decision["recommended_action"], decision.get("reason", ""),
    )

    # 決定單順手帶回的 issue(情緒/分類/摘要)→ 更新交接摘要原料(只覆寫有值的欄位)
    for key in ("category", "summary", "user_emotion"):
        val = decision.get("issue", {}).get(key)
        if val:
            state["issue_context"][key] = val

    # 新偵測到的意圖記進 intent_log
    for det in decision["new_intents_to_log"]:
        _ensure_in_intent_log(
            state, det["text"], in_scope=det["in_scope"], role=det["role"],
        )

    # 5. 執行對應 action
    return _execute_action(state, user_message, decision, session_id)


def _try_handle_phase(state: dict, user_message: str, session_id: str) -> dict | None:
    """特殊 phase 處理；回傳 None 代表 fall through。"""
    if state["phase"] == "等待轉真人確認":
        decision = ticket_handler.decide(user_message)
        logger.info("session=%s 轉真人確認 decision=%s", session_id, decision)
        if decision == "Y":
            return _execute_handoff(state, user_message, session_id)
        if decision == "N":
            return ticket_handler.handle_decline(state)
        # U：重置 phase,fall through 給分診腦（答非所問=當新問題）
        state["phase"] = "對話中"
        state["ticket_state"]["ticket_suggested"] = False
        return None

    return None


def _handle_greeting_fast_path(state: dict, user_message: str, session_id: str) -> dict:
    """純問候 fast-path：regex 攔下、不打分診腦，但仍走 greeting_count 洗版防線。"""
    intent = state["intent_state"]
    intent["greeting_count"] += 1

    if intent["greeting_count"] > intent["max_greeting_count"]:
        ai_response = GREETING_BLOCKED_MSG
        response_type = "greeting_blocked"
    else:
        ai_response = greeting_handler.respond(state, user_message)
        response_type = "greeting"

    logger.info("session=%s greeting count=%d", session_id, intent["greeting_count"])

    return _finalize_turn(
        state, user_message, ai_response, response_type,
        increment_turn=False,  # greeting 不增 turn_count
        session_id=session_id,
    )


def _check_daily_quota(state: dict, user_message: str, session_id: str) -> dict | None:
    """每日訊息配額守衛(規格 §14-8)。回傳 None=額度內放行(並計數)。

    超額:固定話術+進「等待轉真人確認」(用戶回「好」即交接),完全不打 LLM。
    正常學員一天問不到門檻(預設 30),無感;惡意洗版則被斷糧(不再燒模型費)。
    """
    sl = state["service_limits"]
    today = date.today().isoformat()
    if sl.get("daily_date") != today:
        sl["daily_date"] = today
        sl["daily_count"] = 0

    limit = runtime_config.get_threshold("max_daily_messages", DEFAULT_MAX_DAILY_MESSAGES)
    if sl["daily_count"] >= limit:
        logger.info("session=%s 每日配額 %d 已滿,擋下並提議轉真人", session_id, limit)
        state["ticket_state"]["ticket_suggested"] = True
        state["ticket_state"]["handoff_reason"] = "daily_limit"
        state["phase"] = "等待轉真人確認"
        return _finalize_turn(
            state, user_message, DAILY_LIMIT_MSG, "daily_limit",
            increment_turn=True, session_id=session_id,
        )

    sl["daily_count"] += 1
    return None


# ────────────────────────────────────────────────────────────────────
# Action Executor（照決定單派工）
# ────────────────────────────────────────────────────────────────────


def _execute_action(
    state: dict, user_message: str, decision: dict, session_id: str
) -> dict:
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

    if action == "continue_intent":
        return _execute_continue_intent(state, user_message, decision, session_id)

    # 不該走到這裡（brain 已校驗），保險 fallback
    logger.warning("未知 action=%r,fallback 為 acknowledge_uncertainty", action)
    return _execute_uncertainty(state, user_message, decision, session_id)


def _execute_clarify(state, user_message, decision, session_id) -> dict:
    """連續 unclear 達上限 → 強制轉真人；但使用者已拒絕過,這次 session 不再自動強逼。"""
    intent = state["intent_state"]
    intent["consecutive_unclear_count"] += 1

    already_declined = state["ticket_state"].get("user_decision") == "declined"
    if (
        intent["consecutive_unclear_count"]
        >= runtime_config.get_threshold("max_unclear", MAX_UNCLEAR_BEFORE_FORCE_TICKET)
        and not already_declined
    ):
        return _execute_force_escalation(state, user_message, session_id)

    msg = decision.get("clarify_message") or DEFAULT_UNCERTAINTY_MSG
    return _finalize_turn(
        state, user_message, msg, "clarification",
        increment_turn=True, session_id=session_id,
    )


def _execute_uncertainty(state, user_message, decision, session_id) -> dict:
    """誠實說我聽不懂,請用戶澄清；已拒絕過的 session 不再自動強逼。"""
    state["intent_state"]["consecutive_unclear_count"] += 1
    already_declined = state["ticket_state"].get("user_decision") == "declined"
    if (
        state["intent_state"]["consecutive_unclear_count"]
        >= runtime_config.get_threshold("max_unclear", MAX_UNCLEAR_BEFORE_FORCE_TICKET)
        and not already_declined
    ):
        return _execute_force_escalation(state, user_message, session_id)

    msg = decision.get("clarify_message") or DEFAULT_UNCERTAINTY_MSG
    return _finalize_turn(
        state, user_message, msg, "clarification",
        increment_turn=True, session_id=session_id,
    )


def _execute_answer_with_faq(state, user_message, decision, session_id) -> dict:
    """用決定單指定的 FAQ 走 faq_responder(混合模式:答案本體程式貼、模型只加開場收尾)。"""
    state["intent_state"]["consecutive_unclear_count"] = 0
    _switch_to_decision_intent(state, decision)

    faq_id = decision.get("faq_id")
    faq_data = faq_matcher.load_faq_by_id(faq_id) if faq_id else None
    if not faq_data:
        # brain 已白名單驗證過,理論上到不了;保險:轉真人,不硬答
        logger.warning("faq_id=%r 讀取失敗,降級提議轉真人", faq_id)
        return _execute_suggest_ticket(state, user_message, decision, session_id)

    state["faq_context"]["matched_faq_id"] = faq_id
    state["faq_context"]["answer_strategy"] = "faq_template"
    state["kb_context"]["articles_used_in_response"] = []

    ai_response = faq_responder.respond(state, faq_data, user_message)
    return _finalize_turn(
        state, user_message, ai_response, "faq",
        increment_turn=True, session_id=session_id, mark_answered=True,
    )


def _execute_answer_with_kb(state, user_message, decision, session_id) -> dict:
    """照決定單編號取 KB 全文 → 寫手。編號→全文由程式做,零失真。"""
    state["intent_state"]["consecutive_unclear_count"] = 0
    _switch_to_decision_intent(state, decision)

    articles = []
    for kid in decision.get("kb_article_ids") or []:
        art = kb_indexer.load_kb_article(kid)
        if art:
            articles.append(art)

    if not articles:
        # brain 已驗證過編號,理論上到不了(檔案臨時被刪等);保險:轉真人
        logger.warning("kb 文章讀取全數失敗 ids=%r,降級提議轉真人", decision.get("kb_article_ids"))
        return _execute_suggest_ticket(state, user_message, decision, session_id)

    ai_response = cs_response.respond(state, articles, user_message)
    state["kb_context"]["articles_used_in_response"] = [a["id"] for a in articles]
    state["faq_context"]["answer_strategy"] = "rag"

    if ai_response.startswith("[SUGGEST_TICKET]"):
        # 寫手舉手:已答多次仍不滿/金流個案等(寫手指令的嚴格條件)
        ai_response = ai_response.replace("[SUGGEST_TICKET]", "", 1).strip()
        state["ticket_state"]["ticket_suggested"] = True
        state["ticket_state"]["handoff_reason"] = "needs_human"
        state["phase"] = "等待轉真人確認"
        return _finalize_turn(
            state, user_message, ai_response, "handoff_offer",
            increment_turn=True, session_id=session_id, mark_answered=True,
        )

    return _finalize_turn(
        state, user_message, ai_response, "rag",
        increment_turn=True, session_id=session_id, mark_answered=True,
    )


def _execute_out_of_scope(state, user_message, decision, session_id) -> dict:
    """非業務範圍 → 固定禮貌拒答+離題計數(超限鎖住);不再花 LLM 生成拒答。"""
    state["intent_state"]["consecutive_unclear_count"] = 0
    sl = state["service_limits"]
    if sl["off_topic_count"] >= sl["max_off_topic_count"]:
        ai_response, response_type = OFF_TOPIC_BLOCKED_MSG, "off_topic_blocked"
    else:
        sl["off_topic_count"] += 1
        ai_response, response_type = DEFAULT_OUT_OF_SCOPE_MSG, "off_topic"
    return _finalize_turn(
        state, user_message, ai_response, response_type,
        increment_turn=True, session_id=session_id,
    )


def _execute_acknowledge_confirmation(state, user_message, decision, session_id) -> dict:
    """用戶說「謝謝/我知道了/OK/好吧」等確認語。

    「好吧」誤結案修正(規格 §4):只有 user_satisfied=True(明確正面表態)才把
    current_intent 標 confirmed_resolved;消極接受(好吧/喔/嗯)→ 溫和回應、不結案。
    """
    state["intent_state"]["consecutive_unclear_count"] = 0

    if decision.get("user_satisfied"):
        intent = state["intent_state"]
        current = intent.get("current_intent")
        if current:
            for item in intent.get("intent_log", []):
                if item["text"] == current and item["status"] != "confirmed_resolved":
                    item["status"] = "confirmed_resolved"
                    logger.info("intent %r 標記為 confirmed_resolved（明確滿意）", current)
                    break
    else:
        logger.info("消極接受(user_satisfied=False),不標 confirmed_resolved")

    msg = acknowledge_handler.respond(state, user_message)
    return _finalize_turn(
        state, user_message, msg, "acknowledge",
        increment_turn=True, session_id=session_id,
    )


def _execute_suggest_ticket(state, user_message, decision, session_id) -> dict:
    """提議轉真人(兩段式第一步)。"""
    state["intent_state"]["consecutive_unclear_count"] = 0
    _switch_to_decision_intent(state, decision)

    reason = decision.get("reason_to_user") or "這個問題交給真人客服會比較快"
    ai_response = f"{reason}\n要幫您轉真人嗎？（回覆「好」或「不用」）"
    state["ticket_state"]["ticket_suggested"] = True
    # 精確原因(交接資料要完整):no_kb_match=真人會看到「知識庫沒有對應資料」=該補 KB 的訊號
    state["ticket_state"]["handoff_reason"] = decision.get("handoff_reason") or "needs_human"
    state["phase"] = "等待轉真人確認"
    return _finalize_turn(
        state, user_message, ai_response, "handoff_offer",
        increment_turn=True, session_id=session_id,
    )


def _execute_continue_intent(state, user_message, decision, session_id) -> dict:
    """用戶在補充 current_intent → 依決定單的 faq/kb 續答;都沒有=請他澄清。"""
    state["intent_state"]["consecutive_unclear_count"] = 0
    if decision.get("faq_id"):
        return _execute_answer_with_faq(state, user_message, decision, session_id)
    if decision.get("kb_article_ids"):
        return _execute_answer_with_kb(state, user_message, decision, session_id)
    return _execute_uncertainty(state, user_message, decision, session_id)


def _execute_force_escalation(state, user_message, session_id) -> dict:
    """unclear 連續達上限 → 主動提議轉真人,不再嘗試溝通。"""
    state["ticket_state"]["ticket_suggested"] = True
    state["ticket_state"]["handoff_reason"] = "unclear_limit"
    state["phase"] = "等待轉真人確認"
    return _finalize_turn(
        state, user_message, FORCE_ESCALATION_MSG, "force_escalation",
        increment_turn=True, session_id=session_id,
    )


def _execute_handoff(state, user_message, session_id) -> dict:
    """使用者同意轉真人 → 講一句安撫話、設交接旗標與訊號後退場。"""
    ts = state["ticket_state"]
    ts["handed_off"] = True
    ts["ticket_suggested"] = False
    ts["user_decision"] = "accepted"
    if not ts.get("handoff_reason"):
        ts["handoff_reason"] = "user_request"
    state["phase"] = "對話中"
    msg = runtime_config.get_message("handoff_message", DEFAULT_HANDOFF_MSG)
    return _finalize_turn(
        state, user_message, msg, "handoff",
        increment_turn=True, session_id=session_id,
    )


def _handed_off_holding(state, user_message, session_id) -> dict:
    """已交接後被繼續輸入時的固定回應（閉環,不打 LLM、不重問）。"""
    return _finalize_turn(
        state, user_message, HANDED_OFF_HOLDING_MSG, "handoff",
        increment_turn=False, session_id=session_id,
    )


# ────────────────────────────────────────────────────────────────────
# 收尾（_finalize_turn）——每輪照樣更新狀態+存檔;唯 evaluator 站已裁,
# 情緒/分類/摘要改由分診腦在決定單裡同源輸出(規格 §3/§7)。
# ────────────────────────────────────────────────────────────────────


def _finalize_turn(
    state: dict,
    user_message: str,
    ai_response: str,
    response_type: str,
    *,
    increment_turn: bool,
    session_id: str,
    mark_answered: bool = False,
) -> dict:
    """共用收尾：append、意圖推進、turn_count、save。"""
    append_message(state, "assistant", ai_response, response_type=response_type)

    if mark_answered:
        _mark_current_answered(state)

    if increment_turn:
        state["turn_count"] += 1
    state["updated_at"] = now_iso()

    save_state(state)

    return _build_response(state, ai_response, response_type)


def _build_response(state: dict, ai_response: str, response_type: str) -> dict:
    return {
        "ai_response": ai_response,
        "response_type": response_type,
        # 轉真人改版後不再有前端建單按鈕；欄位保留為相容舊呼叫端，恆為 False/None
        "show_ticket_button": False,
        "ticket_id": None,
        # 轉真人交接訊號（給 HiSupport 讀）
        "handoff": build_handoff(state),
        "state": state,
    }


# ────────────────────────────────────────────────────────────────────
# intent_log 管理
# ────────────────────────────────────────────────────────────────────


def _switch_to_decision_intent(state: dict, decision: dict) -> None:
    """依決定單切換 current_intent(target_intent_index 優先,其次新 primary 意圖)。"""
    intent_log = state["intent_state"].get("intent_log") or []
    idx = decision.get("target_intent_index")
    if idx is not None and 0 <= idx < len(intent_log):
        _switch_current_intent(state, intent_log[idx]["text"])
        return
    for det in decision.get("new_intents_to_log", []):
        if det.get("role") == "primary":
            _switch_current_intent(state, det["text"])
            return


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

    found = next((item for item in log if item["text"] == intent_text), None)
    if found is None:
        log.append({
            "text": intent_text,
            "status": "in_progress",
            "in_scope": True,
            "role": "primary",
            "first_turn": state["turn_count"],
        })
    else:
        found["status"] = "in_progress"

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
            break
