"""KB 資料存取(一顆腦改版後瘦身,規格 §6)。

v7 的 LLM 挑文站 index_articles() 已裁——挑文職責併入分診腦(nodes/brain.py)。
本模組只留「讀索引/讀文章檔」的純程式函式,供 brain(組索引卡/驗證編號)與
orchestrator(照編號取全文給寫手)使用。
"""
from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def _load_kb_index() -> list[dict]:
    """本地索引＋遠端(HiSupport 說明中心,#7)索引合併;遠端停用時=純本地(現行行為)。"""
    path = Path(os.getenv("KB_INDEX_PATH", "data/kb_index.json"))
    local: list[dict] = []
    if path.exists():
        local = json.loads(path.read_text(encoding="utf-8"))
    else:
        logger.warning("kb_index.json 不存在。請先跑 tools/_hibot_build_indexes.py")

    from core import kb_remote

    return local + kb_remote.load_remote_index()


def load_kb_article(article_id: str) -> dict | None:
    """讀取文章全文。hs_ 前綴=遠端文章:內文讀 data/kb_remote/ 的純內文檔,
    title/category/url/verbatim 一律以 JSON 索引為權威(不靠 front matter,免標題含 '---' 解析錯位)。
    其餘=本地 data/kb/:照舊解析 front matter + 內文。"""
    from core import kb_remote

    if article_id.startswith(kb_remote.REMOTE_PREFIX):
        meta = kb_remote.remote_meta(article_id)
        if not meta:
            logger.warning("遠端 KB 索引無此文章（可能已下架）：%s", article_id)
            return None
        path = Path(os.getenv("KB_REMOTE_DIR", "data/kb_remote")) / f"{article_id}.md"
        if not path.exists():
            logger.warning("遠端 KB 內文檔不存在：%s", path)
            return None
        return {
            "id": article_id,
            "title": meta.get("title", ""),
            "category": meta.get("category", ""),
            "url": meta.get("url"),
            "verbatim": bool(meta.get("verbatim")),
            "content": path.read_text(encoding="utf-8").strip(),
        }

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
