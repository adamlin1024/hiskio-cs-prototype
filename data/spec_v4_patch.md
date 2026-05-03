# 規格 Patch — v3 → v4 改動說明

## 給 Claude Code 的話

這份 patch 描述對既有實作(已完成 Phase 1-5 的 v3 規格)的改動。**不要重做整份系統**,只針對下面標示的部分做修改。

改動完成後,**請在每個 Phase 完成時主動告訴我,等我手動測試確認後再進下一個 Phase**,不要一次改完才回報。如果有任何規格不清楚的地方,先問我再改,不要自己猜。

---

## 改動總覽

這次改動有 4 個重點:

1. **合併「打招呼判斷」與「清晰度檢查」進入新的「入口分類節點」**(取代既有的意圖路由節點)
2. **新增「意圖明確度判斷節點」**:處理「付費遇到退費」這類訊息有多個關鍵詞時的判斷
3. **新增「承認不知道節點」**:當 KB 索引完全空陣列時觸發,不再讓 Sonnet 硬答
4. **State 結構新增 `intent_state` 區塊**,移除既有的 `clarity_state`(若你之前有實作)

---

## 改動 1:新增「入口分類節點」(取代意圖路由節點)

### 背景

既有的「意圖路由節點」只能判斷 A(客服)/ C(離題)。**真實對話中用戶第一句通常是「你好」「在嗎」**,這類訊息既不算離題、也不算明確的客服問題,會被誤判進客服流程,浪費 token。

加上之前討論過的「語意清晰度檢查」,如果跟意圖路由分成兩個獨立節點,會多一次 LLM call 和延遲。**合併成一個入口分類節點最划算**。

### 檔案位置

- 新增:`nodes/entry_classifier.py`(取代 `nodes/router.py`)
- 新增:`prompts/entry_classifier.txt`(取代 `prompts/router.txt`)
- **保留 router.py 但不再使用**(暫時不刪,確認 patch 沒問題後再刪除)

### 規格

**模型**:Haiku
**輸入**:用戶最新訊息、`state.phase`、最近 3 輪 chat_history、`state.intent_state.consecutive_unclear_count`
**輸出**:單一字串,4 種分類之一

```
greeting          # 打招呼、試探語(「你好」「在嗎」「Hello」)
unclear           # 訊息模糊、亂碼、或無實質內容
off_topic         # 跟 HiSKIO 服務無關
customer_service  # 真的是客服問題
```

### Prompt 模板

`prompts/entry_classifier.txt`:

```
你的任務是分類用戶最新訊息的類型,只回傳一個英文單字,不要任何其他文字。

當前對話階段:{phase}
最近對話歷史:
{recent_history}
連續不清楚計數:{consecutive_unclear_count}

用戶最新訊息:
「{user_message}」

分類選項(請寬鬆判斷,只要能猜到大方向就算 customer_service):

greeting = 打招呼或試探語
   特徵:訊息很短、純粹問候、沒有實質問題
   例:「你好」、「Hi」、「在嗎」、「Hello」、「您好」、「嗨」
   注意:如果包含實質問題(例「你好,我影片不能看」),不算 greeting,算 customer_service

unclear = 訊息模糊或亂碼
   特徵:看不出用戶要表達什麼、或是亂打字、或太籠統
   例:「我有問題」、「幫我看一下」、「不行」、「dlsjfkdsf」、「。。。」、「??」
   注意:如果能猜到方向(例「我影片不能看」),不算 unclear,算 customer_service

off_topic = 跟 HiSKIO 服務無關
   特徵:語意清楚但跟客服無關
   例:「你今天午餐吃什麼」、「推薦我餐廳」、「寫程式作業」、「天氣」

customer_service = 跟 HiSKIO 服務有關的問題
   特徵:能看出跟學習平台某個面向有關(影片、購買、退款、帳號、課程內容...)
   例:「影片不能看」、「想退款」、「忘記密碼」、「課程內容適合新手嗎」

只回傳 greeting / unclear / off_topic / customer_service,不要其他文字。
```

### 程式行為

- `max_tokens=10`、`temperature=0`
- 取回傳的第一個有效 token,trim 後比對
- 不是上述 4 種時,**fallback 為 customer_service**(寧可走完整流程,不要誤擋)
- 第一輪沒 history 時,`recent_history` 替換為「(無)」

### Orchestrator 改動

`core/orchestrator.py` 的 `handle_user_message` 函式中,**把原本呼叫 `router_node` 的位置,改為呼叫 `entry_classifier_node`**,並依照 4 種分類做不同處理(下面會詳述)。

---

## 改動 2:greeting 分支的處理

### 行為規格

當 entry_classifier 回傳 `greeting`:

1. **不呼叫任何後續節點**(不走 FAQ、不走 KB)
2. **回應由 Haiku 生成**(不寫死,因為要根據用戶身分動態調整)
3. **`turn_count` 不增加**(這不算正式對話輪次)
4. **`chat_history` 仍然要記錄**(讓下一輪能看到上下文)
5. `response_type` 標記為 `greeting`
6. **不更新 `issue_context`**(這不是問題,沒東西好提煉)

### 新增節點

`nodes/greeting_handler.py`,prompt:

```
你是 HiSKIO 線上學習平台的 AI 客服。用戶剛才打了招呼。

用戶身分:
- 是否登入:{is_logged_in}
- 用戶名稱:{user_name_or_default}
- 是否舊客戶:{is_returning_customer}

用戶訊息:「{user_message}」

請生成一段簡短回應(1-2 句),包含:
1. 簡短的回應問候
2. 主動詢問用戶有什麼需要協助的

風格規則:
- 繁體中文,口吻自然
- 已登入用戶可以稱呼名字(若不為訪客)
- 訪客就用「您好」開頭
- 不要過度客套
- 絕對不要條列

直接輸出回應,不要 metadata。
```

`max_tokens=120`、`temperature=0.5`

### 前端

新增訊息類型 `greeting`,使用**淺藍色邊框**(跟 `rag` 的藍色區分,可以淺一點或加虛線)。

---

## 改動 3:unclear 分支的處理

### 行為規格

當 entry_classifier 回傳 `unclear`:

1. `state.intent_state.consecutive_unclear_count += 1`
2. 若 `consecutive_unclear_count <= 2`,呼叫釐清節點
3. 若 `consecutive_unclear_count >= 3`,**強制觸發建單流程**,不再嘗試釐清
4. **任何不為 unclear 的訊息進來時,把 `consecutive_unclear_count` 重置為 0**
5. `turn_count` **照常 +1**(這算正式對話輪次,只是用戶沒講清楚)
6. `response_type` 標記為 `clarification`

### 新增節點

`nodes/clarification_handler.py`

**第 1 次釐清**(consecutive_unclear_count == 1)用 Haiku 生成,prompt:

```
你是 HiSKIO 客服,用戶的訊息我們無法理解他想問什麼。請用溫和友善的方式請他補充。

用戶訊息:「{user_message}」

請生成 1-2 句回應:
1. 不要顯得不耐煩或質疑用戶
2. 引導用戶具體描述,可以給一些大方向作為提示
   (例如「您是想詢問課程觀看、付款、帳號相關的問題嗎?」)
3. 繁體中文、自然口吻

直接輸出回應。
```

**第 2 次釐清**(consecutive_unclear_count == 2)用 Haiku 生成,prompt:

```
用戶連續第二次訊息我們仍然無法理解。請給更明確的選項。

用戶訊息:「{user_message}」

請生成回應:
1. 表達理解(可能用戶不知道怎麼描述)
2. 直接列出 3-4 個常見問題類別,讓用戶用編號選擇
   例如:
   1. 課程觀看問題(影片無法播放、字幕、進度)
   2. 帳務問題(退款、發票、付款)
   3. 帳號問題(登入、密碼、Email)
   4. 其他
3. 邀請用戶回覆數字或描述
4. 繁體中文

可以使用條列式編號(這是少數例外)。
```

**第 3 次達標**:**不呼叫 LLM**,直接觸發建單流程,給用戶固定訊息:

```
看起來這個問題比較複雜,建議由人工客服協助處理會更有效率。
我為您建立工單,客服團隊會主動聯繫您。
```

(然後直接進工單流程,phase = "等待工單確認")

### State 改動需求

新增 `intent_state` 區塊(下面詳述完整 schema)。

### 前端

新增訊息類型 `clarification`,使用**橘黃色邊框**(跟離題的橘色區分,偏黃)。

---

## 改動 4:off_topic 分支(行為不變)

當 entry_classifier 回傳 `off_topic`:**沿用原本的離題處理邏輯**,不需要改動。

---

## 改動 5:新增「意圖明確度判斷節點」

### 背景

只有在 entry_classifier 回傳 `customer_service` 後才會走到這個節點。

真實場景:
- 「我付費後想退費」→ 看起來有「付費」和「退費」兩個關鍵詞,但其實主意圖是退費
- 「我影片不能看,還有發票問題」→ 真的有兩個獨立問題,需要請用戶選一個

這個節點負責區分這兩種情況。

### 檔案位置

- 新增:`nodes/intent_clarity.py`
- 新增:`prompts/intent_clarity.txt`

### 規格

**模型**:Haiku
**輸入**:用戶訊息、最近 3 輪 history
**輸出**:JSON

```json
{
  "clarity": "simple | ambiguous_subordinate | parallel_multiple",
  "primary_intent": "用一句話描述用戶的主要意圖",
  "secondary_intents": ["如果有並列的次要意圖,列在這裡"],
  "needs_user_selection": true/false
}
```

### Prompt 模板

`prompts/intent_clarity.txt`:

```
分析用戶的訊息,判斷意圖的明確度。只回傳合法 JSON,不要其他文字。

用戶訊息:「{user_message}」
最近對話歷史:
{recent_history}

# 判斷標準

simple = 單一意圖,沒有歧義
   例:「我想退款」、「影片不能播放」、「忘記密碼怎麼辦」
   primary_intent 填單一意圖,secondary_intents 填空陣列

ambiguous_subordinate = 訊息中有多個關鍵詞,但有主從關係
   例:「我付費後想退費」(主=退費, 從=付費的脈絡)
   例:「買了課程後想取消訂閱」(主=取消訂閱, 從=買了課程是脈絡)
   你能判斷出主意圖,直接填 primary_intent
   needs_user_selection = false

parallel_multiple = 兩個或以上獨立的問題,沒有主從關係
   例:「我影片不能看,還有發票問題」(影片問題和發票問題各自獨立)
   例:「想退款,順便問一下怎麼改 Email」
   primary_intent 填第一個提到的意圖,secondary_intents 列出其他
   needs_user_selection = true

# 輸出格式

{
  "clarity": "simple" 或 "ambiguous_subordinate" 或 "parallel_multiple",
  "primary_intent": "簡短描述主意圖",
  "secondary_intents": ["次意圖 1", "次意圖 2"],
  "needs_user_selection": true 或 false
}

只輸出 JSON,不要其他文字。
```

### 程式行為

- `max_tokens=200`、`temperature=0`
- 解析失敗時 fallback 為 `simple`,primary_intent = 用戶原訊息
- 解析成功後更新 State

### 三種 clarity 的後續處理

```python
if clarity == "simple":
    # 直接走 FAQ → KB,行為不變
    pass

elif clarity == "ambiguous_subordinate":
    # 把 primary_intent 當作有效訊息,走 FAQ → KB
    # 在 FAQ 比對和 KB 索引時,使用 primary_intent 而非原訊息
    effective_message = state.intent_state.primary_intent
    
elif clarity == "parallel_multiple":
    # 不走 FAQ/KB,呼叫「多重意圖選項節點」
    # 該節點生成回應讓用戶從選項中挑一個
    pass
```

### 新增節點:多重意圖選項節點

`nodes/intent_selector.py`

當 `clarity == "parallel_multiple"` 時呼叫,prompt:

```
用戶剛才同時提到了多個獨立的問題。請生成回應,讓用戶選擇要先處理哪一個。

用戶訊息:「{user_message}」
偵測到的意圖:
- 主意圖:{primary_intent}
- 次意圖:{secondary_intents}

請生成回應:
1. 自然地承接用戶的訊息(例如「了解您同時有幾個問題」)
2. 列出每個意圖,用編號讓用戶選
3. 詢問用戶想先處理哪一個
4. 表示其他問題稍後可以再處理

格式可以使用編號條列(這是例外)。
繁體中文,口吻自然。

直接輸出回應。
```

`max_tokens=200`、`temperature=0.5`

### State 改動

呼叫多重意圖選項節點後:
- `state.phase = "等待用戶選擇意圖"`
- `state.intent_state.awaiting_selection = True`

下一輪用戶輸入時:
- 在 orchestrator 開頭檢查 phase,若為「等待用戶選擇意圖」,**跳過入口分類節點**
- 把用戶這輪訊息(可能是「1」或「我想先處理影片」)當成意圖選擇
- 用 Haiku 簡單判斷對應到哪個 `secondary_intents` 或 `primary_intent`
- 把選中的意圖當作有效訊息,走 FAQ/KB

### 前端

新增訊息類型 `intent_selection`,使用**深紫色邊框**(跟工單流程的紫色區分,可以更深)。

---

## 改動 6:新增「承認不知道節點」

### 背景

既有 KB 索引節點若回傳空陣列,**目前會被傳到 cs_response 節點讓 Sonnet 硬答**。Sonnet 看到沒有 KB 文章,會嘗試「禮貌地不回答」,但回答品質不穩定,而且浪費 Sonnet 的 token。

正確處理應該是:KB 索引空陣列 → 直接觸發承認不知道 + 建單建議。

### 檔案位置

- 新增:`nodes/no_kb_handler.py`
- 新增:`prompts/no_kb_handler.txt`

### 規格

**模型**:Haiku
**觸發條件**:`state.kb_context.indexed_articles` 為空陣列(KB 索引完全沒選到任何文章)
**輸入**:用戶訊息、`state.user_info`
**輸出**:給用戶的回應

### Prompt 模板

`prompts/no_kb_handler.txt`:

```
你是 HiSKIO 客服。用戶問了一個我們知識庫中沒有相關資訊的問題。

用戶訊息:「{user_message}」
用戶身分:{is_logged_in_text}

請生成回應(2-3 句):
1. 坦白告知這個問題我們的知識庫沒有對應資訊(不要編造答案)
2. 主動建議建立工單,由人工客服協助
3. 簡短說明工單會由客服團隊跟進

風格:
- 繁體中文,語氣專業且誠懇
- 不要找理由(像「可能是因為...」),直接承認
- 不要條列

直接輸出回應。
```

`max_tokens=200`、`temperature=0.4`

### 程式行為

呼叫此節點後:
- `state.escalation_signals.no_kb_match = True`(新增此欄位,讓建單原因可追蹤)
- `state.ticket_state.ticket_suggested = True`
- `state.phase = "等待工單確認"`
- `response_type = "no_kb_match"`
- 前端顯示「建立工單」按鈕

### Orchestrator 改動

在 KB 索引節點後:

```python
kb_ids = kb_indexer_node(state, effective_message)
state.kb_context.indexed_articles = kb_ids

if len(kb_ids) == 0:
    # 改動:KB 索引空陣列,改呼叫 no_kb_handler 而不是 cs_response
    ai_response = no_kb_handler_node(state, effective_message)
    state.escalation_signals.no_kb_match = True
    state.ticket_state.ticket_suggested = True
    state.phase = "等待工單確認"
    response_type = "no_kb_match"
else:
    # 既有邏輯不變
    kb_articles = [load_kb_article(kid) for kid in kb_ids]
    ai_response = cs_response_node(state, kb_articles, effective_message)
    response_type = "rag"
```

### 前端

新增訊息類型 `no_kb_match`,使用**粉紅色邊框**(跟工單流程的紫色區分)。

---

## State 結構改動

### 新增 `intent_state` 區塊

加在 State 既有結構中:

```python
"intent_state": {
    "input_classification": None,     # greeting | unclear | off_topic | customer_service | None
    "consecutive_unclear_count": 0,
    "max_unclear_count": 2,
    
    "intent_clarity": None,           # simple | ambiguous_subordinate | parallel_multiple | None
    "primary_intent": None,           # 主意圖文字
    "secondary_intents": [],          # 次意圖陣列
    "awaiting_selection": False       # 是否在等用戶從多重意圖中選擇
}
```

### 修改 `escalation_signals` 區塊

新增欄位 `no_kb_match`:

```python
"escalation_signals": {
    "user_explicitly_requested_human": False,
    "ai_low_confidence_count": 0,
    "off_topic_count": 0,
    "issue_complexity_high": False,
    "user_anger_threshold_hit": False,
    "no_kb_match": False              # 新增
}
```

### 移除欄位(若你 Phase 4 有實作清晰度檢查)

如果你之前的版本有 `clarity_state` 區塊,**整個移除**,因為功能已經被 `intent_state.consecutive_unclear_count` 取代。

### State 預設值

新建 session 時,`intent_state` 所有欄位用上述預設值初始化。

---

## Orchestrator 主流程虛擬碼

`core/orchestrator.py` 的 `handle_user_message` 函式,改動後的虛擬碼:

```python
def handle_user_message(session_id: str, user_message: str) -> dict:
    state = load_state(session_id)
    state.chat_history.append({"role": "user", "content": user_message, "timestamp": now()})
    
    # 0. 特殊 phase 處理(維持原有邏輯)
    if state.phase == "等待工單確認":
        return handle_ticket_confirmation(state, user_message)
    if state.phase == "等待 Email":
        return handle_email_input(state, user_message)
    if state.phase == "已結束":
        return {"ai_response": "您的工單已建立...", ...}
    
    # 0.1 新增:處理「等待用戶選擇意圖」
    if state.phase == "等待用戶選擇意圖":
        return handle_intent_selection(state, user_message)
    
    # 1. 服務限制檢查(維持原有)
    if state.service_limits.limit_reached:
        return suggest_ticket_due_to_limit(state)
    
    # 2. 改動:呼叫入口分類節點(取代原本的路由節點)
    classification = entry_classifier_node(state, user_message)
    state.intent_state.input_classification = classification
    
    # 3. 根據分類分流
    if classification == "greeting":
        ai_response = greeting_handler_node(state, user_message)
        response_type = "greeting"
        # 重要:不增加 turn_count
        state.intent_state.consecutive_unclear_count = 0  # 重置
        # 跳過評估節點
        
    elif classification == "unclear":
        state.intent_state.consecutive_unclear_count += 1
        
        if state.intent_state.consecutive_unclear_count >= 3:
            # 連續 3 次不清楚,強制建單
            ai_response = "看起來這個問題比較複雜,建議由人工客服協助..."
            state.ticket_state.ticket_suggested = True
            state.phase = "等待工單確認"
            response_type = "force_escalation"
        else:
            ai_response = clarification_handler_node(state, user_message)
            response_type = "clarification"
        
        state.turn_count += 1
        # 跳過評估節點
        
    elif classification == "off_topic":
        # 既有邏輯,沿用
        if state.service_limits.off_topic_count >= state.service_limits.max_off_topic_count:
            ai_response = "對話僅處理 HiSKIO 服務相關問題,如無客服需求請關閉視窗"
            response_type = "off_topic_blocked"
        else:
            ai_response = off_topic_node(state, user_message)
            state.service_limits.off_topic_count += 1
            response_type = "off_topic"
        
        state.intent_state.consecutive_unclear_count = 0  # 重置
        state.turn_count += 1
        
    elif classification == "customer_service":
        state.intent_state.consecutive_unclear_count = 0  # 重置
        
        # 4. 新增:意圖明確度判斷
        intent_result = intent_clarity_node(state, user_message)
        state.intent_state.intent_clarity = intent_result["clarity"]
        state.intent_state.primary_intent = intent_result["primary_intent"]
        state.intent_state.secondary_intents = intent_result["secondary_intents"]
        
        if intent_result["clarity"] == "parallel_multiple":
            # 給用戶選項
            ai_response = intent_selector_node(state, user_message)
            state.intent_state.awaiting_selection = True
            state.phase = "等待用戶選擇意圖"
            response_type = "intent_selection"
        else:
            # simple 或 ambiguous_subordinate,正常走 FAQ/KB
            # 注意:ambiguous_subordinate 用 primary_intent 而非 user_message
            effective_message = (
                state.intent_state.primary_intent 
                if intent_result["clarity"] == "ambiguous_subordinate" 
                else user_message
            )
            
            # 5. FAQ 比對(維持原邏輯,但用 effective_message)
            faq_result = faq_matcher_node(state, effective_message)
            
            if faq_result["matched_id"] and faq_result["confidence"] >= 0.7:
                faq_data = load_faq(faq_result["matched_id"])
                ai_response = faq_responder_node(state, faq_data, effective_message)
                response_type = "faq"
            else:
                # 6. KB 索引(維持原邏輯)
                kb_ids = kb_indexer_node(state, effective_message)
                state.kb_context.indexed_articles = kb_ids
                
                if len(kb_ids) == 0:
                    # 改動:KB 空陣列,改呼叫 no_kb_handler
                    ai_response = no_kb_handler_node(state, effective_message)
                    state.escalation_signals.no_kb_match = True
                    state.ticket_state.ticket_suggested = True
                    state.phase = "等待工單確認"
                    response_type = "no_kb_match"
                else:
                    # 既有 RAG 邏輯
                    kb_articles = [load_kb_article(kid) for kid in kb_ids]
                    ai_response = cs_response_node(state, kb_articles, effective_message)
                    response_type = "rag"
        
        state.turn_count += 1
    
    # 7. 寫入 history
    state.chat_history.append({"role": "assistant", "content": ai_response, ...})
    
    # 8. 背景評估(只在 customer_service 且非工單流程時跑,維持原邏輯)
    if classification == "customer_service" and state.phase == "對話中":
        evaluator_node(state, user_message, ai_response)
    
    # 9. 服務限制檢查(維持原邏輯)
    check_and_update_limits(state)
    
    state.updated_at = now()
    save_state(state)
    
    return {
        "ai_response": ai_response,
        "response_type": response_type,
        "show_ticket_button": state.ticket_state.ticket_suggested,
        "ticket_id": state.ticket_state.ticket_id,
        "state": state.to_dict()
    }
```

---

## 開發與測試流程

請按以下順序進行,**每個階段完成後告訴我,我手動測試後再進下一階段**:

### Phase A:State 結構與資料層改動(預計 0.5 天)

1. 修改 `core/state.py`,加入 `intent_state` 區塊與 `escalation_signals.no_kb_match`
2. 移除舊的 `clarity_state`(若有)
3. 確保新建 session 時 State 預設值正確
4. **不要動其他節點**,先讓既有測試還能跑

**驗收**:用 `/api/session/new` 建一個新 session,回傳的 State JSON 結構正確,既有功能不受影響。

### Phase B:入口分類節點 + greeting/unclear/off_topic 分支(預計 1 天)

1. 新增 `nodes/entry_classifier.py` 與對應 prompt
2. 新增 `nodes/greeting_handler.py` 與對應 prompt
3. 新增 `nodes/clarification_handler.py` 與對應 prompt(處理第 1 次和第 2 次釐清)
4. 修改 orchestrator,讓它呼叫 entry_classifier 而非 router
5. 既有的離題處理沿用,但要從 entry_classifier 的 off_topic 分支進入
6. 前端新增 `greeting`、`clarification` 兩種 response_type 的樣式

**驗收測試案例**(請在這個 Phase 結束後,我會丟以下訊息給你的系統測):

| 測試輸入 | 預期分類 | 預期行為 |
|---|---|---|
| 「你好」 | greeting | greeting_handler 回應,turn_count 不變 |
| 「Hi」 | greeting | 同上 |
| 「helloo」(故意打錯) | greeting | 同上 |
| 「在嗎在嗎」 | greeting | 同上 |
| 「我有問題」 | unclear | clarification_handler 第 1 次釐清 |
| 「不行」 | unclear | 同上,consecutive_unclear_count = 1 |
| 「dlsjfkdsf」 | unclear | 同上 |
| 連續 3 次都送 unclear | unclear → 強制建單 | 第 3 次直接觸發建單,phase=等待工單確認 |
| 「你今天午餐吃什麼」 | off_topic | 既有離題處理 |
| 「我影片不能看」 | customer_service | 進到下一階段(意圖明確度判斷) |
| 「你好,我影片不能看」 | customer_service | **不應該被誤判為 greeting** |

### Phase C:意圖明確度判斷節點 + parallel_multiple 處理(預計 1 天)

1. 新增 `nodes/intent_clarity.py` 與對應 prompt
2. 新增 `nodes/intent_selector.py` 與對應 prompt
3. 修改 orchestrator,讓 customer_service 分支進入意圖明確度判斷
4. 實作「等待用戶選擇意圖」的 phase 處理(`handle_intent_selection` 函式)
5. 前端新增 `intent_selection` 樣式

**驗收測試案例**:

| 測試輸入 | 預期 clarity | 預期行為 |
|---|---|---|
| 「我想退款」 | simple | 直接走 FAQ |
| 「我影片不能播放」 | simple | 同上 |
| 「我付費後想退費」 | ambiguous_subordinate | primary_intent="退費",走 FAQ 用「退費」搜尋 |
| 「買了課程後想取消訂閱」 | ambiguous_subordinate | primary_intent="取消訂閱" |
| 「我影片不能看,還有發票問題」 | parallel_multiple | 給選項讓用戶選 |
| 用戶選「1」回應上述 | - | 進入第 1 個意圖的 FAQ/KB 流程 |

### Phase D:no_kb_handler 節點(預計 0.5 天)

1. 新增 `nodes/no_kb_handler.py` 與對應 prompt
2. 修改 orchestrator,當 `kb_indexer_node` 回傳空陣列時改走此節點
3. 前端新增 `no_kb_match` 樣式

**驗收測試案例**:

刻意問一個 KB 完全無法回答的問題,例如「你們公司有附設停車場嗎?」(語意清楚、是客服問題,但 KB 完全沒有相關文章)。

預期行為:
- entry_classifier → customer_service
- intent_clarity → simple
- FAQ 比對 → 沒命中
- KB 索引 → 空陣列
- 觸發 no_kb_handler,回應「這個問題我們知識庫中沒有對應資訊...」
- State 中 `escalation_signals.no_kb_match = True`、`ticket_suggested = True`
- 前端顯示「建立工單」按鈕

---

## 我會怎麼跟你配合測試

每個 Phase 完成後,請主動告訴我「Phase X 完成」,然後**等我回覆測試結果**再進下一個 Phase。

我測試時會做這些事:

1. **跑驗收測試案例**(上面列的)
2. **觀察右側 State 除錯面板**確認 State 變化正確
3. **觀察前端 response_type 樣式**確認顏色區分正確
4. **跑一些非預期的訊息**(刻意亂打、極端 case)看系統會不會壞

如果測試有問題,我會把:
- 我輸入的訊息
- 系統回應的訊息
- 當下的 State JSON

貼給你,你針對這些 bug 做修正。**修正完不要直接進下一 Phase,讓我重測**。

---

## 風險與注意事項

1. **不要刪除舊的 router.py** 和對應 prompt,先保留。確認新流程穩定後我再告訴你刪。

2. **chat_history 結構不變**,這是相容性的關鍵——既有對話資料不會壞。

3. **既有的 FAQ 比對、KB 索引、cs_response、評估節點都不需要改**,只是 orchestrator 呼叫順序變了。如果你發現需要改這些節點,**先停下來問我**。

4. **意圖明確度判斷的 prompt 偏寬鬆**,寧可判定為 simple 也不要過度精細。如果測試發現 ambiguous_subordinate 過度觸發(普通訊息也被歸類),回報給我調 prompt。

5. **greeting 不增加 turn_count**——這個容易寫錯,請確認 orchestrator 在 greeting 分支結束時 turn_count 沒被增加。
