"""節點 4：KB 索引比對（RAG 第一階段，Haiku）。

從 data/kb_index.json 中挑出最多 3 篇最相關文章的 ID。
索引清單只給 id + title + summary + key_questions（不給全文，省 token）。
"""
from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from pathlib import Path

from core.llm_client import call_haiku, load_prompt
from core.text_utils import extract_json_array, format_recent_history

logger = logging.getLogger(__name__)

_PROMPT = load_prompt("kb_indexer")


@lru_cache(maxsize=1)
def _load_kb_index() -> list[dict]:
    path = Path(os.getenv("KB_INDEX_PATH", "data/kb_index.json"))
    if not path.exists():
        logger.warning("kb_index.json 不存在，KB 索引將回傳空列表。請先跑 scripts/build_kb_index.py")
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def _format_index_list(index: list[dict]) -> str:
    if not index:
        return "（KB 索引為空）"
    lines = []
    for item in index:
        kqs = "、".join(item.get("key_questions", []))
        lines.append(
            f"- {item['id']}｜{item.get('title', '')}｜{item.get('category', '')}\n"
            f"  摘要：{item.get('summary', '')}\n"
            f"  常見問法：{kqs}"
        )
    return "\n".join(lines)


def index_articles(state: dict, user_message: str, max_articles: int = 3) -> list[str]:
    """回傳挑選出的 article id 列表（最多 max_articles 篇）。"""
    kb_index = _load_kb_index()
    if not kb_index:
        return []

    prompt = _PROMPT.format(
        kb_index_list=_format_index_list(kb_index),
        user_message=user_message,
        recent_history=format_recent_history(state["chat_history"]),
    )

    raw = call_haiku(prompt, max_tokens=100, temperature=0.0, fallback="[]")
    data = extract_json_array(raw) or []
    valid_ids = {item["id"] for item in kb_index}
    return [str(x) for x in data if isinstance(x, str) and x in valid_ids][:max_articles]


def load_kb_article(article_id: str) -> dict | None:
    """讀取 data/kb/{article_id}.md 並解析 front matter + 內文。"""
    kb_dir = Path(os.getenv("KB_DIR", "data/kb"))
    path = kb_dir / f"{article_id}.md"
    if not path.exists():
        logger.warning("KB 文章不存在：%s", path)
        return None
    text = path.read_text(encoding="utf-8")
    return _parse_markdown_with_frontmatter(text, article_id)


def _parse_markdown_with_frontmatter(text: str, fallback_id: str) -> dict:
    """簡易 front matter 解析（不引入額外套件）。"""
    meta: dict = {"id": fallback_id, "title": "", "category": "", "content": text}
    if text.startswith("---"):
        end = text.find("---", 3)
        if end != -1:
            fm = text[3:end].strip()
            body = text[end + 3:].lstrip("\n")
            for line in fm.splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    meta[k.strip()] = v.strip()
            meta["content"] = body
    return meta
