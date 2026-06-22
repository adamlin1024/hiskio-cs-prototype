# Chatbot — HiSKIO AI 客服雛形

> **繼承**：全域 `~/.claude/CLAUDE.md` ＋ `Adam_lab/CLAUDE.md` ＋ `Adam_lab/CONVENTIONS.md`。通用規則（白話文、第一性原理、開發紀律、UI／體驗慣例）一律沿用上層，**本檔不重抄**。
> **棧別**：小工具／原型（Python FastAPI + SQLite + Claude）
> 本檔只放本專案特有的穩定規則；**不列資料夾現有內容**（看現況）、**不堆歷史進度**（進規格文件）。

## 專案說明
本地可跑的 AI 客服雛形，三段式流程：FAQ 快查 → RAG → 工單建立。
- 完整規格書：`data/hiskio_cs_prototype_spec_v6.md`（最新版）
- 歷史紀錄：`HISTORY.md`

## 技術選型
Python 3.11 + FastAPI + SQLite + Anthropic Claude（不接其他 LLM 服務）。
主對話 = Sonnet (`claude-sonnet-4-6`)，輕量任務 = Haiku (`claude-haiku-4-5-20251001`)。

## 知識庫更新

用 `/kb-review` skill 啟動完整流程（KB / FAQ / 最近問答審視，互動式更新）。
使用者只需丟資料 + 確認統籌文件，其他全自動。

### 重要約束
- KB 兩層分離：`data/kb_source/`（原稿）與 `data/kb/`（系統檔）
- FAQ 兩層分離：`data/faq_source/`（原稿）與 `data/faq.json`（系統檔）
- 追溯表：`data/kb_mapping.md` / `data/faq_mapping.md`（由 skill 自動維護）
- KB / FAQ 變更後必重啟 server（`kb_indexer.py` 與 `faq_matcher.py` 都有 `lru_cache`）

## 開發習慣

- **動手前先掃影響範圍**：改一個檔案前，先確認其他節點 / state / cache 會不會被波及
- **改動分批做**：KB 跟 FAQ 不要同批改，出問題才分得清
- **Prompt 模板放 `prompts/`，不寫死在 code 裡**
- **LLM 呼叫一律 try/except + log + fallback**
- **不在 CLAUDE.md 寫 code 能推導的東西**（state 結構、節點功能等都看 code）

## 模型選用
- 對話、推理、生成工單摘要 → **Sonnet**
- 路由、分類、JSON 結構化抽取、FAQ 比對、KB 索引、評估 → **Haiku**

## 進行中事項
（用一兩行寫目前在做什麼，做完清掉）
