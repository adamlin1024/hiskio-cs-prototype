"""整合層端點測試：/health 存活偵測 + /api/* 存取金鑰（shared secret）。

範圍鐵則：只加對接用的「殼」，不動對話流程。
金鑰未設定＝維持開放（現行行為）；設定後才鎖 /api/*，/health 永遠開放。
"""
import os
import tempfile

# 測試用臨時 DB，避免污染 data/prototype.db；並確保預設無金鑰（開放）
os.environ["DB_PATH"] = os.path.join(tempfile.gettempdir(), "hibot_endpoint_test.db")
os.environ.pop("HIBOT_API_KEY", None)

from fastapi.testclient import TestClient  # noqa: E402
import app as app_module  # noqa: E402
from core.state import init_db  # noqa: E402

init_db()  # 建表，讓 /api/session/new 能存（測試不觸發 lifespan startup）

client = TestClient(app_module.app)


def test_health_returns_ok():
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_api_open_when_no_key(monkeypatch):
    monkeypatch.delenv("HIBOT_API_KEY", raising=False)
    r = client.get("/api/mock_users")
    assert r.status_code == 200  # 未設金鑰＝維持開放


def test_api_blocked_without_header_when_key_set(monkeypatch):
    monkeypatch.setenv("HIBOT_API_KEY", "secret123")
    r = client.get("/api/mock_users")
    assert r.status_code == 401


def test_api_allowed_with_correct_bearer(monkeypatch):
    monkeypatch.setenv("HIBOT_API_KEY", "secret123")
    r = client.get("/api/mock_users", headers={"Authorization": "Bearer secret123"})
    assert r.status_code == 200


def test_api_blocked_with_wrong_bearer(monkeypatch):
    monkeypatch.setenv("HIBOT_API_KEY", "secret123")
    r = client.get("/api/mock_users", headers={"Authorization": "Bearer nope"})
    assert r.status_code == 401


def test_health_open_even_when_key_set(monkeypatch):
    monkeypatch.setenv("HIBOT_API_KEY", "secret123")
    r = client.get("/health")
    assert r.status_code == 200


def test_session_new_accepts_member_data(monkeypatch):
    monkeypatch.delenv("HIBOT_API_KEY", raising=False)
    r = client.post("/api/session/new", json={
        "is_logged_in": True,
        "user_id": "u1",
        "user_email": "a@b.com",
        "user_name": "小明",
        "purchase_history": ["Python 課"],
    })
    assert r.status_code == 200
    info = r.json()["state"]["user_info"]
    assert info["is_logged_in"] is True
    assert info["user_email"] == "a@b.com"
    assert info["user_name"] == "小明"
    assert info["purchase_history"] == ["Python 課"]


def test_config_roundtrip(monkeypatch, tmp_path):
    from core import runtime_config
    monkeypatch.setenv("RUNTIME_CONFIG_PATH", str(tmp_path / "rc.json"))
    monkeypatch.setenv("HIBOT_API_KEY", "k")
    runtime_config.reset()
    hdr = {"Authorization": "Bearer k"}
    r = client.post("/api/config", headers=hdr, json={
        "thresholds": {"max_off_topic_count": 8},
        "prompts": {"cs_response_system": "溫柔"},
    })
    assert r.status_code == 200
    assert r.json()["thresholds"]["max_off_topic_count"] == 8
    assert r.json()["prompts"]["cs_response_system"] == "溫柔"
    g = client.get("/api/config", headers=hdr)
    assert g.json()["thresholds"]["max_off_topic_count"] == 8
    runtime_config.reset()


def test_config_write_blocked_without_key(monkeypatch):
    """失敗關閉：沒設金鑰時，能改寫 bot 腦的 /api/config 一律拒絕。"""
    monkeypatch.delenv("HIBOT_API_KEY", raising=False)
    r = client.post("/api/config", json={"thresholds": {"max_off_topic_count": 8}})
    assert r.status_code == 403
