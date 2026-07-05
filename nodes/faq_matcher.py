"""FAQ 資料存取(一顆腦改版後瘦身,規格 §6)。

v7 的 LLM 比對站 match() 已裁——比對職責併入分診腦(nodes/brain.py)。
本模組只留「讀 faq.json」的純程式函式,供 brain(組資料表/驗證編號)與
faq_responder(取答案本體)使用。
"""
from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _load_faq() -> list[dict]:
    path = Path(os.getenv("FAQ_PATH", "data/faq.json"))
    if not path.exists():
        logger.warning("faq.json 不存在：%s", path)
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def load_faq_by_id(faq_id: str) -> dict | None:
    for item in _load_faq():
        if item["id"] == faq_id:
            return item
    return None
