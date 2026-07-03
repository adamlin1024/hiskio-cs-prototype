# HiBot — 模型無關化 LLM 層 設計文件

- 日期：2026-07-03
- 狀態：定案（待實作）
- 專案：HiBot（原 Chatbot，= repo `adamlin1024/hiskio-cs-prototype`）
- 相關檔案：`core/llm_client.py`（現況唯一模型呼叫收斂點）

> 白話一句話：把「叫模型回話」這件事跟「用哪一家模型」徹底拆開。程式只喊「等級」，
> 換模型＝改一個設定檔，不動任何程式邏輯；且平常走 OpenRouter、想省錢時可直連 Claude 原廠。

---

## 1. 背景與現況

- 現在整支 App（Python FastAPI）所有 LLM 呼叫都收斂在 `core/llm_client.py`，對外只暴露
  `call_sonnet()` / `call_haiku()` 兩個函式，18 個檔案透過它們呼叫。
- 直接綁死 Anthropic：底層用 `anthropic` SDK 的 `client.messages.create(...)`。
- 「哪個工作用哪個模型」目前寫死成兩檔（結構上就是 2 個等級）：
  - **聰明檔（Sonnet, `claude-sonnet-4-6`）**：主對話（`cs_response`）、管理判斷（`manager`，預設）、
    工單摘要（`ticket`）。
  - **快省檔（Haiku, `claude-haiku-4-5-20251001`）**：其餘 13 個輕活（路由、分類、抽取、
    判斷離題、FAQ 比對、KB 索引、評估、澄清、致意、招呼、無 KB 處理…）。
- 模型 ID 由環境變數 `MODEL_SONNET` / `MODEL_HAIKU` 提供（有預設值）。
- 用量/成本統計（後台 `/api/admin/usage`）以**寫死的 Anthropic 價目表**估算（僅分 sonnet / haiku）。
- 快取折扣：走 Anthropic 的 `cache_control: ephemeral` 結構化 system block。

## 2. 目標

1. **不綁死單一模型／單一供應商**：未來可接任何模型。
2. **可接 OpenRouter**（一個窗口打各家模型：Claude / GPT / Gemini…）。
3. **由使用者自行設定「哪個等級用哪個模型」**（比照 Hyperbots 的分級思路）。
4. 改造範圍集中、對「使用者正在用的對外聊天流程」零風險（預設行為與現況完全一致）。

## 3. 定案決策（brainstorming 2026-07-03）

| 項目 | 決策 | 理由 |
|---|---|---|
| 供應商策略 | **可插拔**：OpenRouter 當預設總機 ＋ 保留「直連原廠」（Anthropic 等） | 只走 OpenRouter 等於改綁 OpenRouter；可插拔才真正不綁死，且需要時可直連原廠拿快取折扣、避開總機抽成 |
| 等級數量 | **2 個**（`reasoning` / `fast`），對齊現況 | 現在結構上就是 2 檔；使用者定的規則「現在幾個就做幾個」。第 3 級（如工單摘要專用的更便宜級）為未來可加項 |
| 設定方式 | **TOML 設定檔**（`config/models.toml`），Phase 2 再補後台畫面 | Python 3.11 內建 `tomllib` 可讀、零新依賴、可加註解、非工程師好編輯 |
| 命名 | 函式改為**與等級同名、不綁型號**：`call_reasoning` / `call_fast`；**移除** `call_sonnet` / `call_haiku`（不留誤導別名） | 目的是「不綁模型」，函式卻叫 Claude 型號名會自打嘴巴；改名是機械式重命名、非邏輯變更，低風險 |
| 金鑰位置 | API 金鑰仍放 `.env`，設定檔只用「環境變數名」引用，不放明碼 | 設定檔可安心進 git／給人看，金鑰不外洩 |

## 4. 架構設計

### 4.1 元件（都在 `core/` 下，小而專一）

- **`core/llm_client.py`（對外門面，保留）**
  對外 API：`call_reasoning`、`call_fast`、`call_role`、`load_prompt`、`get_usage_summary`、`reset_usage`。
  內部改為委派給 provider 層；不再直接碰 `anthropic` SDK。
- **`core/model_config.py`（新增）**
  載入 `config/models.toml`；解析「等級 → (provider, model)」；建立並快取 provider 實例。
- **`core/llm_providers.py`（新增）**
  `LLMProvider` 基底 ＋ `AnthropicNativeProvider` ＋ `OpenAICompatProvider`；
  各自把「中立請求」翻成該家 SDK 的呼叫，回傳「中立回應」。
- **`config/models.toml`（新增）**
  可編輯的 provider 定義 ＋ 等級對應 ＋（選用）價目表。
- **`.env`（沿用）**
  只放秘密與路徑：`ANTHROPIC_API_KEY`、`OPENROUTER_API_KEY`（走 OpenRouter 時才需要）、既有 `DB_PATH` 等。
  `MODEL_SONNET`/`MODEL_HAIKU` 由設定檔取代（不再作為真理來源）。

### 4.2 中立的請求／回應（provider-neutral）

- **請求**：`role`（或明確 `model`）、`system`、`prompt`（user 訊息）、`max_tokens`、`temperature`、
  `cache_system`（快取提示，供應商能吃就吃、不能吃就忽略）、`fallback`。
- **回應 `LLMResponse`**：`text`、`usage {input, output, cache_read, cache_create}`、`model`、`provider`、
  `cost_usd`（可選；供應商有回傳就帶）。

### 4.3 Provider 介面

```
LLMProvider.complete(*, model, system, prompt, max_tokens, temperature, cache_system) -> LLMResponse
```

- **`AnthropicNativeProvider`**：包現有 `anthropic` SDK 呼叫（等於把現在 `call_claude` 的主體搬過來）。
  - 支援 `cache_control: ephemeral`（保留快取折扣）。
  - 讀原生 usage：`input_tokens` / `output_tokens` / `cache_read_input_tokens` / `cache_creation_input_tokens`。
- **`OpenAICompatProvider(base_url, api_key, provider_name, cost_from_response)`**：用 `openai` SDK。
  - `base_url` 可設 → OpenRouter（預設 `https://openrouter.ai/api/v1`）／OpenAI 官方／自架相容端點。
  - 請求對應：`system` → system 訊息；`prompt` → user 訊息；呼叫 `chat.completions.create(...)`。
  - 解析：`choices[0].message.content`；usage 用 `prompt_tokens` / `completion_tokens`。
  - 走 OpenRouter 時，若回應含實際費用則抓來當 `cost_usd`。
  - `cache_system` 忽略（優雅退化；不報錯，只是沒有折扣）。

### 4.4 等級解析與對外函式

- `call_role(role, prompt, *, max_tokens, temperature, system, cache_system, fallback) -> str`：
  `model_config` 把 `role` 解析成 `(provider, model)` → 呼叫 `provider.complete(...)` → 記錄 usage → 回傳文字。
  失敗時：記 log ＋ 回傳 `fallback`（行為與現況一致）。
- `call_reasoning(prompt, ...)` = `call_role("reasoning", ...)`，預設 `max_tokens=600, temperature=0.6`。
- `call_fast(prompt, ...)` = `call_role("fast", ...)`，預設 `max_tokens=200, temperature=0.0`。
- `core/manager.py` 的 `MANAGER_MODEL`（`sonnet|haiku` 切換）→ 改為 `MANAGER_ROLE`（`reasoning|fast`）。

### 4.5 設定檔範例（`config/models.toml`）

```toml
# ── 供應商定義：每家一個區塊，金鑰只寫「環境變數名」不寫明碼 ──
[providers.anthropic]
type        = "anthropic"
api_key_env = "ANTHROPIC_API_KEY"

[providers.openrouter]
type        = "openai_compat"
base_url    = "https://openrouter.ai/api/v1"
api_key_env = "OPENROUTER_API_KEY"

# ── 等級 → 指到某供應商的某個模型 ──
[roles.reasoning]   # 聰明檔：主對話 / 推理 / 工單摘要
provider = "anthropic"
model    = "claude-sonnet-4-6"

[roles.fast]        # 快省檔：路由 / 分類 / 抽取 / 判斷離題 …
provider = "anthropic"
model    = "claude-haiku-4-5-20251001"

# ── （選用）價目表：給「原廠沒回傳費用」的模型算成本，USD / 百萬 token ──
[pricing."claude-sonnet-4-6"]
input = 3.0
output = 15.0
cache_read = 0.30
cache_create = 3.75

[pricing."claude-haiku-4-5-20251001"]
input = 0.80
output = 4.0
cache_read = 0.08
cache_create = 1.0
```

想把「聰明檔」換成走 OpenRouter 的 GPT，只改 `[roles.reasoning]` 兩行：
```toml
provider = "openrouter"
model    = "openai/gpt-5"
```

## 5. 用量／成本統計改造

- `usage_log` 每筆改為：`{provider, model, role, input, output, cache_read, cache_create, cost_usd}`。
- `cost_usd` 取得順序：
  1. 供應商回應直接帶費用（OpenRouter）→ 用真實金額。
  2. 否則查設定檔 `[pricing.<model>]` 價目表計算。
  3. 都沒有 → **顯示用量、金額標「待補」**，絕不硬套錯的 Claude 價目。
- `get_usage_summary` 依 model 分組加總（不再假設只有 sonnet / haiku）。

## 6. 快取折扣處理

- `cache_system` 為「提示」：`AnthropicNativeProvider` 會實作（`cache_control`），
  `OpenAICompatProvider` 忽略（優雅退化）。
- 文件註明：**快取折扣只在直連 Anthropic 原廠時生效**；走 OpenRouter 接他家時可能沒有。
  這正是「可插拔」的價值——想省錢的工作留原廠、想接他家的工作走 OpenRouter，由使用者分配。

## 7. 安全與相容（對外聊天流程保護）

- **預設 `models.toml` 完全複製現況**：`reasoning → claude-sonnet-4-6`、`fast → claude-haiku-4-5-20251001`，
  皆走 anthropic 原廠。改造完當下：功能、成本、回話品質、快取折扣**與現在一模一樣**，只是打開了「換模型」的門。
- 18 個呼叫點：把 `call_sonnet → call_reasoning`、`call_haiku → call_fast`（純機械式重命名，非邏輯變更）。
- **自動化測試**（釘死對外聊天不被改壞）：
  1. 等級解析：測試用 toml 下，`reasoning`/`fast` 解析出正確 provider＋model。
  2. 請求翻譯：中立請求 → anthropic kwargs；中立請求 → openai `chat.completions` kwargs（mock SDK，不打真 API）。
  3. 成本計算：OpenRouter 回傳費用直接採用；anthropic 走價目表；未知模型不給錯數字。
  4. 對外行為：`call_reasoning`/`call_fast` 成功回文字、供應商出錯回 `fallback`。
  5. 相容性：預設 toml 把 `reasoning` 導到 sonnet、`fast` 導到 haiku（釘住現有分派）。

## 8. 相依變更

- 新增：`openai`（Python SDK）。
- 沿用：`anthropic`、`fastapi`、`uvicorn[standard]`、`python-dotenv`、`pydantic`。
- `tomllib`：Python 3.11 內建，**零新依賴**。
- `.env` 新增：`OPENROUTER_API_KEY`（選用，只有某等級走 OpenRouter 時才需要）。

## 9. 施工範圍（檔案清單）

- **新增**：`core/model_config.py`、`core/llm_providers.py`、`config/models.toml`、
  `tests/test_llm_layer.py`。
- **修改**：
  - `core/llm_client.py`（門面＋等級命名＋usage 改造）
  - 約 15 個 node/core 檔（`call_sonnet→call_reasoning`、`call_haiku→call_fast`）
  - `core/manager.py`（`MANAGER_MODEL` → `MANAGER_ROLE`）
  - `requirements.txt`（加 `openai`）
  - `.env` / `.env.example`（加 `OPENROUTER_API_KEY`）
  - `CLAUDE.md`（「技術選型」段更新為模型無關化說明）
  - `HISTORY.md`（追加本次架構調整紀錄）
- **不動**：各 node 的判斷邏輯、`prompts/`、`static/`、DB schema、對外 API 路由。

## 10. 分階段

- **Phase 1（本設計）**：後端 provider 層 ＋ TOML 設定檔，預設等於現況。
  交付後：靠改 `models.toml` 即可自由換模型／分派等級。
- **Phase 2（另立規格）**：`/admin` 後台加「模型設定」頁——下拉選每個等級用哪個模型、填金鑰、存檔重載。

## 11. 非目標（YAGNI）

- 多供應商「自動故障切換」鏈（主模型掛了自動換備援）——先不做，列未來。
- 串流（streaming）回應——現況非串流，不在本次範圍。
- 單一任務（13 個 node 各自）指定模型——本次用 2 個等級；未來要細分再加第 3 級或覆寫機制。
- 後台設定畫面——Phase 2。

## Amendments

（尚無）
