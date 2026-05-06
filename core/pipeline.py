"""v7 流水線預判：跑流水線 4 個 Haiku 節點，產出 hint dict 給主管參考。

v7 設計核心：
- 主管仍是統一決策者（從 12 個 action 選 1）
- 流水線預先比對 FAQ / KB，把結果濃縮成 hint
- 主管不用自己重做 FAQ / KB 比對 → system prompt 縮短、判斷更聚焦
- 流水線判錯時主管可 override（hint 只是「參考」不是「指令」）

不在以下情況呼叫流水線（省 token）：
- phase 攔截（等待選擇意圖 / 工單確認 / Email / 已結束）
- greeting fast-path（regex 攔下純問候）
"""
from __future__ import annotations

import logging

from nodes import entry_classifier, faq_matcher, intent_clarity, kb_indexer

logger = logging.getLogger(__name__)


def run(state: dict, user_message: str) -> dict:
    """跑流水線預判。回傳 hint dict 給 manager 當參考。

    結構：
    {
      "classification": "greeting | unclear | off_topic | customer_service",
      "detected_intents": [{"text", "role", "in_scope"}, ...],
      "needs_user_selection": bool,
      "referenced_intent_index": int | None,
      "faq_match": {"matched_id": str|None, "confidence": float},
      "kb_picks": [article_id, ...]   # 最多 3 篇
    }

    注意：classification != customer_service 時，後續節點會跳過、相關欄位填空，
    避免不必要的 Haiku 呼叫。
    """
    hint: dict = {
        "classification": "customer_service",
        "detected_intents": [],
        "needs_user_selection": False,
        "referenced_intent_index": None,
        "faq_match": {"matched_id": None, "confidence": 0.0},
        "kb_picks": [],
    }

    # 1. 入口分類（4 種）
    try:
        hint["classification"] = entry_classifier.classify(state, user_message)
    except Exception as e:
        logger.warning("entry_classifier 失敗，fallback=customer_service: %s", e)
        hint["classification"] = "customer_service"

    # 非 customer_service 直接回傳，後續省 token
    if hint["classification"] != "customer_service":
        logger.info("pipeline classification=%s，跳過後續節點", hint["classification"])
        return hint

    # 2. 意圖明確度（含 detected_intents / referenced_intent_index）
    try:
        clarity = intent_clarity.analyze(state, user_message)
        hint["detected_intents"] = clarity.get("detected_intents", [])
        hint["needs_user_selection"] = bool(clarity.get("needs_user_selection", False))
        hint["referenced_intent_index"] = clarity.get("referenced_intent_index")
    except Exception as e:
        logger.warning("intent_clarity 失敗，使用預設值: %s", e)

    # 3. FAQ 比對
    try:
        hint["faq_match"] = faq_matcher.match(user_message)
    except Exception as e:
        logger.warning("faq_matcher 失敗: %s", e)
        hint["faq_match"] = {"matched_id": None, "confidence": 0.0}

    # 4. KB 索引（FAQ 信心 < 0.7 才跑、避免重複）
    if hint["faq_match"].get("confidence", 0.0) < 0.7:
        try:
            hint["kb_picks"] = kb_indexer.index_articles(state, user_message)
        except Exception as e:
            logger.warning("kb_indexer 失敗: %s", e)
            hint["kb_picks"] = []

    logger.info(
        "pipeline hint: classification=%s, faq=%s/%.2f, kb=%s, needs_selection=%s",
        hint["classification"],
        hint["faq_match"].get("matched_id"),
        hint["faq_match"].get("confidence", 0.0),
        hint["kb_picks"],
        hint["needs_user_selection"],
    )
    return hint


def format_hint_for_prompt(hint: dict) -> str:
    """把 hint dict 序列化成給主管 prompt 看的多行文字。"""
    if not hint:
        return "（流水線未跑）"

    lines = [f"- 入口分類：{hint.get('classification', 'unknown')}"]

    detected = hint.get("detected_intents", [])
    if detected:
        intent_strs = [
            f"{d['text']}（role={d.get('role', 'primary')}, "
            f"in_scope={d.get('in_scope', True)}）"
            for d in detected
        ]
        lines.append(f"- 偵測意圖：{'、'.join(intent_strs)}")
    else:
        lines.append("- 偵測意圖：（無）")

    if hint.get("needs_user_selection"):
        lines.append("- 需用戶選擇意圖：是")

    ref_idx = hint.get("referenced_intent_index")
    if ref_idx is not None:
        lines.append(f"- 用戶指稱詞 → 對應 intent_log[{ref_idx}]")

    faq = hint.get("faq_match", {})
    if faq.get("matched_id"):
        lines.append(
            f"- FAQ 比對：命中 {faq['matched_id']}（信心 {faq.get('confidence', 0.0):.2f}）"
        )
    else:
        lines.append("- FAQ 比對：未命中")

    kb_picks = hint.get("kb_picks", [])
    if kb_picks:
        lines.append(f"- KB 文章建議：{', '.join(kb_picks)}")
    else:
        lines.append("- KB 文章建議：（無）")

    return "\n".join(lines)
