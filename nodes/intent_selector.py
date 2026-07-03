"""多重意圖選項節點（v4.1，從 intent_log 取選項）。

只在 intent_clarity = parallel_multiple 時呼叫。
顯示給用戶的選項與 parse_selection 的對應，都從 intent_log 中
status=pending 或 status=in_progress 的項目取，確保畫面與 state 一致。
"""
from __future__ import annotations

import logging

from core.llm_client import call_fast

logger = logging.getLogger(__name__)


def _selectable_indices(intent_log: list[dict]) -> list[int]:
    """回傳可被選擇的 intent_log 索引：
    - status 為 pending 或 in_progress
    - role 為 primary 或 secondary（context 是純脈絡不算待辦）
    """
    return [
        i for i, item in enumerate(intent_log)
        if item.get("status") in ("pending", "in_progress")
        and item.get("role", "primary") != "context"
    ]


def respond(state: dict, user_message: str) -> str:
    """產生編號清單，直接從 intent_log 渲染。"""
    intent_log = state["intent_state"].get("intent_log") or []
    indices = _selectable_indices(intent_log)
    if not indices:
        return "了解，請告訴我您想處理的問題是什麼。"

    lines = ["了解您同時提到幾個問題，我們可以一個一個處理。\n您提到的問題有："]
    for display_no, idx in enumerate(indices, 1):
        lines.append(f"{display_no}. {intent_log[idx]['text']}")
    lines.append("\n請回覆編號（例如「1」）告訴我想先處理哪一個，其他問題稍後可以再協助您。")
    return "\n".join(lines)


_SELECTION_PROMPT = """用戶剛才被詢問「您想先處理哪一個問題」並列出了選項。判斷用戶這次的回覆是不是在選擇選項。

選項列表：
{options_list}

用戶回覆：「{user_message}」

# 判斷規則（嚴格）

**S（在選）的標準很嚴格**：用戶必須是「指認某個選項」，而不是「描述/陳述/抱怨」
- 「1」、「2」、「3」、「我選 1」、「第二個」、「先處理 X」（X 出現在選項中）→ S
- 不確定就回 N，不要硬選

**N（不在選）涵蓋很廣**：
- 用戶用「我有 X 方面的問題」「我想問 X」這種陳述句 → N（在描述新事情，不是在選）
- 用戶問「下一個問題呢」「為什麼...」「不選了」「都重要」 → N
- 用戶說的內容跟所有選項都不直接對應 → N
- 用戶在抱怨、閒聊、質疑 → N

# 範例
- 「1」→ S1
- 「我選第 2 個」→ S2
- 「先處理發票」→ S2（若發票是選項 2）
- **「我有水果方面的問題」→ N**（這是「我有 X 方面的問題」陳述句，是新訴求）
- **「我有發票方面的問題」→ N**（同上，即使發票在選項裡，這仍是陳述新訴求）
- 「為什麼你刪掉做愛」→ N
- 「下一個」→ N
- 「不選了」→ N

只回傳 S1/S2/.../SN 或 N 一個 token，不要其他文字。
"""


def parse_selection(state: dict, user_message: str) -> int | None:
    """從用戶回覆判斷他選了哪個意圖。

    回傳 intent_log 的索引；若用戶沒在「選」（在說新事情）回 None，
    讓 orchestrator 把訊息丟回正常流程處理（entry_classifier）。
    """
    intent_log = state["intent_state"].get("intent_log") or []
    indices = _selectable_indices(intent_log)
    if not indices:
        return None

    options_str = "\n".join(
        f"{display_no}. {intent_log[idx]['text']}"
        for display_no, idx in enumerate(indices, 1)
    )
    prompt = _SELECTION_PROMPT.format(options_list=options_str, user_message=user_message)
    raw = call_fast(prompt, max_tokens=5, temperature=0.0, fallback="N")
    if not raw:
        return None
    token = raw.strip().upper()
    if token.startswith("N"):
        logger.info("parse_selection 判定不是在選：%r", user_message[:50])
        return None
    if not token.startswith("S"):
        return None
    digits = "".join(c for c in token[1:] if c.isdigit())
    if not digits:
        return None
    try:
        display_no = int(digits)
    except ValueError:
        return None
    pos = display_no - 1
    if pos < 0 or pos >= len(indices):
        return None
    return indices[pos]
