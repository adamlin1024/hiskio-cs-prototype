"""主管節點（v6，Sonnet）。

每一輪用戶訊息進來都會呼叫這個節點，由 Sonnet 看「全貌」做一次完整決策：
- 用戶這句到底想做什麼
- 該執行哪個 action（greeting / answer_with_faq / answer_with_kb /
  acknowledge_uncertainty / acknowledge_out_of_scope / suggest_ticket / ...）
- 偵測到哪些新意圖（含 role: primary/secondary/context、in_scope）
- 是否對應 intent_log 中既有項目（指稱詞解析）

主管不生成最終回覆內容，只決定「要做什麼」。實際生成由 executor 節點負責
（faq_responder、cs_response、no_kb_handler、off_topic、ticket_handler 等）。
"""
from __future__ import annotations

import json
import logging
import os
from functools import lru_cache
from pathlib import Path

from core.llm_client import call_haiku, call_sonnet, load_prompt
from core.text_utils import extract_json_object, format_recent_history

logger = logging.getLogger(__name__)

_SYSTEM_TPL = load_prompt("manager_system")
_USER_TPL = load_prompt("manager_user")

_VALID_ACTIONS = {
    "greeting",
    "clarify",
    "answer_with_faq",
    "answer_with_kb",
    "acknowledge_out_of_scope",
    "acknowledge_uncertainty",
    "suggest_ticket",
    "list_pending_intents",
    "continue_intent",
    "force_escalation",
}
_VALID_ROLES = {"primary", "secondary", "context"}


@lru_cache(maxsize=1)
def _load_faq_summary() -> str:
    """FAQ 清單摘要：id + category + question_patterns（供主管參考）。"""
    path = Path(os.getenv("FAQ_PATH", "data/faq.json"))
    if not path.exists():
        return "（FAQ 清單為空）"
    faqs = json.loads(path.read_text(encoding="utf-8"))
    if not faqs:
        return "（FAQ 清單為空）"
    lines = []
    for item in faqs:
        patterns = "、".join(item.get("question_patterns", []))
        lines.append(f"- {item['id']}（{item.get('category', '')}）：{patterns}")
    return "\n".join(lines)


@lru_cache(maxsize=1)
def _load_kb_summary() -> str:
    """KB 索引摘要：id + 標題 + 摘要 + 常見問法（供主管挑選文章用）。"""
    path = Path(os.getenv("KB_INDEX_PATH", "data/kb_index.json"))
    if not path.exists():
        return "（KB 索引為空，請先跑 build_kb_index.py）"
    index = json.loads(path.read_text(encoding="utf-8"))
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


def _format_intent_log(intent_log: list[dict]) -> str:
    if not intent_log:
        return "（無）"
    lines = []
    for i, item in enumerate(intent_log):
        role = item.get("role", "primary")
        in_scope = item.get("in_scope", True)
        lines.append(
            f"  [{i}] {item['text']}"
            f"（status={item['status']}, role={role}, in_scope={in_scope}）"
        )
    return "\n".join(lines)


def decide(state: dict, user_message: str, hint: dict | None = None) -> dict:
    """呼叫主管做決策，回傳結構化 action JSON。

    v7：可選帶入 hint（流水線預判結果），主管會把 hint 當作參考但保有 override 權。
    若不帶 hint（v6 模式），會在 prompt 中註明「流水線未跑」。

    解析失敗時 fallback 為 acknowledge_uncertainty，避免讓系統卡住。
    """
    user = state["user_info"]
    sl = state["service_limits"]

    # static 部分（每次都一樣，可被 prompt cache 命中）
    system_prompt = _SYSTEM_TPL.format(
        faq_list=_load_faq_summary(),
        kb_index_list=_load_kb_summary(),
    )

    # 流水線 hint 序列化（v7 新增）
    if hint:
        from core.pipeline import format_hint_for_prompt
        hint_str = format_hint_for_prompt(hint)
    else:
        hint_str = "（流水線未跑，請依完整對話與意圖紀錄判斷）"

    # dynamic 部分（每輪變動）
    user_prompt = _USER_TPL.format(
        user_message=user_message,
        full_history=format_recent_history(state["chat_history"], turns=10, empty="（首次對話）"),
        intent_log_str=_format_intent_log(state["intent_state"].get("intent_log") or []),
        is_logged_in="是" if user["is_logged_in"] else "否",
        user_name=user.get("user_name") or "（訪客）",
        is_returning_customer="是" if user.get("purchase_history") else "否",
        phase=state["phase"],
        off_topic_count=sl["off_topic_count"],
        max_off_topic_count=sl["max_off_topic_count"],
        low_confidence_count=sl["low_confidence_count"],
        unresolved_count=sl["unresolved_count"],
        ticket_suggested=state["ticket_state"]["ticket_suggested"],
        user_declined_ticket=(state["ticket_state"]["user_decision"] == "declined"),
        pipeline_hint=hint_str,
    )

    # 可由 env var MANAGER_MODEL 切換 sonnet（預設）/ haiku，方便 benchmark 比對
    caller = call_haiku if os.getenv("MANAGER_MODEL", "sonnet").lower() == "haiku" else call_sonnet
    raw = caller(
        user_prompt,
        max_tokens=600,
        temperature=0.0,
        system=system_prompt,
        cache_system=True,
        fallback="",
    )
    parsed = extract_json_object(raw)

    fallback = {
        "user_intent_summary": "（解析失敗）",
        "is_in_scope": True,
        "matched_intent_in_log_index": None,
        "system_can_help": False,
        "recommended_action": "acknowledge_uncertainty",
        "faq_id": None,
        "kb_article_ids": [],
        "clarify_message": "抱歉，我不太確定您想問的內容，能否再多描述一下？",
        "reason_to_user": None,
        "new_intents_to_log": [],
        "target_intent_index": None,
        "reason": "manager 解析失敗 fallback",
    }

    if parsed is None:
        logger.warning("manager 解析失敗，fallback。raw=%r", raw[:300])
        return fallback

    action = parsed.get("recommended_action")
    if action not in _VALID_ACTIONS:
        logger.warning("manager 回傳未知 action=%r，fallback=acknowledge_uncertainty", action)
        return fallback

    # 清洗 new_intents_to_log
    raw_intents = parsed.get("new_intents_to_log", []) or []
    new_intents: list[dict] = []
    if isinstance(raw_intents, list):
        seen: set[str] = set()
        for it in raw_intents:
            if not isinstance(it, dict):
                continue
            text = str(it.get("text", "")).strip()
            role = str(it.get("role", "primary")).lower().strip()
            if role not in _VALID_ROLES:
                role = "primary"
            in_scope = bool(it.get("in_scope", True))
            if text and text not in seen:
                seen.add(text)
                new_intents.append({"text": text, "role": role, "in_scope": in_scope})

    # 清洗 indices
    intent_log = state["intent_state"].get("intent_log") or []
    matched_idx = parsed.get("matched_intent_in_log_index")
    if matched_idx is not None:
        try:
            matched_idx = int(matched_idx)
            if not (0 <= matched_idx < len(intent_log)):
                matched_idx = None
        except (TypeError, ValueError):
            matched_idx = None

    target_idx = parsed.get("target_intent_index")
    if target_idx is not None:
        try:
            target_idx = int(target_idx)
            if not (0 <= target_idx < len(intent_log)):
                target_idx = None
        except (TypeError, ValueError):
            target_idx = None

    kb_ids = parsed.get("kb_article_ids", []) or []
    if not isinstance(kb_ids, list):
        kb_ids = []
    kb_ids = [str(x) for x in kb_ids if isinstance(x, str)][:3]

    return {
        "user_intent_summary": parsed.get("user_intent_summary", ""),
        "is_in_scope": bool(parsed.get("is_in_scope", True)),
        "matched_intent_in_log_index": matched_idx,
        "system_can_help": bool(parsed.get("system_can_help", False)),
        "recommended_action": action,
        "faq_id": parsed.get("faq_id"),
        "kb_article_ids": kb_ids,
        "clarify_message": parsed.get("clarify_message"),
        "reason_to_user": parsed.get("reason_to_user"),
        "new_intents_to_log": new_intents,
        "target_intent_index": target_idx,
        "reason": parsed.get("reason", ""),
    }
