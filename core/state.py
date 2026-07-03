"""State 結構與 SQLite 持久化。

雛形階段直接用 dict + JSON 序列化存進 SQLite，避免 ORM 額外複雜度。
schema 與規格書 v2「State 結構規格」一節對應。
"""
from __future__ import annotations

import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core import runtime_config

DB_PATH = os.getenv("DB_PATH", "data/prototype.db")


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_state(user_info: dict | None = None) -> dict:
    """建立新 session 的初始 State。user_info 可帶會員資料，否則為訪客預設。"""
    ts = now_iso()
    default_user = {
        "is_logged_in": False,
        "user_id": None,
        "user_email": None,
        "user_name": None,
        "purchase_history": [],
    }
    if user_info:
        default_user.update(user_info)

    return {
        "session_id": str(uuid.uuid4()),
        "created_at": ts,
        "updated_at": ts,
        "phase": "對話中",
        "turn_count": 0,
        "user_info": default_user,
        "issue_context": {
            "category": None,
            "sub_category": None,
            "summary": None,
            "user_emotion": "中性",
        },
        "faq_context": {
            "matched_faq_id": None,
            "match_confidence": 0.0,
            "answer_strategy": None,
        },
        "kb_context": {
            "indexed_articles": [],
            "articles_used_in_response": [],
        },
        "service_limits": {
            # 門檻可由 HiSupport 注入覆寫；沒注入＝現況預設。
            "max_turns_per_session": runtime_config.get_threshold("max_turns_per_session", 20),
            "max_off_topic_count": runtime_config.get_threshold("max_off_topic_count", 3),
            "max_low_confidence_count": 3,
            "max_unresolved_count": 3,
            "off_topic_count": 0,
            "low_confidence_count": 0,
            "unresolved_count": 0,
            "limit_reached": False,
            "limit_reached_reason": None,
        },
        "ticket_state": {
            "ticket_suggested": False,
            "user_decision": None,
            "collecting_email": False,
            "email_attempts": 0,
            "ticket_id": None,
            "ticket_created_at": None,
        },
        # v4 新增 + v4.1 重設計：入口分類 + 意圖追蹤
        "intent_state": {
            "input_classification": None,      # greeting | unclear | off_topic | customer_service | None
            "consecutive_unclear_count": 0,
            "max_unclear_count": 2,            # 第 3 次 unclear 強制建單
            "greeting_count": 0,
            "max_greeting_count": 3,
            "intent_clarity": None,            # simple | ambiguous_subordinate | parallel_multiple | None
            "awaiting_selection": False,       # 是否在等用戶從多重意圖中選擇
            # v4.1 新模型：捨棄 primary/secondary/pending 三個欄位，改用：
            "current_intent": None,            # 現在正在跟用戶討論的意圖文字
            "intent_log": [],                  # 整個 session 偵測到的所有意圖
                                               # 每項：{"text": str, "status": str, "first_turn": int}
                                               # status: pending | in_progress | answered | confirmed_resolved
        },
        # v4 新增：升級信號（與 service_limits 並列，但職責不同）
        # service_limits 是定量上限（輪數/離題次數等），escalation_signals 是質性事件
        "escalation_signals": {
            "user_explicitly_requested_human": False,
            "ai_low_confidence_count": 0,
            "off_topic_count": 0,
            "issue_complexity_high": False,
            "user_anger_threshold_hit": False,
            "no_kb_match": False,              # KB 索引完全空陣列時設 True
        },
        "chat_history": [],
    }


def _connect() -> sqlite3.Connection:
    Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    """啟動時呼叫，確保 sessions 與 tickets 表存在。"""
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                state_json TEXT NOT NULL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS tickets (
                ticket_id INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                user_email TEXT NOT NULL,
                user_id TEXT,
                is_member BOOLEAN NOT NULL,
                issue_category TEXT,
                issue_summary TEXT NOT NULL,
                user_emotion_at_close TEXT,
                key_attempts TEXT,
                full_chat_history TEXT NOT NULL,
                status TEXT DEFAULT 'open',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            """
        )


def save_state(state: dict) -> None:
    """寫入或更新 sessions 表。"""
    state["updated_at"] = now_iso()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO sessions (session_id, state_json, created_at, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(session_id) DO UPDATE SET
                state_json = excluded.state_json,
                updated_at = excluded.updated_at
            """,
            (
                state["session_id"],
                json.dumps(state, ensure_ascii=False),
                state["created_at"],
                state["updated_at"],
            ),
        )


def load_state(session_id: str) -> dict | None:
    """讀取 session，找不到回 None。"""
    with _connect() as conn:
        row = conn.execute(
            "SELECT state_json FROM sessions WHERE session_id = ?",
            (session_id,),
        ).fetchone()
    if not row:
        return None
    return json.loads(row["state_json"])


def append_message(state: dict, role: str, content: str, response_type: str | None = None) -> None:
    """把一輪訊息加進 chat_history，由 orchestrator 呼叫。"""
    msg: dict[str, Any] = {
        "role": role,
        "content": content,
        "timestamp": now_iso(),
    }
    if response_type:
        msg["response_type"] = response_type
    state["chat_history"].append(msg)
