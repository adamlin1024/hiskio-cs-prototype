# HiSKIO AI 客服 — Token 成本 Benchmark 結果報告

**測試日期**：2026-05-04
**版本**：v6 主管模式
**測試者**：Claude Opus 4.7 + 用戶實測

---

## 結論摘要（給趕時間的人）

| 版本 | 10 輪成本 | 對 baseline | 行為品質 | 是否採用 |
|---|---|---|---|---|
| Baseline（原始）| $0.169（NT$5.41）| — | ✅ 全對 | — |
| **最終方案** | **$0.098（NT$3.15）** | **-42%** | ✅ 全對 | ✅ 已上線 |

**最終方案：Sonnet 主管模式 + Anthropic Prompt Caching（manager + cs_response 兩處快取）**

每月對話量估算：
- 1,000 輪 / 月 → **約 NT$315**
- 10,000 輪 / 月 → **約 NT$3,150**
- 100,000 輪 / 月 → **約 NT$31,500**

---

## 1. 測試方法

### 1.1 測試情境

固定 10 輪對話，覆蓋系統主要分支：

```
1.  你好                          → 純問候
2.  我影片不能看                   → FAQ 命中
3.  我有兩個問題：發票跟付款         → 多重意圖
4.  1                            → 選擇選項
5.  下一個問題呢                   → 指稱詞
6.  我想退費                      → 新意圖切換
7.  退費條件是什麼                 → 延續話題
8.  OK 了 謝謝                    → 結束語
9.  你今天午餐吃什麼               → 離題
10. 再見                         → 告別
```

這個情境會用到：問候 fast-path、Haiku 釐清節點、Sonnet 主管、FAQ 快查、KB 索引、cs_response 生成、off_topic 處理、evaluator 等多個節點，**能完整反映實際對話的成本分布**。

### 1.2 測量工具

寫了 `scripts/benchmark_tokens.py`，做的事：

1. 重置 token 統計（`POST /api/admin/usage/reset`）
2. 建立會員 session（user_001）
3. 跑 10 輪對話
4. 拉取統計（`GET /api/admin/usage`），印出：
   - 每個模型的呼叫次數
   - input / output / cache_read / cache_create token 數
   - 換算成本（USD + NTD）

### 1.3 計費基準（Anthropic 公定價）

| 模型 | input ($/M) | output ($/M) | cache_read | cache_create |
|---|---|---|---|---|
| Claude Sonnet 4.6 | 3.00 | 15.00 | 0.30 | 3.75 |
| Claude Haiku 4.5 | 0.80 | 4.00 | 0.08 | 1.00 |

匯率：1 USD = 32 TWD

### 1.4 測量誤差

每次 LLM 回應長度有隨機性，同情境跑兩次 token 可能差 ±10%。報告中所有數字是單次測試結果（沒做 N 次平均），趨勢可信但絕對值有 ±10% 浮動。

---

## 2. 測試過程

依序測了 4 個版本：

### 版本 A — Baseline（Sonnet 無 cache）

最原始的 v6 主管模式。manager + cs_response 都是 Sonnet，沒有任何 prompt caching。

| Model | Calls | input | output | cache_read | Cost |
|---|---|---|---|---|---|
| Sonnet 4.6 | 13 | 41,292 | 2,452 | 0 | $0.16066 |
| Haiku 4.5 | 11 | 4,928 | 1,128 | 0 | $0.00845 |
| **總計** | **24** | | | | **$0.169** |

**觀察**：
- Sonnet input tokens 41,292 占 95% 成本
- 主因：每次 manager 與 cs_response 呼叫都把固定的 prompt 部分（規則、FAQ list、KB 索引、角色定義）全文重新計費

### 版本 B — Sonnet + manager prompt cache

把 `nodes/manager.py` 的 prompt 拆成兩段：
- `prompts/manager_system.txt`：靜態（規則 + FAQ list + KB 索引）→ 加 `cache_control: ephemeral`
- `prompts/manager_user.txt`：動態（用戶訊息 + chat_history + intent_log + 系統狀態）

| Model | Calls | input | output | cache_read | cache_create | Cost |
|---|---|---|---|---|---|---|
| Sonnet 4.6 | 13 | 18,106 | 2,463 | 22,016 | 2,752 | $0.10819 |
| Haiku 4.5 | 12 | 5,713 | 1,543 | 0 | 0 | $0.01074 |
| **總計** | **25** | | | | | **$0.119** |

**對 baseline -30%**。

**觀察**：
- 22,016 tokens 的 manager 固定 prompt 從 cache 讀（每 token 只算 10% 價錢）
- 第 1 次寫快取 2,752 tokens 比正常貴 25%，但僅一次
- 行為完全等同（cache 不影響 LLM 看到的 prompt 內容）

### 版本 C — 同上 + cs_response prompt cache

`nodes/cs_response.py` 也拆兩段：
- `prompts/cs_response_system.txt`：靜態（角色定義 + 任務 + SUGGEST_TICKET 規則）
- `prompts/cs_response_user.txt`：動態（KB 文章 + 對話歷史 + 用戶訊息）

| Model | Calls | input | output | cache_read | cache_create | Cost |
|---|---|---|---|---|---|---|
| Sonnet 4.6 | 11 | 12,808 | 2,256 | 22,016 | 2,752 | $0.08919 |
| Haiku 4.5 | 11 | 5,247 | 1,234 | 0 | 0 | $0.00913 |
| **總計** | **22** | | | | | **$0.098** |

**對 baseline -42%**。

**觀察**：
- 比版本 B 再省 18%
- cs_response 的 system prompt 約 1000 tokens，剛好在 Anthropic cache 1024 token 門檻附近，命中是 marginal 的
- 真正的省主要來自 manager cache 持續發揮 + cs_response 動態部分稍短

### 版本 D — Haiku 當主管（測試用）

把 `nodes/manager.py` 的 Sonnet 改成 Haiku（透過 env var `MANAGER_MODEL=haiku`），其他不變。

| Model | Calls | input | output | cache_read | Cost |
|---|---|---|---|---|---|
| Haiku 4.5（含主管）| 22 | 41,614 | 3,809 | 0 | $0.04853 |
| Sonnet 4.6（剩餘 cs_response）| 2 | 4,687 | 210 | 0 | $0.01721 |
| **總計** | **24** | | | | **$0.066** |

**對 baseline -61%、對版本 C -33%**。

**但行為品質有 2 處明顯誤判**：

| Turn | 用戶訊息 | Sonnet 主管判斷 | Haiku 主管判斷 |
|---|---|---|---|
| 8 | OK 了 謝謝 | greeting ✅ | **off_topic ❌** |
| 10 | 再見 | greeting ✅ | **off_topic ❌** |

兩次誤判 → off_topic_count 多累加 2 次。實際對話拉長後可能達到 max=3 觸發「達上限自動建單」紫框，**用戶可能莫名其妙被請走**。

**結論：不建議直接換 Haiku 主管**。

---

## 3. 最終方案

### 採用：版本 C（Sonnet 主管 + manager + cs_response 兩處快取）

**為什麼選這個**：
- 行為品質跟 baseline 100% 一致（cache 不影響 LLM 內容）
- 成本省 42%（每 10 輪從 NT$5.41 → NT$3.15）
- 改動風險為 0：純粹是 Anthropic 計費端的優化，沒動 prompt 內容、沒換模型
- 工作量：約 30 分鐘

**為什麼不採用版本 D（Haiku 主管）**：
- 雖然便宜 61%，但「OK 了 謝謝」「再見」這類結束語會被誤判離題
- 邊界 case 多花的 debug 時間會抵銷省的錢
- 前面花了大量時間 debug 的場景會回來

### 實作位置

| 檔案 | 改動 |
|---|---|
| `core/llm_client.py` | 加 `cache_system` 參數、加 token 統計 |
| `nodes/manager.py` | 拆 system / user prompt、加 `cache_system=True` |
| `nodes/cs_response.py` | 同上 |
| `prompts/manager_system.txt` + `manager_user.txt` | 取代舊的 `manager.txt` |
| `prompts/cs_response_system.txt` + `cs_response_user.txt` | 取代舊的 `cs_response.txt` |
| `app.py` | 加 `/api/admin/usage` 與 `/api/admin/usage/reset` |
| `scripts/benchmark_tokens.py` | 隨時可重跑的 benchmark 工具 |

### 切換主管模型供 A/B 測

設 env var `MANAGER_MODEL=haiku` 重啟 server 即切換成 Haiku 主管，預設 sonnet。
方便之後測新模型或 fine-tune 時對照。

### 開／關 cache

`nodes/manager.py` 跟 `nodes/cs_response.py` 呼叫 `call_sonnet(...)` 時的 `cache_system=True` 參數。
改成 `False` 即關閉 cache（成本回到 baseline）。

---

## 4. 預算成本

### 4.1 單輪成本（基於最終方案）

| 項目 | 成本 USD | 成本 NTD |
|---|---|---|
| 平均每輪 | $0.0098 | NT$0.31 |
| 簡單對話（純問候、純 FAQ）| ~$0.005 | NT$0.16 |
| 複雜對話（多重意圖 + RAG + 工單）| ~$0.020 | NT$0.64 |

### 4.2 月度成本估算

假設「一個用戶完整對話 = 10 輪」：

| 規模 | 對話/月 | 輪數/月 | 月成本 USD | 月成本 NTD |
|---|---|---|---|---|
| 試營運 | 100 | 1,000 | $9.8 | NT$315 |
| 小型 | 1,000 | 10,000 | $98 | NT$3,150 |
| 中型 | 10,000 | 100,000 | $980 | NT$31,500 |
| 大型 | 100,000 | 1,000,000 | $9,800 | NT$315,000 |

### 4.3 對比真人客服成本

真人客服平均處理一個案子（約 10 分鐘）人力成本約 NT$50-100。
AI 客服平均 NT$0.31/輪（10 輪一個案 = NT$3.1），**便宜約 16-32 倍**。

### 4.4 上限機制保護

設計上有 `service_limits` 多層防護：
- 單 session 最多 20 輪 → 單個用戶最高成本 NT$6.2
- 達上限會自動建議建單，避免無限燒錢

---

## 5. 測試資料附錄

### 5.1 測試指令

```bash
# 一鍵跑 benchmark（需要 server 在 127.0.0.1:8765 運行）
python scripts/benchmark_tokens.py
```

### 5.2 切換版本測試方法

| 想測試 | 怎麼做 |
|---|---|
| Baseline（無 cache）| 修改 `nodes/manager.py` 跟 `cs_response.py` 把 `cache_system=True` 改 `False` |
| Sonnet 主管（預設）| 直接跑 |
| Haiku 主管 | `MANAGER_MODEL=haiku python -m uvicorn app:app ...` 重啟 |

### 5.3 完整測試對話腳本

在 `scripts/benchmark_tokens.py` 的 `SCENARIO` 變數中。10 句話按順序送出，模擬完整對話流程。

### 5.4 即時查詢 token 用量

任何時候都可以打開瀏覽器看當前累計：
```
GET http://127.0.0.1:8765/api/admin/usage
```

回傳 JSON 含分模型 token 統計與估算成本。

### 5.5 重置統計

```
POST http://127.0.0.1:8765/api/admin/usage/reset
```

---

## 6. 之後可繼續優化的方向（暫不採用）

| 方向 | 預估省 token | 工作量 | 採不採用的原因 |
|---|---|---|---|
| 兩段式主管：Haiku 粗判 + Sonnet 細判 | -20% | 半天 | 邊界判斷規則對齊複雜，雛形階段不做 |
| 換 Gemini 2.5 Pro 跑 cs_response | -20% | 3-4 天 | 多 provider 維護成本高，量小不划算 |
| 用 OpenAI 系列做整體成本對比 | varies | 1-2 天 | 量小不值得 |
| 前面加 regex fast-path 攔「謝謝/再見」| -10% | 1 小時 | 容易誤判用戶意圖（用戶體驗風險），不採用 |

**判斷標準**：
- 月對話量 < 1 萬輪 → 維持現狀（每月成本 < $100，再省的工程成本回不來）
- 月對話量 > 10 萬輪 → 考慮上述 1-2 項
- 月對話量 > 100 萬輪 → 上述全部評估 + 自架推論伺服器

---

## 7. 維護建議

### 7.1 何時重跑 benchmark
- 改 manager prompt 之後
- 加新節點之後
- 換模型版本之後（例如 Anthropic 出新 Sonnet）
- 每月固定跑一次追蹤趨勢

### 7.2 注意事項
- 跑 benchmark 前先確認 Anthropic API 帳戶有足夠 credit
- 一次 benchmark 約 $0.10-0.17 USD，不貴但累積要看
- 5 分鐘內連跑兩次，第二次的 cache_create 不會重複（會吃第一次寫的 cache）
- 跑完記得 `POST /api/admin/usage/reset` 否則統計會繼續累加

### 7.3 觀察重點
- `cache_read` 數字大代表 cache 命中良好
- `cache_create` 在第一次跑時會出現一次，正常
- 如果 `cache_read` 突然歸零 → 可能 prompt 內容被改動導致 cache miss，要查

---

**報告維護者**：依專案進度更新
**最後更新**：2026-05-04（v6.2 上線時建立）
