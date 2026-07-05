"""節點 5：RAG 客服解答（Sonnet）。

輸入：State、選中的 KB 文章全文、用戶訊息
輸出：給用戶的回應字串（可能以 [SUGGEST_TICKET] 開頭，由 orchestrator 解析）
"""
from __future__ import annotations

import logging

from core import runtime_config
from core.llm_client import call_writer, load_prompt
from core.text_utils import format_recent_history

logger = logging.getLogger(__name__)

_SYSTEM_TPL = load_prompt("cs_response_system")   # 人設與語氣(後台可注入覆寫)
_GUARD_TPL = load_prompt("cs_response_guard")     # 防捏造鐵則+任務+標記規則(不可覆寫)
_USER_TPL = load_prompt("cs_response_user")


def _format_purchase_summary(purchase_history: list[str]) -> str:
    if not purchase_history:
        return "尚未購買任何課程"
    return f"已購買 {len(purchase_history)} 門課程：{', '.join(purchase_history)}"


def _format_kb_articles(articles: list[dict]) -> str:
    if not articles:
        return "（KB 沒有相關文章，請謹慎回應，必要時建議轉真人客服）"
    blocks = []
    for art in articles:
        blocks.append(
            f"## [{art['id']}] {art.get('title', '')}\n"
            f"分類：{art.get('category', '未分類')}\n\n"
            f"{art.get('content', '')}"
        )
    return "\n\n---\n\n".join(blocks)


def respond(state: dict, kb_articles: list[dict], user_message: str) -> str:
    """產生 RAG 解答。回應字串可能以 [SUGGEST_TICKET] 開頭。"""
    user = state["user_info"]
    issue = state["issue_context"]

    # static 部分（角色、任務、規則）→ 可被 prompt cache 命中。
    # 組成＝「人設(後台可注入覆寫) + 守則(永遠附加、不可被蓋)」——
    # 2026-07-06 live 實測抓到的漏洞根治:舊設計注入=整份蓋掉,一行簡短人設就把
    # 防捏造鐵則/禁粗體全洗掉;改為人設歸人設、安全守則永遠生效。
    persona = runtime_config.get_prompt_override("cs_response_system") or _SYSTEM_TPL
    system_prompt = persona.rstrip() + "\n\n" + _GUARD_TPL

    # dynamic 部分（用戶狀態、KB 文章、歷史、訊息）每輪都不同
    user_prompt = _USER_TPL.format(
        is_logged_in="是" if user["is_logged_in"] else "否",
        is_returning_customer="是" if user.get("purchase_history") else "否",
        purchase_summary=_format_purchase_summary(user.get("purchase_history", [])),
        user_emotion=issue.get("user_emotion", "中性"),
        turn_count=state["turn_count"],
        category=issue.get("category") or "尚未分類",
        sub_category=issue.get("sub_category") or "尚未細分",
        summary=issue.get("summary") or "（首輪尚無摘要）",
        kb_articles_full_content=_format_kb_articles(kb_articles),
        chat_history_recent=format_recent_history(state["chat_history"], turns=3, empty="（首次對話）"),
        user_message=user_message,
    )

    fallback = "抱歉，目前系統有點忙不過來，請您稍後再試一次。"
    return call_writer(
        user_prompt,
        max_tokens=600,
        temperature=0.6,
        system=system_prompt,
        cache_system=True,
        fallback=fallback,
    )
