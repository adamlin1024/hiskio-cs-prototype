"""Token 消耗 benchmark 腳本。

從 data/test_cases/*.json 讀對話情境，跑完印出 token 統計與成本。
跨版本（v6 / v7）用同一份 scenario 才能對比。

使用方法（本機 uvicorn 已啟動）：
    python scripts/benchmark_tokens.py                     # 預設 benchmark_v1
    python scripts/benchmark_tokens.py benchmark_v1        # 指定名稱
    python scripts/benchmark_tokens.py path/to/case.json   # 指定路徑
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

BASE_URL = "http://127.0.0.1:8765"
TEST_CASES_DIR = Path(__file__).resolve().parent.parent / "data" / "test_cases"


def load_scenario(arg: str | None) -> dict:
    """從 data/test_cases/ 讀 JSON。預設 benchmark_v1。"""
    name = arg or "benchmark_v1"
    candidates = [
        Path(name) if Path(name).is_file() else None,
        TEST_CASES_DIR / f"{name}.json",
        TEST_CASES_DIR / name,
    ]
    for p in candidates:
        if p is not None and p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    raise FileNotFoundError(f"找不到 scenario：{name}（試過 {[str(c) for c in candidates if c]}）")


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
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    scenario_data = load_scenario(arg)
    turns = scenario_data["turns"]
    user_type = scenario_data.get("user_type", {"is_logged_in": True, "user_id": "user_001"})

    print("=== Token Benchmark ===")
    print(f"Base URL: {BASE_URL}")
    print(f"Scenario: {scenario_data.get('name', '?')}（{len(turns)} turns）")
    if scenario_data.get("description"):
        print(f"Description: {scenario_data['description']}")
    print()

    # 1. 重置 token 統計
    post("/api/admin/usage/reset", {})
    print("✓ usage 已重置")

    # 2. 建 session（依 scenario 指定的身分）
    s = post("/api/session/new", user_type)
    sid = s["session_id"]
    print(f"✓ 建立 session: {sid[:8]}（{'會員' if user_type.get('is_logged_in') else '訪客'}）\n")

    # 3. 跑全部輪
    t_start = time.time()
    for i, msg in enumerate(turns, 1):
        print(f"[{i:2d}] 用戶：{msg}")
        try:
            r = post("/api/chat", {"session_id": sid, "message": msg})
            print(f"     AI ({r['response_type']:18s}): {r['ai_response'][:80]}")
        except Exception as e:
            print(f"     ❌ 失敗：{e}")
            return 1
        print()
    t_total = time.time() - t_start
    print(f"\n總耗時：{t_total:.1f} 秒（平均 {t_total/len(turns):.2f} 秒/輪）\n")

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
    print(f"\n平均每輪成本：${summary['total_usd']/len(turns):.5f} USD")
    print(f"平均每輪 LLM 次數：{summary['calls']/len(turns):.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
