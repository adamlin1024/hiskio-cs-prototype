"""HiSupport 說明中心遠端知識來源(#7 知識庫改讀說明中心)。

**HISUPPORT_KB_URL 未設＝完全停用**——維持現行本地 data/kb/ 行為,已上線服務零影響(feature flag)。
設定後:啟動先全量同步;之後**純事件驅動**(HiSupport 文章異動時打門鈴 POST /api/kb/refresh)增量,
無定時輪詢(Adam 拍板;見 CLAUDE.md「遠端知識來源」)。

資料落地(與本地 KB 兩層分離,不互相污染):
- KB_REMOTE_INDEX_PATH(data/kb_remote_index.json):遠端文章「權威中繼資料」——id/title/category/url/verbatim
  ＋索引卡 summary/key_questions。json.dumps 寫入,絕不會被標題內的字元(例 '---')弄壞。
- KB_REMOTE_DIR(data/kb_remote/)/hs_<id>.md:**只放內文本體**(不寫 front matter)——
  中繼資料一律以上面的 JSON 索引為準,徹底避開「標題含 '---' 讓 front matter 解析錯位」的坑。
- KB_REMOTE_STATE_PATH(data/kb_remote_state.json):增量游標(=上次回應 generated_at 往回退安全邊界,
  避免「查詢完成→產 generated_at」空檔或同秒編輯永久漏抓;重抓已同步文章是冪等的、只多重建索引卡)。

索引卡 summary/key_questions 由寫手 LLM 生成(同 build_kb_index 流程);LLM 失敗退化為
「內文前 60 字+標題」,同步不中斷。HiSupport 失聯 → 保留最後一次成功資料(fallback)。

環境變數:
- HISUPPORT_KB_URL   HiSupport 位址(例 https://help.hiskio.com);未設=停用
- HISUPPORT_KB_KEY   拉知識用的金鑰(對到 HiSupport 的 HIBOT_API_KEY);未設時退用本服務的 HIBOT_API_KEY。
                     與本服務門鎖分開設定的原因:本機單機測試常「HiBot 不鎖、HiSupport 鎖」,
                     若共用一把,設了金鑰會連本機測試頁一起鎖掉。
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import urllib.parse
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

REMOTE_PREFIX = "hs_"

_sync_lock = threading.Lock()

_INDEX_PROMPT = """請根據以下 KB 文章，產生簡潔的索引資訊。
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


def enabled() -> bool:
    return bool((os.getenv("HISUPPORT_KB_URL") or "").strip())


def _paths() -> tuple[Path, Path, Path]:
    return (
        Path(os.getenv("KB_REMOTE_INDEX_PATH", "data/kb_remote_index.json")),
        Path(os.getenv("KB_REMOTE_DIR", "data/kb_remote")),
        Path(os.getenv("KB_REMOTE_STATE_PATH", "data/kb_remote_state.json")),
    )


def load_remote_index() -> list[dict]:
    """讀遠端索引卡(給 kb_indexer 合併用)。停用=空(舊快取檔不外漏)。"""
    if not enabled():
        return []
    index_path, _, _ = _paths()
    if not index_path.exists():
        return []
    try:
        return json.loads(index_path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001 — 壞檔不擋服務,重同步會蓋回
        logger.exception("kb_remote index 讀取失敗:%s", index_path)
        return []


def sync(full: bool = False) -> dict:
    """向 HiSupport 拉「啟用中」文章,落地+剪枝+清快取。回統計 dict(絕不丟例外)。"""
    if not enabled():
        return {"skipped": "disabled"}

    with _sync_lock:
        index_path, kb_dir, state_path = _paths()
        params: dict = {}
        if not full:
            cursor = _read_state(state_path).get("last_generated_at")
            if cursor:
                params["updated_since"] = cursor

        try:
            feed = _fetch_feed(params)
        except Exception as exc:  # noqa: BLE001 — 失聯 fallback:保留最後一次成功資料
            logger.warning("kb_remote 同步失敗(沿用最後快取):%s", exc)
            return {"error": str(exc)}

        articles = feed.get("articles") or []
        # active_ids 只收「乾淨數字 id」(防禦:對方資料異常時不讓髒 id 汙染剪枝集合)
        active = {f"{REMOTE_PREFIX}{i}" for i in (feed.get("active_ids") or []) if _valid_id(i)}

        index = {e["id"]: e for e in load_remote_index()}

        # 1) 更新/新增有變動的文章:寫內文檔+重建索引卡(LLM,失敗退化)。
        #    id 必須是乾淨數字才處理——否則(對方資料異常/被冒充)可能路徑跳脫寫到別的檔;跳過並記警告。
        kb_dir.mkdir(parents=True, exist_ok=True)
        indexed = 0
        for art in articles:
            raw_id = art.get("id")
            if not _valid_id(raw_id):
                logger.warning("kb_remote 略過無效 id 的文章:%r", raw_id)
                continue
            rid = f"{REMOTE_PREFIX}{raw_id}"
            body = (art.get("body_text") or "").strip()
            title = (art.get("title") or "").strip()
            category = (art.get("category") or "").strip()
            _write_article_md(kb_dir / f"{rid}.md", body)
            index[rid] = {
                "id": rid,
                "title": title,
                "category": category,
                "url": art.get("url"),
                "verbatim": bool(art.get("verbatim")),  # #18 照答:跳過寫手,一字不改用內文
                "updated_at": art.get("updated_at"),
                **_index_card(title, category, body),
            }
            indexed += 1

        # 2) 剪枝:不在 active_ids=被停用/隱藏改草稿/刪除 → 索引與內文一起移除。
        #    防呆:active_ids 全空但本地原本有資料、且本次也沒有任何更新 → 疑似對方查詢異常,
        #    不執行大規模清空(避免一次刪光整庫);全量同步(full=True)才允許歸零。
        pruned = 0
        suspicious_wipe = not active and index and not articles and not full
        if suspicious_wipe:
            logger.warning(
                "kb_remote:active_ids 為空但本地有 %d 篇且本次無更新,疑似異常,跳過剪枝(可手動全量同步歸零)",
                len(index),
            )
        else:
            for rid in list(index.keys()):
                if rid not in active:
                    index.pop(rid)
                    (kb_dir / f"{rid}.md").unlink(missing_ok=True)
                    pruned += 1

        index_path.parent.mkdir(parents=True, exist_ok=True)
        index_path.write_text(
            json.dumps(list(index.values()), ensure_ascii=False, indent=2), encoding="utf-8")
        # 游標往回退安全邊界(見檔頭):跳過異常剪枝時不推進游標,免得漏抓那批
        if not suspicious_wipe:
            _write_state(state_path, {"last_generated_at": _rewind_cursor(feed.get("generated_at"))})

        _bust_caches()
        stats = {"indexed": indexed, "pruned": pruned, "active": len(active)}
        logger.info("kb_remote 同步完成:%s", stats)
        return stats


def _fetch_feed(params: dict) -> dict:
    """GET {HISUPPORT_KB_URL}/api/hibot/knowledge(Bearer 金鑰)。用 stdlib,零新相依。"""
    base = (os.getenv("HISUPPORT_KB_URL") or "").strip().rstrip("/")
    key = (os.getenv("HISUPPORT_KB_KEY") or os.getenv("HIBOT_API_KEY") or "").strip()
    url = base + "/api/hibot/knowledge"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "Accept": "application/json",
        "Authorization": f"Bearer {key}",
    })
    with urllib.request.urlopen(req, timeout=15) as res:  # noqa: S310 — url 來自環境設定
        return json.loads(res.read().decode("utf-8"))


def _index_card(title: str, category: str, body: str) -> dict:
    """索引卡(summary+key_questions):LLM 生成,失敗退化不擋同步。"""
    try:
        card = _llm_index_card(title, category, body)
        summary = str(card.get("summary") or "").strip()
        questions = [str(q).strip() for q in (card.get("key_questions") or []) if str(q).strip()]
        if summary and questions:
            return {"summary": summary, "key_questions": questions}
        raise ValueError("LLM 索引卡欄位不完整")
    except Exception as exc:  # noqa: BLE001
        logger.warning("索引卡 LLM 生成失敗(退化為內文前段+標題):%s", exc)
        return {"summary": body[:60] or title, "key_questions": [title]}


def _llm_index_card(title: str, category: str, body: str) -> dict:
    """呼叫寫手 LLM 產索引卡(同 scripts/build_kb_index.py 的 prompt)。測試時整顆換掉。"""
    from core.llm_client import call_writer

    raw = call_writer(_INDEX_PROMPT.format(title=title, category=category, body=body[:4000]))
    match = _JSON_OBJ_RE.search(raw or "")
    if not match:
        raise ValueError(f"LLM 回傳非 JSON:{(raw or '')[:80]}")
    return json.loads(match.group(0))


def _valid_id(raw) -> bool:
    """遠端文章 id 必須是乾淨的正整數(HiSupport 自動編號)。防路徑跳脫、防髒資料。"""
    return raw is not None and re.fullmatch(r"\d+", str(raw)) is not None


def _rewind_cursor(generated_at: str | None, seconds: int = 5) -> str | None:
    """把增量游標往回退幾秒:避開「查詢完成→產 generated_at」空檔與同秒編輯漏抓。
    重抓已同步文章是冪等的(依 rid 覆寫),代價只是偶爾多重建一兩張索引卡。解析失敗＝原樣存。"""
    if not generated_at:
        return generated_at
    try:
        from datetime import datetime, timedelta
        return (datetime.fromisoformat(generated_at) - timedelta(seconds=seconds)).isoformat()
    except (ValueError, TypeError):
        return generated_at


def remote_meta(rid: str) -> dict:
    """遠端文章的權威中繼資料(來自 JSON 索引,絕不會被標題內字元弄壞)。找不到回空 dict。"""
    for e in load_remote_index():
        if e.get("id") == rid:
            return e
    return {}


def _write_article_md(path: Path, body: str) -> None:
    """遠端文章只寫內文本體——不寫 front matter。中繼資料一律以 kb_remote_index.json 為權威,
    徹底避開「標題含 '---' 讓 front matter 解析錯位、verbatim/url 遺失」的坑(#18 照答保證的守門)。"""
    path.write_text(body + "\n", encoding="utf-8")


def _read_state(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _write_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")


def _bust_caches() -> None:
    """知識變了 → 清掉所有吃它的快取(分診腦系統指令含整份索引卡,必清)。"""
    from nodes import brain, kb_indexer

    kb_indexer._load_kb_index.cache_clear()
    brain.reset_caches()
