"""Token 消耗 benchmark 腳本。

跑一個固定的 10 輪對話情境，最後印出 token 統計與估算成本。
測試 prompt caching 之前 / 之後可以用同一個情境跑兩次比對。

使用方法（本機 uvicorn 已啟動）：
    python scripts/benchmark_tokens.py
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request

BASE_URL = "http://127.0.0.1:8765"

# 固定 10 輪測試情境（覆蓋 greeting / FAQ / 多重意圖 / 指稱詞 / 離題）
SCENARIO = [
    "你好",
    "我影片不能看",
    "我有兩個問題：發票跟付款",
    "1",
    "下一個問題呢",
    "我想退費",
    "退費條件是什麼",
    "OK 了 謝謝",
    "你今天午餐吃什麼",
    "再見",
]


def post(path: str, body: dict) -> dict:
    req = urllib.request.Request(
        BASE_URL + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
    )
    return json.loads(urllib.request.urlopen(req).read().decode("utf-8"))


def get(path: str) -> dict:
    return json.loads(urllib.request.urlopen(BASE_URL + path).read().decode("utf-8"))


def main() -> int:
    print("=== Token Benchmark ===")
    print(f"Base URL: {BASE_URL}")
    print(f"Scenario: {len(SCENARIO)} turns\n")

    # 1. 重置 token 統計
    post("/api/admin/usage/reset", {})
    print("✓ usage 已重置")

    # 2. 建會員 session
    s = post("/api/session/new", {"is_logged_in": True, "user_id": "user_001"})
    sid = s["session_id"]
    print(f"✓ 建立 session: {sid[:8]}\n")

    # 3. 跑 10 輪
    t_start = time.time()
    for i, msg in enumerate(SCENARIO, 1):
        print(f"[{i:2d}] 用戶：{msg}")
        try:
            r = post("/api/chat", {"session_id": sid, "message": msg})
            print(f"     AI ({r['response_type']:18s}): {r['ai_response'][:80]}")
        except Exception as e:
            print(f"     ❌ 失敗：{e}")
            return 1
        print()
    t_total = time.time() - t_start
    print(f"\n總耗時：{t_total:.1f} 秒（平均 {t_total/len(SCENARIO):.2f} 秒/輪）\n")

    # 4. 印統計
    summary = get("/api/admin/usage")
    print("=" * 60)
    print(f"總 LLM 呼叫次數：{summary['calls']}")
    print(f"總成本（USD）：${summary['total_usd']:.5f}")
    print(f"總成本（NTD，1USD=32）：NT${summary['total_usd'] * 32:.2f}")
    print()
    print("分模型統計：")
    for model, b in sorted(summary["by_model"].items()):
        print(f"  {model}")
        print(f"    呼叫次數: {b['calls']}")
        print(f"    input tokens: {b['input']:,}")
        print(f"    output tokens: {b['output']:,}")
        if b["cache_read"] > 0 or b["cache_create"] > 0:
            print(f"    cache_read: {b['cache_read']:,}")
            print(f"    cache_create: {b['cache_create']:,}")
        print(f"    成本: ${b['usd']:.5f}")
    print("=" * 60)
    print(f"\n平均每輪成本：${summary['total_usd']/len(SCENARIO):.5f} USD")
    print(f"平均每輪 LLM 次數：{summary['calls']/len(SCENARIO):.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
