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

import hmac
import json
import logging
import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

from core import orchestrator  # noqa: E402
from core.state import init_db, load_state, new_state, save_state  # noqa: E402

# 互動 API 文件(/docs、/openapi.json)預設關閉——與「對外只開三個入口」一致，
# 避免未授權就能讀到完整 API 藍圖。本機開發要看時設環境變數 ENABLE_API_DOCS=1。
_ENABLE_API_DOCS = os.getenv("ENABLE_API_DOCS", "").lower() in ("1", "true", "yes")
app = FastAPI(
    title="HiSKIO AI 客服雛形 (Phase 4)",
    docs_url="/docs/api" if _ENABLE_API_DOCS else None,
    redoc_url=None,
    openapi_url="/openapi.json" if _ENABLE_API_DOCS else None,
)

STATIC_DIR = Path(__file__).resolve().parent / "static"


@app.middleware("http")
async def _require_api_key(request, call_next):
    """服務對接用存取金鑰（shared secret）。

    未設 HIBOT_API_KEY 環境變數 ＝ 維持開放（現行行為，不影響既有原型）。
    設定後：/api/* 一律需 `Authorization: Bearer <金鑰>`；/health 與頁面入口不鎖。
    """
    expected = (os.getenv("HIBOT_API_KEY") or "").strip()
    if expected and request.url.path.startswith("/api/"):
        provided = request.headers.get("authorization") or ""
        want = f"Bearer {expected}"
        # 固定時間比對，避免逐字元計時試出金鑰
        if not hmac.compare_digest(provided.encode("utf-8", "ignore"), want.encode("utf-8")):
            return JSONResponse(status_code=401, content={"detail": "未授權：缺少或錯誤的存取金鑰"})
    return await call_next(request)


@app.on_event("startup")
def _startup() -> None:
    init_db()
    logger.info("DB 已初始化：%s", os.getenv("DB_PATH", "data/prototype.db"))
    from core import runtime_config
    runtime_config.init()
    logger.info("執行期設定 runtime_config 已載入")
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
    # #7：HiSupport 可直接帶真實會員資料進來（HiBot 不自己存會員）
    user_email: str | None = None
    user_name: str | None = None
    purchase_history: list[str] | None = None


@app.post("/api/session/new")
def new_session(req: NewSessionReq):
    user_info = None
    if req.is_logged_in:
        # 優先：HiSupport 直接帶會員資料（email／name／purchase 有帶任一即採信）
        if req.user_email or req.user_name or req.purchase_history is not None:
            user_info = {
                "is_logged_in": True,
                "user_id": req.user_id,
                "user_email": req.user_email,
                "user_name": req.user_name,
                "purchase_history": req.purchase_history or [],
            }
        else:
            # 舊路徑（開發用）：只帶 user_id → 查本機 mock_users
            if not req.user_id:
                raise HTTPException(status_code=400, detail="登入需提供會員資料或 user_id")
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


# ────────────────────────────────────────────────────────────────────
# 設定注入（#1）：HiSupport 推入人設 prompt + 關鍵門檻；未給＝維持現況
# ────────────────────────────────────────────────────────────────────


class ConfigReq(BaseModel):
    prompts: dict[str, str] | None = None
    thresholds: dict[str, int] | None = None


@app.get("/api/config")
def get_config():
    """讀目前生效的注入設定（人設 prompt 覆寫 + 關鍵門檻）。"""
    from core import runtime_config
    return runtime_config.get_overlay()


@app.post("/api/config")
def set_config(req: ConfigReq):
    """推入設定（疊加覆寫、即時生效、持久化）。未給的欄位維持現況。

    白名單外的鍵／型別錯誤的值會被靜默忽略；回傳套用後的實際設定。
    失敗關閉：這端點可全域改寫 bot 人設/門檻，未設 HIBOT_API_KEY 時一律拒絕，
    避免「忘了設金鑰」＝「任何人都能改機器人的腦」。
    """
    if not (os.getenv("HIBOT_API_KEY") or "").strip():
        raise HTTPException(status_code=403, detail="設定注入需先設定 HIBOT_API_KEY 存取金鑰")
    from core import runtime_config
    return runtime_config.set_overlay(
        {"prompts": req.prompts or {}, "thresholds": req.thresholds or {}},
        merge=True,
    )


@app.post("/api/ticket/create")
def create_ticket(req: TicketReq):
    """前端「建立工單」按鈕觸發，跳過「好/不用」直接走收 email / 建單流程。"""
    state = load_state(req.session_id)
    if state is None:
        raise HTTPException(status_code=404, detail=f"session {req.session_id} 不存在")
    return orchestrator.initiate_ticket_from_button(req.session_id)


@app.get("/health")
def health():
    """給 HiSupport 偵測 HiBot 活著沒；掛掉時 HiSupport 端可自動把對話轉真人。"""
    return {"status": "ok", "service": "hibot"}


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
