"""分診腦決定單測試(規格 §4/§14-2)。

釘死:①解析失敗 → 安全 fallback ②幻覺編號白名單剔除、剔完空手 → 降級轉真人
③user_satisfied 預設 False(「好吧」誤結案防線的資料面)。
用假 call_triage,不連線、不花錢;FAQ/KB 用臨時檔控制白名單內容。
"""
import json

import pytest

from nodes import brain, faq_matcher, kb_indexer


@pytest.fixture
def kb_env(tmp_path, monkeypatch):
    """臨時 FAQ/KB 白名單:只有 faq_001 與 kb_001 存在。"""
    faq = [{"id": "faq_001", "category": "測試", "question_patterns": ["怎麼退費"],
            "core_steps": ["步驟一"], "fallback_message": "找真人"}]
    idx = [{"id": "kb_001", "title": "退費", "category": "測試",
            "summary": "退費規定", "key_questions": ["能退嗎"]}]
    (tmp_path / "faq.json").write_text(json.dumps(faq, ensure_ascii=False), encoding="utf-8")
    (tmp_path / "kb_index.json").write_text(json.dumps(idx, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("FAQ_PATH", str(tmp_path / "faq.json"))
    monkeypatch.setenv("KB_INDEX_PATH", str(tmp_path / "kb_index.json"))
    faq_matcher._load_faq.cache_clear()
    kb_indexer._load_kb_index.cache_clear()
    brain.reset_caches()
    yield
    faq_matcher._load_faq.cache_clear()
    kb_indexer._load_kb_index.cache_clear()
    brain.reset_caches()


def _state():
    from core.state import new_state
    return new_state()


def _fake_triage(monkeypatch, payload):
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    monkeypatch.setattr(brain, "call_triage", lambda *a, **k: text)


def test_parse_failure_falls_back_to_uncertainty(kb_env, monkeypatch):
    _fake_triage(monkeypatch, "我不是 JSON")
    out = brain.decide(_state(), "亂講")
    assert out["recommended_action"] == "acknowledge_uncertainty"
    assert out["clarify_message"]  # 有安全話術,不讓對外流程壞掉


def test_unknown_action_falls_back(kb_env, monkeypatch):
    _fake_triage(monkeypatch, {"recommended_action": "self_destruct"})
    out = brain.decide(_state(), "hi")
    assert out["recommended_action"] == "acknowledge_uncertainty"


def test_hallucinated_kb_id_is_dropped_and_downgraded(kb_env, monkeypatch):
    """幻覺編號 kb_099 → 剔除;空手 → 降級 suggest_ticket,不硬答(規格 §14-2)。"""
    _fake_triage(monkeypatch, {
        "recommended_action": "answer_with_kb",
        "kb_article_ids": ["kb_099"],
    })
    out = brain.decide(_state(), "退費")
    assert out["recommended_action"] == "suggest_ticket"
    assert out["kb_article_ids"] == []


def test_hallucinated_faq_id_downgrades_to_ticket(kb_env, monkeypatch):
    _fake_triage(monkeypatch, {
        "recommended_action": "answer_with_faq",
        "faq_id": "faq_031",
        "kb_article_ids": [],
    })
    out = brain.decide(_state(), "退費")
    assert out["recommended_action"] == "suggest_ticket"
    assert out["faq_id"] is None


def test_hallucinated_faq_with_valid_kb_degrades_to_kb(kb_env, monkeypatch):
    """faq 編的、但 kb 是真的 → 改走 KB,不轉真人(能答就答)。"""
    _fake_triage(monkeypatch, {
        "recommended_action": "answer_with_faq",
        "faq_id": "faq_031",
        "kb_article_ids": ["kb_001"],
    })
    out = brain.decide(_state(), "退費")
    assert out["recommended_action"] == "answer_with_kb"
    assert out["kb_article_ids"] == ["kb_001"]


def test_valid_decision_passes_through(kb_env, monkeypatch):
    _fake_triage(monkeypatch, {
        "recommended_action": "answer_with_faq",
        "faq_id": "faq_001",
        "user_satisfied": False,
        "issue": {"category": "退款", "summary": "想退費", "user_emotion": "中性"},
        "new_intents_to_log": [{"text": "退費", "role": "primary", "in_scope": True}],
    })
    out = brain.decide(_state(), "怎麼退費")
    assert out["recommended_action"] == "answer_with_faq"
    assert out["faq_id"] == "faq_001"
    assert out["issue"]["category"] == "退款"
    assert out["new_intents_to_log"][0]["text"] == "退費"


def test_user_satisfied_defaults_false(kb_env, monkeypatch):
    """決定單沒帶 user_satisfied → 一律 False(寧可不結案,也不誤結案)。"""
    _fake_triage(monkeypatch, {"recommended_action": "acknowledge_confirmation"})
    out = brain.decide(_state(), "好吧")
    assert out["user_satisfied"] is False
