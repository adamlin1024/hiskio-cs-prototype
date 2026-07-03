"""節點 7：工單流程多階段狀態機。

依規格 v2「節點 7」一節。orchestrator 在 phase=等待工單確認 / 等待 Email 時直接呼叫這裡。

階段對應：
- 階段 1（建議建單）：由 orchestrator 的 _suggest_ticket_due_to_limit 或 cs_response 的 [SUGGEST_TICKET] 觸發
- 階段 2（等待用戶決定）：handle_confirmation
- 階段 3-5（檢查身分 / 收 Email / 生成工單）：confirm_and_create / handle_email_input
- 階段 6（結束）：orchestrator 的 _ended_session

決定意圖（好/不用）用 Haiku 判斷，避免硬比對「好的」「不要」「OK」這類變化。
"""
from __future__ import annotations

import logging
import re

from core import ticket
from core.llm_client import call_fast
from core.state import append_message, now_iso, save_state

logger = logging.getLogger(__name__)

EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
MAX_EMAIL_ATTEMPTS = 3

_DECISION_PROMPT = """判斷用戶是否**明確同意**建立服務工單。只回傳一個英文字母。

Y = 明確同意建單
   嚴格條件：用戶**單純表達確認**，沒有附加新問題或新訴求
   例：「好」「好的」「OK」「可以」「麻煩你」「請建立」「對」「yes」「go ahead」

N = 明確拒絕
   例：「不用」「不要」「不需要」「算了」「no」「先不要」「等等」

U = 不明確 / 不在回答 yes/no
   - 用戶又開始問新問題：「我要下一個問題」「下一題呢」「再問你個事」
   - 用戶在描述狀況：「影片還是不能看」「我有 XX 問題」
   - 用戶在閒聊或質疑：「為什麼要建工單」「你是 AI 嗎」
   - 用戶說「要」但接著還有別的內容（例「我要先看影片」）→ 不算同意建單，是新訴求

# 範例
- 「好」→ Y
- 「OK 麻煩你」→ Y
- 「不用」→ N
- 「我要下一個問題」→ U（這是要繼續問，不是確認建單）
- 「我要建單」→ Y（明確說建單）
- 「我要看影片」→ U（要看影片，不是建單）

用戶回覆：「{user_message}」

只回 Y、N 或 U，不要其他文字。
"""

_EMAIL_REQUEST = (
    "為了讓客服與您聯繫，請提供您的 Email。\n"
    "我們會將工單編號與後續處理進度寄送到您填寫的信箱。"
)
_EMAIL_FORMAT_ERROR = "您填寫的 Email 格式似乎不太對，請再確認一次。"
_EMAIL_GIVE_UP = "Email 格式有誤已重試多次，本次工單無法建立，請稍後再開啟新對話重試。"
_DECLINED_MESSAGE = "了解，那就先不建立工單。如果之後還有需要，再告訴我即可。"


def decide(user_message: str) -> str:
    """回傳 'Y' / 'N' / 'U'。給 orchestrator 用。"""
    raw = call_fast(
        _DECISION_PROMPT.format(user_message=user_message),
        max_tokens=5,
        temperature=0.0,
        fallback="U",
    )
    if not raw:
        return "U"
    first = raw.strip()[:1].upper()
    return first if first in ("Y", "N", "U") else "U"


def handle_accept(state: dict) -> dict:
    """orchestrator 在 decision=Y 時呼叫。"""
    logger.info("session=%s 工單確認 decision=Y", state["session_id"])
    return _accept_and_proceed(state)


def handle_decline(state: dict) -> dict:
    """orchestrator 在 decision=N 時呼叫。"""
    logger.info("session=%s 工單確認 decision=N", state["session_id"])
    return _decline(state)


def _accept_and_proceed(state: dict) -> dict:
    """同意建單。會員直接帶 email 建單；訪客進入收 email 階段。"""
    state["ticket_state"]["user_decision"] = "accepted"
    user = state["user_info"]
    if user.get("is_logged_in") and user.get("user_email"):
        return _create_and_finish(state, user["user_email"])

    state["phase"] = "等待 Email"
    state["ticket_state"]["collecting_email"] = True
    return _emit(state, _EMAIL_REQUEST, "ticket_flow", show_button=False)


def _decline(state: dict) -> dict:
    """拒絕建單。回到對話模式。

    刻意保留 service_limits 所有欄位（counter 與 limit_reached）：
    - counter 留著是要讓 _handle_off_topic 偵測 >= max 後走灰框 off_topic_blocked
    - limit_reached 留著是事實記錄（這次 session 真的達上限了）
    再次觸發建單建議的循環，由 orchestrator 用 user_decision == 'declined' 阻擋。
    """
    state["ticket_state"]["user_decision"] = "declined"
    state["ticket_state"]["ticket_suggested"] = False
    state["phase"] = "對話中"
    return _emit(state, _DECLINED_MESSAGE, "ticket_flow", show_button=False)


def handle_email_input(state: dict, user_message: str) -> dict:
    """phase=等待 Email 時呼叫。"""
    email = user_message.strip()
    if EMAIL_RE.match(email):
        return _create_and_finish(state, email)

    state["ticket_state"]["email_attempts"] += 1
    if state["ticket_state"]["email_attempts"] >= MAX_EMAIL_ATTEMPTS:
        state["phase"] = "已結束"
        state["ticket_state"]["collecting_email"] = False
        return _emit(state, _EMAIL_GIVE_UP, "session_ended", show_button=False)

    return _emit(state, _EMAIL_FORMAT_ERROR, "ticket_flow", show_button=False)


def _create_and_finish(state: dict, user_email: str) -> dict:
    """執行建單，更新 state、phase=已結束，回給用戶工單編號。"""
    ticket_id = ticket.create_ticket(state, user_email)
    state["phase"] = "已結束"
    state["ticket_state"]["collecting_email"] = False
    msg = (
        f"您的工單已建立，工單編號為 #{ticket_id}。\n"
        "我們的客服團隊會在 1-2 個工作日內透過 Email 與您聯繫。\n"
        "若您還有其他問題，可以重新開啟對話。感謝您的耐心。"
    )
    return _emit(state, msg, "ticket_flow", show_button=False, ticket_id=ticket_id)


def initiate(state: dict) -> dict:
    """從前端「建立工單」按鈕觸發。直接從目前 state 推進到下一步，
    不需要等使用者再說一句。
    """
    state["ticket_state"]["ticket_suggested"] = True
    user = state["user_info"]
    if user.get("is_logged_in") and user.get("user_email"):
        return _create_and_finish(state, user["user_email"])

    state["phase"] = "等待 Email"
    state["ticket_state"]["collecting_email"] = True
    state["ticket_state"]["user_decision"] = "accepted"
    return _emit(state, _EMAIL_REQUEST, "ticket_flow", show_button=False)


def _emit(
    state: dict,
    ai_response: str,
    response_type: str,
    *,
    show_button: bool,
    ticket_id: int | None = None,
) -> dict:
    append_message(state, "assistant", ai_response, response_type=response_type)
    state["turn_count"] += 1
    state["updated_at"] = now_iso()
    save_state(state)
    return {
        "ai_response": ai_response,
        "response_type": response_type,
        "show_ticket_button": show_button,
        "ticket_id": ticket_id or state["ticket_state"].get("ticket_id"),
        "state": state,
    }
