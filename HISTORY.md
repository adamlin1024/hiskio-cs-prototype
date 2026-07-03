# Chatbot HISTORY

每次重要更新（KB Review、規格升級、架構調整）追加在最上方。

## 2026-07-04 — LLM 層模型無關化（Phase 1）
- 動機：不綁死單一模型／供應商；未來可接 OpenRouter／任何模型，並可自訂「哪個等級用哪個模型」。
- 作法：模型呼叫收斂點改為可插拔 provider 層（AnthropicNative／OpenAICompat）＋ `config/models.toml` 設定 ＋ 等級化命名（`call_reasoning`／`call_fast`，移除 `call_sonnet`／`call_haiku`）。**預設設定＝原行為**（reasoning→Sonnet、fast→Haiku、走 Anthropic 原廠）。
- 影響：新增 `core/llm_providers.py`、`core/model_config.py`、`config/models.toml`、`tests/`（21 測試全過）；15 個呼叫點命名遷移；`manager` `MANAGER_MODEL`→`MANAGER_ROLE`；`requirements.txt` 加 `openai`；`.env` 加 `OPENROUTER_API_KEY`。
- 用量統計改為多模型：成本優先取供應商回傳費用、次查價目表、都沒有標「金額待補」。
- 設計文件：`data/design-model-agnostic-llm-2026-07-03.md`。
- 註：專案更名 Chatbot→HiBot 因資料夾被佔用暫緩，未做整份字串替換。
