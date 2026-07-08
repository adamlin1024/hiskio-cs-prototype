"""#18 回覆對齊測試:照答旗標(verbatim)+參考來源(sources)。

釘死:①verbatim 文章=跳過寫手、一字不改用內文(寫手被呼叫=失敗)②sources 只列有公開
網址的文章(隱藏文章無 url,天生不外洩)③一般文章照走寫手、sources 照帶
④非 KB 路徑(問候/離題等)的回應 sources 恆為空陣列(契約穩定)。
不連線、不花錢(寫手整顆換掉)。
"""
import json

import pytest

from core import orchestrator
from core.state import new_state
from nodes import kb_indexer


@pytest.fixture
def kb_env(tmp_path, monkeypatch):
    kb = tmp_path / "kb"
    kb.mkdir()
    (kb / "kb_std.md").write_text(
        "---\nid: kb_std\ntitle: 組合包可以退費嗎?\ncategory: 購課\n"
        "url: http://hs.test/zh-tw/article/xyz\nverbatim: true\n---\n"
        "可以，組合包 7 天內未觀看可退費。\n",
        encoding="utf-8",
    )
    (kb / "kb_norm.md").write_text(
        "---\nid: kb_norm\ntitle: 退費規則\ncategory: 購課\n"
        "url: http://hs.test/zh-tw/article/abc\n---\n7 天內可退費。\n",
        encoding="utf-8",
    )
    (kb / "kb_hidden.md").write_text(
        "---\nid: kb_hidden\ntitle: 活動限定答案\ncategory: 活動\n---\n限時活動說明。\n",
        encoding="utf-8",
    )
    index = [
        {"id": "kb_std", "title": "組合包可以退費嗎?", "category": "購課", "summary": "s", "key_questions": ["q"]},
        {"id": "kb_norm", "title": "退費規則", "category": "購課", "summary": "s", "key_questions": ["q"]},
        {"id": "kb_hidden", "title": "活動限定答案", "category": "活動", "summary": "s", "key_questions": ["q"]},
    ]
    (tmp_path / "kb_index.json").write_text(json.dumps(index, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("KB_DIR", str(kb))
    monkeypatch.setenv("KB_INDEX_PATH", str(tmp_path / "kb_index.json"))
    monkeypatch.delenv("HISUPPORT_KB_URL", raising=False)
    kb_indexer._load_kb_index.cache_clear()
    monkeypatch.setattr(orchestrator, "save_state", lambda s: None)  # 不碰 DB
    yield
    kb_indexer._load_kb_index.cache_clear()


def _decision(ids):
    return {"action": "answer_with_kb", "kb_article_ids": ids}


def test_verbatim_skips_writer_and_returns_content_as_is(kb_env, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("照答文章不該呼叫寫手")

    monkeypatch.setattr(orchestrator.cs_response, "respond", boom)

    res = orchestrator._execute_answer_with_kb(new_state(), "組合包能退嗎", _decision(["kb_std"]), session_id="t1")

    assert res["ai_response"] == "可以，組合包 7 天內未觀看可退費。"
    assert res["sources"] == [{"title": "組合包可以退費嗎?", "url": "http://hs.test/zh-tw/article/xyz"}]


def test_verbatim_top_pick_ignores_other_articles(kb_env, monkeypatch):
    monkeypatch.setattr(
        orchestrator.cs_response, "respond",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("不該呼叫寫手")),
    )

    res = orchestrator._execute_answer_with_kb(
        new_state(), "組合包能退嗎", _decision(["kb_std", "kb_norm"]), session_id="t1")

    # 照答=只用首選那篇;來源也只列它
    assert res["ai_response"] == "可以，組合包 7 天內未觀看可退費。"
    assert [s["url"] for s in res["sources"]] == ["http://hs.test/zh-tw/article/xyz"]


def test_normal_article_uses_writer_and_carries_sources(kb_env, monkeypatch):
    monkeypatch.setattr(orchestrator.cs_response, "respond", lambda *a, **k: "改寫後的回答。")

    res = orchestrator._execute_answer_with_kb(new_state(), "退費?", _decision(["kb_norm"]), session_id="t1")

    assert res["ai_response"] == "改寫後的回答。"
    assert res["sources"] == [{"title": "退費規則", "url": "http://hs.test/zh-tw/article/abc"}]


def test_article_without_url_yields_no_source(kb_env, monkeypatch):
    monkeypatch.setattr(orchestrator.cs_response, "respond", lambda *a, **k: "活動回答。")

    res = orchestrator._execute_answer_with_kb(new_state(), "活動?", _decision(["kb_hidden"]), session_id="t1")

    assert res["sources"] == []


def test_non_kb_paths_return_empty_sources(kb_env):
    res = orchestrator._build_response(new_state(), "您好!", "greeting")
    assert res["sources"] == []


# ===== 審查後補洞(2026-07-08) =====

def test_verbatim_honored_even_when_not_first_pick(kb_env, monkeypatch):
    """審查發現(中):分診腦不保證最相關排第一;標準答案排第二也要照答,不可送寫手改寫。"""
    monkeypatch.setattr(
        orchestrator.cs_response, "respond",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("有 verbatim 篇時不該呼叫寫手")),
    )
    # kb_norm(非照答) 排第一、kb_std(照答) 排第二
    res = orchestrator._execute_answer_with_kb(
        new_state(), "組合包能退嗎", _decision(["kb_norm", "kb_std"]), session_id="t1")

    assert res["ai_response"] == "可以，組合包 7 天內未觀看可退費。"  # 照答那篇
    assert [s["url"] for s in res["sources"]] == ["http://hs.test/zh-tw/article/xyz"]


def test_empty_verbatim_content_falls_back_to_writer(kb_env, monkeypatch, tmp_path):
    """審查發現(中):照答文章內文為空 → 不回空白訊息,退回寫手正常改寫。"""
    # 把 kb_std 內文清空(保留 verbatim front matter)
    (tmp_path / "kb" / "kb_std.md").write_text(
        "---\nid: kb_std\ntitle: 空的照答\ncategory: 購課\n"
        "url: http://hs.test/zh-tw/article/xyz\nverbatim: true\n---\n\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(orchestrator.cs_response, "respond", lambda *a, **k: "寫手補的回答。")

    res = orchestrator._execute_answer_with_kb(
        new_state(), "組合包能退嗎", _decision(["kb_std"]), session_id="t1")

    assert res["ai_response"] == "寫手補的回答。"  # 沒回空白
