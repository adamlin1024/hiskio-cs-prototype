# HiSKIO AI 客服對話系統 — 雛形規格書 v5

## 文件版本說明

v5 取代 v2（原始）+ v4 patch 的所有片段，整合為單一最新規格。主要差異：

- 移除「意圖路由節點 A/C」，改成「**入口分類節點**」（4 種：greeting / unclear / off_topic / customer_service）
- 新增「**意圖明確度判斷節點**」（3 種 clarity：simple / ambiguous_subordinate / parallel_multiple）
- 新增「**承認不知道節點**」（KB 索引空陣列時走這個）
- 新增「**問候處理節點**」（greeting 時用 LLM 動態回應）
- 新增「**釐清節點**」（unclear 時引導用戶補充）
- 新增「**多重意圖選項節點**」（parallel_multiple 時列選項）
- State 重設計：捨棄 `primary_intent / secondary_intents / pending_intents`，改用 `current_intent + intent_log`，每個 intent 帶 `status` 與 `in_scope`
- 服務上限機制與工單流程沿用 v2，但加入：「greeting 上限」、「unclear 連續 3 次強制建單」、「decline 後不再重複建議建單」
- 「greeting 不增加 turn_count」是預期行為

---

## 專案目標

建立一個本地可跑的 AI 客服雛形，**驗證「入口分類 + 意圖追蹤 + FAQ 快查 + RAG + 工單建立」**多階段流程，並具備會員身分識別與服務限制能力。

### 雛形要做到的事

- 用戶能透過簡易網頁跟 AI 客服對話
- session 開始時可以選擇「會員」或「訪客」身分
- 入口分類自動區分：問候 / 模糊 / 離題 / 客服問題，各走不同路徑
- 客服問題進一步判斷意圖明確度，多重意圖會列選項讓用戶挑
- 一個 session 累計的所有意圖會被記錄在 `intent_log` 並追蹤其 `status` 流轉
- AI 優先用 FAQ 標準答案處理常見問題，沒命中才進 RAG
- KB 完全沒命中時直接承認不知道並建議建單，不讓 Sonnet 硬答
- 達到服務限制或 AI 偵測到棘手問題時，主動詢問是否建立工單
- 工單建立後存進 DB，有極簡後台可以瀏覽與標記處理狀態
- 右側除錯面板即時顯示 State 變化

### 雛形不需要做

- 真實會員系統（用 mock 假資料模擬登入）
- 真的寄信給用戶
- 多用戶並發
- 部署到雲端
- 串接 Crisp

---

## 技術棧

| 層 | 選擇 | 備註 |
|---|---|---|
| 語言 | Python 3.11+ | |
| 框架 | FastAPI | 自帶 API 文件 |
| LLM | Anthropic Claude | 只用 Claude，不接其他服務 |
| 主對話模型 | `claude-sonnet-4-6` | |
| 輕量任務模型 | `claude-haiku-4-5-20251001` | 入口分類、意圖判斷、FAQ/KB 比對、評估 |
| 儲存 | SQLite | 單檔本地，內含 sessions 與 tickets 兩張表 |
| 前端 | 純 HTML/JS | 單一檔案，不用框架 |

**為什麼不用 embedding**：使用者只想用 Claude，KB 索引改用 Haiku 對「標題+摘要+關鍵問題」做語意比對。100 篇以下的規模 token 效率夠用。

---

## 專案結構

```
Chatbot/
├── .env
├── .env.example
├── requirements.txt
├── app.py                                # FastAPI 主程式
│
├── core/
│   ├── __init__.py
│   ├── state.py                          # State schema + SQLite
│   ├── llm_client.py                     # Anthropic API 封裝 + load_prompt
│   ├── orchestrator.py                   # 流程編排
│   ├── ticket.py                         # 工單建立與 DB 寫入
│   └── text_utils.py                     # 共用 helpers（JSON 解析、history 格式化）
│
├── nodes/
│   ├── __init__.py
│   ├── entry_classifier.py               # 入口分類（4 種）
│   ├── greeting_handler.py               # greeting 動態回應
│   ├── clarification_handler.py          # unclear 釐清節點
│   ├── intent_clarity.py                 # 意圖明確度判斷（3 種 clarity）
│   ├── intent_selector.py                # parallel_multiple 列選項 + 解析選擇
│   ├── faq_matcher.py                    # FAQ 快查比對
│   ├── faq_responder.py                  # FAQ 回應（混合模式）
│   ├── kb_indexer.py                     # KB 索引比對（RAG 第一階段）
│   ├── cs_response.py                    # RAG 客服解答（Sonnet）
│   ├── no_kb_handler.py                  # KB 索引空陣列時走這個
│   ├── off_topic.py                      # 離題處理
│   ├── ticket_handler.py                 # 工單流程狀態機
│   └── evaluator.py                      # 背景評估
│
├── prompts/
│   ├── entry_classifier.txt
│   ├── greeting_handler.txt
│   ├── clarification_first.txt
│   ├── clarification_second.txt
│   ├── intent_clarity.txt
│   ├── intent_selector.txt
│   ├── faq_matcher.txt
│   ├── faq_responder.txt
│   ├── kb_indexer.txt
│   ├── cs_response.txt
│   ├── no_kb_handler.txt
│   ├── off_topic.txt
│   ├── ticket_summary.txt
│   └── evaluator.txt
│
├── data/
│   ├── faq.json
│   ├── kb/
│   │   ├── kb_001.md
│   │   └── ...
│   ├── kb_index.json                     # 由 build_kb_index.py 自動生成
│   ├── mock_users.json
│   └── prototype.db                      # SQLite，自動生成
│
├── scripts/
│   └── build_kb_index.py                 # 一次性腳本：從 kb/ 生成 kb_index.json
│
└── static/
    ├── index.html                        # 用戶對話介面
    └── admin.html                        # 後台工單管理介面
```

---

## State 結構規格

```python
{
    "session_id": "uuid 字串",
    "created_at": "ISO 8601",
    "updated_at": "ISO 8601",

    "phase": "對話中 | 等待用戶選擇意圖 | 等待工單確認 | 等待 Email | 已結束",
    "turn_count": 0,

    "user_info": {
        "is_logged_in": False,
        "user_id": None,
        "user_email": None,
        "user_name": None,
        "purchase_history": []          # mock 資料，例 ["python_basics", "ai_fundamentals"]
    },

    "issue_context": {
        "category": None,               # 技術問題 | 課程內容 | 帳務退款 | 帳號登入 | 課程操作 | 其他
        "sub_category": None,
        "summary": None,                # AI 生成的一句話摘要
        "user_emotion": "中性"          # 中性 | 困惑 | 焦慮 | 不滿 | 憤怒
    },

    "faq_context": {
        "matched_faq_id": None,         # 命中的 FAQ ID
        "match_confidence": 0.0,        # 0.0-1.0，Haiku 判斷的信心
        "answer_strategy": None         # "faq_template" | "rag" | "no_kb_match" | None
    },

    "kb_context": {
        "indexed_articles": [],         # kb_indexer 選出的 article_id 列表
        "articles_used_in_response": [] # 實際塞進 cs_response prompt 的文章 ID
    },

    "service_limits": {
        "max_turns_per_session": 20,
        "max_off_topic_count": 3,
        "max_low_confidence_count": 3,
        "max_unresolved_count": 3,

        "off_topic_count": 0,
        "low_confidence_count": 0,
        "unresolved_count": 0,          # 答了但用戶不滿意的次數

        "limit_reached": False,
        "limit_reached_reason": None    # turn_max | off_topic_max | low_confidence_max | unresolved_max
    },

    "ticket_state": {
        "ticket_suggested": False,      # AI 是否已建議建單
        "user_decision": None,          # accepted | declined | None
        "collecting_email": False,      # 是否正在向訪客收集 Email
        "email_attempts": 0,            # Email 格式錯誤重試次數
        "ticket_id": None,
        "ticket_created_at": None
    },

    "intent_state": {
        "input_classification": None,    # greeting | unclear | off_topic | customer_service | None
        "consecutive_unclear_count": 0,
        "max_unclear_count": 2,          # 第 3 次 unclear 強制建單
        "greeting_count": 0,             # session 內累計 greeting 次數
        "max_greeting_count": 3,         # 第 4 次 greeting 起永久轉灰框硬擋
        "intent_clarity": None,          # simple | ambiguous_subordinate | parallel_multiple | None
        "awaiting_selection": False,     # 是否在等用戶從多重意圖選一個
        "current_intent": None,          # 現在正在跟用戶討論的意圖文字
        "intent_log": [                  # 整個 session 偵測到的所有意圖（永不刪除）
            # {
            #   "text": "影片問題",
            #   "status": "pending | in_progress | answered | confirmed_resolved",
            #   "in_scope": True,        # 是否屬 HiSKIO 業務範圍
            #   "first_turn": 1
            # }
        ]
    },

    "escalation_signals": {
        "user_explicitly_requested_human": False,
        "ai_low_confidence_count": 0,
        "off_topic_count": 0,
        "issue_complexity_high": False,
        "user_anger_threshold_hit": False,
        "no_kb_match": False             # KB 索引完全空陣列時設 True
    },

    "chat_history": []                  # [{role, content, timestamp, response_type}]
}
```

### intent_log 的 status 流轉

```
pending      ─── 第一次被偵測，但還沒處理
   │
   ↓ orchestrator 呼叫 _switch_current_intent(text)
in_progress  ─── 現在正在跟用戶討論這個
   │
   ↓ AI 給完答案後（_mark_current_answered）
answered     ─── 答過但用戶沒明確說解決
   │
   ↓ evaluator 偵測到 user_confirmed_resolution=true
confirmed_resolved
```

**重要：intent_log 永遠不刪除任何項目**，只更新 status。讓使用者能透過「下一個」「另一個」這類指稱詞回到先前的議題。

### State 更新規則

**每輪用戶輸入後，程式自動處理**：
- 開頭 `chat_history.append({user message})`
- 結尾 `turn_count += 1`（greeting 例外，不增加）
- 結尾 `updated_at = now()`
- 結尾 `check_and_update_limits(state)` → 達上限設 `limit_reached`
- 結尾 `save_state(state)`

**節點各自負責更新**：
- entry_classifier：`intent_state.input_classification`
- intent_clarity：`intent_state.intent_clarity`、`intent_log` 新增/更新
- _switch_current_intent：`intent_state.current_intent` + 對應 intent_log 項目 status
- _mark_current_answered：current intent 從 in_progress → answered
- faq_matcher：`faq_context.matched_faq_id` / `match_confidence`
- faq_responder / cs_response：`faq_context.answer_strategy`、`kb_context.articles_used_in_response`
- off_topic：`service_limits.off_topic_count += 1`
- ticket_handler：`ticket_state` 全部欄位、`phase`
- evaluator：`issue_context`、`service_limits.low_confidence_count`、`service_limits.unresolved_count`、`ticket_state.user_decision`、intent_log 中 current_intent 標 confirmed_resolved
- no_kb_handler 觸發時：`escalation_signals.no_kb_match = True`、`ticket_state.ticket_suggested = True`

---

## 13 個節點的詳細規格

### 節點 1：入口分類（entry_classifier）

**檔案**：`nodes/entry_classifier.py`
**模型**：Haiku
**輸入**：用戶最新訊息、`state.phase`、最近 3 輪 history、`state.intent_state.consecutive_unclear_count`
**輸出**：4 種字串之一

```
greeting          # 純打招呼、試探語（你好 / Hi / 在嗎 / hello）
unclear           # 訊息模糊、亂碼、或無實質內容
off_topic         # 跟 HiSKIO 服務無關
customer_service  # 真的是客服問題
```

**程式行為**：
- `max_tokens=10`、`temperature=0`
- 取回傳的第一個有效 token，trim 後比對
- 不是上述 4 種時 fallback 為 `customer_service`（寧可走完整流程，不要誤擋）
- 第一輪沒 history 時 `recent_history` 替換為「（無）」

**Prompt 重點**：
- greeting 例「你好、Hi、在嗎、hello、嗨」；含實質問題就改為 customer_service
- unclear 例「我有問題、不行、亂碼」；能猜到方向就改為 customer_service
- off_topic 範圍嚴格定義為「不在 HiSKIO（線上學習平台）業務範圍」，包含食物 / 寵物 / 推薦商品 / 質疑系統行為等
- customer_service 例「影片不能看、想退款、忘記密碼」

---

### 節點 2：問候處理（greeting_handler）

**檔案**：`nodes/greeting_handler.py`
**模型**：Haiku
**輸入**：用戶身分、用戶訊息
**輸出**：1-2 句問候 + 主動詢問需求

**程式行為**：
- `max_tokens=120`、`temperature=0.5`
- 已登入用戶可以稱呼名字；訪客用「您好」開頭
- **由 orchestrator 累加 `greeting_count`，超過 `max_greeting_count` 後不呼叫此節點，改回固定灰框訊息**
- 灰框訊息：「如果您有客服問題（影片、退款、帳號等），請直接描述問題；若沒有客服需求，可以關閉視窗結束對話。」

**Greeting 上限機制**：
- 一個 session 累計超過 `max_greeting_count`（預設 3）次後，**永久轉灰框硬擋**
- **不重置**：用戶中途回到客服問題或離題，後續又改打招呼仍維持灰框

---

### 節點 3：釐清處理（clarification_handler）

**檔案**：`nodes/clarification_handler.py`
**模型**：Haiku（兩個 prompt：第 1 次與第 2 次）
**輸入**：用戶訊息、`state.intent_state.consecutive_unclear_count`
**輸出**：給用戶的引導訊息

**程式行為**：
- `max_tokens=300`、`temperature=0.4`
- 第 1 次（count==1）：溫和引導補充，提示大方向（例「您是想詢問課程觀看、付款、帳號相關的問題嗎？」）
- 第 2 次（count==2）：列出 3-4 個常見問題類別，請用戶用編號選擇
- 第 3 次（count>=3）：**不呼叫 LLM**，由 orchestrator 直接觸發建單流程，回固定訊息：

```
看起來這個問題比較複雜，建議由人工客服協助處理會更有效率。
我為您建立工單，客服團隊會主動聯繫您。
```

**狀態流轉**：
- 任何非 unclear 訊息進來時，把 `consecutive_unclear_count` 重置為 0
- response_type = `clarification`（第 1/2 次）或 `force_escalation`（第 3 次）

---

### 節點 4：意圖明確度判斷（intent_clarity）

**檔案**：`nodes/intent_clarity.py`
**模型**：Haiku
**只在 entry_classifier 回傳 customer_service 時呼叫**
**輸入**：用戶訊息、最近 3 輪 history、現有 intent_log
**輸出**：JSON

```json
{
  "clarity": "simple | ambiguous_subordinate | parallel_multiple",
  "detected_intents": [
    {"text": "意圖 1", "in_scope": true},
    {"text": "意圖 2", "in_scope": false}
  ],
  "referenced_intent_index": 0 | null
}
```

**clarity 三種**：
- `simple`：用戶這句話講一件事
- `ambiguous_subordinate`：多個關鍵詞但有主從關係（例「我付費後想退費」→ 主=退費，從=付費的脈絡）；secondary 不算獨立意圖
- `parallel_multiple`：多個獨立問題並列（例「影片不能看，還有發票問題」）

**referenced_intent_index 機制**：
- 若用戶用指稱性詞彙（「下一個」「另外那個」「剩下的」「那發票呢」）且 intent_log 有 status=pending 或 answered 的項目
- → `referenced_intent_index` 填對應 intent_log 索引（0-based）
- → `clarity` 仍填 `simple`、`detected_intents` 填空陣列
- → 優先選 status=pending 最早的；無 pending 才選 status=answered 最早的

**in_scope 標記**：
- `true` = HiSKIO 業務範圍：課程內容 / 影片播放 / 帳號 / 付款 / 退款 / 發票 / 平台操作
- `false` = 非業務範圍：食物 / 水果 / 寵物 / 推薦商品 / 天氣 / 寫作業 / 質疑系統
- **即使 in_scope=false 也要列出**（不可偷偷過濾），由 orchestrator 決定路由

**嚴格規則**：
- `detected_intents` 內部不可重複
- `primary_intent` 不可同時出現在 `secondary_intents`（但本版本已合併為單一 `detected_intents` 陣列）

**程式行為**：
- `max_tokens=300`、`temperature=0`
- 解析失敗時 fallback 為 `simple`，detected_intents = `[{text: user_message, in_scope: true}]`
- 兼容兩種輸入格式：純字串陣列（舊）或物件陣列（新）

---

### 節點 5：多重意圖選項（intent_selector）

**檔案**：`nodes/intent_selector.py`
**只在 intent_clarity = parallel_multiple 時呼叫，phase 改為「等待用戶選擇意圖」**

**`respond(state, user_message)` 函式**：
- **不用 LLM 生成**，直接從 intent_log 中 status=pending 或 in_progress 的項目組編號清單，避免畫面與 state 對不上
- 訊息範例：

```
了解您同時提到幾個問題，我們可以一個一個處理。
您提到的問題有：
1. 影片問題
2. 發票問題
3. 水果問題

請回覆編號（例如「1」）告訴我想先處理哪一個，其他問題稍後可以再協助您。
```

**`parse_selection(state, user_message)` 函式**：
- 模型：Haiku
- 輸出 `S1` / `S2` / ... / `SN` / `N`
- `S` = Selection（用戶在選）；`N` = Not selection（用戶在說新事情、抱怨、閒聊、質疑）
- **嚴格：「我有 X 方面的問題」這種陳述句一律 N**（即使 X 在選項中）
- 回 `N` → orchestrator 退出選擇 phase，把訊息丟回 `entry_classifier` 重新分類
- 回 `S` → orchestrator 把對應 intent_log 項目切成 in_progress、走 FAQ/RAG（若 in_scope=false 則改走 off_topic）

---

### 節點 6：FAQ 快查（faq_matcher）

**檔案**：`nodes/faq_matcher.py`
**模型**：Haiku
**輸入**：用戶訊息、FAQ 清單（從 `data/faq.json` 載入，只給 id + question_patterns，省 token）
**輸出**：`{"matched_id": "faq_X" | null, "confidence": 0.0-1.0}`

**FAQ JSON 結構**（`data/faq.json`）：

```json
[
    {
        "id": "faq_001",
        "category": "技術問題",
        "question_patterns": ["影片無法播放", "影片打不開", ...],
        "core_steps": ["重新整理頁面", "清除快取", ...],
        "fallback_message": "若以上方式都試過仍無法解決，可以建立工單..."
    }
]
```

**程式行為**：
- `max_tokens=100`、`temperature=0`
- **信心 >= 0.7 走 faq_responder；< 0.7 走 KB 索引（RAG）**
- 解析失敗時回傳 `{"matched_id": null, "confidence": 0.0}`

---

### 節點 7：FAQ 回應（faq_responder，混合模式）

**檔案**：`nodes/faq_responder.py`
**模型**：Haiku
**輸入**：命中的 FAQ 資料、用戶訊息、`state.user_info`、`state.issue_context.user_emotion`
**輸出**：給用戶的回應

**核心邏輯**：core_steps **一字不漏照抄**，LLM 只負責**開場與結尾語氣的潤飾**。
- `max_tokens=400`、`temperature=0.5`
- 開場根據用戶情緒調整（中性/困惑 → 簡短承接；焦慮/不滿/憤怒 → 先安撫）
- 結尾固定加上 fallback_message 的內容（可改寫語氣）

---

### 節點 8：KB 索引（kb_indexer，RAG 第一階段）

**檔案**：`nodes/kb_indexer.py`
**模型**：Haiku
**輸入**：用戶訊息、KB 索引清單、最近 3 輪 history
**輸出**：`["kb_001", "kb_005", "kb_018"]`（最多 3 個 article_id）

**KB 索引結構**（`data/kb_index.json`，由 `scripts/build_kb_index.py` 自動生成）：

```json
[
    {
        "id": "kb_001",
        "title": "課程影片無法播放完整排查",
        "category": "技術問題",
        "summary": "詳細排查影片播放失敗的所有可能原因",
        "key_questions": ["影片完全打不開", "影片載入後不能播放", ...]
    }
]
```

**KB 文章結構**（`data/kb/kb_001.md`）：

```markdown
---
id: kb_001
title: 課程影片無法播放完整排查
category: 技術問題
last_updated: 2025-08-15
---

# 課程影片無法播放完整排查
...
```

**程式行為**：
- `max_tokens=100`、`temperature=0`
- 解析失敗或回傳空陣列時，**orchestrator 改走 no_kb_handler 節點**（v4 改動）

---

### 節點 9：客服解答（cs_response，RAG 第二階段）

**檔案**：`nodes/cs_response.py`
**模型**：Sonnet
**輸入**：State 全部、選中的 KB 文章全文（從 `data/kb/*.md` 載入）
**輸出**：給用戶的回應

**程式行為**：
- `max_tokens=600`、`temperature=0.6`
- 只能根據提供的 KB 文章回答，不要憑空生成
- 已給過 2 次解答用戶仍不滿意 → 在回應中提出建單建議
- 回應 2-4 句，不要長篇大論

**特殊標記**：
- 若 Sonnet 判斷需要建議建單，在回應**最前面**加上 `[SUGGEST_TICKET]` 標記
- orchestrator 偵測到後移除標記、設定 `ticket_state.ticket_suggested = True`、`phase = "等待工單確認"`、`response_type = "ticket_flow"`
- prompt 對標記使用條件嚴格規定：第一輪絕對不能加；KB 沒命中時不能加；純情緒抒發不能加；只有「給過 ≥2 次解答仍不滿意」「明確要求人工」「帳號被鎖等個案」才能加

---

### 節點 10：承認不知道（no_kb_handler）

**檔案**：`nodes/no_kb_handler.py`
**模型**：Haiku
**觸發條件**：`kb_indexer` 回傳空陣列（KB 索引完全沒選到任何文章）
**輸入**：用戶訊息、`state.user_info`
**輸出**：給用戶的回應

**程式行為**：
- `max_tokens=200`、`temperature=0.4`
- 坦白告知知識庫沒有對應資訊（不要編造）
- 主動建議建立工單

**呼叫後 orchestrator 的副作用**：
- `state.escalation_signals.no_kb_match = True`
- `state.ticket_state.ticket_suggested = True`
- `state.phase = "等待工單確認"`
- `response_type = "no_kb_match"`
- 前端顯示「建立工單」按鈕

---

### 節點 11：離題處理（off_topic）

**檔案**：`nodes/off_topic.py`
**模型**：Haiku
**輸入**：用戶離題訊息、原本的 issue summary
**輸出**：給用戶的回應

**程式行為**：
- `max_tokens=200`、`temperature=0.6`
- 進入此節點時 `service_limits.off_topic_count += 1`
- 達到 `max_off_topic_count` 時，**下一輪用戶說話如果路由仍判斷 C，直接回固定訊息**「對話僅處理 HiSKIO 服務相關問題，如無客服需求請關閉視窗。」**不再呼叫 LLM**
- response_type：`off_topic`（橘框）或 `off_topic_blocked`（灰框）

**規格設計：灰框觸發路徑**：
- 達 max_off_topic_count → 下一輪通常先觸發「建議建單」紫框（因 limit_reached）
- 用戶若拒絕建單（回「不用」）→ ticket_state.user_decision = "declined"
- 之後的離題訊息才會走灰框 off_topic_blocked

---

### 節點 12：工單流程（ticket_handler，多階段狀態機）

**檔案**：`nodes/ticket_handler.py`
**模型**：Haiku（處理 yes/no 判斷）+ Sonnet（生成工單摘要 → 在 `core/ticket.py`）
**輸入**：State 全部
**輸出**：不同階段不同回應

**狀態機**：

```
階段 1：AI 建議建單（由 cs_response 觸發 SUGGEST_TICKET、no_kb_handler、unclear 第 3 次、或 service_limits 達上限）
   ↓ phase = "等待工單確認"
階段 2：等待用戶決定（回應「好」「不要」）
   ├─ Y → 進階段 3
   ├─ N → handle_decline：保留所有 service_limits、phase 重置為「對話中」、ticket_state.ticket_suggested = False
   │       此後 orchestrator 不再重複建議建單（user_decision == "declined" 守門）
   └─ U → orchestrator 重置 phase 為「對話中」、ticket_state.ticket_suggested = False，fall through 走正常流程
       ↓
階段 3：檢查身分
   ├─ 已登入 → 直接抓 user_info.user_email，跳到階段 5
   └─ 未登入 → phase = "等待 Email"，進階段 4
       ↓
階段 4：收集 Email
   - 詢問用戶 Email
   - 用 regex 驗證格式：`^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$`
   - 失敗最多 3 次，失敗後告知「Email 格式有誤已重試多次，本次工單無法建立，請稍後再開啟新對話重試」、phase = "已結束"
   ↓
階段 5：生成工單
   - core/ticket.py 用 Sonnet 生成問題摘要 JSON
   - 寫入 SQLite tickets 表
   - 回給用戶工單編號與後續說明
   ↓ phase = "已結束"
階段 6：結束
   - 之後用戶若繼續輸入，固定回「您的工單已建立(#XXX)，請耐心等候回覆。若您還想再問新問題，請按右上方「新對話」按鈕重新開始。」
```

**`decide(user_message)` 嚴格性**：
- 必須是「單純確認建單」才回 Y（例「好」「OK」「麻煩你」「請建立」）
- 「我要 X」（X 不是建單）→ U（例「我要下一個問題」「我要看影片」）
- 「不用」「先不要」→ N
- 其他不確定 → U

**`initiate(state)` 函式**：
- 前端「建立工單」按鈕觸發，跳過 yes/no 階段直接進階段 3

---

### 節點 13：背景評估（evaluator）

**檔案**：`nodes/evaluator.py`
**模型**：Haiku
**輸入**：這輪的 user_message + ai_response、previous_category、turn_count
**輸出**：JSON

```json
{
  "issue_category": "技術問題 | 課程內容 | 帳務退款 | 帳號登入 | 課程操作 | 其他",
  "issue_sub_category": "更細的分類例如『無法觀看影片』",
  "issue_summary": "一句話總結",
  "user_emotion": "中性 | 困惑 | 焦慮 | 不滿 | 憤怒",
  "user_satisfied_with_answer": true/false/null,
  "ai_confidence_in_answer": 0.0-1.0,
  "user_explicitly_wants_ticket": true/false,
  "user_confirmed_resolution": true/false
}
```

**`user_confirmed_resolution`**（v4.1 新增）：
- `true` = 用戶**這一句**明確表達「問題解決了 / OK 了 / 謝謝 / 沒問題了 / 搞定」
- `false` = 用戶在問新問題、表達不滿、提供補充資訊、或單純沉默

**程式行為**：
- `max_tokens=300`、`temperature=0`
- 只在 `classification == "customer_service"` 且 `phase == "對話中"` 時才跑
- 解析失敗 log error，跳過 State 更新

**解析成功後副作用**：
- 更新 `issue_context.{category, sub_category, summary, user_emotion}`
- `ai_confidence_in_answer < 0.4` → `service_limits.low_confidence_count += 1`
- `user_satisfied_with_answer is False` → `service_limits.unresolved_count += 1`
- `user_explicitly_wants_ticket is True` → `ticket_state.user_decision = "accepted"`
- `user_confirmed_resolution is True` → 把 intent_log 中 current_intent 對應項目 status 改為 `confirmed_resolved`

---

## Orchestrator 主流程

**檔案**：`core/orchestrator.py`

```python
def handle_user_message(session_id: str, user_message: str) -> dict:
    state = load_state(session_id)
    append_message(state, "user", user_message)

    # 1. 特殊 phase 攔截
    phase_result = _try_handle_phase(state, user_message, session_id)
    if phase_result is not None:
        return phase_result

    # 2. 服務上限攔截（已達上限 + 未建議過 + 用戶沒拒絕過）
    if _should_suggest_ticket_now(state):
        return _suggest_ticket_due_to_limit(state, session_id)

    # 3. 入口分類 + 分派
    classification = entry_classifier.classify(state, user_message)
    state["intent_state"]["input_classification"] = classification

    return _dispatch_and_finalize(state, user_message, classification, session_id)


def _try_handle_phase(state, user_message, session_id):
    phase = state["phase"]

    if phase == "等待用戶選擇意圖":
        return _try_select_or_fall_through(state, user_message, session_id)
        # 用戶真的在選 → 處理；不是 → 退出 phase 回 None（fall through）

    if phase == "等待工單確認":
        decision = ticket_handler.decide(user_message)
        if decision == "Y": return ticket_handler.handle_accept(state)
        if decision == "N": return ticket_handler.handle_decline(state)
        # U：重置 phase + ticket_suggested = False，fall through
        state["phase"] = "對話中"
        state["ticket_state"]["ticket_suggested"] = False
        return None

    if phase == "等待 Email":
        return ticket_handler.handle_email_input(state, user_message)

    if phase == "已結束":
        return _ended_session(state)

    return None


def _dispatch_and_finalize(state, user_message, classification, session_id):
    # 根據 classification 分派
    if classification == "greeting":
        ai_response, response_type, increment_turn = _handle_greeting(state, user_message)
    elif classification == "unclear":
        ai_response, response_type, increment_turn = _handle_unclear(state, user_message)
    elif classification == "off_topic":
        state["intent_state"]["consecutive_unclear_count"] = 0
        ai_response, response_type = _handle_off_topic(state, user_message)
        increment_turn = True
    else:  # customer_service
        state["intent_state"]["consecutive_unclear_count"] = 0
        ai_response, response_type = _handle_service_intent(state, user_message, session_id)
        increment_turn = True

    return _finalize_turn(state, user_message, ai_response, response_type,
                          increment_turn=increment_turn,
                          classification=classification, session_id=session_id)


def _finalize_turn(state, user_message, ai_response, response_type,
                   *, increment_turn, classification, session_id):
    append_message(state, "assistant", ai_response, response_type=response_type)

    if classification == "customer_service" and state["phase"] == "對話中":
        evaluator.evaluate(state, user_message, ai_response)
        if state["ticket_state"]["user_decision"] == "accepted" and not state["ticket_state"]["ticket_id"]:
            state["phase"] = "等待工單確認"
            state["ticket_state"]["ticket_suggested"] = True
        if response_type != "intent_selection":
            _mark_current_answered(state)

    if increment_turn:
        state["turn_count"] += 1
    state["updated_at"] = now_iso()

    check_and_update_limits(state)
    save_state(state)
    return _build_response(state, ai_response, response_type)
```

### customer_service 分支內部（_handle_service_intent）

```python
def _handle_service_intent(state, user_message, session_id):
    clarity_result = intent_clarity.analyze(state, user_message)

    # case 1: parallel_multiple → 列選項
    if clarity_result["clarity"] == "parallel_multiple":
        for det in clarity_result["detected_intents"]:
            _ensure_in_intent_log(state, det["text"], in_scope=det["in_scope"])
        ai_response = intent_selector.respond(state, user_message)
        state["intent_state"]["awaiting_selection"] = True
        state["phase"] = "等待用戶選擇意圖"
        return ai_response, "intent_selection"

    # case 2: 用指稱詞 → 取 intent_log 對應項
    if clarity_result["referenced_intent_index"] is not None:
        log_item = state["intent_state"]["intent_log"][clarity_result["referenced_intent_index"]]
        _switch_current_intent(state, log_item["text"])
        if not log_item.get("in_scope", True):
            return _route_off_topic_with_count(state, log_item["text"])
        return _run_faq_then_rag(state, user_message, log_item["text"], session_id)

    # case 3: simple / ambiguous_subordinate → 單一意圖
    detected = clarity_result["detected_intents"]
    chosen = detected[0]["text"] if detected else user_message
    chosen_in_scope = detected[0]["in_scope"] if detected else True
    _ensure_in_intent_log(state, chosen, in_scope=chosen_in_scope)
    _switch_current_intent(state, chosen)
    if not chosen_in_scope:
        return _route_off_topic_with_count(state, chosen)
    return _run_faq_then_rag(state, user_message, chosen, session_id)
```

### FAQ → RAG 流程（_run_faq_then_rag）

```python
def _run_faq_then_rag(state, user_message, effective_message, session_id):
    faq_result = faq_matcher.match(effective_message)

    if faq_result["confidence"] >= 0.7:
        return faq_responder.respond(...), "faq"

    kb_ids = kb_indexer.index_articles(state, effective_message)
    if not kb_ids:
        # KB 完全空 → 承認不知道 + 建議建單
        ai_response = no_kb_handler.respond(state, effective_message)
        state["escalation_signals"]["no_kb_match"] = True
        state["ticket_state"]["ticket_suggested"] = True
        state["phase"] = "等待工單確認"
        return ai_response, "no_kb_match"

    articles = [kb_indexer.load_kb_article(kid) for kid in kb_ids]
    ai_response = cs_response.respond(state, articles, effective_message)

    if ai_response.startswith("[SUGGEST_TICKET]"):
        ai_response = ai_response.replace("[SUGGEST_TICKET]", "", 1).strip()
        state["ticket_state"]["ticket_suggested"] = True
        state["phase"] = "等待工單確認"
        return ai_response, "ticket_flow"

    return ai_response, "rag"
```

---

## 用戶路徑與 response_type 對照

| response_type | 觸發來源 | 前端顏色 | turn_count |
|---|---|---|---|
| `greeting` | greeting_handler | 淺藍虛線 | 不增加 |
| `greeting_blocked` | greeting_count > max | 灰色（小字） | 不增加 |
| `clarification` | clarification_handler 第 1/2 次 | 橘黃實線 | +1 |
| `force_escalation` | unclear 第 3 次 | 紫框（同 ticket_flow） | +1 |
| `off_topic` | off_topic 節點 | 橘色 | +1 |
| `off_topic_blocked` | off_topic_count >= max | 灰色（小字） | +1 |
| `intent_selection` | intent_selector.respond | 深紫色（4px 邊框） | +1 |
| `faq` | faq_responder | 綠色 | +1 |
| `rag` | cs_response 一般情況 | 藍色 | +1 |
| `no_kb_match` | no_kb_handler | 粉紅色 | +1 |
| `ticket_flow` | ticket_handler 各階段 / cs_response 加 SUGGEST_TICKET | 紫色 | +1 |
| `session_ended` | _ended_session | 紅色 | +1 |

---

## 工單管理（core/ticket.py）

### SQLite 表結構

```sql
CREATE TABLE tickets (
    ticket_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    user_email TEXT NOT NULL,
    user_id TEXT,
    is_member BOOLEAN NOT NULL,
    issue_category TEXT,
    issue_summary TEXT NOT NULL,
    user_emotion_at_close TEXT,
    key_attempts TEXT,
    full_chat_history TEXT NOT NULL,
    status TEXT DEFAULT 'open',          -- open | in_progress | closed
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE sessions (
    session_id TEXT PRIMARY KEY,
    state_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
```

---

## API 端點規格

### 用戶對話 API

**`POST /api/session/new`**

Request:
```json
{
    "is_logged_in": true | false,
    "user_id": "user_001" | null
}
```

Response:
```json
{
    "session_id": "uuid",
    "state": { ...完整 state... }
}
```

**`POST /api/chat`**

Request:
```json
{
    "session_id": "uuid",
    "message": "用戶說的話"
}
```

Response:
```json
{
    "ai_response": "AI 的回應文字",
    "response_type": "greeting | greeting_blocked | clarification | force_escalation | off_topic | off_topic_blocked | intent_selection | faq | rag | no_kb_match | ticket_flow | session_ended",
    "show_ticket_button": true | false,
    "ticket_id": 123 | null,
    "state": { ...完整 state... }
}
```

**`POST /api/ticket/create`**（用戶按下「建立工單」按鈕時）

Request:
```json
{ "session_id": "uuid" }
```

Response: 同 `/api/chat`

**`GET /api/mock_users`**：回傳 mock_users.json 中所有 user_id + user_name（前端「模擬會員登入」下拉選單用）

### 後台 API

**`GET /api/admin/tickets?status=open|in_progress|closed`**

Response:
```json
{
    "tickets": [
        {
            "ticket_id": 1,
            "session_id": "...",
            "user_email": "...",
            "user_id": "user_001",
            "is_member": true,
            "issue_category": "技術問題",
            "issue_summary": "用戶反映影片無法播放...",
            "user_emotion_at_close": "不滿",
            "status": "open",
            "created_at": "..."
        }
    ]
}
```

**`GET /api/admin/tickets/{ticket_id}`**：回傳完整工單資料含 full_chat_history

**`POST /api/admin/tickets/{ticket_id}/status`**

Request:
```json
{ "status": "open | in_progress | closed" }
```

---

## 前端介面規格

### 用戶對話介面（`static/index.html`）

新建 session 前，先選擇身分（訪客 / 模擬會員登入）。

對話介面布局：
```
┌──────────────────────────────────────────────────────────────┐
│  HiSKIO AI 客服  [會員: 王小明]              [後台] [新對話]  │
├───────────────────────────────────────┬──────────────────────┤
│  對話區（60%）                         │  State 除錯（40%）   │
│  訊息以 response_type 對應的顏色邊框   │  完整 JSON 即時顯示  │
│  「建立工單」按鈕在 show_ticket_button │                      │
│   為 true 時顯示                       │                      │
├───────────────────────────────────────┤                      │
│  [輸入框]                    [送出]    │                      │
└───────────────────────────────────────┴──────────────────────┘
```

CSS 重點：
- `#chat-pane` 與 `#debug-pane` 都要 `min-height: 0` + `overflow: hidden`，讓 grid 子元素的 overflow-y: auto 能正常滾動
- `#state-json` 用 `white-space: pre-wrap` + `word-break: break-word` 避免長字串撐出橫向 overflow

### 後台介面（`static/admin.html`）

- 工單列表，支援篩選（全部 / Open / In Progress / Closed）
- 每筆工單顯示：編號、類別、會員/訪客、Email、狀態、建立時間、摘要
- 操作按鈕：標記 Open / 標記處理中 / 標記結案 / 查看完整對話
- 「查看完整對話」彈 modal 顯示摘要 + AI 嘗試過什麼 + 完整 chat history（含每則訊息的 response_type）

---

## 共用 helpers（core/text_utils.py）

避免 JSON 解析、history 格式化邏輯散落在各節點重複實作：

- `extract_json_object(raw)`：容忍 markdown code fence 與外層文字
- `extract_json_array(raw)`：同上但對 list
- `format_recent_history(history, turns=3, empty="（無）")`：格式化 chat_history 後段為 prompt 字串

---

## Mock 會員資料

**`data/mock_users.json`**：

```json
[
    {
        "user_id": "user_001",
        "user_name": "王小明",
        "user_email": "wang@example.com",
        "purchase_history": ["python_basics", "ai_fundamentals"]
    },
    {
        "user_id": "user_002",
        "user_name": "李小華",
        "user_email": "li@example.com",
        "purchase_history": ["web_development_2024"]
    },
    {
        "user_id": "user_003",
        "user_name": "陳大偉",
        "user_email": "chen@example.com",
        "purchase_history": []
    }
]
```

---

## 測試用例

### T1：FAQ 命中
```
身分：訪客
輪 1：我的影片不能播放
預期：走 entry_classifier=customer_service → intent_clarity=simple →
      faq_matcher 命中 faq_001 信心 ≥0.7 → faq_responder
      response_type=faq（綠框）
```

### T2：RAG 走查
```
身分：會員
輪 1：我的 python 課程作業繳交按鈕跑掉了
預期：FAQ 不命中 → kb_indexer 選相關技術文章 → cs_response
      response_type=rag（藍框）
```

### T3：問候 fast-path
```
輪 1：你好
預期：entry_classifier=greeting → greeting_handler 動態回應
      turn_count 仍是 0、greeting_count=1
      response_type=greeting（淺藍虛線）
```

### T4：Greeting 上限（不重置）
```
輪 1-3：你好 / Hi / 嗨
輪 4：你好
預期：第 4 次起 greeting_blocked 灰框（不打 LLM）
中間插入：我影片不能看 → faq 綠框
之後：你好 → 仍然 greeting_blocked（greeting_count 不重置）
```

### T5：Unclear 三次強制建單
```
輪 1：我有問題
輪 2：不行
輪 3：dlsjfkdsf
預期：第 3 次直接 force_escalation 紫框 + 自動 phase=等待工單確認
```

### T6：離題逐次警告 + 建單建議 + 灰框
```
輪 1：影片不能看 → faq
輪 2-4：天氣 / 餐廳 / 笑話 → off_topic 橘框
輪 5：再離題 → off_topic_count 達 max → ticket_flow 紫框（建議建單）
輪 6：「不用」 → 紫框拒絕回應，user_decision=declined
輪 7：再離題 → off_topic_blocked 灰框（硬擋，不打 LLM）
```

### T7：多重意圖選擇 + 指稱詞 + 解決確認
```
輪 1：「我影片不能看，還有發票問題」
  → intent_selection 紫框列 2 選項
  → intent_log = [影片(pending, in_scope=true), 發票(pending, in_scope=true)]

輪 2：「2」
  → 切換到「發票問題」當 current_intent
  → 發票 → in_progress、其他維持 pending
  → faq_002 綠框
  → AI 回完答案後：發票 → answered

輪 3：「下一個問題呢」
  → intent_clarity 看到指稱詞 + intent_log 還有 pending
  → referenced_intent_index = 0（影片）
  → 影片 → in_progress
  → faq_001 綠框
  → AI 回完：影片 → answered

輪 4：「OK 了，謝謝」
  → evaluator 偵測 user_confirmed_resolution=true
  → 影片 → confirmed_resolved
```

### T8：多重意圖含離題項
```
輪 1：「我有影片、發票、跟水果問題」
  → intent_clarity 偵測：
     {影片問題, in_scope=true}
     {發票問題, in_scope=true}
     {水果問題, in_scope=false}
  → intent_log 三項各帶 in_scope
  → intent_selector 列 3 個選項（不過濾，保留可見性）

輪 2：「3」
  → 切換到「水果問題」
  → in_scope=false → _route_off_topic_with_count → off_topic 橘框
```

### T9：選單 phase 中用陳述句
```
（接 T8 的場景）
輪 X：「我有水果方面的問題」
  → parse_selection 判 N（陳述句不是選項）
  → 退出 intent_selection phase
  → entry_classifier 重新分類 → off_topic
  → off_topic 橘框
```

### T10：訪客建單流程
```
身分：訪客
（觸發建單後）
輪 N：紫框「請建立工單嗎？」
輪 N+1：「好」 → 「請提供 Email」（紫框）phase=等待 Email
輪 N+2：「abc」 → 格式錯誤（紫框）attempt=1
輪 N+3：「abc@gmail.com」 → 建單成功（紫框 + #ticket_id）→ phase=已結束
```

### T11：會員建單流程
```
身分：會員（user_001）
（觸發建單後）
輪 N：紫框「請建立工單嗎？」
輪 N+1：「好」 → 直接建單（自動帶 wang@example.com）→ session_ended 紅框
```

### T12：KB 完全沒命中
```
身分：訪客
輪 1：「你們公司有附設停車場嗎？」（語意清楚但 HiSKIO 沒這資料）
預期：
  → entry_classifier=customer_service
  → intent_clarity=simple, in_scope=true（看不出非業務）
  → faq 不命中、kb 索引空
  → no_kb_handler 回應「知識庫沒有對應資訊...」（粉紅框）
  → escalation_signals.no_kb_match=true
  → 顯示「建立工單」按鈕
```

---

## 已知限制（雛形階段不處理）

1. KB 索引用 LLM 不用 embedding，在 100+ 篇規模 token 會變貴、且 LLM 注意力品質下降
2. 沒有真的寄信通知工單建立
3. 後台沒有編輯工單內容、回覆用戶的功能
4. State 沒做 schema migration（升版需手動清 sessions 表）
5. 沒有 streaming（回應一次性吐出）
6. 沒有錯誤恢復機制
7. mock_users 是寫死的，沒有真的會員系統
8. WatchFiles 在 Windows 上 hot reload 不穩定，改動 .py 後常需手動重啟 uvicorn
9. Anthropic 安全過濾會悄悄移除敏感詞，intent_clarity 偶爾會漏掉部分意圖
10. intent_log 永不刪除，極端情況下單一 session 累積上百筆會吃 token

---

## 開發注意事項

1. **prompt 模板存在 `prompts/` 資料夾**，程式從檔案讀取，**不要寫死在程式裡**
2. **LLM 呼叫要 try/except**，失敗時要 log 並 fallback
3. **JSON 解析統一用 `core/text_utils.extract_json_object/array`**，避免每個節點重寫
4. **history 格式化統一用 `core/text_utils.format_recent_history`**
5. **節點函式都要有 docstring** 說明輸入輸出
6. **orchestrator 流程**：分派 → finalize 兩階段，所有路徑（包括 fall through）共用 `_finalize_turn`，避免程式碼重複
