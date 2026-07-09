# Chatbot HISTORY

每次重要更新（KB Review、規格升級、架構調整）追加在最上方。

## 2026-07-09 — 知識單一真理:遠端啟用=本地 KB 全數退場
- 動機:#7 接上 HiSupport 說明中心後,`_load_kb_index()` 是「本地(7/4 凍結拷貝 32 篇)＋遠端(說明中心現行版 32 篇)」合併——同內容兩份並存,說明中心編輯只動遠端,分診腦可能引到過期拷貝(Adam 拍板:以說明中心為準,本機測的不算數)。
- 作法:`kb_indexer._load_kb_index()` 改「遠端啟用(HISUPPORT_KB_URL 有設)→只回遠端 hs_*;停用→純本地」。斷線韌性(沿用最後快取/防誤清)本來就在 kb_remote 內部,不重複兜底。FAQ 22 條自包含(question_patterns/core_steps/fallback_message),不受影響。
- 測試:新增 2 顆釘死(遠端啟用=本地退場/停用=純本地);test_brain 的 kb_env fixture 補「明確關遠端」(開發機 .env 有 HISUPPORT_KB_URL 會讓本地白名單隱形);全套 124 綠。
- 注意:`data/kb/`+`data/kb_index.json` 檔案保留(本機純本地開發還在用);雲端(遠端啟用)等於它們退役。

## 2026-07-06 — v8「一顆腦」架構收斂(P0~P4 完整落地)
- 動機:①7/4 換 DeepSeek V4 後「查不到 KB/時好時壞/20~40 秒」——根因=V4 全系列自動思考,
  思考 token 吃掉小額度呼叫(faq_matcher/kb_indexer max_tokens=100)→ 空答靜默失敗;
  ②結構性病:每句 5~7 次串行 LLM(骰子相乘),主管只看站台紙條不看原件。
  實測定案(26 題考卷 2×2+寫手盲測,Adam 親評):一顆腦×關思考完勝。
- 作法(單一真理=`data/design-one-brain-2026-07-06.md`):
  - **P0 接線**:provider 支援 `reasoning_enabled=false`(OpenRouter reasoning.enabled;
    關了卻回思考 token 會記警告);等級改名 **triage/writer**(call_triage/call_writer);
    triage=DeepSeek V4-Pro 關思考(考卷 26/26)、writer=V4-Flash 關思考(盲測冠軍)。
  - **P1 分診腦**:新 `nodes/brain.py` 直讀全部 FAQ 問法表+KB 索引卡,一次輸出決定單
    (含 user_satisfied「好吧」誤結案閘門、issue 同源輸出、幻覺編號白名單剔除→空手降級轉真人);
    裁六站+話術站(entry_classifier/intent_clarity/faq_matcher 比對/kb_indexer 挑文/evaluator/
    intent_selector/clarification/no_kb/off_topic/pipeline);state 殭屍欄位大掃除;
    新增**每日訊息配額**(預設 30 句/日,超額=固定話術+提議轉真人、零 LLM,防洗版燒錢)。
  - **P2 寫手**:防捏造鐵則+「先給解法再追問」+禁粗體;全節點掛新等級;拆過渡別名。
  - **P3 驗收**:30 題考卷(含多輪/注入/好吧)29/30=97%、紅線零失誤;真實回放 10 筆全合理;
    live E2E(HiSupport→HiBot)整輪 2.4~10.8s(原 20~40s)。
    **live 抓到真漏洞並根治**:後台人設注入=整份蓋掉 system prompt → 改為
    「人設(可覆寫)+`prompts/cs_response_guard.txt` 守則(永遠附加、不可蓋)」。
- 影響:core/{orchestrator,state,llm_client,llm_providers,model_config,runtime_config}、
  nodes/*、prompts/*、config/models.toml;測試 66→93 全綠;
  驗收工具 scripts/run_routing_exam.py、run_replay_sample.py 入庫。
- 對外契約(/api/chat handoff 訊號、/api/config 注入鍵)一字未動,HiSupport 端零改動。
- 規格偏差備註:決定單 action 集拿掉 list_pending_intents(多意圖由腦直接處理,
  「等待用戶選擇意圖」phase 作廢);continue_intent 帶有效編號=等效回答。

## 2026-07-04 — 工單流程 → 轉真人交接（配合 HiSupport 整合）
- 動機：HiSupport 已定案「工單系統永久不做」，HiBot 搞不定 → 轉真人交接。HiBot 只負責「判斷＋摘要＋訊號＋閉環」，其餘（訊息、Email、通知真人）由 HiSupport 處理。並要求「單機 HiBot 體驗＝正式 HiSupport 體驗、只差介面」。
- 作法（甲案：HiBot 講安撫話、HiSupport 推字）：
  - `/api/chat` 回應新增 `handoff{requested,reason,summary}` 訊號（`core/state.py:build_handoff`）。
  - `/api/config` 注入白名單新增 `handoff_message`（`core/runtime_config.py`）；轉真人時用注入字、沒有就用內建預設（與 HiSupport 預設一致）。
  - 轉真人分支瘦身：**移除** 收 Email／格式重試／工單編號／`phase=已結束` 死路／前端「建立工單」按鈕／`/api/ticket/create` 端點；`_execute_handoff` 只講一句安撫話＋設 `handed_off`＋退場。
  - 新增 `handed_off` 旗標＋閉環（`_handed_off_holding`）：已交接後不再重問、不打 LLM。
  - phase 正名 `等待工單確認`→`等待轉真人確認`；`ticket_state` 移除舊工單欄位（封存見交接約定 §7）；`core/ticket.py` 不再被引用（保留存查）。
- 影響：`core/{orchestrator,state,runtime_config}.py`、`app.py`、`nodes/ticket_handler.py`、`static/index.html`；新增 `tests/test_handoff_flow.py`（7 測試）；全 60 測試過。
- 單一真理文件：`../HiSupport/docs/2026-07-04-hibot-handoff-contract.md`（兩邊 to-do、訊號規格、摘要格式、封存記錄）。
- 待辦：`static/guide.html` 產品說明仍有舊工單字眼待更新；Railway 端 `OPENROUTER_API_KEY`／`handoff_message` 尚未設。

## 2026-07-04 — LLM 層模型無關化（Phase 1）
- 動機：不綁死單一模型／供應商；未來可接 OpenRouter／任何模型，並可自訂「哪個等級用哪個模型」。
- 作法：模型呼叫收斂點改為可插拔 provider 層（AnthropicNative／OpenAICompat）＋ `config/models.toml` 設定 ＋ 等級化命名（`call_reasoning`／`call_fast`，移除 `call_sonnet`／`call_haiku`）。**預設設定＝原行為**（reasoning→Sonnet、fast→Haiku、走 Anthropic 原廠）。
- 影響：新增 `core/llm_providers.py`、`core/model_config.py`、`config/models.toml`、`tests/`（21 測試全過）；15 個呼叫點命名遷移；`manager` `MANAGER_MODEL`→`MANAGER_ROLE`；`requirements.txt` 加 `openai`；`.env` 加 `OPENROUTER_API_KEY`。
- 用量統計改為多模型：成本優先取供應商回傳費用、次查價目表、都沒有標「金額待補」。
- 設計文件：`data/design-model-agnostic-llm-2026-07-03.md`。
- 註：專案更名 Chatbot→HiBot 因資料夾被佔用暫緩，未做整份字串替換。
