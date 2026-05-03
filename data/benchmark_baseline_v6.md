# Token Benchmark Baseline（v6 主管模式 / 無 caching）

跑日：2026-05-04
腳本：`scripts/benchmark_tokens.py`
情境：固定 10 輪對話（greeting / FAQ / 多重意圖 / 指稱詞 / 解決確認 / 離題）

## 結果摘要

| 項目 | 數值 |
|---|---|
| 對話輪數 | 10 |
| LLM 呼叫總次數 | 24（平均每輪 2.4 次） |
| 總耗時 | 61 秒 |
| **總成本** | **$0.169 USD ≈ NT$5.41** |
| 平均每輪成本 | $0.017 USD ≈ NT$0.54 |

## 模型細項

| 模型 | 呼叫次數 | input tokens | output tokens | 成本（USD） |
|---|---|---|---|---|
| claude-sonnet-4-6 | 13 | 41,292 | 2,452 | $0.16066 |
| claude-haiku-4-5-20251001 | 11 | 4,928 | 1,128 | $0.00845 |

## 觀察

- **Sonnet input tokens（41,292）是主要成本**（95%）
- 主管 + cs_response 兩個 Sonnet 節點各自重複塞固定 prompt（FAQ list、KB 索引、規則）
- 加 prompt caching 後預期能省 50-70%

## 場景對話順序

```
1.  你好                          → greeting
2.  我影片不能看                   → faq
3.  我有兩個問題：發票跟付款        → rag (intent_selection)
4.  1                            → rag (選 1 處理)
5.  下一個問題呢                   → intent_selection
6.  我想退費                      → rag
7.  退費條件是什麼                 → rag
8.  OK 了 謝謝                    → greeting
9.  你今天午餐吃什麼               → off_topic
10. 再見                         → greeting
```

## 加 caching 後預期

manager prompt 中固定部分約 2000 tokens / 次，13 次 manager call 共 26,000 tokens。
caching 後：
- 第 1 次寫快取：2,000 × 1.25 = 2,500 等效 tokens
- 後 12 次讀快取：12 × 2,000 × 0.1 = 2,400 等效 tokens
- 共 4,900 等效 tokens（省 21,100 tokens）

預估降幅：總成本從 $0.169 → 約 $0.10-0.12（省 30-45%）。

待 caching 上線後重跑同情境驗證。
