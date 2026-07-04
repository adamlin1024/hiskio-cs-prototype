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
        # 服務計數（2026-07-04 清理：移除只算不觸發的死碼——max_turns_per_session「20 輪」、
        # 低信心/未解決上限、limit_reached/limit_reached_reason）。
        # 目前唯一會觸發動作的是離題：off_topic_count 達 max_off_topic_count → off_topic_blocked。
        # low_confidence_count／unresolved_count 僅由 evaluator 累計、供主管當軟參考，不觸發動作。
        "service_limits": {
            "max_off_topic_count": runtime_config.get_threshold("max_off_topic_count", 3),
            "off_topic_count": 0,
            "low_confidence_count": 0,
            "unresolved_count": 0,
        },
        # 轉真人交接狀態（2026-07-04 改版；舊工單欄位 collecting_email/email_attempts/
        # ticket_id/ticket_created_at 已移除，封存見 handoff-contract §7）
        "ticket_state": {
            "ticket_suggested": False,   # AI 是否已提議轉真人（等待用戶確認）
            "user_decision": None,       # accepted／declined／null
            "handed_off": False,         # 已交接真人＝True → 閉環、回應 handoff.requested=True
            "handoff_reason": None,      # no_kb_match／unclear_limit／needs_human／user_request
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
    """啟動時呼叫，確保 sessions 表存在。（tickets 表已隨工單流程移除，2026-07-04）"""
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                session_id TEXT PRIMARY KEY,
                state_json TEXT NOT NULL,
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


# ────────────────────────────────────────────────────────────────────
# 轉真人交接訊號（給 HiSupport 讀）— 放這裡讓 orchestrator 與 ticket_handler 共用、避免循環引用
# ────────────────────────────────────────────────────────────────────

_HANDOFF_REASON_LABEL = {
    "no_kb_match": "知識庫沒有對應資料",
    "unclear_limit": "多次無法理解用戶問題",
    "user_request": "用戶主動要求真人",
    "needs_human": "需要真人協助",
}


def build_handoff(state: dict) -> dict:
    """組轉真人訊號。requested=True（已交接）才帶 reason／summary；未交接一律 None（衛生）。"""
    ts = state["ticket_state"]
    requested = bool(ts.get("handed_off"))
    return {
        "requested": requested,
        "reason": ts.get("handoff_reason") if requested else None,
        "summary": build_handoff_summary(state) if requested else None,
    }


def build_handoff_summary(state: dict) -> str:
    """組給真人客服看的短摘要（人看的，非 JSON）。沿用 issue_context 既有欄位。"""
    ui = state["user_info"]
    ic = state["issue_context"]
    if ui.get("is_logged_in"):
        identity = f"會員（{ui.get('user_name') or '未提供姓名'}）"
    else:
        identity = "訪客"
    reason_code = state["ticket_state"].get("handoff_reason")
    reason_text = _HANDOFF_REASON_LABEL.get(reason_code, reason_code or "AI 無法完整處理")
    lines = [
        "【真人交接摘要】",
        f"• 身分：{identity}",
        f"• 問題類別：{ic.get('category') or '未分類'}",
        f"• 客戶想解決：{ic.get('summary') or '（尚無摘要）'}",
        f"• 轉真人原因：{reason_text}",
        f"• 客戶情緒：{ic.get('user_emotion') or '中性'}",
    ]
    return "\n".join(lines)
