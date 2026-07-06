# -*- coding: utf-8 -*-
"""多重意圖多輪實測(Adam 2026-07-06 指定;打真 API 走 8765 伺服器)。

驗三件事:①兩問題同時來 → 直接答優先的、另一個記進待辦
②用戶說 OK/謝謝 → 已答的正確結案 + 主動引導到下一個待辦
③全部問完能走完;外加「知識庫沒資料 → 轉真人原因=no_kb_match」精確標記。
劇本取材自真實歷史資料的常見組合(退費+發票/影片+抵用券/沒資料的服務詢問)。
"""
from __future__ import annotations

import io
import json
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

HIBOT = "http://localhost:8765"
OUT = io.StringIO()


def log(s=""):
    OUT.write(s + "\n")


def post(path, payload, timeout=120):
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(HIBOT + path, data=data,
                                 headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def new_session():
    s = post("/api/session/new", {"is_logged_in": True, "user_id": "990911",
                                  "user_email": "multi@test", "user_name": "小華",
                                  "purchase_history": ["Vibe Coding"]})
    return s["session_id"]


def turn(sid, msg):
    t0 = time.time()
    r = post("/api/chat", {"session_id": sid, "message": msg})
    dt = time.time() - t0
    st = r["state"]
    intents = [
        f"{i['text']}({i['status']})" for i in st["intent_state"].get("intent_log", [])
    ]
    log(f"👤「{msg}」")
    log(f"🤖({dt:.1f}s|{r['response_type']}) {r['ai_response']}")
    log(f"   待辦清單:{intents or '(空)'} | handoff={r['handoff']['requested']}"
        f" reason={st['ticket_state'].get('handoff_reason')}")
    log("")
    return r, st


log("═══ 劇本一:退費+發票統編(真實高頻組合)═══")
sid = new_session()
turn(sid, "我想申請退費,另外我發票統編也打錯了")
turn(sid, "了解,謝謝,退費的部分我知道了")     # 明確滿意 → 應結案退費+引導發票
turn(sid, "對,統編打錯了要怎麼改?")
turn(sid, "好的謝謝,都解決了")

log("═══ 劇本二:影片+抵用券,用戶指定優先(取材考卷 Q26)═══")
sid = new_session()
turn(sid, "影片看不了,而且我抵用券也不能用,先幫我處理影片")
turn(sid, "可以看了!那抵用券的問題呢?")

log("═══ 劇本三:知識庫沒資料 → 轉真人精確標記 ═══")
sid = new_session()
r, st = turn(sid, "你們有提供 Java 認證考試的代報名服務嗎?")
r, st = turn(sid, "好")
log(f"交接訊號:requested={r['handoff']['requested']} reason={r['handoff']['reason']}")
log(f"交接摘要:\n{r['handoff']['summary']}")

dest = ROOT / "data" / "_multiintent_report_latest.txt"
dest.write_text(OUT.getvalue(), encoding="utf-8")
print(OUT.getvalue())
print(f"報告:{dest}")
