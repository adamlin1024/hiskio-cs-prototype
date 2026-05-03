# HiSKIO AI 客服對話系統 — 雛形規格書 v6（主管模式）

## v6.1 修正紀錄（2026-05-04 部署 Railway 後發現）

### 修正 1：移除 evaluator 後台靜默推進建單

**Bug**：用戶情緒抒發（例「我心情很差，我想退費」）後，evaluator 的 Haiku 把
`user_explicitly_wants_ticket` 判為 true → orchestrator 自動設定
`ticket_state.user_decision = "accepted"` + `ticket_suggested = True`，
**rag 藍框訊息底下無端冒出「建立工單」按鈕**。

**修法**：
- `core/orchestrator._finalize_turn` 移除「evaluator 偵測到 user_decision=accepted 就靜默推進建單」那段
- `nodes/evaluator.py` 不再寫 `state.ticket_state.user_decision = "accepted"`

**為什麼**：v3 時代 evaluator 是唯一能偵測「用戶想建單」的地方，所以靠它後台推進。
v6 主管模式下，主管自己會在下一輪看 chat_history 判斷並選 `suggest_ticket` action，
不再需要 evaluator 走後門。Evaluator 改回單純「填知識性欄位」（情緒/分類/解決確認）。

### 修正 2：llm_client 自動 strip 環境變數前後空白

**Bug**：Railway Variables 設定 `MODEL_SONNET` 時若不小心開頭打了空白
（例：`" claude-sonnet-4-5"`），Anthropic API 收到含空白的 model name 會回 404 not_found_error。
debug 過程花很久才發現，因為 `*****` 顯示看不出空白。

**修法**：
- `core/llm_client._model` 自動 `.strip()` env var
- `core/llm_client._get_client` 也順手 strip `ANTHROPIC_API_KEY`

### Railway 部署相關

新增 `railway.json` 控制 startCommand：
```json
{
  "deploy": {
    "startCommand": "python scripts/build_kb_index.py && python -m uvicorn app:app --host 0.0.0.0 --port ${PORT:-8000}"
  }
}
```

部署需要：
- Variables：`ANTHROPIC_API_KEY`、`MODEL_SONNET`、`MODEL_HAIKU`、`DB_PATH=/data/prototype.db` 等
- Volume：掛 `/data` 持久化 SQLite（不掛每次部署資料消失）
- Public Domain：Settings → Networking → Generate Domain

### Git 版本管理

- repo：https://github.com/adamlin1024/hiskio-cs-prototype（private）
- `main` 分支 + `v6-manager-mode` tag = 主管模式（當前）
- `v5-pipeline` 分支 + `v5-pipeline-mode` tag = 還原點（分類器流水線）

切換指令：
```bash
git checkout v5-pipeline-mode    # 還原 v5
git checkout main                # 切回 v6
```

### 已知特性（不是 bug）

- **連續 3 次模糊（manager clarify 或 acknowledge_uncertainty）→ 強制建單**
  即使用戶有在往具體方向走（「我想要看看成問題」→「課程問題」），
  系統仍可能因為連續判斷不出具體議題而觸發 force_escalation。
  常數：`MAX_UNCLEAR_BEFORE_FORCE_TICKET = 3`，可調整。
- **主管 Sonnet 在邊界 case 會被 chat_history 影響判斷**
  混合 in_scope + out_of_scope 多意圖（例「退費 + 水果」）容易判 clarify。
  之後可在 manager prompt 加強「混合意圖優先處理 in_scope 那個」規則。

---

## 文件版本說明

v6 取代 v5。最大變動：**架構從「分類器流水線」改為「主管統一決策」**。

### v5 → v6 主要差異

| 面向 | v5 分類器流水線 | v6 主管模式 |
|---|---|---|
| 核心決策節點 | entry_classifier + intent_clarity + faq_matcher + kb_indexer 四層 | 單一 manager 節點（Sonnet） |
| 每輪 LLM call | 4-5 次（Haiku 為主） | 2-3 次（Sonnet 主管 + 執行者） |
| 決策視野 | 每個節點只看自己負責的片段 | 主管看全貌（chat_history、intent_log、phase、KB list、FAQ list） |
| 「我聽不懂」處理 | 沒有，會硬塞到既有分類 | 一級公民 action：`acknowledge_uncertainty` |
| 邊界處理 | 每種新場景開新 if/else | 主管 prompt 加新 action enum |
| Debug | 看 4 個節點 log 拼湊 | 看 manager 的 `reason` 一目了然 |

### 為什麼改

v5 的根本問題：**5 個小幫手各看自己那塊，沒人看全貌**。導致：

1. 「人工件單為什麼以後不能動作了」 → entry_classifier 判 customer_service（含服務相關詞）→ kb_indexer 被歷史污染挑「發票」→ cs_response 答「請問發票問題是哪方面」（鬼打牆）
2. 用戶選號「3」走 phase 攔截 vs 打陳述句「我有水果方面的問題」走 entry_classifier，**兩條 code path 結果不一致**
3. ticket_state 已 declined 後系統永久不再建議；但用戶可能希望另起新意圖時重新評估
4. **每個新場景都要在多個節點各加一段判斷**，邊界永遠補不完

v6 主管模式把決策權集中到一個 Sonnet call，能：
- 看完整對話歷史 + 完整 intent_log + 系統狀態 → 一次決策
- 有「acknowledge_uncertainty」這個出口（誠實說我聽不懂）
- 有「acknowledge_out_of_scope」（明確擋下非業務問題）
- 有「continue_intent」（識別用戶在補充當前話題、不切換）
- 有「list_pending_intents」（識別「下一個」這類指稱詞，列出待辦）

### v6 沿用 v5 的部分

- State schema 大致相同（`intent_log` 結構、status 流轉、role 標記）
- 執行者節點（faq_responder、cs_response、no_kb_handler、off_topic、ticket_handler、greeting_handler、clarification_handler、evaluator）職責不變
- 服務上限機制（`service_limits`）、工單流程狀態機、phase 攔截邏輯都沿用
- Greeting fast-path（regex 攔純問候）保留，省一次 Sonnet call

---

## 專案目標（沿用 v5）

建立本地可跑的 AI 客服雛形，驗證**「主管統一決策 + 執行者分工」**架構，並具備會員身分識別與服務限制能力。

---

## 技術棧（沿用 v5）

| 層 | 選擇 | 備註 |
|---|---|---|
| 語言 | Python 3.11+ | |
| 框架 | FastAPI | |
| LLM | Anthropic Claude | |
| 主管模型 | `claude-sonnet-4-6` | 統一決策 + 生答案（cs_response） |
| 執行者輕量任務 | `claude-haiku-4-5-20251001` | greeting_handler / faq_responder / off_topic / clarification / no_kb_handler / evaluator / ticket_handler.decide |
| 儲存 | SQLite | |
| 前端 | 純 HTML/JS | |

---

## 專案結構

```
Chatbot/
├── app.py
├── core/
│   ├── state.py
│   ├── llm_client.py
│   ├── orchestrator.py        # v6 改寫，由主管驅動
│   ├── ticket.py
│   └── text_utils.py
├── nodes/
│   ├── manager.py             # v6 新增：主管統一決策
│   ├── greeting_handler.py
│   ├── clarification_handler.py
│   ├── intent_selector.py     # 仍保留：parse_selection 給 phase=等待用戶選擇意圖 用
│   ├── faq_matcher.py         # 仍保留：load_faq_by_id 給執行者用
│   ├── faq_responder.py
│   ├── kb_indexer.py          # 仍保留：load_kb_article + 主管沒給 kb_ids 時 fallback
│   ├── cs_response.py
│   ├── no_kb_handler.py
│   ├── off_topic.py
│   ├── ticket_handler.py
│   └── evaluator.py
├── prompts/
│   ├── manager.txt            # v6 新增
│   ├── greeting_handler.txt
│   ├── clarification_first.txt
│   ├── clarification_second.txt
│   ├── faq_matcher.txt        # 仍保留供 fallback
│   ├── faq_responder.txt
│   ├── kb_indexer.txt         # 仍保留供 fallback
│   ├── cs_response.txt
│   ├── no_kb_handler.txt
│   ├── off_topic.txt
│   ├── ticket_summary.txt
│   ├── intent_selector.txt
│   ├── intent_clarity.txt     # 仍保留（v5.1 階段 1 的 role/needs_user_selection 邏輯）
│   ├── entry_classifier.txt   # 仍保留（暫不刪，確認穩定後再清）
│   └── evaluator.txt
├── data/
│   ├── faq.json
│   ├── kb/*.md
│   ├── kb_index.json
│   ├── mock_users.json
│   └── prototype.db
├── scripts/
│   └── build_kb_index.py
└── static/
    ├── index.html
    └── admin.html
```

**註**：v6 引入主管後，`entry_classifier` / `intent_clarity` / `faq_matcher.match` / `kb_indexer.index_articles` 不再被 orchestrator 主流程呼叫，但檔案保留待規格穩定後再清理。

---

## State 結構（沿用 v5）

完整 schema 見 v5 規格書。重點欄位：

```python
"intent_state": {
    "input_classification": None,  # 改填 manager 的 recommended_action
    "consecutive_unclear_count": 0,
    "max_unclear_count": 2,
    "greeting_count": 0,
    "max_greeting_count": 3,
    "intent_clarity": None,         # v6 不再使用，留空
    "awaiting_selection": False,
    "current_intent": None,
    "intent_log": [
        # {"text": "...", "status": "pending|in_progress|answered|confirmed_resolved",
        #  "in_scope": bool, "role": "primary|secondary|context", "first_turn": int}
    ]
}
```

`intent_log` 永不刪除任何項目，只更新 status。

---

## 主管節點規格（v6 核心）

**檔案**：`nodes/manager.py` + `prompts/manager.txt`
**模型**：Sonnet
**呼叫時機**：每輪用戶訊息進來，除非走 phase 攔截或 greeting fast-path

### 輸入（manager 看到的全貌）

- 用戶最新這一句訊息
- 完整對話歷史（最近 10 輪）
- 整個 `intent_log`（含 status / role / in_scope）
- 用戶身分（is_logged_in / user_name / 是否舊客戶）
- 系統狀態（phase / 各種計數器 / ticket_suggested / user_declined_ticket）
- **完整 FAQ 清單**（id + question_patterns，主管自己決定要不要選哪筆）
- **完整 KB 索引清單**（id + 標題 + 摘要 + 常見問法，主管自己決定要不要挑哪些文章）

### 輸出（結構化決策 JSON）

```json
{
  "user_intent_summary": "白話描述用戶這次想做什麼",
  "is_in_scope": true | false,
  "matched_intent_in_log_index": 0 | null,
  "system_can_help": true | false,
  "recommended_action": "...10 種 action 之一...",
  "faq_id": "faq_001" | null,
  "kb_article_ids": ["kb_001", "kb_005"] | [],
  "clarify_message": "若 action 為 clarify / acknowledge_uncertainty 才填",
  "reason_to_user": "若 action 為 suggest_ticket 才填",
  "new_intents_to_log": [
    {"text": "意圖文字", "role": "primary|secondary|context", "in_scope": true|false}
  ],
  "target_intent_index": 0 | null,
  "reason": "決策理由（給 debug 看）"
}
```

### 10 種 action

| action | 何時用 | 對應的 payload |
|---|---|---|
| `greeting` | 純打招呼（理論上 fast-path 已擋下，極少觸發） | （無） |
| `clarify` | 用戶訊息模糊或亂碼，需請用戶補充 | `clarify_message` |
| `answer_with_faq` | 用戶意圖明確 + 命中某 FAQ | `faq_id` |
| `answer_with_kb` | FAQ 沒命中但 KB 有相關文章 | `kb_article_ids`（1-3 個） |
| `acknowledge_out_of_scope` | 用戶問題不在 HiSKIO 業務範圍 | （無） |
| `acknowledge_uncertainty` | 主管不確定用戶想問什麼 | `clarify_message` |
| `suggest_ticket` | 用戶要求人工 / KB 找不到 / 達服務上限 / 質疑系統等需要人工介入 | `reason_to_user` |
| `list_pending_intents` | 用戶用指稱詞「下一個」「另外那個」 | （無，系統自動列出 intent_log） |
| `continue_intent` | 用戶在補充 current_intent 細節 | `target_intent_index` |
| `force_escalation` | 連續多次無法溝通 | （無） |

### 主管的決策原則

manager prompt 中明確要求：

1. **站在「全新解讀這句話」的角度判斷**，不要被歷史話題引導
2. 不確定用戶在問什麼 → 寧可選 `acknowledge_uncertainty` 也不要硬塞分類
3. 用戶問題明顯不在業務範圍 → 直接 `acknowledge_out_of_scope`，不走 KB 流程
4. KB 找不到對應文章 → 直接 `suggest_ticket`，不要勉強用無關文章硬答
5. 偵測到的每個意圖都要標 `role`（primary/secondary/context）+ `in_scope`

### 解析 fallback

manager 解析失敗 → 統一回 `acknowledge_uncertainty`，避免讓系統卡住或胡答。

---

## Orchestrator 主流程（v6）

```python
def handle_user_message(session_id, user_message):
    state = load_state(session_id)
    append_message(state, "user", user_message)

    # 1. 特殊 phase 攔截
    if phase_result := _try_handle_phase(state, user_message, session_id):
        return phase_result

    # 2. greeting fast-path（regex 純問候，省一次 Sonnet）
    if _GREETING_RE.match(user_message.strip()):
        return _handle_greeting_fast_path(state, user_message, session_id)

    # 3. 主管統一決策（Sonnet）
    decision = manager.decide(state, user_message)

    # 4. 把主管偵測到的新意圖記進 intent_log
    for det in decision["new_intents_to_log"]:
        _ensure_in_intent_log(state, det["text"], in_scope=det["in_scope"], role=det["role"])

    # 5. 按 recommended_action 派任務給對應執行者
    return _execute_action(state, user_message, decision, session_id)
```

### Phase 攔截邏輯（沿用 v5）

| phase | 處理 |
|---|---|
| 等待用戶選擇意圖 | parse_selection 判斷是真的在選 → 切 current_intent；不是 → 退出 phase fall through 走主管 |
| 等待工單確認 | ticket_handler.decide → Y 接受 / N 拒絕 / U fall through 走主管 |
| 等待 Email | ticket_handler.handle_email_input |
| 已結束 | _ended_session（固定訊息） |

### Action Executor

| action | 對應執行者 |
|---|---|
| `greeting` | 跳到 fast-path（理論上不會走到這條） |
| `clarify` | 用 manager 給的 clarify_message 或 fallback 跑 clarification_handler |
| `answer_with_faq` | `faq_matcher.load_faq_by_id` 取 FAQ → `faq_responder.respond` |
| `answer_with_kb` | 用主管給的 kb_article_ids（沒給就 fallback 跑 kb_indexer.index_articles）→ `cs_response.respond` |
| `acknowledge_out_of_scope` | `_route_off_topic_with_count`（off_topic_handler + 累加 off_topic_count） |
| `acknowledge_uncertainty` | 用 manager 給的 clarify_message 或 DEFAULT_UNCERTAINTY_MSG |
| `suggest_ticket` | 用 manager 給的 reason_to_user → 「請問需要建立工單嗎？」 phase=等待工單確認 |
| `list_pending_intents` | `intent_selector.respond` 列出 intent_log → phase=等待用戶選擇意圖 |
| `continue_intent` | 維持 current_intent 走 FAQ/KB（同 answer_with_faq/kb 但保留 current） |
| `force_escalation` | 固定 FORCE_ESCALATION_MSG → phase=等待工單確認 |

所有 executor 結尾都呼叫 `_finalize_turn`：append_message → evaluator（若是客服路徑）→ status 更新 → save_state。

### KB 空陣列 fallback

如果 manager 給 `answer_with_kb` 但 `kb_article_ids = []`，executor 內仍會呼叫 `kb_indexer.index_articles` 試一次；
仍空 → 走 `no_kb_handler` 並設 `escalation_signals.no_kb_match = True`、`phase = 等待工單確認`。

這是雙重保險：主管可能漏挑文章、舊的 kb_indexer 仍能補位。

---

## 用戶路徑與 response_type 對照（v6）

| response_type | 觸發來源 | 前端顏色 | turn_count |
|---|---|---|---|
| `greeting` | greeting fast-path 或 manager action=greeting | 淺藍虛線 | 不增加 |
| `greeting_blocked` | greeting_count > max | 灰色（小字） | 不增加 |
| `clarification` | manager action=clarify / acknowledge_uncertainty | 橘黃 | +1 |
| `force_escalation` | manager action=force_escalation 或 unclear 連續 3 次 | 紫框 | +1 |
| `off_topic` | manager action=acknowledge_out_of_scope | 橘色 | +1 |
| `off_topic_blocked` | off_topic_count >= max 後再離題 | 灰色（小字） | +1 |
| `intent_selection` | manager action=list_pending_intents 或 phase=等待用戶選擇意圖 | 深紫色 | +1 |
| `faq` | manager action=answer_with_faq | 綠色 | +1 |
| `rag` | manager action=answer_with_kb | 藍色 | +1 |
| `no_kb_match` | KB 空陣列 fallback 觸發 no_kb_handler | 粉紅色 | +1 |
| `ticket_flow` | manager action=suggest_ticket / 服務上限觸發 / [SUGGEST_TICKET] | 紫色 | +1 |
| `session_ended` | phase=已結束 | 紅色 | +1 |

---

## 共用 helpers（沿用 v5）

`core/text_utils.py`：
- `extract_json_object(raw)` / `extract_json_array(raw)`：容忍 markdown code fence
- `format_recent_history(history, turns=3, empty="（無）")`：格式化 chat_history

---

## 工單管理（沿用 v5）

SQLite tickets 表 schema、`core/ticket.py`、後台 API 完全不變。

---

## API 端點規格（沿用 v5）

`POST /api/session/new`、`POST /api/chat`、`POST /api/ticket/create`、`GET /api/mock_users`、`GET/POST /api/admin/tickets`。

`/api/chat` 回應的 `response_type` 集合多了 v6 新加的（其實大部分跟 v5 一樣，只是觸發來源不同）。

---

## 測試用例（更新版）

### T1：FAQ 命中（會員）
```
身分：會員
輸入：「我影片不能看」
預期：manager → answer_with_faq, faq_id=faq_001 → 綠框
intent_log: [影片問題(answered, primary, in_scope=true)]
```

### T2：質疑系統行為（v5 會出 bug）
```
（前面有過 chat_history）
輸入：「人工件單為什麼以後不能動作了」
預期：manager → suggest_ticket（reason_to_user 解釋會請客服查工單）→ 紫框
不會再被誤判為發票或付款問題
```

### T3：純離題（陳述句）
```
輸入：「我有水果方面的問題」
預期：manager → acknowledge_out_of_scope → 橘框
```

### T4：多重意圖（用戶沒排序）
```
輸入：「我有 3 個問題：發票、退費、影片」
預期：manager → list_pending_intents 或 new_intents_to_log 加 3 個 primary
intent_log 含 3 項 → intent_selector 列選項 → 紫框
```

### T5：多重意圖（用戶有排序，新增）
```
輸入：「我發票要先解決，順便看一下退費」
預期：manager → answer_with_faq（faq_id=發票）
new_intents_to_log: [{發票, primary}, {退費, secondary}]
不會列選項，直接處理發票
```

### T6：主從關係（context 不算待辦）
```
輸入：「我付費後想退費」
預期：manager → answer_with_faq（faq_id=退款）
new_intents_to_log: [{退費, primary}, {付費, context}]
intent_log 中付費 role=context，不會出現在 intent_selector 選項清單
```

### T7：指稱詞解析
```
（intent_log 已有 [影片(answered), 發票(pending)]）
輸入：「下一個問題呢」
預期：manager → continue_intent 或 list_pending_intents
target_intent_index = 1（發票）
切換 current_intent 為發票 → 走 FAQ/KB
```

### T8：不清楚的訊息
```
輸入：「不行」
預期：manager → clarify 或 acknowledge_uncertainty
clarification handler 引導用戶補充 → 橘黃框
連續第 3 次 unclear → force_escalation 紫框
```

### T9：KB 完全沒命中（會員）
```
輸入：「你們公司有附設停車場嗎？」
預期：manager → suggest_ticket 或 answer_with_kb（試挑後 kb_indexer fallback 也空）→ no_kb_match 粉紅框 + 建單按鈕
escalation_signals.no_kb_match = true
```

### T10：解決確認
```
（先處理過影片問題後）
輸入：「OK 了，謝謝」
預期：evaluator 偵測 user_confirmed_resolution=true
→ intent_log 中影片項目 status 改為 confirmed_resolved
```

### T11：拒絕建單後仍能繼續對話
```
（達服務上限觸發建單建議後）
輸入：「不用」
→ ticket_handler.handle_decline → user_decision=declined, phase=對話中
輸入：「我有發票問題」
→ manager（看到 user_declined_ticket=true）仍會走正常流程處理發票（不再硬塞建單）
```

### T12：質疑系統 → 連續質疑 → 強制建單
```
輸入：「為什麼你都聽不懂」
預期：manager → clarify 或 acknowledge_uncertainty
連續 3 次 → force_escalation 紫框
```

---

## 已知限制

1. KB 索引用 LLM 不用 embedding，主管 prompt 含完整 KB 標題清單，**100+ 篇規模 token 開銷會明顯**
2. Sonnet 主管成本比 Haiku 高，雖然次數少但單次貴
3. 沒有真的寄信通知工單建立
4. State 沒做 schema migration（升版需手動清 sessions 表）
5. 沒有 streaming（回應一次性吐出）
6. WatchFiles 在 Windows 上 hot reload 不穩定，改動 .py 後常需手動重啟 uvicorn
7. Anthropic 安全過濾會悄悄移除敏感詞，主管偶爾仍會漏掉部分意圖
8. intent_log 永不刪除，極端情況下單一 session 累積上百筆會吃 token
9. 主管 prompt 含 FAQ + KB 清單，**改 FAQ/KB 後不需重啟即生效（lru_cache 會抓舊版，需重啟才會看到新內容）**

---

## 開發注意事項

1. **prompt 模板存在 `prompts/` 資料夾**，程式從檔案讀取
2. **LLM 呼叫要 try/except**，失敗時要 log 並 fallback
3. **JSON 解析統一用 `core/text_utils`**
4. **manager 是 Sonnet**，成本控制重要 — greeting fast-path 是必要的省 token 措施
5. **不要在執行者內做決策**：執行者只生成內容，所有「該做什麼」由主管決定
6. **fallback 鏈**：manager 解析失敗 → acknowledge_uncertainty；manager 給的 kb_ids 為空 → kb_indexer 補一次 → no_kb_handler
7. 改 manager prompt 要小心：它影響所有對話路徑

---

## v5 → v6 升級摘要

**新增**：
- `nodes/manager.py` + `prompts/manager.txt`
- orchestrator `_execute_action` 派發機制（10 種 action）

**改動**：
- `core/orchestrator.py` 主流程從「分類器流水線」改為「主管 + 派發」
- `intent_state.input_classification` 改填 manager action 名稱（不再是 entry_classifier 的 4 分類）

**保留但不再被主流程使用**：
- `entry_classifier` / `intent_clarity` / `faq_matcher.match` / `kb_indexer.index_articles`
- 原因：暫不刪以便降級回滾；確認 v6 穩定後可清理

**不變**：
- State schema、執行者節點、phase 攔截、工單流程狀態機、後台 API、SQLite schema、前端 UI
