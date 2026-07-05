"""HiSupport 可注入的執行期設定（單租戶：一份全域設定）。

範圍鐵則（Adam 定）：**外部沒給 ＝ 照現況預設**，疊加式覆寫、不動核心流程。
目前開放（最小可用版）：
- 人設 prompt：覆寫 `prompts/*.txt`（白名單目前只開主答人設 `cs_response_system`）。
- 對外訊息：覆寫特定固定訊息（白名單目前只開轉真人安撫話 `handoff_message`）。
- 關鍵門檻：`max_off_topic_count` / `max_unclear`（正整數）。

設定持久化到 `data/runtime_config.json`；HiSupport 透過 `POST /api/config` 推入。

⚠️ 生效時機不一致（待「收尾流程重設計」時統一）：
- 人設 prompt、`handoff_message`、`max_unclear`：每輪即時生效（含進行中的對話）。
- `max_off_topic_count`：開「新對話」時才烤進該對話（進行中的仍用舊值）。
持久化需 `data/` 為持久磁碟（同 SQLite）；Railway 未掛 volume 時，重新部署會歸零退回預設。
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

logger = logging.getLogger(__name__)

_lock = threading.Lock()

# 允許注入的白名單（防止亂塞、也讓「開放範圍」明確可控）
_THRESHOLD_KEYS = {"max_off_topic_count", "max_unclear", "max_daily_messages"}
_PROMPT_KEYS = {"cs_response_system"}
_MESSAGE_KEYS = {"handoff_message"}  # 轉真人安撫話（＝HiSupport 後台「期待管理訊息」推來的字）
_MAX_PROMPT_CHARS = 8000  # 注入人設長度上限，防有人塞超長 prompt 灌爆每輪成本
_MAX_MESSAGE_CHARS = 2000  # 注入訊息長度上限

_overlay: dict = {"prompts": {}, "messages": {}, "thresholds": {}}


def _path() -> Path:
    return Path(os.getenv("RUNTIME_CONFIG_PATH", "data/runtime_config.json"))


def _sanitize(data: dict) -> dict:
    """只留白名單內、型別正確的值；其餘一律丟棄（不報錯、靜默過濾）。"""
    prompts: dict = {}
    messages: dict = {}
    thresholds: dict = {}
    if isinstance(data, dict):
        for k, v in (data.get("prompts") or {}).items():
            if k in _PROMPT_KEYS and isinstance(v, str) and v.strip():
                prompts[k] = v[:_MAX_PROMPT_CHARS]
        for k, v in (data.get("messages") or {}).items():
            if k in _MESSAGE_KEYS and isinstance(v, str) and v.strip():
                messages[k] = v[:_MAX_MESSAGE_CHARS]
        for k, v in (data.get("thresholds") or {}).items():
            if k not in _THRESHOLD_KEYS:
                continue
            try:
                iv = int(v)
            except (TypeError, ValueError, OverflowError):
                continue
            if iv > 0:
                thresholds[k] = iv
    return {"prompts": prompts, "messages": messages, "thresholds": thresholds}


def init() -> None:
    """app 啟動時呼叫：把磁碟上已存的設定載回記憶體。"""
    global _overlay
    p = _path()
    if not p.exists():
        return
    try:
        _overlay = _sanitize(json.loads(p.read_text(encoding="utf-8")))
        logger.info("runtime_config 已載入：%s", p)
    except Exception as e:  # 壞檔不讓服務掛，退回空設定（＝現況）
        logger.warning("runtime_config 載入失敗，改用空設定：%s", e)
        _overlay = {"prompts": {}, "messages": {}, "thresholds": {}}


def get_overlay() -> dict:
    return {
        "prompts": dict(_overlay["prompts"]),
        "messages": dict(_overlay.get("messages", {})),
        "thresholds": dict(_overlay["thresholds"]),
    }


def set_overlay(data: dict, *, merge: bool = True) -> dict:
    """設定（驗證 → 套用 → 持久化）。merge=True 則只覆蓋有給的鍵、其餘維持現況。"""
    global _overlay
    clean = _sanitize(data)
    with _lock:
        if merge:
            merged = get_overlay()
            merged["prompts"].update(clean["prompts"])
            merged["messages"].update(clean["messages"])
            merged["thresholds"].update(clean["thresholds"])
            _overlay = merged
        else:
            _overlay = clean
        p = _path()
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(_overlay, ensure_ascii=False, indent=2), encoding="utf-8")
    return get_overlay()


def get_prompt_override(name: str) -> str | None:
    """有注入覆寫回覆寫字串，否則 None（呼叫端就用檔案預設）。"""
    return _overlay["prompts"].get(name)


def get_message(name: str, default: str) -> str:
    """有注入覆寫回覆寫字串，否則回傳呼叫端給的預設（＝現況）。"""
    v = _overlay.get("messages", {}).get(name)
    return v if isinstance(v, str) and v.strip() else default


def get_threshold(name: str, default: int) -> int:
    """有注入覆寫回覆寫值，否則回傳呼叫端給的預設（＝現況）。"""
    v = _overlay["thresholds"].get(name)
    return v if isinstance(v, int) else default


def reset() -> None:
    """測試用：清空記憶體 overlay（不動磁碟）。"""
    global _overlay
    _overlay = {"prompts": {}, "messages": {}, "thresholds": {}}
