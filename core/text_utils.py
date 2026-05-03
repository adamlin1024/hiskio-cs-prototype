"""共用文字處理 helpers。

避免 JSON 解析、歷史格式化等邏輯散落在各節點重複實作。
"""
from __future__ import annotations

import json
import re
from typing import Any

_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)
_JSON_ARR_RE = re.compile(r"\[.*?\]", re.DOTALL)
_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)


def extract_json_object(raw: str) -> dict | None:
    """從 LLM 輸出中抽出 JSON 物件，容忍 markdown code fence 與外層文字。"""
    if not raw:
        return None
    cleaned = _FENCE_RE.sub("", raw.strip())
    try:
        result = json.loads(cleaned)
        return result if isinstance(result, dict) else None
    except json.JSONDecodeError:
        m = _JSON_OBJ_RE.search(cleaned)
        if not m:
            return None
        try:
            result = json.loads(m.group(0))
            return result if isinstance(result, dict) else None
        except json.JSONDecodeError:
            return None


def extract_json_array(raw: str) -> list | None:
    """從 LLM 輸出中抽出 JSON 陣列。"""
    if not raw:
        return None
    cleaned = _FENCE_RE.sub("", raw.strip())
    try:
        result = json.loads(cleaned)
        return result if isinstance(result, list) else None
    except json.JSONDecodeError:
        m = _JSON_ARR_RE.search(cleaned)
        if not m:
            return None
        try:
            result = json.loads(m.group(0))
            return result if isinstance(result, list) else None
        except json.JSONDecodeError:
            return None


def format_recent_history(history: list[dict[str, Any]], turns: int = 3, empty: str = "（無）") -> str:
    """把 chat_history 後段格式化成 prompt 可用的字串。turns=N 表示最後 N 輪（2N 則訊息）。"""
    if not history:
        return empty
    recent = history[-turns * 2:]
    if not recent:
        return empty
    lines = []
    for msg in recent:
        role = "用戶" if msg.get("role") == "user" else "AI"
        lines.append(f"{role}：{msg.get('content', '')}")
    return "\n".join(lines)
