"""FastAPI 主程式（v7.2）。

對外路由（只開 3 個 HTML 入口、其餘是 API）：
- GET  /                       對話介面
- GET  /admin                  後台
- GET  /docs/guide             產品說明
- POST /api/session/new        新建 session（會員/訪客）
- POST /api/chat               送訊息、拿回應
- POST /api/ticket/create      用戶按下「建立工單」按鈕觸發

啟動：
    uvicorn app:app --reload --port 8765
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

from core import orchestrator  # noqa: E402
from core.state import init_db, load_state, new_state, save_state  # noqa: E402

app = FastAPI(title="HiSKIO AI 客服雛形 (Phase 4)")

STATIC_DIR = Path(__file__).resolve().parent / "static"


@app.on_event("startup")
def _startup() -> None:
    init_db()
    logger.info("DB 已初始化：%s", os.getenv("DB_PATH", "data/prototype.db"))
    # 開機即驗證模型設定：設定檔壞掉/等級解析不出來 → 直接讓服務起不來（大聲失敗），
    # 而不是等線上請求才 500。設定正確時線上請求都能正常解析。
    from core.model_config import validate_model_config
    try:
        validate_model_config()
        logger.info("模型設定 config/models.toml 驗證通過")
    except Exception as e:
        logger.error("模型設定 config/models.toml 有問題，服務不啟動：%s", e)
        raise


class NewSessionReq(BaseModel):
    is_logged_in: bool = False
    user_id: str | None = None


@app.post("/api/session/new")
def new_session(req: NewSessionReq):
    user_info = None
    if req.is_logged_in:
        if not req.user_id:
            raise HTTPException(status_code=400, detail="登入需提供 user_id")
        mock_path = Path(os.getenv("MOCK_USERS_PATH", "data/mock_users.json"))
        if not mock_path.exists():
            raise HTTPException(status_code=500, detail=f"找不到 mock_users 檔：{mock_path}")
        users = json.loads(mock_path.read_text(encoding="utf-8"))
        match = next((u for u in users if u.get("user_id") == req.user_id), None)
        if not match:
            raise HTTPException(status_code=404, detail=f"找不到 user_id={req.user_id}")
        user_info = {
            "is_logged_in": True,
            "user_id": match["user_id"],
            "user_email": match.get("user_email"),
            "user_name": match.get("user_name"),
            "purchase_history": match.get("purchase_history", []),
        }

    state = new_state(user_info=user_info)
    save_state(state)
    return {"session_id": state["session_id"], "state": state}


@app.get("/api/mock_users")
def list_mock_users():
    """前端「模擬會員登入」下拉選單用。"""
    mock_path = Path(os.getenv("MOCK_USERS_PATH", "data/mock_users.json"))
    if not mock_path.exists():
        return {"users": []}
    users = json.loads(mock_path.read_text(encoding="utf-8"))
    return {"users": [
        {"user_id": u["user_id"], "user_name": u.get("user_name", "")}
        for u in users
    ]}


class ChatReq(BaseModel):
    session_id: str
    message: str


@app.post("/api/chat")
def chat(req: ChatReq):
    if not req.message.strip():
        raise HTTPException(status_code=400, detail="message 不可為空")
    state = load_state(req.session_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"session {req.session_id} 不存在")
    return orchestrator.handle_user_message(req.session_id, req.message)


class TicketReq(BaseModel):
    session_id: str


@app.get("/api/admin/usage")
def get_usage():
    """v6.2 token 統計（自上次 reset 以來）。"""
    from core import llm_client
    return llm_client.get_usage_summary()


@app.post("/api/admin/usage/reset")
def reset_usage():
    """重置 token 統計。"""
    from core import llm_client
    llm_client.reset_usage()
    return {"reset": True}


@app.post("/api/ticket/create")
def create_ticket(req: TicketReq):
    """前端「建立工單」按鈕觸發，跳過「好/不用」直接走收 email / 建單流程。"""
    state = load_state(req.session_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"session {req.session_id} 不存在")
    return orchestrator.initiate_ticket_from_button(req.session_id)


@app.get("/")
def root():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/admin")
def admin_page():
    return FileResponse(STATIC_DIR / "admin.html")


@app.get("/docs/guide")
def guide_page():
    return FileResponse(STATIC_DIR / "guide.html")


# ────────────────────────────────────────────────────────────────────
# 後台 API（Phase 5）
# ────────────────────────────────────────────────────────────────────

_VALID_TICKET_STATUS = {"open", "in_progress", "closed"}


@app.get("/api/admin/tickets")
def list_tickets(status: str | None = None):
    """工單列表。可用 status 參數過濾。"""
    from core.state import _connect  # 避免在頂部循環依賴

    sql = (
        "SELECT ticket_id, session_id, user_email, user_id, is_member, "
        "issue_category, issue_summary, user_emotion_at_close, status, "
        "created_at, updated_at FROM tickets"
    )
    params: tuple = ()
    if status:
        if status not in _VALID_TICKET_STATUS:
            raise HTTPException(status_code=400, detail=f"status 必須是 {_VALID_TICKET_STATUS}")
        sql += " WHERE status = ?"
        params = (status,)
    sql += " ORDER BY ticket_id DESC"

    with _connect() as conn:
        rows = conn.execute(sql, params).fetchall()

    tickets = []
    for r in rows:
        tickets.append({
            "ticket_id": r["ticket_id"],
            "session_id": r["session_id"],
            "user_email": r["user_email"],
            "user_id": r["user_id"],
            "is_member": bool(r["is_member"]),
            "issue_category": r["issue_category"],
            "issue_summary": r["issue_summary"],
            "user_emotion_at_close": r["user_emotion_at_close"],
            "status": r["status"],
            "created_at": r["created_at"],
            "updated_at": r["updated_at"],
        })
    return {"tickets": tickets}


@app.get("/api/admin/tickets/{ticket_id}")
def get_ticket(ticket_id: int):
    """單筆工單完整資料，含 full_chat_history。"""
    from core.state import _connect

    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM tickets WHERE ticket_id = ?", (ticket_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail=f"找不到工單 #{ticket_id}")

    chat_history = json.loads(row["full_chat_history"]) if row["full_chat_history"] else []
    return {
        "ticket_id": row["ticket_id"],
        "session_id": row["session_id"],
        "user_email": row["user_email"],
        "user_id": row["user_id"],
        "is_member": bool(row["is_member"]),
        "issue_category": row["issue_category"],
        "issue_summary": row["issue_summary"],
        "user_emotion_at_close": row["user_emotion_at_close"],
        "key_attempts": row["key_attempts"],
        "status": row["status"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "full_chat_history": chat_history,
    }


class TicketStatusReq(BaseModel):
    status: str


@app.post("/api/admin/tickets/{ticket_id}/status")
def update_ticket_status(ticket_id: int, req: TicketStatusReq):
    """標記工單為 open / in_progress / closed。"""
    from core.state import _connect, now_iso

    if req.status not in _VALID_TICKET_STATUS:
        raise HTTPException(status_code=400, detail=f"status 必須是 {_VALID_TICKET_STATUS}")

    with _connect() as conn:
        cur = conn.execute(
            "UPDATE tickets SET status = ?, updated_at = ? WHERE ticket_id = ?",
            (req.status, now_iso(), ticket_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(status_code=404, detail=f"找不到工單 #{ticket_id}")
    return {"ticket_id": ticket_id, "status": req.status}


# v7.2 移除 /static mount：三份 HTML 都 self-contained，沒有 /static/* 對外資源需求。
# 對外只暴露三個明確入口：/、/admin、/docs/guide
