"""節點 2：FAQ 快查比對（Haiku）。

把 faq.json 的 id + question_patterns 餵給 Haiku（不給 core_steps，省 token），
讓它判斷用戶訊息是否命中。

回傳：{"matched_id": str | None, "confidence": float}
解析失敗或信心 < 0.7 → orchestrator 改走 RAG。
"""
from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from pathlib import Path

from core.llm_client import call_fast, load_prompt
from core.text_utils import extract_json_object

logger = logging.getLogger(__name__)

_PROMPT = load_prompt("faq_matcher")


@lru_cache(maxsize=1)
def _load_faq() -> list[dict]:
    path = Path(os.getenv("FAQ_PATH", "data/faq.json"))
    if not path.exists():
        logger.warning("faq.json 不存在：%s", path)
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _format_faq_for_prompt(faqs: list[dict]) -> str:
    """只給 id + 類別 + question_patterns，core_steps 留到 responder 用。"""
    if not faqs:
        return "（FAQ 清單為空）"
    lines = []
    for item in faqs:
        patterns = "、".join(item.get("question_patterns", []))
        lines.append(f"- {item['id']}（{item.get('category', '')}）：{patterns}")
    return "\n".join(lines)


def match(user_message: str) -> dict:
    """回傳 {'matched_id': str | None, 'confidence': float}。"""
    faqs = _load_faq()
    if not faqs:
        return {"matched_id": None, "confidence": 0.0}

    prompt = _PROMPT.format(
        faq_list=_format_faq_for_prompt(faqs),
        user_message=user_message,
    )
    raw = call_fast(prompt, max_tokens=100, temperature=0.0, fallback="")
    parsed = extract_json_object(raw)
    if parsed is None:
        logger.warning("faq_matcher 解析失敗：%r", raw[:200])
        return {"matched_id": None, "confidence": 0.0}

    valid_ids = {f["id"] for f in faqs}
    mid = parsed.get("matched_id")
    if mid is not None and mid not in valid_ids:
        logger.warning("faq_matcher 回傳未知 id：%r", mid)
        mid = None
    try:
        conf = float(parsed.get("confidence", 0.0))
    except (TypeError, ValueError):
        conf = 0.0
    return {"matched_id": mid, "confidence": max(0.0, min(1.0, conf))}


def load_faq_by_id(faq_id: str) -> dict | None:
    for item in _load_faq():
        if item["id"] == faq_id:
            return item
    return None
