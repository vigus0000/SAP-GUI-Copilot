# Changelog

本專案的所有重要變更都記錄在此檔案中。

格式基於 [Keep a Changelog](https://keepachangelog.com/zh-TW/1.1.0/)，版本號遵循 [Semantic Versioning](https://semver.org/lang/zh-TW/)。

---

## [0.2.0] - 2026-06-02

### 🎯 Phase 2: 監控系統與 Record / Ask Mode

#### Added
- **`sap_monitor.py`** — Polling-based 背景監控器
  - 在背景執行緒每 0.3 秒快照 SAP 畫面狀態
  - 透過前後快照差異比對，偵測 4 類事件：
    - `TCODE_CHANGE`：T-Code 切換
    - `SCREEN_CHANGE`：同一交易中畫面跳轉
    - `FIELD_CHANGE`：可編輯欄位值改變
    - `STATUS_MESSAGE`：狀態列出現新訊息
  - 使用 `threading.Event` 實現優雅的啟動/停止

- **`sap_recorder.py`** — SOP 操作紀錄管理器
  - `start_recording(name)` / `stop_recording()` 錄製控制
  - 自動產生摘要文字（涉及的交易、修改欄位數、步驟數）
  - JSON 格式儲存至 `./recordings/` 目錄
  - `list_recordings()` / `load_recording()` / `get_recording_summary()` 查詢介面
  - 錄製中即時在 CLI 顯示偵測到的操作事件

- **Ask Mode (`llm_brain.py`)**
  - 新增 `SYSTEM_PROMPT_ASK`：AI 只回答問題，不執行任何操作
  - `set_mode("ask" | "auto")` 切換模式
  - `_process_ask()` 結合畫面狀態 + SOP 紀錄回答問題

- **6 個新 CLI 指令 (`main.py`)**
  - `/record [名稱]`：開始錄製（啟動 Monitor + Recorder）
  - `/stop`：停止錄製並儲存
  - `/recordings`：列出所有已錄製的 SOP
  - `/play [名稱]`：顯示指定 SOP 的操作步驟
  - `/ask`：切換至 Ask Mode
  - `/auto`：切回 Auto Mode

#### Changed
- CLI prompt 顯示即時模式指示器：`🔴 REC` / `🟢 ASK` / `🟣 AUTO`
- Banner 版本號升級至 v0.2
- `/quit` 與 EOF 自動清理（停止錄製 + 停止 Monitor）

#### Architecture Decisions
- **Polling 取代 WithEvents**：`win32com.client.WithEvents` 在 Python + SAP GUI COM 環境下極度不穩定（`AssertionError`、事件永不觸發），改用穩定的 Polling 方案
- **JSON 取代 SQLite**：Phase 2 使用 JSON 儲存 SOP（簡單、可讀），Phase 4 再考慮遷移

---

## [0.1.0] - 2026-06-01

### 🎯 Phase 1: 基礎建設與 Auto Mode 雛形 (CLI)

#### Added
- **`copilot_auth.py`** — GitHub Copilot OAuth Device Flow 認證
  - 自動引導使用者在瀏覽器中授權
  - Token 快取至 `~/.sap_copilot_token.json`，支援自動刷新
  - Copilot session token 約 30 分鐘過期，自動重新換取

- **`sap_core.py`** — SAP GUI COM 連線管理器
  - 重用現有 SAP Session（不啟動新連線）
  - 防禦性設計：不快取 UI 元件，每次操作重新取得
  - `get_session()` / `get_session_info()` / `get_status_bar()` 安全介面

- **`sap_agent_tools.py`** — SAP GUI Scanner & Actor 工具集
  - **Scanner**：遞迴遍歷 SAP GUI DOM 樹，過濾排版元件只保留可互動元件（Token 優化）
  - **Actor**：`set_text()` / `click()` / `send_vkey()` / `set_tcode()` 操作工具
  - **安全機制**：敏感操作（Save/Delete/Post）自動標記 `requires_confirmation`

- **`llm_brain.py`** — LLM Agent 大腦
  - 透過 GitHub Copilot API (`api.githubcopilot.com`) 進行意圖識別
  - ReAct Loop：Scan → Think → Act → Verify（最大 10 次迭代）
  - OpenAI 相容的 Function Calling 格式
  - Token 過期自動刷新 + 重試

- **`main.py`** — CLI REPL 入口
  - 彩色終端機輸出
  - `/scan` 顯示結構化畫面掃描結果
  - `/login` / `/reset` / `/quit` 基礎指令

#### Fixed
- 修復 `hasattr()` 對 `win32com` 動態 dispatch 物件不安全的問題
  - 替換為 `_safe_get_attr()` 全捕獲函數
  - 根因：`win32com` 的 `BuildCallList` 在解析不完整型別描述時觸發 `IndexError`
- 修復首次登入後重複要求授權的問題
  - `login()` 成功後立即換取 Copilot Token
  - `_refresh_copilot_token()` 加入重試邏輯（最多 3 次，含延遲）
