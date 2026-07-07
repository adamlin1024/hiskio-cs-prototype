"""FastAPI 主程式（v7.2）。

對外路由（只開 3 個 HTML 入口、其餘是 API）：
- GET  /                       對話介面
- GET  /docs/guide             產品說明
- POST /api/session/new        新建 session（會員/訪客）
- POST /api/chat               送訊息、拿回應（回應含 handoff 轉真人訊號）

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
    from core.model_config import missing_api_keys, validate_model_config
    try:
        validate_model_config()
        logger.info("模型設定 config/models.toml 驗證通過")
    except Exception as e:
        logger.error("模型設定 config/models.toml 有問題，服務不啟動：%s", e)
        raise

    # 缺 API 金鑰就明講（L1）：沒填金鑰不會擋開機，但線上每次呼叫模型才 401、
    # 被 llm_client 靜默退化成罐頭回覆。這裡在開機階段就大聲點名缺哪個環境變數。
    _missing = missing_api_keys()
    if _missing:
        logger.warning("=" * 64)
        for m in _missing:
            logger.warning(
                "⚠️  缺少 API 金鑰：環境變數 %s 未設定 → provider「%s」(供 %s 等級) "
                "的 LLM 呼叫會失敗、自動退化為罐頭回覆。請在 .env 設定 %s 後重啟。",
                m["env"], m["provider"], "/".join(m["roles"]), m["env"],
            )
        logger.warning("=" * 64)
    else:
        logger.info("API 金鑰檢查通過：所有等級用到的 provider 金鑰都已設定")

    # 遠端知識來源(#7):設了 HISUPPORT_KB_URL 才啟用;開機對齊一次(背景跑,不擋啟動、失敗沿用最後快取)。
    # 之後的更新全靠 HiSupport 門鈴(POST /api/kb/refresh)——**沒有定時輪詢**(Adam 2026-07-08 拍板)。
    from core import kb_remote
    if kb_remote.enabled():
        import threading
        threading.Thread(target=kb_remote.sync, name="kb-remote-boot-sync", daemon=True).start()
        logger.info("遠端知識來源已啟用:%s(開機對齊已排入背景)", os.getenv("HISUPPORT_KB_URL"))


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
    messages: dict[str, str] | None = None  # HiSupport 推入對外訊息（如轉真人安撫話 handoff_message）
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
        {
            "prompts": req.prompts or {},
            "messages": req.messages or {},
            "thresholds": req.thresholds or {},
        },
        merge=True,
    )


@app.post("/api/kb/refresh")
def kb_refresh():
    """HiSupport 門鈴(#7):說明中心文章有異動時打這裡 → 立即增量同步遠端知識。

    也是後台「立即同步知識」按鈕的落點。零輪詢設計:更新只由本門鈴+開機對齊觸發。
    金鑰把關走全域中介層(設 HIBOT_API_KEY 後 /api/* 一律要 Bearer)。
    """
    from core import kb_remote
    if not kb_remote.enabled():
        raise HTTPException(status_code=409, detail="遠端知識來源未啟用(未設 HISUPPORT_KB_URL)")
    stats = kb_remote.sync()
    if stats.get("error"):
        raise HTTPException(status_code=502, detail=f"同步失敗(沿用最後快取):{stats['error']}")
    return stats


@app.get("/health")
def health():
    """給 HiSupport 偵測 HiBot 活著沒；掛掉時 HiSupport 端可自動把對話轉真人。"""
    return {"status": "ok", "service": "hibot"}


@app.get("/")
def root():
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/docs/guide")
def guide_page():
    return FileResponse(STATIC_DIR / "guide.html")


# 工單後台（/admin + /api/admin/tickets*）已隨工單流程移除（2026-07-04 轉真人改版）。
# 真人交接的摘要改由 HiSupport 端顯示，不再進 HiBot 的 tickets 表。
# 對外只暴露兩個 HTML 入口：/（對話介面）、/docs/guide（產品說明）；其餘為 API。
