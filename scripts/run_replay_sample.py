# -*- coding: utf-8 -*-
"""真實對話回放抽測(規格 §11-3;打真 API,約 US$0.03/次)。

從 data/conversations.json(Crisp 歷史)抽真實開場問句,丟給分診腦,
輸出「問句 → 決定」對照表供人工抽查(不自動判分——真實訊息沒有標準答案)。
"""
from __future__ import annotations

import io
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

from dotenv import load_dotenv

load_dotenv(ROOT / ".env")

from core.state import new_state  # noqa: E402
from nodes import brain  # noqa: E402

MEMBER = {"is_logged_in": True, "user_id": "replay", "user_email": "r@test",
          "user_name": "回放", "purchase_history": []}

# 抽樣策略:取「像會員會在泡泡問」的真實開場句(短於 120 字、非 EDM/合作信)
def _sample(n=10):
    convs = json.loads((ROOT / "data" / "conversations.json").read_text(encoding="utf-8"))
    picked, seen = [], set()
    for c in convs[::-1]:  # 從最近的開始
        for m in c.get("messages", []):
            if m.get("from") == "user" and m.get("type") == "text":
                t = (m.get("content") or "").strip().replace("\n", " ")
                if 4 <= len(t) <= 120 and "http" not in t and "**" not in t and t not in seen:
                    seen.add(t)
                    picked.append(t)
                break
        if len(picked) >= n:
            break
    return picked


def run():
    out = io.StringIO()
    for msg in _sample():
        s = new_state(user_info=MEMBER)
        t0 = time.time()
        d = brain.decide(s, msg)
        dt = time.time() - t0
        out.write(
            f"「{msg[:60]}」\n"
            f"  → {d['recommended_action']}"
            f" faq={d.get('faq_id')} kb={d.get('kb_article_ids')}"
            f" | {dt:.1f}s | {d.get('reason', '')}\n\n"
        )
    dest = ROOT / "data" / "_replay_report_latest.txt"
    dest.write_text(out.getvalue(), encoding="utf-8")
    print(out.getvalue())
    print(f"報告:{dest}")


if __name__ == "__main__":
    run()
