"""圖片三件套(契約 2026-07-17b):讀圖員描述併進問題、失敗退化、閉環不花讀圖錢。"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import orchestrator, runtime_config  # noqa: E402
from core import state as state_mod  # noqa: E402
from nodes import brain, vision  # noqa: E402


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setenv("HISKIO_CS_DB", str(tmp_path / "t.db"))
    state_mod.init_db()
    runtime_config.reset()
    yield
    runtime_config.reset()


def _fresh_state():
    s = state_mod.new_state()
    state_mod.save_state(s)
    return s


def _canned_decision(**extra):
    d = {
        "recommended_action": "acknowledge_uncertainty",
        "faq_id": None, "kb_article_ids": [], "clarify_message": "請再描述一下",
        "reason_to_user": None, "handoff_reason": "needs_human", "user_satisfied": False,
        "issue": {"category": "其他", "summary": "", "user_emotion": "中性"},
        "new_intents_to_log": [], "target_intent_index": None, "reason": "test",
    }
    d.update(extra)
    return d


def test_image_description_merged_into_message(db, monkeypatch):
    """附圖=讀圖員描述併進 user_message,分診腦看到完整內容;chat_history 同源。"""
    seen = {}

    def _decide(st, msg):
        seen["msg"] = msg
        return _canned_decision()

    monkeypatch.setattr(brain, "decide", _decide)
    monkeypatch.setattr(vision, "describe_images", lambda urls: "1. 訂單頁截圖，錯誤訊息：付款失敗 E102")
    s = _fresh_state()

    orchestrator.handle_user_message(s["session_id"], "這是什麼錯誤?", image_urls=["https://x/a.png"])

    assert "付款失敗 E102" in seen["msg"]
    assert "這是什麼錯誤?" in seen["msg"]
    reloaded = state_mod.load_state(s["session_id"])
    assert "付款失敗 E102" in reloaded["chat_history"][-2]["content"]  # user 那則


def test_image_only_message_gets_placeholder(db, monkeypatch):
    """只有圖沒文字:自動補「（用戶傳來圖片）」開頭,不會空訊息。"""
    seen = {}
    monkeypatch.setattr(brain, "decide", lambda st, msg: seen.update(msg=msg) or _canned_decision())
    monkeypatch.setattr(vision, "describe_images", lambda urls: "1. 一張課程畫面截圖")
    s = _fresh_state()
    orchestrator.handle_user_message(s["session_id"], "", image_urls=["https://x/a.png"])
    assert seen["msg"].startswith("（用戶傳來圖片）")
    assert "課程畫面截圖" in seen["msg"]


def test_vision_failure_degrades_to_text(db, monkeypatch):
    """讀圖全失敗:附註「無法讀取」照常走文字流程,不炸整輪。"""
    seen = {}
    monkeypatch.setattr(brain, "decide", lambda st, msg: seen.update(msg=msg) or _canned_decision())
    monkeypatch.setattr(vision, "describe_images", lambda urls: "")
    s = _fresh_state()
    res = orchestrator.handle_user_message(s["session_id"], "看一下這張", image_urls=["https://x/broken.png"])
    assert "無法讀取" in seen["msg"]
    assert res["response_type"] == "clarification"


def test_handed_off_skips_vision(db, monkeypatch):
    """已交接真人:閉環固定回覆,不花讀圖錢。"""
    def _boom(urls):
        raise AssertionError("已交接不該呼叫讀圖員")

    monkeypatch.setattr(vision, "describe_images", _boom)
    s = _fresh_state()
    s["ticket_state"]["handed_off"] = True
    state_mod.save_state(s)
    res = orchestrator.handle_user_message(s["session_id"], "再看一下", image_urls=["https://x/a.png"])
    assert res["response_type"] == "handoff"


def test_kb_answer_returns_sources_all_with_hidden_flag(db, monkeypatch):
    """sources_all(後台測試間用):含隱藏文章(無 url→hidden=True);sources 維持只列公開。"""
    monkeypatch.setattr(brain, "decide", lambda st, msg: _canned_decision(
        recommended_action="answer_with_kb", kb_article_ids=["kb_x", "kb_h"]))
    from nodes import cs_response, kb_indexer
    arts = {
        "kb_x": {"id": "kb_x", "title": "公開文", "content": "內容", "url": "https://h/x"},
        "kb_h": {"id": "kb_h", "title": "隱藏文", "content": "內容", "url": None},
    }
    monkeypatch.setattr(kb_indexer, "load_kb_article", lambda kid: arts.get(kid))
    monkeypatch.setattr(cs_response, "respond", lambda st, arts, msg: "答案")
    s = _fresh_state()
    res = orchestrator.handle_user_message(s["session_id"], "問題")
    assert res["sources"] == [{"title": "公開文", "url": "https://h/x"}]
    assert res["sources_all"] == [
        {"id": "kb_x", "title": "公開文", "hidden": False},
        {"id": "kb_h", "title": "隱藏文", "hidden": True},
    ]
