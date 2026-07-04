# Chatbot HISTORY

每次重要更新（KB Review、規格升級、架構調整）追加在最上方。

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
