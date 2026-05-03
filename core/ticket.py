"""工單管理：用 Sonnet 生成摘要、寫入 SQLite tickets 表。

依規格 v2 第 820 行附近。Sonnet 解析 chat_history 產出 summary/category/emotion/key_attempts，
然後 INSERT 進 tickets 表，回傳 ticket_id。
"""
from __future__ import annotations

import json
import logging
from typing import Any

from core.llm_client import call_sonnet, load_prompt
from core.state import _connect, now_iso  # noqa: SLF001 — 內部模組共用 connection helper
from core.text_utils import extract_json_object

logger = logging.getLogger(__name__)

_PROMPT = load_prompt("ticket_summary")


def _format_chat_history(history: list[dict]) -> str:
    if not history:
        return "（無對話）"
    lines = []
    for msg in history:
        role = "用戶" if msg["role"] == "user" else "AI"
        lines.append(f"{role}：{msg['content']}")
    return "\n".join(lines)


def generate_summary(state: dict) -> dict[str, Any]:
    """用 Sonnet 從 chat_history 產出摘要欄位。失敗時回傳 fallback。"""
    prompt = _PROMPT.format(full_chat_history=_format_chat_history(state["chat_history"]))
    raw = call_sonnet(prompt, max_tokens=400, temperature=0.2, fallback="")
    parsed = extract_json_object(raw)
    if parsed is None:
        logger.warning("ticket summary 解析失敗，使用 fallback。raw=%r", raw[:200])
        issue = state["issue_context"]
        return {
            "summary": issue.get("summary") or "用戶提出客服問題（AI 摘要失敗）",
            "category": issue.get("category") or "其他",
            "user_emotion_at_close": issue.get("user_emotion") or "中性",
            "key_attempts": "（摘要失敗）",
        }
    return {
        "summary": parsed.get("summary") or "（無摘要）",
        "category": parsed.get("category") or "其他",
        "user_emotion_at_close": parsed.get("user_emotion_at_close") or "中性",
        "key_attempts": parsed.get("key_attempts") or "",
    }


def create_ticket(state: dict, user_email: str) -> int:
    """寫入 SQLite tickets 表並回傳 ticket_id。"""
    summary_data = generate_summary(state)
    user_info = state["user_info"]
    is_member = bool(user_info.get("is_logged_in"))
    user_id = user_info.get("user_id")
    chat_history_json = json.dumps(state["chat_history"], ensure_ascii=False)
    ts = now_iso()

    with _connect() as conn:
        cur = conn.execute(
            """
            INSERT INTO tickets (
                session_id, user_email, user_id, is_member,
                issue_category, issue_summary, user_emotion_at_close, key_attempts,
                full_chat_history, status, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)
            """,
            (
                state["session_id"],
                user_email,
                user_id,
                is_member,
                summary_data["category"],
                summary_data["summary"],
                summary_data["user_emotion_at_close"],
                summary_data["key_attempts"],
                chat_history_json,
                ts,
                ts,
            ),
        )
        ticket_id = cur.lastrowid

    state["ticket_state"]["ticket_id"] = ticket_id
    state["ticket_state"]["ticket_created_at"] = ts
    logger.info(
        "工單已建立 #%s（session=%s, member=%s, email=%s）",
        ticket_id,
        state["session_id"],
        is_member,
        user_email,
    )
    return ticket_id
