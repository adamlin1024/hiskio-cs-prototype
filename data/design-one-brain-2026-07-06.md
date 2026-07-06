# HiBot 架構收斂:一顆腦(One-Brain)設計規格

- 日期:2026-07-06
- 狀態:**已實作完成(2026-07-06,P0~P4 全落地+驗收通過)**——實作結果與偏差見 §15
- 前情:`design-model-agnostic-llm-2026-07-03.md`(模型無關層)、`../../HiSupport/docs/2026-07-04-hibot-integration-spec.md`(HiSupport 整合契約)

---

## 1. 背景與動機(為什麼改)

### 1.1 事故:2026-07-04 換模型後系統變笨變慢
7/4 將 reasoning/fast 由 Sonnet/Haiku 換成 DeepSeek V4 Pro/Flash(OpenRouter)後,出現「明明 KB 有資料卻查不到」「同一句話時好時壞」「一句話 20~40 秒」。

**根因(2026-07-05~06 實測定位)**:DeepSeek V4 全系列為混合思考模型,會**自動開啟思考**;經 OpenAI 相容接口,思考 token 計入 `max_tokens` 額度。`faq_matcher`/`kb_indexer` 的 `max_tokens=100` 被思考吃光 → 正式答案空白 → 站台回空 → 被判「沒命中」→「聽不懂」。全程無錯誤訊息(靜默失敗)。

### 1.2 結構性問題:每句話擲 5~7 顆骰子
現行 v7 流程:入口分類 → 意圖判斷 → FAQ 比對 → KB 挑文 → 主管決策(→ 寫手 → 事後評估),每站一次 LLM 呼叫、串行。單站 85% 準,串 5 站僅 ~44% 全對;延遲與成本疊加;主管只看「站台紙條」不看原始卡片,站台錯報則主管盲判。此設計源於 2025 年為省 Sonnet/Haiku 費用而拆碎任務;2026 年模型單價已跌一個數量級,前提消失。

### 1.3 實測數據(全部存證於對話,考卷檔隨本次實作入庫)

**26 題分診考卷(結構 × 模型 2×2)**:

| 配置 | 答對率 | 平均耗時/題 | 每題成本 |
|---|---|---|---|
| 流水線 × 全 Gemini Lite | 21/26 | 3.6s | US$0.00061 |
| **一顆腦 × 全 Gemini Lite** | **25/26** | **1.0s** | **US$0.00050** |
| 流水線 × Haiku 主管 | 24/26 | 4.9s | US$0.00356 |
| 一顆腦 × Haiku | 25/26 | 2.2s | US$0.00620 |

流水線 × 便宜模型的 5 個錯全是危險錯(把「查個人訂單」誤導向 KB 回答);一顆腦兩種模型皆正確轉真人。

**追加考(2026-07-06,同卷同結構)**:一顆腦 × **V4-Pro(關思考)=26/26 滿分**(唯一不靠指令補丁就把「我要找真人」判對)、1.7s/題、US$0.00231/題;V4-Flash(關思考)=24/26;V4-Pro(開思考=事故時現役)=24/26、4.6s/題、且一題輸出格式損毀——再證自動思考在此類任務的危害。

**寫手盲測(Adam 親評,遮名)**:規則題冠軍=DeepSeek V4-Flash(關思考,零捏造);情緒題冠軍=DeepSeek V3.2(但其在規則題捏造「規定因付款方式而不同」→ 出局);Haiku 中後段(溢價無可見品質)。

## 2. 目標/非目標

**目標**:①每句 20~40 秒 → 2~4 秒 ②消滅「時好時壞」(決策擲骰 5~7 → 1) ③維持/提升分診正確率(≥96%) ④寫手零捏造 ⑤月成本 < NT$50(實估平常月 ~NT$24、忙月 ~NT$40~45)。

**非目標(本次不做)**:接訂單/會員 API 工具、FAQ 自動增長機制(候選採集→人工核准,另案)、向量檢索層(KB+FAQ 超過門檻才做,見 §10)、HiBot 大腦答題品質優化以外的新功能。

## 3. 新架構

```
訪客訊息(HiSupport → /api/chat)
 │
 ├─【程式守衛】(零 LLM,全保留)
 │   ├ handed_off → holding 話術,HiBot 退場
 │   ├ 純問候 regex → greeting 計數/回應
 │   ├ phase=等待轉真人確認 → ticket_handler.decide(規則優先、語意備援)
 │   └ 離題/洗版超限 → 固定話術
 │
 ├─【呼叫① 分診腦(triage)】Gemini 2.5 Flash-Lite
 │   輸入:決策規則 + 32 張 KB 索引卡 + 22 條 FAQ 問法表 + 近況 + 本句
 │   輸出:決定單 JSON(見 §4)
 │   ├ answer_with_faq → faq_responder(混合模式,保留)
 │   ├ answer_with_kb  → 程式抓全文 → 呼叫②
 │   ├ suggest_ticket  → 兩段式轉真人(問「好/不用」)
 │   ├ clarify / acknowledge_uncertainty → 決定單內 clarify_message 直接用
 │   ├ acknowledge_out_of_scope / greeting / acknowledge_confirmation → 對應處理
 │   └ force_escalation(連續 unclear 達上限,orchestrator 觸發)
 │
 └─【呼叫② 寫手(writer)】DeepSeek V4-Flash(關思考)
     輸入:選中 1~3 篇 KB「全文」+ 人設 + 近況 + 本句
     輸出:給訪客的回覆(防捏造硬規則,見 §8)
```

每輪 LLM 呼叫:守衛路徑 0 次;clarify/離題/轉真人 1 次;FAQ 2 次(潤飾極輕);KB 2 次。(現行 5~7 次)

## 4. 溝通契約(分診腦決定單)

分診腦唯一輸出(嚴格 JSON;解析失敗 fallback=acknowledge_uncertainty,沿用現行防呆):

```json
{
  "recommended_action": "answer_with_faq | answer_with_kb | clarify | acknowledge_uncertainty | acknowledge_out_of_scope | acknowledge_confirmation | suggest_ticket | greeting | list_pending_intents | continue_intent",
  "faq_id": "faq_xxx | null",
  "kb_article_ids": ["kb_xxx"],
  "clarify_message": "…(clarify/uncertainty 才填)",
  "reason_to_user": "…(suggest_ticket 才填)",
  "issue": {"category": "…", "summary": "…", "user_emotion": "…"},
  "user_satisfied": false,
  "new_intents_to_log": [{"text": "…", "role": "primary|secondary|context", "in_scope": true}],
  "target_intent_index": null,
  "reason": "30 字內 debug 理由"
}
```

- 腦→寫手只傳**文章編號**,程式照編號取全文(零失真,非模型轉述)。
- `issue.*` 由分診腦順手輸出(取代事後評估站 evaluator),供交接摘要使用。
- 分診腦 prompt 必含硬規則:「用戶點名要真人 → 一律 suggest_ticket(即使 FAQ 有『聯繫客服』條目)」「需查個人訂單/購買紀錄/退款進度/改個資 → suggest_ticket」(考卷 Q15~Q20 釘住)。
- **「好吧」誤結案修正(舊架構已知 bug)**:`user_satisfied` 只在用戶**明確正面**表態(謝謝/解決了/沒問題了/我知道了)時為 true;「好吧/喔/嗯」等**消極接受**一律 false。orchestrator 僅在 `action=acknowledge_confirmation 且 user_satisfied=true` 時把 current_intent 標 `confirmed_resolved`;消極接受 → 溫和回應、intent 停在 `answered` 不結案。舊架構誤判源之一(evaluator 另行猜「滿意度」)已隨站裁撤。註:phase=等待轉真人確認 時「好吧」=同意轉真人(ticket_handler 規則層已涵蓋,行為正確、不動)。

### 4.1 轉真人觸發總表(三層)

**第一層:提議轉真人**(進兩段式,問「好/不用」;phase=等待轉真人確認):

| 觸發 | 說明 | handoff_reason |
|---|---|---|
| 用戶點名要真人 | 分診腦硬規則,即使 FAQ 有「聯繫客服」條目也一律提議轉 | needs_human |
| 需查個人資料 | 訂單/購買紀錄/退款進度/改個資(**新增規則**,舊版會拿 KB 硬答) | needs_human |
| FAQ+KB 皆無資料且問題明確 | 誠實說沒資料、提議轉 | no_kb_match |
| 連續聽不懂達上限(預設 3,可注入) | 程式計數守衛 force_escalation | unclear_limit |
| 寫手舉手 [SUGGEST_TICKET] | 已答 2 次仍不滿/帳號鎖/金流糾紛個案 | needs_human |

例外:該 session 用戶已拒絕過轉真人(user_decision=declined)→ 不再自動強逼,被動等用戶開口。

**第二層:完成交接**:等待確認時用戶同意(規則 regex 優先:好/OK/好吧/麻煩你…;語意判斷備援)→ 安撫話+交接摘要+`handoff.requested=true`,HiBot 閉環退場。

**第三層:HiSupport 端保險絲**(對外流程鐵則,不問直接轉):HiBot 健檢失敗=bot_down/開 session 失敗=session_failed/回答逾時錯誤=chat_failed。(HiSupport 關鍵詞秒轉已於 2026-07-05 拔除,轉不轉全由 HiBot 判。)

## 5. 模型配置(config/models.toml)

等級改名 `reasoning/fast` → **`triage/writer`**(呼叫門面同步 `call_triage`/`call_writer`;`call_reasoning`/`call_fast` 移除):

```toml
[roles.triage]   # 分診腦 + ticket_handler 語意判斷 + greeting 回應
provider = "openrouter"
model    = "deepseek/deepseek-v4-pro-20260423"
reasoning_enabled = false   # 關思考=考卷滿分關鍵;開思考=事故行為(§1.1/§1.3)

[roles.writer]   # KB 寫手 + FAQ 潤飾 + acknowledge 回應
provider = "openrouter"
model    = "deepseek/deepseek-v4-flash-20260423"
reasoning_enabled = false   # 新增欄位:關閉自動思考(§5.1)
```

### 5.1 供應商層小改造(事故根治)
`OpenAICompatProvider` 支援 role 層 `reasoning_enabled=false` → 轉譯為 OpenRouter `extra_body={"reasoning":{"enabled":false}}`(已於 2026-07-05 benchmark 驗證有效)。未設=不帶參數(現行為)。Anthropic 原廠 provider 忽略此欄(優雅退化)。開機驗證(validate_model_config)與缺金鑰警示(missing_api_keys)沿用。

備援(考卷永久有效,隨時重考換手):分診備胎 **Gemini 2.5 Flash-Lite**(25/26、最便宜最快;加「點名要真人」硬規則後有望滿分)/ Gemini 3.1 Flash-Lite / Haiku 4.5;寫手備胎 Gemini 2.5 Flash-Lite。

## 6. 節點裁撤/保留

| 節點 | 處置 | 說明 |
|---|---|---|
| entry_classifier / intent_clarity / faq_matcher / kb_indexer(挑文) / evaluator / intent_selector / manager / clarification_handler / no_kb_handler / off_topic | **裁** | 職責併入分診腦(off_topic/uncertainty 話術由決定單或固定句提供) |
| kb_indexer.load_kb_article / _load_kb_index(讀檔函式) | 留 | 搬至新腦模組或 util |
| faq_responder(混合模式) | **留** | 「FAQ 不會幻覺」核心保護:core_steps 程式貼、模型只加開場收尾 |
| cs_response(寫手) | 留+強化 | 防捏造硬規則 §8;model → writer |
| ticket_handler | 留 | 兩段式確認:規則 regex 優先、語意判斷備援(→ triage) |
| greeting_handler / acknowledge_handler | 留 | 輕量,→ triage / writer |
| orchestrator | 改寫 | phase 守衛保留;pipeline+manager 段落改為單一 brain 呼叫 |
| pipeline.py | 刪 | v7 流水線預判整檔退役 |

## 7. State 清理(prototype.db sessions.state_json)

**留**:session_id/created_at/updated_at/turn_count;`phase`(值縮為「對話中/等待轉真人確認」);user_info;chat_history;ticket_state 四欄;greeting_count/max、off_topic_count/max、consecutive_unclear_count/max;current_intent/intent_log;issue_context.category/summary/user_emotion(改由分診腦填);faq_context.matched_faq_id/answer_strategy;kb_context.articles_used_in_response。

**刪**:`escalation_signals` 整塊(4 欄從未讀寫+2 欄重複);intent_state.intent_clarity(從未寫入)/input_classification/awaiting_selection;service_limits.low_confidence_count/unresolved_count(evaluator 裁撤後無人填、本就不觸發動作);faq_context.match_confidence;kb_context.indexed_articles;issue_context.sub_category。

舊 session 相容:讀取端一律 `.get()` 帶預設,不做資料遷移(雛形 DB)。

## 8. 防捏造三道防線(寫手路徑)

1. **模型體質**:寫手=盲測規則題零捏造的 V4-Flash(關思考);V3.2 因捏造出局。
2. **指令硬規則**(cs_response_system 強化):「只能改寫文章裡有的內容;文章沒提到的規則、理由、條件一律不得自行補充,改說『這部分我幫您跟真人確認』」;新增「先給 1~2 個立刻可試的解法,再追問細節」(修盲測情緒題偷懶);沿用「泡泡內不用粗體/Markdown」。
3. **考卷釘死**:驗收考卷新增寫作查核題(組合包退費),回覆出現文章外規則=不及格。

FAQ 路徑天然免疫(core_steps 程式貼上,模型不得改寫)。

## 9. 對外契約(一字不動)

- `/api/chat` 回應格式(ai_response/response_type/handoff{requested,reason,summary}/state)不變;
- `/api/session/new`、`/api/config` 設定注入(人設/門檻/handoff_message)、`/health`、金鑰 middleware 不變;
- **HiSupport 端零改動**(BotResponder 忠實轉傳已於 2026-07-05 落地,commit 99b3e1e);
- 交接摘要 build_handoff_summary 沿用(資料源 issue_context 改由分診腦填)。

## 10. 未來擴充路線(本次不做,先立規則)

- **KB 卡+FAQ 合計使每題輸入 > 2~3 萬 token(約 KB 150~200 張或 FAQ 200~300 條)** → 加語意向量檢索層(程式先篩 top 5~10 給腦),不換模型;
- **考卷答對率下滑或需接工具(訂單查詢等)** → 升級 triage 模型(改設定兩行+重考考卷);
- FAQ 自動增長:對話結束採集候選 → `faq_candidates` 待審 → 人工核准入庫(另案設計)。

## 11. 驗收標準(全過才算完成)

1. 26+2 題考卷(入庫 `tests/routing_exam.json`)≥ 96%,含①寫作查核題零捏造 ②多輪「好吧」題(機器人剛給完答案、用戶回「好吧」→ 期望 user_satisfied=false、intent 不得標 confirmed_resolved);
2. 全測試套件綠(現 60 測試隨結構改寫更新,數量不減反增:新增 brain 決定單解析/守衛/防呆測試);
3. 真實對話回放抽測(conversations.json 取樣)人工抽查無亂答;
4. live 驗證:localhost 8765+8000 端到端——一般問答、FAQ、KB、兩段式轉真人(含「OK」「我才不要」變化球)、交接摘要進 HiSupport 內部留言;
5. 平均延遲 < 5 秒/句(目標 2~4);
6. HiSupport 端 190 測試不受影響(不動它任何檔案)。

## 12. 實作階段

- **P0 接線**:OpenAICompatProvider reasoning_enabled + models.toml 改名/換模型 + llm_client 門面改名 + 開機驗證(TDD);
- **P1 分診腦**:新 brain 節點(prompt+決定單解析防呆)+ orchestrator 改接 + 裁撤六站 + state 清理;
- **P2 寫手強化**:cs_response 防捏造硬規則 + faq_responder/acknowledge/greeting 換 role;
- **P3 驗收**:考卷入庫+回歸、測試套件更新、回放抽測、live 端到端;
- **P4 回填**:HISTORY.md 變更紀錄、本檔狀態改「已實作」、static/guide.html 沿路修正。

## 13. 風險與回退

- 分診腦 prompt 品質=單點 → 考卷回歸把關;決定單解析失敗 fallback=acknowledge_uncertainty(不丟例外,對外流程鐵則沿用);
- OpenRouter 單點依賴 → Anthropic 原廠 provider 保留,備胎模型列 §5;
- 回退方案:git revert + models.toml 改回即可(P0~P2 各自獨立 commit)。

## 14. 二次檢視補強(2026-07-06,Adam 要求再挑漏洞)

| # | 漏洞 | 補救(落在哪一段工) |
|---|---|---|
| 1 | 考卷全單句題,多輪(追問/換題/指代/好吧)無保護 | P3:考卷增補多輪題組;真實回放照計畫 |
| 2 | 決定單「幻覺編號」(kb_099 等)無防線 | P1:faq_id/kb_article_ids 白名單驗證;剔除後空手 → 轉真人,不硬答 |
| 3 | OpenRouter 供應端可能無視 reasoning_enabled=false | P0:接線層偵測回應含思考 token 即記警告;P3 驗收斷言全程思考 token=0;必要時鎖供應商路由 |
| 4 | 正式機逾時線:新架構 2~4s/句(最壞 ~11s),HiSupport 預設 8s 貼線 | P3:量最壞值後建議 HIBOT_TIMEOUT=20~30s;原「調長 vs 非同步」待決結案 |
| 5 | 舊 session 幽靈 phase(等待用戶選擇意圖) | **Adam 拍板:無舊對話相容需求**——上版時清空 sessions 測試資料即可;程式僅留「未知 phase 當對話中」一行防呆(零成本) |
| 6 | 惡意指令注入(叫模型忽略規則) | 既有三白名單防線;考卷加一題注入題(P3) |
| 7 | HiSupport /api/config 注入鍵相容(人設/門檻/安撫話) | 鍵名一字不改;P3 驗收加「後台推設定→生效」實測 |
| 8 | **洗版燒錢**:離題/亂問「擋之前」每句仍花 1 次分診腦(60 句/分速限下極端 ~NT$260/時) | P1:**會員每日訊息配額**(預設 30 句/日、可調)——超過即固定話術+主動提議轉真人,**不再呼叫模型**;轉真人後本就閉環零 LLM |
| 9 | HiBot 金鑰(HIBOT_API_KEY)本機未設=API 裸奔 | 部署檢查表:雲端必設 shared secret 才上線;/api/config 已 fail-closed(無金鑰即拒) |
| 10 | 財務保險絲 | OpenRouter 金鑰已設 US$5 花費上限(2026-07-06 帳單實查)=任何濫用的硬天花板;上線前依月量調至合理值+餘額告警;燒穿=呼叫失敗→HiSupport 自動轉真人,服務不死 |
| 11 | 匿名/被封鎖者 | 既有:未登入 401(聊天限會員);後台封鎖=靜默入庫、不呼叫 HiBot、零模型費(維持) |

(§11 驗收第 1 項的考卷據此增補;通過標準不變:≥96% 且紅線題零失誤。)

## 15. 實作結果(2026-07-06 完工回填)

**驗收成績**:
- 30 題考卷(26 單句+多輪追問/換題/「好吧」/注入)**29/30=97%**,紅線(好吧不結案/注入拒絕/寫作零加料零粗體)全過;唯一失分=Q20 企業團購判成禮貌拒答而非轉真人(臨界抖動,正式環境限會員泡泡幾乎不出現此類)
- 分診平均 4.0s/題(最慢 7.4s);live 整輪(HiSupport→HiBot,含健檢/開session/寫手)2.4~10.8s(原 20~40s)。**§2 的 2~4s 是純模型估計;實測整輪體感約 3~11s,誠實記錄**。逾時建議:正式機 HIBOT_TIMEOUT=30s(§14-4 結案)
- 真實對話回放 10 筆:判斷全數合理(模糊→澄清、明確→對條目、查個資→轉真人)
- pytest 66→93 全綠;HiSupport 端零改動

**與 §4 的偏差(定案)**:
- 決定單 action 集**拿掉 list_pending_intents**——多意圖由分診腦直接處理(答主要、其餘記 intent_log),「等待用戶選擇意圖」phase 作廢;`continue_intent` 帶有效編號=等效回答(orchestrator 照編號走 FAQ/KB)
- `reason_to_user` 加措辭規則:只寫「為何建議轉真人」,禁寫「已為您轉接」(live 抓到與確認句矛盾)

**§9 修訂(live 抓到的漏洞根治)**:後台人設注入原語意=整份覆寫 cs_response_system → 一行簡短人設會洗掉防捏造鐵則。改為:**注入只覆寫「人設段」;守則(`prompts/cs_response_guard.txt`:防捏造+任務+SUGGEST_TICKET 規則)永遠附加、不可被蓋**。注入鍵名不變,HiSupport 端無感。

**遺留(不擋結案)**:正式機部署檢查表(HIBOT_API_KEY 必設/HIBOT_TIMEOUT=30/OpenRouter 額度上限調整)在 §14;考卷臨界題(如單詞課名「vibe coding」判離題 vs 請澄清)存在 run-to-run 抖動,97% 水位穩定。

**2026-07-06 補強(Adam 拍板後追加)**:
- **多重意圖定案=直接答**(不列選單):答用戶指定優先的、其餘記 intent_log;感謝→結案+引導下一個待辦;「引導哪一題」由程式 `_next_pending` 判定(最早的 pending),模型只管措辭——實測小模型會把已解決的又端出來+捏造「已更正完成」,故收回其判斷權。多輪實測 3 劇本全過(`scripts/run_multiintent_test.py`)。
- **轉真人精確原因**:決定單新增 `handoff_reason`(白名單 no_kb_match/needs_human);「知識庫沒有對應資料」現在會寫進真人交接摘要=補 KB 的訊號。
- **離題 vs 沒資料邊界寫死**:「詢問 HiSKIO 有沒有某課程/服務/方案」=業務相關詢問,資料庫沒有→轉真人(no_kb_match),不算離題(Q20 因此轉正)。
- 測試 99 全綠;考卷回歸 29/30(97%)紅線零失誤。

## 變更紀錄

- 2026-07-06 初版(Claude 起草,基於 07-05~06 全部實測)。
- 2026-07-06 補:§4.1 轉真人三層總表、user_satisfied(「好吧」誤結案)、分診模型改 V4-Pro 關思考(追加考 26/26)、§14 二次檢視 11 條補強(多輪考題/幻覺編號驗證/思考偵測警報/逾時線/每日配額/金鑰部署/財務保險絲)。**Adam 核准動工;無舊對話相容需求。**
