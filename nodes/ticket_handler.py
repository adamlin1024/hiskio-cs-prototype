"""轉真人「確認 / 拒絕」判斷（工單流程改版後的精簡版）。

orchestrator 在 phase=等待轉真人確認 時呼叫 decide() 判斷用戶回覆：
- Y → orchestrator._execute_handoff（講安撫話、發交接訊號）
- N → handle_decline（回對話、不再自動強逼）
- U → orchestrator 自行 fall through

※ 舊「工單/留言」流程（收 Email、建工單、編號、phase=已結束 死路、前端建單按鈕）
   已於 2026-07-04「轉真人改版」移除；封存記錄見
   HiSupport/docs/2026-07-04-hibot-handoff-contract.md §7。

判斷「好/不用」用便宜模型，避免硬比對「好的」「不要」「OK」這類變化。
"""
from __future__ import annotations

import logging
import re

from core.llm_client import call_fast
from core.state import append_message, build_handoff, now_iso, save_state

logger = logging.getLogger(__name__)

# 「等待轉真人確認」下先用規則判定明確的好/不用，不靠快速模型
# （釘死實測 bug：使用者回「好」時快速模型偶爾判成 U → 漏接掉回主管 → 被誤判成 acknowledge）
_YES_RE = re.compile(
    r"^\s*(好(的|喔|啊|吧)?|可以|要|對|沒錯|是的?|ok(ay)?|yes|嗯+|"
    r"麻煩(你|您)?了?|請(幫我?|你)?|幫我?(轉|處理)?|轉(真人|接)?)"
    r"\s*[。.!！?？~～\s]*$",
    re.IGNORECASE,
)
_NO_RE = re.compile(
    r"^\s*(不用|不要|不需要|不必了?|不了|沒(關係|事)|算了|先不(用|要)|no(pe)?|別了?)"
    r"\s*[。.!！?？~～\s]*$",
    re.IGNORECASE,
)

_DECISION_PROMPT = """判斷用戶是否**明確同意**把這段對話轉給真人客服。只回傳一個英文字母。

Y = 明確同意轉真人
   嚴格條件：用戶**單純表達確認**，沒有附加新問題或新訴求
   例：「好」「好的」「OK」「可以」「麻煩你」「請幫我」「對」「yes」「go ahead」

N = 明確拒絕
   例：「不用」「不要」「不需要」「算了」「no」「先不要」「等等」

U = 不明確 / 不在回答 yes/no
   - 用戶又開始問新問題：「我要下一個問題」「下一題呢」「再問你個事」
   - 用戶在描述狀況：「影片還是不能看」「我有 XX 問題」
   - 用戶在閒聊或質疑：「為什麼要轉真人」「你是 AI 嗎」
   - 用戶說「要」但接著還有別的內容（例「我要先看影片」）→ 不算同意轉真人，是新訴求

# 範例
- 「好」→ Y
- 「OK 麻煩你」→ Y
- 「不用」→ N
- 「我要下一個問題」→ U（這是要繼續問，不是確認轉真人）
- 「幫我轉真人」→ Y（明確要轉）
- 「我要看影片」→ U（要看影片，不是轉真人）

用戶回覆：「{user_message}」

只回 Y、N 或 U，不要其他文字。
"""

_DECLINED_MESSAGE = "好的，那就先不轉真人。之後有需要，再直接跟我說一聲就好。"


def decide(user_message: str) -> str:
    """回傳 'Y' / 'N' / 'U'。給 orchestrator 用。

    先用規則判定明確的好/不用（不呼叫模型，避免「好」被誤判成 U、漏接掉回主管被當成
    acknowledge）；只有帶新訴求或模糊的回覆才交給模型判 Y/N/U。
    """
    msg = user_message.strip()
    if _NO_RE.match(msg):
        return "N"
    if _YES_RE.match(msg):
        return "Y"
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


def handle_decline(state: dict) -> dict:
    """orchestrator 在 decision=N 時呼叫。"""
    logger.info("session=%s 轉真人確認 decision=N", state["session_id"])
    return _decline(state)


def _decline(state: dict) -> dict:
    """拒絕轉真人。回到對話模式，且這次 session 不再自動強逼。

    - 重置 consecutive_unclear_count：給使用者喘息空間，避免「才拒絕又被逼」。
    - 保留 user_decision == "declined"：orchestrator 的強制轉接閘門會據此不再自動強逼，
      改成被動等待（使用者需要時再開口）。
    - off_topic 等 service_limits 計數仍保留：搗亂照樣會走灰框鎖住。
    """
    state["ticket_state"]["user_decision"] = "declined"
    state["ticket_state"]["ticket_suggested"] = False
    state["intent_state"]["consecutive_unclear_count"] = 0
    state["phase"] = "對話中"
    return _emit(state, _DECLINED_MESSAGE, "handoff_declined")


def _emit(state: dict, ai_response: str, response_type: str) -> dict:
    append_message(state, "assistant", ai_response, response_type=response_type)
    state["turn_count"] += 1
    state["updated_at"] = now_iso()
    save_state(state)
    return {
        "ai_response": ai_response,
        "response_type": response_type,
        "show_ticket_button": False,
        "ticket_id": state["ticket_state"].get("ticket_id"),
        "handoff": build_handoff(state),
        "state": state,
    }
