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
    # 之後帶游標=上次 generated_at 往回退安全邊界(5 秒),避免查詢空檔/同秒編輯漏抓
    from datetime import datetime
    since = datetime.fromisoformat(calls[1]["updated_since"])
    assert since == datetime.fromisoformat("2026-07-07T12:00:00+08:00") - __import__("datetime").timedelta(seconds=5)


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


# ===== 審查後補洞(2026-07-08 對抗式健檢) =====

def test_title_with_triple_dash_keeps_metadata(remote_env, monkeypatch):
    """審查發現(嚴重):標題含 '---' 曾讓 front matter 解析錯位、verbatim/url 遺失。
    改成內文檔+JSON 索引後,中繼資料不再靠 front matter,標題含任何字元都不影響。"""
    feed = {
        "articles": [{
            "id": 20, "language": "zh-tw", "slug": "std1", "title": "課程退費 --- 常見問題",
            "category": "購課", "body_text": "可以，7 天內辦理。",
            "status": "visible", "url": "http://hs.test/zh-tw/article/std1", "verbatim": True,
            "content_updated_at": "2026-07-07T10:00:00+08:00", "updated_at": "2026-07-07T10:00:00+08:00",
        }],
        "active_ids": [20], "generated_at": "2026-07-07T12:00:00+08:00",
    }
    _fake_fetch(monkeypatch, feed)
    kb_remote.sync()

    art = kb_indexer.load_kb_article("hs_20")
    assert art["title"] == "課程退費 --- 常見問題"  # 標題完整
    assert art["verbatim"] is True                  # 照答旗標沒遺失
    assert art["url"] == "http://hs.test/zh-tw/article/std1"
    assert art["content"] == "可以，7 天內辦理。"    # 內文乾淨、不含洩漏的中繼資料


def test_invalid_id_skipped_not_crash(remote_env, monkeypatch):
    """審查發現(中/資安):缺 id 或 id 非數字(路徑跳脫)→ 略過該篇,不炸、不寫到別的檔。"""
    feed = {
        "articles": [
            {"id": "../../evil", "title": "壞", "body_text": "x", "status": "visible", "url": None},
            {"title": "沒id", "body_text": "y", "status": "visible", "url": None},
            {"id": 21, "title": "好文章", "category": "c", "body_text": "正常", "status": "visible",
             "url": "http://hs.test/zh-tw/article/ok", "verbatim": False,
             "updated_at": "2026-07-07T10:00:00+08:00"},
        ],
        "active_ids": [21], "generated_at": "2026-07-07T12:00:00+08:00",
    }
    _fake_fetch(monkeypatch, feed)
    stats = kb_remote.sync()

    assert stats["indexed"] == 1  # 只有合法那篇
    assert {e["id"] for e in kb_indexer._load_kb_index()} == {"hs_21"}
    assert not (remote_env / "kb_remote" / "hs_../../evil.md").exists()


# ===== 審查後補洞(2026-07-08 第二輪 Opus 健檢) =====

def test_empty_active_ids_present_prunes_all(remote_env, monkeypatch):
    """第二輪(中,修正首輪誤判):回應健康但 active_ids=[](小編把全部文章下架)→ 誠實清空,
    機器人不再拿已下架內容回答。首輪『空 active 一律不剪』會讓全下架永遠卡住,此為根治。"""
    _fake_fetch(monkeypatch, FEED)
    kb_remote.sync()  # 先有 hs_12/hs_15

    _fake_fetch(monkeypatch, {"articles": [], "active_ids": [], "generated_at": "2026-07-07T13:00:00+08:00"})
    stats = kb_remote.sync()

    assert stats["pruned"] == 2
    assert kb_indexer._load_kb_index() == []  # 全清空、閉嘴


def test_missing_active_ids_key_does_not_wipe(remote_env, monkeypatch):
    """第二輪(中):真正該防的是『回應壞掉、根本沒給 active_ids 欄位』——這時才保守不剪、保住舊資料。
    以「有沒有這個欄位」判斷,而非「是不是空的」。"""
    _fake_fetch(monkeypatch, FEED)
    kb_remote.sync()

    # 回應缺 active_ids 欄位(格式壞掉/舊版回應)
    _fake_fetch(monkeypatch, {"articles": [], "generated_at": "2026-07-07T13:00:00+08:00"})
    stats = kb_remote.sync()

    assert stats["pruned"] == 0
    assert {e["id"] for e in kb_indexer._load_kb_index()} == {"hs_12", "hs_15"}  # 保住

    # 全量同步(full=True)仍允許歸零(手動途徑,不受防呆擋)
    _fake_fetch(monkeypatch, {"articles": [], "generated_at": "2026-07-07T14:00:00+08:00"})
    kb_remote.sync(full=True)
    assert kb_indexer._load_kb_index() == []


def test_wrong_shape_feed_keeps_cache(remote_env, monkeypatch):
    """第二輪(中):合法 JSON 但結構不對(回陣列/字串)→ 視同失敗,沿用快取、不讓 sync 拋例外炸開機線程。"""
    _fake_fetch(monkeypatch, FEED)
    kb_remote.sync()

    _fake_fetch(monkeypatch, ["not", "a", "dict"])
    stats = kb_remote.sync()

    assert stats.get("error")
    assert {e["id"] for e in kb_indexer._load_kb_index()} == {"hs_12", "hs_15"}


def test_corrupt_index_file_non_list_is_ignored(remote_env, monkeypatch):
    """第二輪(弱):遠端索引檔被損成合法 JSON 但非 list(如 {})→ 當空清單,不讓合併載入 TypeError。"""
    _fake_fetch(monkeypatch, FEED)
    kb_remote.sync()

    (remote_env / "kb_remote_index.json").write_text("{}", encoding="utf-8")
    kb_indexer._load_kb_index.cache_clear()

    assert kb_remote.load_remote_index() == []
    assert kb_indexer._load_kb_index() == []  # 本地空 + 遠端當空 → 不炸


def test_article_not_in_active_ids_skipped_no_waste(remote_env, monkeypatch):
    """第二輪(弱):對方回了某篇更新卻沒把它列進 active_ids → 不寫檔、不花 LLM 索引(反正要被剪)。"""
    called = []
    monkeypatch.setattr(
        kb_remote, "_llm_index_card",
        lambda t, c, b: (called.append(t), {"summary": "s", "key_questions": [t]})[1],
    )
    feed = {
        "articles": [
            {"id": 30, "title": "會被剪", "category": "c", "body_text": "x", "status": "hidden",
             "url": None, "updated_at": "2026-07-07T10:00:00+08:00"},
            {"id": 31, "title": "留著", "category": "c", "body_text": "y", "status": "visible",
             "url": "http://hs.test/a", "updated_at": "2026-07-07T10:00:00+08:00"},
        ],
        "active_ids": [31], "generated_at": "2026-07-07T12:00:00+08:00",
    }
    _fake_fetch(monkeypatch, feed)
    stats = kb_remote.sync()

    assert stats["indexed"] == 1        # 只 index 了在 active 的那篇
    assert called == ["留著"]           # 被剪的那篇沒花 LLM
    assert not (remote_env / "kb_remote" / "hs_30.md").exists()
    assert {e["id"] for e in kb_indexer._load_kb_index()} == {"hs_31"}


def test_cursor_rewound_to_avoid_same_second_miss(remote_env, monkeypatch):
    """審查發現(中):游標往回退安全邊界,避免查詢空檔/同秒編輯永久漏抓。"""
    _fake_fetch(monkeypatch, FEED)  # generated_at=2026-07-07T12:00:00+08:00
    kb_remote.sync()

    state = json.loads((remote_env / "kb_remote_state.json").read_text(encoding="utf-8"))
    from datetime import datetime
    cursor = datetime.fromisoformat(state["last_generated_at"])
    gen = datetime.fromisoformat("2026-07-07T12:00:00+08:00")
    assert cursor < gen  # 游標比 generated_at 早(退了安全邊界)


def test_remote_enabled_retires_local_kb(remote_env, monkeypatch, tmp_path):
    """單一真理(Adam 2026-07-09 拍板「以說明中心為準」):遠端啟用時本地 kb_index 全數退場,
    分診腦只看得到說明中心的 hs_* 文章——本地凍結拷貝不得與現行版並存(避免引到過期內容)。"""
    (tmp_path / "kb_index.json").write_text(
        json.dumps([{"id": "kb_001", "title": "舊拷貝", "category": "課程購買", "summary": "x", "key_questions": []}]),
        encoding="utf-8",
    )
    _fake_fetch(monkeypatch, FEED)
    kb_remote.sync()

    ids = {e["id"] for e in kb_indexer._load_kb_index()}
    assert ids == {"hs_12", "hs_15"}  # 只有遠端;kb_001 退場


def test_remote_disabled_uses_local_only(monkeypatch, tmp_path):
    """遠端停用(HISUPPORT_KB_URL 未設)=純本地,本機開發行為不變。"""
    monkeypatch.delenv("HISUPPORT_KB_URL", raising=False)
    (tmp_path / "kb_index.json").write_text(
        json.dumps([{"id": "kb_001", "title": "本地", "category": "課程購買", "summary": "x", "key_questions": []}]),
        encoding="utf-8",
    )
    monkeypatch.setenv("KB_INDEX_PATH", str(tmp_path / "kb_index.json"))
    monkeypatch.setenv("KB_REMOTE_INDEX_PATH", str(tmp_path / "kb_remote_index.json"))
    _bust()
    ids = {e["id"] for e in kb_indexer._load_kb_index()}
    _bust()
    assert ids == {"kb_001"}
