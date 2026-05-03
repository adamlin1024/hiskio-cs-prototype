# Chatbot 專案 — HiSKIO AI 客服對話系統(本地雛形)

## 專案說明
建立一個本地可跑的 AI 客服雛形，驗證「**FAQ 快查 + RAG + 工單建立**」三段式流程，並具備會員/訪客身分識別與服務限制能力。

- 完整規格書：`hiskio_cs_prototype_spec_v2.md`（v2，已取代 v1）
- 不做的事：真實會員系統、寄信、多用戶並發、雲端部署、串接 Crisp

## 技術選型
| 層 | 選擇 |
|---|---|
| 語言 | Python 3.11+ |
| 框架 | FastAPI |
| LLM | Anthropic Claude（**只用 Claude，不接其他服務**） |
| 主對話模型 | `claude-sonnet-4-6` |
| 輕量任務模型 | `claude-haiku-4-5-20251001`（路由、FAQ 比對、KB 索引、評估） |
| 儲存 | SQLite 單檔（sessions + tickets 兩張表） |
| 前端 | 純 HTML/JS 單檔，不用框架 |

**為什麼不用 embedding**：使用者只想用 Claude，KB 索引改用 Haiku 對「標題+摘要+關鍵問題」做語意比對。100 篇以下規模 token 效率夠用。

## 核心架構 — 八個節點
1. **router**（Haiku）— 意圖路由，A=客服相關 / C=離題
2. **faq_matcher**（Haiku）— FAQ 快查，信心 ≥ 0.7 命中
3. **faq_responder**（Haiku）— FAQ 回應，**核心步驟一字不漏照抄**，只潤飾開場/結尾
4. **kb_indexer**（Haiku）— RAG 第一階段，從 KB 索引選最多 3 篇
5. **cs_response**（Sonnet）— RAG 第二階段，根據 KB 全文回應；可標記 `[SUGGEST_TICKET]`
6. **off_topic**（Haiku）— 離題處理，第 1/2/3 次語氣漸強
7. **ticket_handler**（Haiku + Sonnet）— 工單流程小型狀態機（建議 → 確認 → 收 Email → 生成 → 結束）
8. **evaluator**（Haiku）— 背景評估，更新 issue_context 與 service_limits

流程編排在 `core/orchestrator.py`，特殊 phase（等待工單確認 / 等待 Email / 已結束）會跳過路由直接處理。

## 專案特定規則

### 開發規範（給 Claude Code）
- 用 Python type hints
- 每個節點函式都要有 docstring 說明輸入輸出
- LLM 呼叫要 try/except，失敗時要 log 並 fallback
- orchestrator 的分流判斷加上詳細註解
- **prompt 模板存在 `prompts/` 資料夾**，程式從檔案讀取，**不要寫死在程式裡**

### 模型選用規則（不要混淆）
- 對話、推理、生成工單摘要 → **Sonnet**
- 路由、分類、JSON 結構化抽取、FAQ 比對、KB 索引 → **Haiku**

### State / 限制機制
- `service_limits` 四個上限：`max_turns_per_session=20`、`max_off_topic_count=3`、`max_low_confidence_count=3`、`max_unresolved_count=3`
- 任何一個達標 → `limit_reached=True` 觸發 AI 主動建議建單
- `state.phase` 控制工單流程階段（對話中 / 等待工單確認 / 等待 Email / 已結束）

### 工單建立關鍵點
- 會員 → 直接抓 `mock_users.json` 的 email
- 訪客 → 收 Email，regex 驗證 `^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$`，最多重試 3 次
- 工單摘要用 Sonnet 生成 JSON，存進 SQLite tickets 表

## 開發順序（5 個 Phase，每 Phase 驗收後才進下一個）
1. **Phase 1**（1.5 天）核心對話打通：state、llm_client、router、cs_response、evaluator、orchestrator 最小版、KB 索引腳本、基本 API + 前端
2. **Phase 2**（0.5 天）FAQ 快查：faq_matcher、faq_responder、orchestrator 加 FAQ 分支
3. **Phase 3**（0.5 天）離題與服務限制：off_topic、`check_and_update_limits`
4. **Phase 4**（1 天）工單流程：ticket_handler 狀態機、`core/ticket.py`、`/api/ticket/create`、前端按鈕與 Email 流程
5. **Phase 5**（0.5 天）後台介面：admin API + `static/admin.html`

## 進行中事項
- [ ] 規格書已讀完，等使用者確認是否開始 Phase 1
- [ ] FAQ 與 KB 實際內容尚未提供（規格只規範格式，內容由使用者另外給或請 Claude 提供草稿）
