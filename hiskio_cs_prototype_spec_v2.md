# HiSKIO AI 客服對話系統 — 本地雛形開發規格書 v2

## 文件版本說明

這份規格取代 v1。主要變動:
- 移除「轉人工」流程,改為「建立工單」
- 加入「FAQ 快查」階段(優先於 RAG)
- 加入「KB 索引」機制(RAG 兩階段查詢)
- 加入會員/訪客身分識別與行為差異
- 加入服務限制(取代原本的 escalation_signals)
- 加入極簡後台管理介面

---

## 專案目標

建立一個本地可跑的 AI 客服雛形,**驗證「FAQ 快查 + RAG + 工單建立」三段式流程**,並具備會員身分識別與服務限制能力。

### 雛形要做到的事

- 用戶能透過簡易網頁跟 AI 客服對話
- session 開始時可以選擇「會員」或「訪客」身分
- AI 優先用 FAQ 標準答案處理常見問題,沒命中才進 RAG
- 達到服務限制或 AI 偵測到棘手問題時,主動詢問是否建立工單
- 用戶也可隨時主動要求建立工單
- 工單建立後存進 DB,有極簡後台可以瀏覽與標記處理狀態
- 右側除錯面板即時顯示 State 變化

### 雛形不需要做

- 真實會員系統(用 mock 假資料模擬登入)
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
| LLM | Anthropic Claude | 只用 Claude,不接其他服務 |
| 主對話模型 | claude-sonnet-4-6 | |
| 輕量任務模型 | claude-haiku-4-5-20251001 | 路由、FAQ 比對、KB 索引、評估 |
| 儲存 | SQLite | 單檔本地,內含 sessions 與 tickets 兩張表 |
| 前端 | 純 HTML/JS | 單一檔案,不用框架 |

**為什麼不用 embedding**:你只想用 Claude,所以 KB 索引改用 Haiku 做語意比對(把 52 篇文章的「標題+摘要+關鍵問題」做成索引清單,Haiku 從中選最相關的 3 篇)。這個方法在 100 篇以下的規模 token 效率夠用。

---

## 專案結構

```
hiskio-cs-prototype/
├── .env
├── .env.example
├── requirements.txt
├── README.md
├── app.py                           # FastAPI 主程式
│
├── core/
│   ├── __init__.py
│   ├── state.py                     # State 定義與管理
│   ├── llm_client.py                # Anthropic API 封裝
│   ├── orchestrator.py              # 流程編排
│   └── ticket.py                    # 工單管理
│
├── nodes/
│   ├── __init__.py
│   ├── router.py                    # 節點 1:意圖路由
│   ├── faq_matcher.py               # 節點 2:FAQ 快查
│   ├── faq_responder.py             # 節點 3:FAQ 回應(混合模式)
│   ├── kb_indexer.py                # 節點 4:KB 索引比對
│   ├── cs_response.py               # 節點 5:RAG 客服解答
│   ├── off_topic.py                 # 節點 6:離題處理
│   ├── ticket_handler.py            # 節點 7:工單流程處理
│   └── evaluator.py                 # 節點 8:背景評估
│
├── prompts/
│   ├── router.txt
│   ├── faq_matcher.txt
│   ├── faq_responder.txt
│   ├── kb_indexer.txt
│   ├── cs_response.txt
│   ├── off_topic.txt
│   ├── ticket_handler.txt
│   └── evaluator.txt
│
├── data/
│   ├── faq.json                     # 常見問題清單(含標準答案核心步驟)
│   ├── kb/                          # 完整 KB 文章資料夾
│   │   ├── kb_001.md
│   │   ├── kb_002.md
│   │   └── ...
│   ├── kb_index.json                # KB 索引(啟動時自動生成)
│   ├── mock_users.json              # 假會員資料
│   └── prototype.db                 # SQLite(自動生成)
│
├── scripts/
│   └── build_kb_index.py            # 一次性腳本:從 kb/ 生成 kb_index.json
│
└── static/
    ├── index.html                   # 用戶對話介面
    └── admin.html                   # 後台工單管理介面
```

---

## State 結構規格

```python
{
    "session_id": "uuid 字串",
    "created_at": "ISO 8601",
    "updated_at": "ISO 8601",
    
    "phase": "對話中 | 等待 Email | 等待工單確認 | 已結束",
    "turn_count": 0,
    
    "user_info": {
        "is_logged_in": False,
        "user_id": None,
        "user_email": None,
        "user_name": None,
        "purchase_history": []          # mock 資料,例如 ["python_basics", "ai_fundamentals"]
    },
    
    "issue_context": {
        "category": None,               # 技術問題 | 課程內容 | 帳務退款 | 帳號登入 | 課程操作 | 其他
        "sub_category": None,
        "summary": None,                # AI 生成的一句話摘要
        "user_emotion": "中性"          # 中性 | 困惑 | 焦慮 | 不滿 | 憤怒
    },
    
    "faq_context": {
        "matched_faq_id": None,         # 命中的 FAQ ID
        "match_confidence": 0.0,        # 0.0-1.0,Haiku 判斷的信心
        "answer_strategy": None         # "faq_template" | "rag" | "off_topic"
    },
    
    "kb_context": {
        "indexed_articles": [],         # 從索引選出的 article_id 列表
        "articles_used_in_response": [] # 實際塞進 prompt 的文章 ID
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
    
    "chat_history": []                  # [{role, content, timestamp, response_type}]
}
```

### State 更新規則

**每輪用戶輸入後,程式自動更新**:
- `turn_count += 1`
- `updated_at` = now()
- `chat_history.append({user message})`

**節點各自負責更新**:
- 路由節點:無更新(只回傳分流結果)
- FAQ 比對節點:更新 `faq_context`
- FAQ 回應 / RAG 解答節點:更新 `kb_context.articles_used_in_response`
- 離題節點:`service_limits.off_topic_count += 1`
- 工單節點:更新 `ticket_state` 與 `phase`
- 評估節點:更新 `issue_context`、`service_limits.low_confidence_count`、`service_limits.unresolved_count`

**限制檢查(每輪結束後)**:
程式檢查 `service_limits` 是否有任何欄位達到上限,有則設定:
- `service_limits.limit_reached = True`
- `service_limits.limit_reached_reason` 填入觸發原因
- 觸發 AI 主動建議建立工單(進入 ticket 流程)

---

## 八個節點的詳細規格

### 節點 1:意圖路由

**檔案**:`nodes/router.py`
**模型**:Haiku
**輸入**:用戶最新訊息、`state.phase`、最近 3 輪 history
**輸出**:單一字元 `A` 或 `C`(不再有 B)

**Prompt**(`prompts/router.txt`):

```
你的任務是分類用戶最新訊息的意圖,只回傳一個英文字母,不要任何其他文字。

當前對話階段:{phase}
最近對話歷史:
{recent_history}

用戶最新訊息:
「{user_message}」

分類選項:

A = 跟客服服務相關的訊息
   包含:描述問題、回答你的提問、補充資訊、確認解答、表達不滿、要求建工單
   即使用戶語氣不耐煩或表達憤怒,只要還在處理服務範圍內,都算 A

C = 跟 HiSKIO 服務無關
   問天氣、聊天、測試 AI、寫程式作業、其他平台問題等

回答:
```

**程式行為**:
- `max_tokens=5`、`temperature=0`
- 取第一個非空白字元
- 不是 A/C 時 fallback 為 A
- 第一輪沒有 history 時,`recent_history` 替換為「(無)」

---

### 節點 2:FAQ 快查比對

**檔案**:`nodes/faq_matcher.py`
**模型**:Haiku
**輸入**:用戶訊息、FAQ 清單(從 `data/faq.json` 載入)
**輸出**:`{"matched_id": "faq_X" | null, "confidence": 0.0-1.0}`

**FAQ JSON 結構**(`data/faq.json`):

```json
[
    {
        "id": "faq_001",
        "category": "技術問題",
        "question_patterns": [
            "影片無法播放",
            "影片打不開",
            "影片卡住",
            "影片黑畫面",
            "看不到影片"
        ],
        "core_steps": [
            "重新整理頁面",
            "清除瀏覽器快取",
            "改用 Chrome 或 Edge 瀏覽器",
            "確認網路連線穩定"
        ],
        "fallback_message": "如以上方法都無效,請提供您使用的瀏覽器版本與課程連結,我們會協助您處理"
    },
    {
        "id": "faq_002",
        "category": "帳務退款",
        "question_patterns": [
            "我想退款",
            "怎麼退款",
            "退款流程",
            "可以退費嗎"
        ],
        "core_steps": [
            "確認購買時間在 7 天內且觀看進度未超過 20%",
            "前往「我的訂單」頁面",
            "點選該訂單的「申請退款」按鈕",
            "填寫退款原因送出",
            "退款處理時間約 3-5 個工作天"
        ],
        "fallback_message": "若不符合退款條件,可建立工單由人工協助評估"
    }
]
```

**Prompt**(`prompts/faq_matcher.txt`):

```
你的任務是比對用戶問題與下面的常見問題清單,找出最相關的一筆。

只回傳合法 JSON,格式如下:
{"matched_id": "faq_001" 或 null, "confidence": 0.0 到 1.0}

判斷標準:
- 信心 >= 0.7:確定命中,直接回傳 ID
- 信心 0.4-0.7:可能相關但不確定,回傳 ID 但信心較低
- 信心 < 0.4:不太相關,matched_id 填 null

# 常見問題清單
{faq_list}

# 用戶問題
「{user_message}」

只輸出 JSON,不要其他文字。
```

**程式行為**:
- `max_tokens=100`、`temperature=0`
- `faq_list` 組裝時只給 id + question_patterns(不給 core_steps,節省 token)
- 解析失敗時回傳 `{"matched_id": null, "confidence": 0.0}`
- **信心 >= 0.7 走 FAQ 回應節點;< 0.7 走 RAG 流程**

---

### 節點 3:FAQ 回應(混合模式)

**檔案**:`nodes/faq_responder.py`
**模型**:Haiku
**輸入**:命中的 FAQ 資料、用戶訊息、`state.user_info`、`state.issue_context.user_emotion`
**輸出**:給用戶的回應

**這個節點的核心邏輯**:核心步驟寫死,LLM 只負責**開場與結尾語氣的潤飾**。

**Prompt**(`prompts/faq_responder.txt`):

```
你是 HiSKIO 客服。請根據下面提供的標準答案核心步驟,生成一段給用戶的自然回應。

# 嚴格規則
1. 中間的「核心步驟」部分必須一字不漏照抄(可以加序號 1. 2. 3.,但內容不能改)
2. 你只能潤飾開場(1 句)和結尾(1 句)
3. 開場要根據用戶情緒做調整:
   - 中性/困惑 → 簡短承接(例:「了解,這邊提供您處理方式」)
   - 焦慮/不滿/憤怒 → 先安撫(例:「了解這讓您困擾,我立即協助您」)
4. 結尾固定加上 fallback_message 的內容,可以稍微改寫語氣讓它自然

# 用戶資料
- 是否登入:{is_logged_in}
- 是否舊客戶:{is_returning_customer}
- 當前情緒:{user_emotion}

# 命中的 FAQ
類別:{faq_category}
核心步驟(請照抄,不要改字):
{core_steps_formatted}
備援訊息:
{fallback_message}

# 用戶問題
「{user_message}」

# 輸出格式範例
[開場 1 句][換行][1. ...步驟一字不漏][2. ...][3. ...][換行][結尾,結合 fallback_message]

直接輸出回應,不要加任何 metadata。
```

**程式行為**:
- `max_tokens=400`、`temperature=0.5`
- core_steps 用程式組成編號清單後塞入 prompt
- 不需要塞 chat_history,因為 FAQ 命中通常是第一輪情境

---

### 節點 4:KB 索引比對(RAG 第一階段)

**檔案**:`nodes/kb_indexer.py`
**模型**:Haiku
**輸入**:用戶訊息、KB 索引清單、最近 3 輪 history
**輸出**:`["kb_001", "kb_005", "kb_018"]`(最多 3 個 article_id)

**KB 索引結構**(`data/kb_index.json`,由 `scripts/build_kb_index.py` 自動生成):

```json
[
    {
        "id": "kb_001",
        "title": "課程影片無法播放完整排查",
        "category": "技術問題",
        "summary": "詳細排查影片播放失敗的所有可能原因,包含瀏覽器、網路、帳號權限",
        "key_questions": [
            "影片完全打不開",
            "影片載入後不能播放",
            "影片只有聲音沒有畫面",
            "特定章節影片打不開"
        ]
    }
]
```

**索引生成腳本**(`scripts/build_kb_index.py`):
- 讀取 `data/kb/*.md` 所有檔案
- 每個檔案前 matter 包含 id、title、category
- 用 Haiku 為每篇生成 summary 和 key_questions
- 輸出到 `data/kb_index.json`
- 這個腳本只跑一次,有新文章才重跑

**KB 文章結構**(`data/kb/kb_001.md`):

```markdown
---
id: kb_001
title: 課程影片無法播放完整排查
category: 技術問題
last_updated: 2025-08-15
---

# 課程影片無法播放完整排查

## 步驟一:基礎排查
首先請嘗試以下動作...

## 步驟二:瀏覽器檢查
...
```

**Prompt**(`prompts/kb_indexer.txt`):

```
你的任務是從 KB 索引清單中,找出最可能解決用戶問題的 3 篇文章。

只回傳合法 JSON 陣列,格式:["kb_001", "kb_005", "kb_018"]
最多 3 篇,如果只有 1 篇相關就回傳 1 篇,沒有相關就回傳空陣列 []。

# KB 索引清單
{kb_index_list}

# 用戶問題
「{user_message}」

# 對話脈絡(供參考)
{recent_history}

只輸出 JSON 陣列,不要其他文字。
```

**程式行為**:
- `max_tokens=100`、`temperature=0`
- `kb_index_list` 組裝時包含每篇的 id、title、summary、key_questions
- 解析失敗或回傳空陣列時,進「無 KB 命中」分支(下個節點會處理)

---

### 節點 5:客服解答(RAG 第二階段)

**檔案**:`nodes/cs_response.py`
**模型**:Sonnet
**輸入**:State 全部、選中的 KB 文章全文(從 `data/kb/*.md` 載入)
**輸出**:給用戶的回應

**Prompt**(`prompts/cs_response.txt`):

```
你是 HiSKIO 線上學習平台的 AI 客服助理。

# 你的身份與原則
- 你代表 HiSKIO 客服團隊,語氣專業、友善、簡潔
- 用繁體中文回應,口吻自然
- 不確定的事情寧可說「我幫您確認」也不要編造
- 只能根據下面提供的 KB 文章回答,不要憑空生成沒有依據的政策或步驟

# 用戶當下狀態
- 是否登入:{is_logged_in}
- 是否舊客戶:{is_returning_customer}
- 已購課程:{purchase_summary}
- 用戶情緒:{user_emotion}
- 對話進行到第 {turn_count} 輪

# 當前問題分類
{category} / {sub_category}
摘要:{summary}

# 可用的 KB 文章
{kb_articles_full_content}

# 對話歷史
{chat_history_recent}

# 用戶最新訊息
「{user_message}」

# 任務
1. 第一輪如果問題還不夠清楚,先用 1-2 句確認問題
2. 如果 KB 有明確解法 → 引用 KB 給出具體步驟,可以說「根據我們的處理流程...」
3. 如果 KB 不夠精確或部分相關 → 給謹慎回應,並表示這個情況可能需要進一步協助
4. 如果用戶情緒是不滿/憤怒 → 先承接情緒再給解答
5. 已經給過 2 次解答用戶仍不滿意 → 在回應中提出「需要為您建立工單由人工協助處理嗎?」
6. 回應 2-4 句,不要長篇大論
7. 不要用 bullet 條列(除非引用 KB 步驟),用自然口吻

只輸出給用戶的訊息,不要 metadata。
```

**特殊輸出格式**:
- 當回應中包含「建議建立工單」的提案時,系統會偵測這個意圖並標記 `state.ticket_state.ticket_suggested = True`
- 偵測方式:在 prompt 末尾要求 LLM 輸出時,如果有提到建單,在訊息開頭加上 `[SUGGEST_TICKET]` 標記;程式偵測到後移除標記、設定 state、前端顯示「建立工單」按鈕

實際 prompt 補充段落:

```
# 特殊標記
如果這個情況你判斷需要建議用戶建工單(例如:用戶問題超出 KB 範圍、已多次解答仍不滿意、明顯需要人工介入的情況),請在你的回應**最前面**加上 [SUGGEST_TICKET] 標記,然後正文。
標記不會被用戶看到,只是給系統的訊號。
```

**程式行為**:
- `max_tokens=600`、`temperature=0.6`
- 解析時偵測 `[SUGGEST_TICKET]` 開頭,有則設定 state、移除標記、前端附上「建立工單」按鈕
- `purchase_summary` 是把 `purchase_history` 轉成自然語言(例:「已購買 3 門課程,包含 Python 基礎、AI 入門」)

---

### 節點 6:離題處理

**檔案**:`nodes/off_topic.py`
**模型**:Haiku
**輸入**:用戶離題訊息、原本的 issue summary
**輸出**:給用戶的回應

**Prompt**(`prompts/off_topic.txt`):

```
你是 HiSKIO 客服,用戶說了跟服務無關的事情。

# 用戶訊息
「{user_message}」

# 用戶原本在處理的問題
{original_issue}

# 離題情況
這是用戶第 {off_topic_count} 次離題。

# 任務
1-2 句簡短回應:
- 第 1 次:幽默或溫和帶過,引導回主題
- 第 2 次:稍微明確一點告知這裡只處理 HiSKIO 服務問題
- 第 3 次以上:語氣要更明確,提示如果不需要客服協助可以結束對話

繁體中文,口吻自然,不要條列。

直接輸出回應。
```

**程式行為**:
- `max_tokens=200`、`temperature=0.6`
- 進入此節點時 `service_limits.off_topic_count += 1`
- 達到 `max_off_topic_count` 時,**下一輪用戶說話如果路由仍判斷 C,直接回固定訊息「對話僅處理 HiSKIO 服務相關問題,如無客服需求請關閉視窗」,不再呼叫 LLM**

---

### 節點 7:工單流程處理

**檔案**:`nodes/ticket_handler.py`
**模型**:Haiku(處理對話流);Sonnet(生成工單摘要)
**輸入**:State 全部
**輸出**:不同階段不同回應

**這個節點是個小型狀態機**,處理工單建立的多階段對話:

```
階段 1:AI 建議建單(由節點 5 觸發 SUGGEST_TICKET 或 service_limits.limit_reached)
   ↓
階段 2:等待用戶決定(回應「好」「不要」)
   ├─ 拒絕 → 結束建單流程,回到對話模式
   └─ 同意
       ↓
階段 3:檢查身分
   ├─ 已登入 → 直接抓 mock_users.json 的 email,跳到階段 5
   └─ 未登入 → 進階段 4
       ↓
階段 4:收集 Email
   - 詢問用戶 Email
   - 用 regex 驗證格式
   - 失敗最多 3 次,失敗後告知「Email 格式有誤,本次工單無法建立,請稍後重試」
   ↓
階段 5:生成工單
   - Sonnet 生成問題摘要
   - 寫入 SQLite tickets 表
   - 回給用戶工單編號與後續說明
   ↓
階段 6:結束
   - state.phase = "已結束"
   - 之後用戶若繼續輸入,固定回「您的工單已建立(#XXX),請耐心等候回覆」
```

**實作關鍵點**:

1. **`state.phase` 用來追蹤工單流程**:
   - `對話中` → 一般狀態
   - `等待工單確認` → 階段 2
   - `等待 Email` → 階段 4
   - `已結束` → 階段 6

2. **路由節點之前先檢查 phase**:orchestrator 看到 `phase == 等待 Email` 時,跳過路由節點,直接呼叫工單節點處理 Email 輸入

3. **Email 驗證 regex**:`^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$`

**Prompt**(`prompts/ticket_handler.txt`,涵蓋多個階段,程式根據 phase 選用):

階段 1 開場(由節點 5 直接生成,這裡只是備案):

```
這個情況看起來需要進一步協助。

請問需要為您建立服務工單,由我們的客服團隊跟進處理嗎?
請回覆「好」或「不用」。
```

階段 4 收集 Email:

```
為了讓客服與您聯繫,請提供您的 Email。
我們會將工單編號與後續處理進度寄送到您填寫的信箱。
```

Email 格式錯誤:

```
您填寫的 Email 格式似乎不太對,請再確認一次。
```

階段 5 工單生成提示(這部分由 Sonnet 生成完整摘要):

```
請根據以下對話歷史,生成工單內容。只回傳合法 JSON:

{
  "summary": "用 1-2 句話描述用戶的問題",
  "category": "技術問題 | 課程內容 | 帳務退款 | 帳號登入 | 課程操作 | 其他",
  "user_emotion_at_close": "中性 | 困惑 | 焦慮 | 不滿 | 憤怒",
  "key_attempts": "之前 AI 嘗試過的解答(1-2 句總結)"
}

# 對話歷史
{full_chat_history}

只輸出 JSON。
```

階段 5 給用戶的回覆訊息(寫死,不用 LLM):

```
您的工單已建立,工單編號為 #{ticket_id}。
我們的客服團隊會在 1-2 個工作日內透過 Email 與您聯繫。
若您還有其他問題,可以重新開啟對話。感謝您的耐心。
```

---

### 節點 8:背景評估

**檔案**:`nodes/evaluator.py`
**模型**:Haiku
**輸入**:這輪的 user_message + ai_response
**輸出**:JSON,程式拿來更新 State

**Prompt**(`prompts/evaluator.txt`):

```
分析這一輪對話,提煉結構化資訊。只回傳合法 JSON,不要其他文字。

# 對話資料
用戶訊息:「{user_message}」
AI 回應:「{ai_response}」
之前的問題分類:{previous_category}
對話總輪數:{turn_count}

# 輸出格式
{
  "issue_category": "技術問題 | 課程內容 | 帳務退款 | 帳號登入 | 課程操作 | 其他",
  "issue_sub_category": "更細的分類例如『無法觀看影片』",
  "issue_summary": "一句話總結",
  "user_emotion": "中性 | 困惑 | 焦慮 | 不滿 | 憤怒",
  "user_satisfied_with_answer": true/false/null,
  "ai_confidence_in_answer": 0.0-1.0,
  "user_explicitly_wants_ticket": true/false
}

只輸出 JSON。
```

**程式行為**:
- 解析失敗 log error,跳過 State 更新
- 解析成功後:

```python
state.issue_context.category = result["issue_category"]
state.issue_context.sub_category = result["issue_sub_category"]
state.issue_context.summary = result["issue_summary"]
state.issue_context.user_emotion = result["user_emotion"]

if result["ai_confidence_in_answer"] < 0.4:
    state.service_limits.low_confidence_count += 1

if result["user_satisfied_with_answer"] is False:
    state.service_limits.unresolved_count += 1

if result["user_explicitly_wants_ticket"]:
    state.ticket_state.user_decision = "accepted"
    # 觸發進入工單流程
```

---

## Orchestrator 流程

**檔案**:`core/orchestrator.py`

主流程虛擬碼:

```python
def handle_user_message(session_id: str, user_message: str) -> dict:
    state = load_state(session_id)
    state.chat_history.append({"role": "user", "content": user_message, "timestamp": now()})
    
    # 0. 特殊 phase 直接處理(工單流程中)
    if state.phase == "等待工單確認":
        return handle_ticket_confirmation(state, user_message)
    if state.phase == "等待 Email":
        return handle_email_input(state, user_message)
    if state.phase == "已結束":
        return {"ai_response": "您的工單已建立(#" + state.ticket_state.ticket_id + "),請耐心等候回覆", ...}
    
    # 1. 檢查服務限制是否已達上限
    if state.service_limits.limit_reached:
        return suggest_ticket_due_to_limit(state)
    
    # 2. 意圖路由
    intent = router_node(state, user_message)  # A or C
    
    if intent == "C":
        # 離題處理
        if state.service_limits.off_topic_count >= state.service_limits.max_off_topic_count:
            ai_response = "對話僅處理 HiSKIO 服務相關問題,如無客服需求請關閉視窗"
            response_type = "off_topic_blocked"
        else:
            ai_response = off_topic_node(state, user_message)
            state.service_limits.off_topic_count += 1
            response_type = "off_topic"
    
    elif intent == "A":
        # 3. FAQ 快查
        faq_result = faq_matcher_node(state, user_message)
        state.faq_context.matched_faq_id = faq_result["matched_id"]
        state.faq_context.match_confidence = faq_result["confidence"]
        
        if faq_result["matched_id"] and faq_result["confidence"] >= 0.7:
            # FAQ 命中
            faq_data = load_faq(faq_result["matched_id"])
            ai_response = faq_responder_node(state, faq_data, user_message)
            state.faq_context.answer_strategy = "faq_template"
            response_type = "faq"
        else:
            # FAQ 沒命中,進 RAG
            kb_ids = kb_indexer_node(state, user_message)
            state.kb_context.indexed_articles = kb_ids
            
            kb_articles = [load_kb_article(kid) for kid in kb_ids]
            ai_response = cs_response_node(state, kb_articles, user_message)
            state.kb_context.articles_used_in_response = kb_ids
            state.faq_context.answer_strategy = "rag"
            
            # 偵測 SUGGEST_TICKET 標記
            if ai_response.startswith("[SUGGEST_TICKET]"):
                ai_response = ai_response.replace("[SUGGEST_TICKET]", "").strip()
                state.ticket_state.ticket_suggested = True
                state.phase = "等待工單確認"
            
            response_type = "rag"
    
    # 4. 寫入 history
    state.chat_history.append({"role": "assistant", "content": ai_response, "timestamp": now(), "response_type": response_type})
    
    # 5. 背景評估(只在 intent A 且非工單流程時跑)
    if intent == "A" and state.phase == "對話中":
        evaluator_node(state, user_message, ai_response)
    
    # 6. 檢查限制是否達標
    check_and_update_limits(state)
    
    # 7. 更新基本欄位
    state.turn_count += 1
    state.updated_at = now()
    save_state(state)
    
    return {
        "ai_response": ai_response,
        "response_type": response_type,
        "show_ticket_button": state.ticket_state.ticket_suggested,
        "state": state.to_dict()
    }


def check_and_update_limits(state):
    sl = state.service_limits
    
    if state.turn_count >= sl.max_turns_per_session:
        sl.limit_reached = True
        sl.limit_reached_reason = "turn_max"
    elif sl.off_topic_count >= sl.max_off_topic_count:
        sl.limit_reached = True
        sl.limit_reached_reason = "off_topic_max"
    elif sl.low_confidence_count >= sl.max_low_confidence_count:
        sl.limit_reached = True
        sl.limit_reached_reason = "low_confidence_max"
    elif sl.unresolved_count >= sl.max_unresolved_count:
        sl.limit_reached = True
        sl.limit_reached_reason = "unresolved_max"
```

---

## 工單管理(`core/ticket.py`)

### SQLite 表結構

```sql
CREATE TABLE tickets (
    ticket_id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id TEXT NOT NULL,
    user_email TEXT NOT NULL,
    user_id TEXT,                          -- 會員 ID,訪客為 NULL
    is_member BOOLEAN NOT NULL,
    issue_category TEXT,
    issue_summary TEXT NOT NULL,
    user_emotion_at_close TEXT,
    key_attempts TEXT,
    full_chat_history TEXT NOT NULL,       -- 完整對話 JSON 字串
    status TEXT DEFAULT 'open',            -- open | in_progress | closed
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

### 工單建立流程

```python
def create_ticket(state) -> int:
    # 用 Sonnet 生成摘要
    summary_data = generate_ticket_summary(state)
    
    ticket_id = db.execute("""
        INSERT INTO tickets (session_id, user_email, user_id, is_member,
            issue_category, issue_summary, user_emotion_at_close, key_attempts,
            full_chat_history, status, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, ?)
    """, (...))
    
    return ticket_id
```

---

## API 端點規格

### 用戶對話 API

**`POST /api/session/new`**

Request:
```json
{
    "is_logged_in": true | false,
    "user_id": "user_123" | null
}
```

Response:
```json
{
    "session_id": "uuid",
    "state": { ...完整 state... }
}
```

如果 `is_logged_in: true`,程式從 `data/mock_users.json` 載入該 user_id 的資料填入 `user_info`。

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
    "response_type": "faq | rag | off_topic | off_topic_blocked | ticket_flow | session_ended",
    "show_ticket_button": true | false,
    "ticket_id": 123 | null,
    "state": { ...完整 state... }
}
```

**`POST /api/ticket/create`**(用戶按下「建立工單」按鈕時)

Request:
```json
{ "session_id": "uuid" }
```

Response:
```json
{
    "phase_changed_to": "等待工單確認" | "等待 Email" | "已結束",
    "ai_response": "下一步指示文字",
    "ticket_id": 123 | null
}
```

### 後台 API

**`GET /api/admin/tickets`**

Response:
```json
{
    "tickets": [
        {
            "ticket_id": 1,
            "session_id": "...",
            "user_email": "...",
            "is_member": true,
            "issue_category": "技術問題",
            "issue_summary": "用戶反映影片無法播放...",
            "status": "open",
            "created_at": "..."
        }
    ]
}
```

**`GET /api/admin/tickets/{ticket_id}`**

Response: 完整工單資料,包含 full_chat_history。

**`POST /api/admin/tickets/{ticket_id}/status`**

Request:
```json
{ "status": "in_progress" | "closed" }
```

---

## 前端介面規格

### 用戶對話介面(`static/index.html`)

新建 session 前,先選擇身分:

```
┌─────────────────────────────────────┐
│  HiSKIO AI 客服雛形                  │
│                                     │
│  請選擇身分:                         │
│  ┌─────────┐  ┌─────────────┐       │
│  │ 訪客模式 │  │ 模擬會員登入 │       │
│  └─────────┘  └─────────────┘       │
│                                     │
│  (模擬會員登入會跳出選單,從 mock      │
│   資料挑一個假會員)                   │
└─────────────────────────────────────┘
```

進入對話後:

```
┌──────────────────────────────────────────────────────────────┐
│  HiSKIO AI 客服  [會員: 王小明]              [新對話] [後台]   │
├───────────────────────────────────────┬──────────────────────┤
│                                       │                      │
│  對話區(60%)                         │  State 除錯(40%)    │
│                                       │                      │
│  ┌───────────────┐                    │  phase: 對話中        │
│  │ AI: 您好...   │                    │  turn_count: 3       │
│  └───────────────┘                    │  faq_match: ...      │
│         ┌──────────────┐              │  service_limits:     │
│         │ 用戶: ...    │              │    off_topic: 0      │
│         └──────────────┘              │    unresolved: 0     │
│                                       │  ...                 │
│  ┌───────────────────────┐            │                      │
│  │ AI: 根據處理流程...   │            │  (完整 JSON)         │
│  │                       │            │                      │
│  │ [建立工單] ←按鈕      │            │                      │
│  └───────────────────────┘            │                      │
│                                       │                      │
├───────────────────────────────────────┤                      │
│  [輸入框]                    [送出]   │                      │
└───────────────────────────────────────┴──────────────────────┘
```

**訊息樣式區分**:
- `faq`:綠色左邊框
- `rag`:藍色左邊框
- `off_topic`:橘色左邊框
- `off_topic_blocked`:灰色左邊框,字體稍小
- `ticket_flow`:紫色左邊框
- `session_ended`:紅色左邊框

**「建立工單」按鈕**:當 `show_ticket_button: true` 時,在訊息下方顯示按鈕。按下後呼叫 `/api/ticket/create`,進入工單流程。

### 後台介面(`static/admin.html`)

```
┌─────────────────────────────────────────────────────────────┐
│  工單管理後台                          [返回對話介面]        │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  篩選:[全部] [Open] [In Progress] [Closed]                  │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐    │
│  │ #5  [技術問題]  王小明(會員)        2025-11-01     │    │
│  │     用戶反映影片載入後無法播放,已嘗試重新整理...    │    │
│  │     [Open]  [標記處理中] [標記結案] [查看完整對話]  │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐    │
│  │ #4  [帳務退款]  訪客(abc@gmail.com)    2025-10-30  │    │
│  │     ...                                             │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

點「查看完整對話」展開 modal 顯示完整 chat_history。

---

## Mock 會員資料

**`data/mock_users.json`**:

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

(用戶 003 沒買過課程,可以拿來測試「會員但沒買課」的情境)

---

## FAQ 與 KB 內容(雛形階段)

雛形階段先做以下範圍。Claude Code 開發時,**`data/faq.json` 與 `data/kb/*.md` 的實際內容由你提供或我之後另外給**,規格只規範格式。

**FAQ 至少包含 5-8 筆**,涵蓋:
- 影片無法播放(技術問題)
- 退款流程(帳務退款)
- 忘記密碼(帳號登入)
- 觀看進度同步問題(技術問題)
- 開立發票(帳務退款)

**KB 雛形階段先放 8-12 篇**,涵蓋上述類別的延伸問題。

---

## 開發順序

### Phase 1:核心對話打通(預計 1.5 天)
1. 建立專案結構、環境設定、SQLite schema
2. 實作 `core/state.py`、`core/llm_client.py`
3. 實作節點 1(路由)、節點 5(RAG 解答)、節點 8(評估)
4. 實作 orchestrator 最小版本(只走 A/C 路由 + RAG)
5. 實作 KB 索引腳本 `scripts/build_kb_index.py` 與節點 4(KB 索引)
6. 實作 `app.py` 基本 API 端點與前端對話介面

**Phase 1 驗收**:會員/訪客可以對話,AI 用 KB 索引找文章回答,State 有變化。

### Phase 2:加上 FAQ 快查(預計 0.5 天)
1. 準備 `data/faq.json` 範例
2. 實作節點 2(FAQ 比對)、節點 3(FAQ 回應)
3. orchestrator 加入 FAQ 分支
4. 前端區分不同 response_type 的樣式

**Phase 2 驗收**:用戶問常見問題會走 FAQ 標準答案、長尾問題走 RAG。

### Phase 3:加上離題與服務限制(預計 0.5 天)
1. 實作節點 6(離題)
2. orchestrator 加入限制檢查 `check_and_update_limits`
3. 達到限制時觸發工單建議

**Phase 3 驗收**:用戶連續離題會被擋、達到輪數上限會建議建單。

### Phase 4:工單流程(預計 1 天)
1. 實作節點 7(工單流程,多階段狀態機)
2. 實作 `core/ticket.py` 工單管理
3. 實作 `/api/ticket/create` 端點
4. 前端加入「建立工單」按鈕與 Email 輸入流程

**Phase 4 驗收**:訪客可以留 Email 建工單,會員自動帶入 Email 建工單,DB 有資料。

### Phase 5:後台介面(預計 0.5 天)
1. 實作 admin API
2. 實作 `static/admin.html`(極簡列表 + modal 查看)

**Phase 5 驗收**:能在後台看到所有工單,能標記狀態,能展開看完整對話。

---

## 測試用例

完整跑通後測這 8 個對話:

**T1 FAQ 命中**
```
身分:訪客
輪 1:我的影片不能播放
預期:走 FAQ,response_type=faq,給出標準步驟
```

**T2 RAG 走查**
```
身分:會員(user_001,有買 python_basics)
輪 1:我的 python 課程作業繳交按鈕跑掉了
預期:FAQ 不命中,走 RAG,KB 索引選相關技術文章
```

**T3 多輪追問**
```
輪 1:我想退款
輪 2:就是上週買的那堂 Python 課
輪 3:好的請幫我處理
預期:第 1 輪 FAQ 命中、後續輪追問走 RAG,issue_summary 逐輪變精準
```

**T4 離題逐次警告**
```
輪 1:影片不能看
輪 2:你今天午餐吃什麼?
輪 3:那推薦我餐廳
輪 4:再說一個笑話
預期:輪 2/3 走離題節點,輪 4 觸發限制(off_topic_count=3),AI 建議建單
```

**T5 用戶主動要求建工單**
```
輪 1:我有個複雜問題,直接幫我建工單吧
預期:評估節點偵測 user_explicitly_wants_ticket,進入工單流程
```

**T6 訪客建單流程**
```
身分:訪客
觸發建單後:
- AI 詢問是否建單 → 用戶說「好」
- AI 詢問 Email → 用戶輸入「abc」(格式錯)
- AI 提示格式錯誤 → 用戶輸入「abc@gmail.com」
- AI 建立工單,顯示工單編號
預期:DB tickets 表有一筆新資料,user_email=abc@gmail.com,is_member=false
```

**T7 會員建單流程**
```
身分:會員(user_001)
觸發建單 → 用戶確認 → AI 直接抓 wang@example.com 建單
預期:DB 有資料,user_email=wang@example.com,is_member=true
```

**T8 三次解答不滿意自動建議**
```
輪 1-3:用戶反覆抱怨同一問題,每輪評估都是 user_satisfied=false
預期:第 3 輪後 unresolved_count=3,觸發 limit_reached,AI 建議建單
```

---

## 已知限制(雛形階段不處理)

1. KB 索引用 LLM 不用 embedding,在 100+ 篇規模會 token 爆掉
2. 沒有真的寄信通知工單建立
3. 後台沒有編輯工單內容、回覆用戶的功能
4. State 沒做 schema migration
5. 沒有 streaming(回應一次性吐出)
6. 沒有錯誤恢復機制
7. mock_users 是寫死的,沒有真的會員系統

---

## 給 Claude Code 的開發提示

```
請閱讀附上的規格書,先告訴我你對整體架構的理解,並指出規格中
任何不清楚或可能有問題的地方,等我確認後再開始實作。

實作時請按 Phase 1 → 5 順序進行,每個 Phase 完成後讓我測試,
確認沒問題再進下一個 Phase。

寫程式時:
- 用 Python type hints
- 每個節點函式都要有 docstring 說明輸入輸出
- LLM 呼叫要 try/except,失敗時要 log 並 fallback
- orchestrator 的分流判斷加上詳細註解
- prompt 模板存在 prompts/ 資料夾,程式從檔案讀取(不要寫死在程式裡)
```

