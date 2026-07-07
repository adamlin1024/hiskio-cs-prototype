"""遠端知識來源測試(#7:知識庫改讀 HiSupport 說明中心)。

釘死:①HISUPPORT_KB_URL 未設=完全停用(現行本地行為不變)②同步把「啟用中」文章
落成 kb_remote_index.json+kb_remote/hs_<id>.md ③active_ids 剪枝停用/刪除篇
④LLM 索引失敗退化(前 60 字+標題)不擋同步 ⑤合併載入:本地+遠端、遠端前綴 hs_
⑥同步後快取確實被清(分診腦看得到新文章)。
用假 HTTP 與假 LLM,不連線、不花錢。
"""
import json

import pytest

from core import kb_remote
from nodes import brain, faq_matcher, kb_indexer


FEED = {
    "articles": [
        {
            "id": 12, "language": "zh-tw", "slug": "abcd1234", "title": "抵用券怎麼用",
            "category": "購課與退費", "body_text": "抵用券在結帳頁輸入代碼即可折抵。",
            "status": "visible", "url": "http://hs.test/zh-tw/article/abcd1234",
            "content_updated_at": "2026-07-07T10:00:00+08:00", "updated_at": "2026-07-07T10:00:00+08:00",
        },
        {
            "id": 15, "language": "zh-tw", "slug": "efgh5678", "title": "活動限定答案",
            "category": "活動", "body_text": "限時活動說明。",
            "status": "hidden", "url": None,
            "content_updated_at": "2026-07-07T11:00:00+08:00", "updated_at": "2026-07-07T11:00:00+08:00",
        },
    ],
    "active_ids": [12, 15],
    "generated_at": "2026-07-07T12:00:00+08:00",
}


@pytest.fixture
def remote_env(tmp_path, monkeypatch):
    """啟用遠端來源、資料落在臨時資料夾;本地 KB/FAQ 為空白名單。"""
    (tmp_path / "kb_index.json").write_text("[]", encoding="utf-8")
    (tmp_path / "faq.json").write_text("[]", encoding="utf-8")
    monkeypatch.setenv("KB_INDEX_PATH", str(tmp_path / "kb_index.json"))
    monkeypatch.setenv("FAQ_PATH", str(tmp_path / "faq.json"))
    monkeypatch.setenv("KB_DIR", str(tmp_path / "kb"))
    monkeypatch.setenv("HISUPPORT_KB_URL", "http://hs.test")
    monkeypatch.setenv("HISUPPORT_KB_KEY", "kb-secret")
    monkeypatch.setenv("KB_REMOTE_DIR", str(tmp_path / "kb_remote"))
    monkeypatch.setenv("KB_REMOTE_INDEX_PATH", str(tmp_path / "kb_remote_index.json"))
    monkeypatch.setenv("KB_REMOTE_STATE_PATH", str(tmp_path / "kb_remote_state.json"))
    # 假 LLM:固定回合法索引 JSON
    monkeypatch.setattr(
        kb_remote, "_llm_index_card",
        lambda title, category, body: {"summary": f"{title}摘要", "key_questions": [title]},
    )
    _bust()
    yield tmp_path
    _bust()


def _bust():
    faq_matcher._load_faq.cache_clear()
    kb_indexer._load_kb_index.cache_clear()
    brain.reset_caches()


def _fake_fetch(monkeypatch, payload):
    calls = []

    def fetch(params):
        calls.append(params)
        return payload

    monkeypatch.setattr(kb_remote, "_fetch_feed", fetch)
    return calls


def test_disabled_without_url(monkeypatch, tmp_path):
    monkeypatch.delenv("HISUPPORT_KB_URL", raising=False)
    assert kb_remote.enabled() is False
    stats = kb_remote.sync()
    assert stats["skipped"] == "disabled"


def test_sync_writes_index_and_articles(remote_env, monkeypatch):
    tmp_path = remote_env
    _fake_fetch(monkeypatch, FEED)

    stats = kb_remote.sync()

    assert stats["indexed"] == 2
    index = json.loads((tmp_path / "kb_remote_index.json").read_text(encoding="utf-8"))
    assert {e["id"] for e in index} == {"hs_12", "hs_15"}
    entry = next(e for e in index if e["id"] == "hs_12")
    assert entry["summary"] == "抵用券怎麼用摘要"
    assert entry["url"] == "http://hs.test/zh-tw/article/abcd1234"

    art = (tmp_path / "kb_remote" / "hs_12.md").read_text(encoding="utf-8")
    assert "抵用券在結帳頁輸入代碼即可折抵。" in art

    # 合併載入:遠端進白名單;取全文走 kb_remote 目錄
    ids = {e["id"] for e in kb_indexer._load_kb_index()}
    assert {"hs_12", "hs_15"} <= ids
    loaded = kb_indexer.load_kb_article("hs_12")
    assert loaded and "抵用券" in loaded["content"]


def test_sync_prunes_inactive_articles(remote_env, monkeypatch):
    tmp_path = remote_env
    _fake_fetch(monkeypatch, FEED)
    kb_remote.sync()

    # 第二次同步:15 被停用(不在 active_ids),且無新變動
    second = {"articles": [], "active_ids": [12], "generated_at": "2026-07-07T13:00:00+08:00"}
    _fake_fetch(monkeypatch, second)
    stats = kb_remote.sync()

    assert stats["pruned"] == 1
    index = json.loads((tmp_path / "kb_remote_index.json").read_text(encoding="utf-8"))
    assert {e["id"] for e in index} == {"hs_12"}
    assert not (tmp_path / "kb_remote" / "hs_15.md").exists()
    # 快取已清:白名單跟著變
    assert {e["id"] for e in kb_indexer._load_kb_index()} == {"hs_12"}


def test_sync_uses_incremental_cursor(remote_env, monkeypatch):
    calls = _fake_fetch(monkeypatch, FEED)
    kb_remote.sync()
    assert "updated_since" not in calls[0]  # 首次全量

    kb_remote.sync()
    assert calls[1].get("updated_since") == "2026-07-07T12:00:00+08:00"  # 之後帶游標(=上次 generated_at)


def test_llm_failure_falls_back_without_blocking(remote_env, monkeypatch):
    def boom(title, category, body):
        raise RuntimeError("llm down")

    monkeypatch.setattr(kb_remote, "_llm_index_card", boom)
    _fake_fetch(monkeypatch, FEED)

    stats = kb_remote.sync()

    assert stats["indexed"] == 2
    index = json.loads(
        (remote_env / "kb_remote_index.json").read_text(encoding="utf-8"))
    entry = next(e for e in index if e["id"] == "hs_12")
    assert entry["summary"].startswith("抵用券在結帳頁輸入代碼")  # 退化=內文前段
    assert entry["key_questions"] == ["抵用券怎麼用"]  # 退化=標題


def test_refresh_endpoint_disabled_returns_409(monkeypatch):
    monkeypatch.delenv("HISUPPORT_KB_URL", raising=False)
    monkeypatch.delenv("HIBOT_API_KEY", raising=False)
    from fastapi.testclient import TestClient

    import app as app_mod

    with TestClient(app_mod.app) as client:
        assert client.post("/api/kb/refresh").status_code == 409


def test_refresh_endpoint_triggers_sync(remote_env, monkeypatch):
    monkeypatch.delenv("HIBOT_API_KEY", raising=False)
    _fake_fetch(monkeypatch, FEED)
    from fastapi.testclient import TestClient

    import app as app_mod

    with TestClient(app_mod.app) as client:
        res = client.post("/api/kb/refresh")
    assert res.status_code == 200
    assert res.json()["indexed"] == 2


def test_fetch_failure_keeps_last_good_data(remote_env, monkeypatch):
    tmp_path = remote_env
    _fake_fetch(monkeypatch, FEED)
    kb_remote.sync()

    def boom(params):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(kb_remote, "_fetch_feed", boom)
    stats = kb_remote.sync()

    assert stats.get("error")
    # 最後一次成功的資料仍在(失聯 fallback)
    assert {e["id"] for e in kb_indexer._load_kb_index()} == {"hs_12", "hs_15"}
