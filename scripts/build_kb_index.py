"""一次性腳本：掃描 data/kb/*.md，用 Haiku 為每篇生成 summary + key_questions，
寫入 data/kb_index.json。

用法：
    python scripts/build_kb_index.py

每篇文章必須有 YAML-style front matter：
    ---
    id: kb_001
    title: 課程影片無法播放完整排查
    category: 技術問題
    ---
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv

load_dotenv()

from core.llm_client import call_fast  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

KB_DIR = Path(os.getenv("KB_DIR", "data/kb"))
INDEX_PATH = Path(os.getenv("KB_INDEX_PATH", "data/kb_index.json"))

PROMPT = """請根據以下 KB 文章，產生簡潔的索引資訊。
只回傳合法 JSON，格式：
{{"summary": "30-60 字內描述本文涵蓋的問題範圍", "key_questions": ["...", "...", "..."]}}

key_questions 是 3-5 個用戶可能會問、且本文可解答的具體問法。

# 文章標題
{title}

# 文章分類
{category}

# 文章內文
{body}

只輸出 JSON。
"""


_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)


def _extract_json(raw: str) -> dict | None:
    """從 LLM 回傳中抽出 JSON 物件，容忍 ```json 外框與多餘文字。"""
    if not raw:
        return None
    # 先去掉 ```json ... ``` 或 ``` ... ``` 外框
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.MULTILINE)
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        m = _JSON_OBJ_RE.search(cleaned)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except json.JSONDecodeError:
            return None


def parse_frontmatter(text: str) -> tuple[dict, str]:
    if not text.startswith("---"):
        return {}, text
    end = text.find("---", 3)
    if end == -1:
        return {}, text
    fm = text[3:end].strip()
    body = text[end + 3:].lstrip("\n")
    meta: dict = {}
    for line in fm.splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, body


def main() -> int:
    if not KB_DIR.exists():
        logger.error("KB 目錄不存在：%s", KB_DIR)
        return 1

    files = sorted(KB_DIR.glob("*.md"))
    if not files:
        logger.warning("KB 目錄沒有 .md 檔，產生空索引。")
        INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
        INDEX_PATH.write_text("[]", encoding="utf-8")
        return 0

    index: list[dict] = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        meta, body = parse_frontmatter(text)
        article_id = meta.get("id") or path.stem
        title = meta.get("title", "")
        category = meta.get("category", "")
        logger.info("處理 %s（%s）", article_id, title)

        prompt = PROMPT.format(title=title, category=category, body=body[:4000])
        raw = call_fast(prompt, max_tokens=400, temperature=0.0, fallback="")
        summary = ""
        key_questions: list[str] = []
        data = _extract_json(raw)
        if data is None:
            logger.warning("  解析失敗，跳過：%s（原始輸出：%r）", path.name, raw[:200])
        else:
            summary = data.get("summary", "")
            key_questions = data.get("key_questions", [])

        index.append({
            "id": article_id,
            "title": title,
            "category": category,
            "summary": summary,
            "key_questions": key_questions,
        })

    INDEX_PATH.parent.mkdir(parents=True, exist_ok=True)
    INDEX_PATH.write_text(
        json.dumps(index, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info("已寫入 %s（共 %d 篇）", INDEX_PATH, len(index))
    return 0


if __name__ == "__main__":
    sys.exit(main())
