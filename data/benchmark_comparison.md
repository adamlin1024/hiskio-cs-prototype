# Token Benchmark 對比

跑日：2026-05-04
情境：固定 10 輪對話
腳本：`scripts/benchmark_tokens.py`

## 四種版本對比

| 版本 | 成本 USD | NTD | 對 baseline | 行為品質 |
|---|---|---|---|---|
| **Baseline**（Sonnet 主管，無 cache）| $0.169 | NT$5.41 | — | ✅ 全對 |
| Sonnet 主管 + cache | $0.119 | NT$3.81 | -30% | ✅ 全對 |
| **Sonnet 主管 + cs_response cache + cache（目前版本）** | **$0.098** | **NT$3.15** | **-42%** | ✅ 全對 |
| Haiku 主管 + cache | $0.066 | NT$2.10 | -61% | ⚠️ 「謝謝」「再見」會誤判離題 |

## 細項

### 目前版本（Sonnet 主管 + 雙 cache）

| Model | Calls | input | output | cache_read | cache_create | Cost |
|---|---|---|---|---|---|---|
| claude-sonnet-4-6 | 11 | 12,808 | 2,256 | 22,016 | 2,752 | $0.089 |
| claude-haiku-4-5 | 11 | 5,247 | 1,234 | 0 | 0 | $0.009 |
| **總計** | **22** | | | | | **$0.098** |

### Baseline

| Model | Calls | input | output | Cost |
|---|---|---|---|---|
| claude-sonnet-4-6 | 13 | 41,292 | 2,452 | $0.161 |
| claude-haiku-4-5 | 11 | 4,928 | 1,128 | $0.008 |
| **總計** | **24** | | | **$0.169** |

## 注意事項

1. **單次 benchmark 變異約 ±10%**：每次 LLM 回應長度不同，路徑也可能不同。多跑幾次平均才準確。
2. **cs_response 的 cache 命中可能 marginal**：cs_response system prompt 約 1000 tokens，剛好在 cache minimum 門檻附近，不一定每次都會命中。即便 marginal cache 也省了一點 input token。
3. **行為品質完全等同 Sonnet**：用 cache 純粹影響計費、不影響 prompt 內容，所以 LLM 看到的 prompt 跟原本一字不差。

## 投資報酬率

| 改動 | 工作量 | 省 token | ROI |
|---|---|---|---|
| manager system prompt cache | 5 分鐘 | -30% | ⭐⭐⭐⭐⭐ |
| cs_response system prompt cache | 30 分鐘 | -12%（額外） | ⭐⭐⭐⭐ |
| 兩段式主管（Haiku 粗判 + Sonnet 細判）| 半天 | 預估 -20% | ⭐⭐⭐ |
| 換 Gemini 2.5 Pro cs_response | 3-4 天 | 預估 -20% | ⭐⭐ |

## 結論

**目前版本（Sonnet 主管 + 雙 cache）已經是最佳 ROI 的優化**。再往下省需要動架構（兩段式主管或換 provider），工程成本明顯升高、行為驗證範圍變大。

10 輪對話 NT$3.15，每輪約 NT$0.32，**已經在合理範圍**。

100 輪對話 = NT$31.5（約 1 美金），1 萬輪對話 = NT$3,150（約 100 美金），可以接受。
