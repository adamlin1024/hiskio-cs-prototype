# -*- coding: utf-8 -*-
"""分診考卷跑分器(規格 §11 驗收第 1 項;打真 API,約 US$0.1/次,勿進 CI 常跑)。

用法:  .venv/Scripts/python.exe scripts/run_routing_exam.py [報告輸出路徑]

- 讀 tests/routing_exam.json(30 題:單句 26+多輪 3+注入 1),逐題呼叫分診腦(真模型)。
- 支援 history 欄(多輪脈絡)、expect_user_satisfied 欄(「好吧」誤結案紅線)。
- 另跑「寫作查核題」(規格 §8):組合包退費由寫手真寫一次,
  回覆必須含整筆退費規則、且不得出現文章外的「依付款方式而不同」型加料。
- 通過標準:總分 ≥96%(30 題 ≥29)且紅線題(28/30/寫作查核)零失誤。
  未達標 exit code 1。
"""
from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path

# Windows 主控台預設 cp950,印不出 ✅ → 一律改 UTF-8
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from core.state import new_state  # noqa: E402
from nodes import brain, cs_response, kb_indexer  # noqa: E402

EXAM_PATH = ROOT / "tests" / "routing_exam.json"
RED_LINE_IDS = {28, 30}  # 好吧不結案、注入不照做

MEMBER = {"is_logged_in": True, "user_id": "exam", "user_email": "exam@test",
          "user_name": "考官", "purchase_history": []}


def _state_for(item: dict) -> dict:
    s = new_state(user_info=MEMBER)
    for role, content in item.get("history", []):
        s["chat_history"].append({"role": role, "content": content, "timestamp": ""})
        if role == "assistant":
            s["turn_count"] += 1
    return s


def _score(item: dict, decision: dict) -> tuple[bool, str]:
    action = decision["recommended_action"]
    # continue_intent 帶有效編號=等效回答:orchestrator 會照編號走 FAQ/KB 路徑,
    # 用戶實際得到正確答案 → 視同 answer_with_*(行為等價,非放水)。
    if (
        action == "continue_intent"
        and (decision.get("faq_id") or decision.get("kb_article_ids"))
        and ("answer_with_faq" in item["allowed"] or "answer_with_kb" in item["allowed"])
    ):
        action = "answer_with_kb" if decision.get("kb_article_ids") else "answer_with_faq"
    if action not in item["allowed"]:
        return False, f"action={action}(期望 {item['allowed']})"
    if item.get("req_ids") and isinstance(action, str) and (
        action.startswith("answer") or action == "continue_intent"
    ):
        got = set(decision.get("kb_article_ids") or [])
        if decision.get("faq_id"):
            got.add(decision["faq_id"])
        if not (got & set(item["req_ids"])):
            return False, f"編號不符 got={sorted(got)}(期望 {item['req_ids']} 之一)"
    if "expect_user_satisfied" in item:
        if decision.get("user_satisfied") != item["expect_user_satisfied"]:
            return False, f"user_satisfied={decision.get('user_satisfied')}(期望 {item['expect_user_satisfied']})"
    return True, ""


def run() -> int:
    exam = json.loads(EXAM_PATH.read_text(encoding="utf-8"))
    out = io.StringIO()
    ok_n = 0
    red_fail = []
    lats = []

    for item in exam:
        state = _state_for(item)
        t0 = time.time()
        decision = brain.decide(state, item["msg"])
        dt = time.time() - t0
        lats.append(dt)
        ok, why = _score(item, decision)
        ok_n += ok
        mark = "✅" if ok else "❌"
        out.write(f"{mark} Q{item['id']:>2} {dt:4.1f}s 「{item['msg'][:30]}」"
                  f"→ {decision['recommended_action']}"
                  f" faq={decision.get('faq_id')} kb={decision.get('kb_article_ids')}"
                  f"{' | ' + why if why else ''}\n")
        if not ok and item["id"] in RED_LINE_IDS:
            red_fail.append(item["id"])

    # ── 寫作查核題(紅線):組合包退費,不得加料 ──
    out.write("\n── 寫作查核(kb_004 組合包退費)──\n")
    state = new_state(user_info=MEMBER)
    art = kb_indexer.load_kb_article("kb_004")
    t0 = time.time()
    reply = cs_response.respond(state, [art] if art else [], "我上禮拜買的組合包,其中一堂想退,可以只退那一堂嗎?")
    wdt = time.time() - t0
    import re
    has_rule = "整筆" in reply
    fabricated = bool(re.search(r"付款方式.{0,8}(不同|而異|有所)", reply))
    has_markdown = "**" in reply  # 泡泡不渲染,粗體=雜訊(guard 規則)
    writing_ok = has_rule and not fabricated and not has_markdown
    out.write(f"{'✅' if writing_ok else '❌'} {wdt:.1f}s 含整筆規則={has_rule} 加料={fabricated} 粗體殘留={has_markdown}\n")
    out.write(f"回覆全文:{reply}\n")
    if not writing_ok:
        red_fail.append("writing")

    total = len(exam)
    score = ok_n / total * 100
    avg = sum(lats) / len(lats)
    passed = (ok_n >= total - 1) and not red_fail
    out.write(
        f"\n===== 總結 =====\n"
        f"分診 {ok_n}/{total}({score:.0f}%)|平均 {avg:.1f}s/題|最慢 {max(lats):.1f}s\n"
        f"紅線題失誤:{red_fail or '無'}\n"
        f"驗收:{'✅ 通過(≥96% 且紅線零失誤)' if passed else '❌ 未達標'}\n"
    )

    report = out.getvalue()
    dest = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data" / "_exam_report_latest.txt"
    dest.write_text(report, encoding="utf-8")
    print(report[-600:])
    print(f"完整報告:{dest}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(run())
