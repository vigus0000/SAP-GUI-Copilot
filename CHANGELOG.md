# Changelog

本專案的所有重要變更都記錄在此檔案中。

格式基於 [Keep a Changelog](https://keepachangelog.com/zh-TW/1.1.0/)，版本號遵循 [Semantic Versioning](https://semver.org/lang/zh-TW/)。

---

## [0.3.0] - 2026-06-02

### Added
- **Study Mode (`main.py`)**
  - 新增 `/study [SOP名稱]` 指令，可在 Auto Mode 執行已錄製 SOP。
  - 使用壓縮後的 `events` 作為重放來源，避免 raw polling events 過度靈敏造成重複輸入。
  - 每個 SOP 步驟完成後固定等待 5 秒，方便觀察 SAP GUI 狀態與彈窗。
  - 新增 Study Mode T-Code 起始畫面判斷；若目前不在起始畫面，會自動使用 `/nTCODE`。
  - 新增 `.env` 設定：`STUDY_PREFIX_TCODE_OUTSIDE_START` / `STUDY_INITIAL_TCODES`。
  - 重放時會依事件語意選擇工具：
    - OKCode / 一般欄位：`set_text()`
    - T-Code 切換：OKCode + Enter 或 `set_tcode()`
    - 下拉式選單：`select_combo()`
    - radio / checkbox：click/Select
    - 畫面跳轉：先嘗試 F8/Execute；若 SAP 回報 virtual key 未啟用，改送 Enter

### Changed
- Banner 版本號升級至 v0.3.0。
- `/recordings` 提示新增 `/study [名稱]` 作為 SOP 執行入口。
- `/study` 重放 OKCode + T-Code 切換時會辨識目前 SAP transaction；預設只有 `SESSION_MANAGER` / `S000` 視為起始畫面，其餘狀態會將 `VF05` 轉成 `/nVF05`。
- README 新增 Study Mode 指令與目前重放策略說明。

### Notes
- 目前錄製器仍以 snapshot diff 偵測操作結果，尚未保存每一次 button click / vkey 原始意圖；因此 `/study` 對 `SCREEN_CHANGE` 會先以 F8/Execute 作為推測，若該 vkey 在當前畫面未啟用，會 fallback 到 Enter。後續可補強按鈕事件或快捷鍵事件錄製，讓 SOP 重放更精準。

## [0.2.1] - 2026-06-02

### Added
- **SAP 彈窗解析強化 (`sap_agent_tools.py`)**
  - 掃描結果新增 `active_window` / `windows` / `active_popup`
  - 彈窗與主畫面新增 `fields` 摘要，將 SAP label 與可編輯欄位配對
  - 彈窗新增 `messages` 摘要，可抽取「錯誤」「輸入一數值」等 dialog 訊息
  - 嘗試擷取 `focused_element`，協助判斷目前紅框或游標所在欄位
  - 多層彈窗時優先解析最上層 `wnd[n]`

- **彈窗操作工具**
  - 新增 `handle_popup()`：支援依 `field_label` 填值，並執行 `ok` / `save` / `cancel` / `close` / `enter`
  - 新增 `select_combo()`：專門處理 SAP `GuiComboBox` 下拉式選單，可依 key 或顯示文字選取
  - 新增 `set_editor_text()`：專門處理 ABAP editor / `GuiShell` 程式碼編輯器
  - `send_vkey()` 支援 `window_id`，未指定時自動送到活動彈窗
  - SAP 視窗 ID 會從 `/app/con[0]/ses[0]/wnd[1]` 正規化為 `wnd[1]`

- **Copilot 設定與節流控制**
  - 新增 `.env` 設定：`COPILOT_MODEL` / `COPILOT_MAX_ITERATIONS` / `COPILOT_MAX_RETRIES`
  - 新增 retry/backoff 設定：`COPILOT_RETRY_BASE_SECONDS` / `COPILOT_RETRY_MAX_SECONDS`
  - 新增 `COPILOT_HISTORY_LIMIT` 控制跨輪對話歷史大小

### Changed
- Auto Mode 每次 tool 執行後會重新掃描 SAP，將最新畫面摘要寫入 `screen_after`
- `screen_after` 改為 compact summary，不再把完整 SAP DOM `elements` 回塞給 LLM，降低 token 與 429 風險
- 初始畫面 context 改用摘要版，保留 `fields` / `messages` / `active_popup` / `focused_element`
- Record Mode 的 `SAPMonitor` 會在背景 thread 內重新取得 SAP session 並初始化 COM，避免跨 thread COM proxy 導致靜默讀取失敗
- Record Mode 監控範圍擴大到多個 `wnd[n]`、ComboBox、checkbox、radio、OKCode、活動視窗、彈窗開關與焦點變化
- Record Mode 停止錄製時會將 raw polling events 壓縮成 SOP steps；同一欄位連續輸入只保留最後值，並保留 `raw_events` 供除錯
- `fields` 會標記 `dropdown=true` 並盡量列出 ComboBox `options`
- 掃描結果新增 `editors` 摘要，列出疑似 ABAP/text editor 的 `GuiShell` 控件
- `set_text()` 遇到 `GuiComboBox` 時會自動改走 ComboBox 選取邏輯，避免把下拉選單當一般輸入格
- `set_text()` 遇到 editor-like `GuiShell` 時會拒絕並提示改用 `set_editor_text()`
- 預設 Copilot model 從已退場的 `gpt-4o` 改為可設定的 `COPILOT_MODEL`，預設 `gpt-5-mini`
- CLI 啟動時顯示目前使用的 Copilot model
- 預設 Auto Mode 最大迭代次數從 10 降為 6，降低高頻 API 呼叫

### Fixed
- 修復畫面切換或彈窗出現後，LLM 仍沿用舊畫面元件 ID 的問題
- 修復活動視窗 ID 比對失準，導致 `active_popup` 無法正確標記的問題
- 修復 Auto Mode 容易把 SAP 下拉式選單當作一般文字欄位填值的問題
- 修復 Auto Mode 嘗試用一般 `set_text()` 寫入 ABAP `GuiShell` editor 而失敗的問題
- 修復 Record Mode 顯示監控已啟動，但背景 thread 因 COM 初始化/跨 thread session 問題導致錄製 0 筆的高風險問題
- 修復 Record Mode 過度靈敏，將每次 keypress 都記成獨立欄位修改的問題
- 修復 Copilot API 遇到 429 直接失敗，沒有依 `Retry-After` 等待重試的問題

### Notes
- 建議目前 `.env` 使用 `COPILOT_MODEL=gpt-5-mini`；若需要更強推理可試 `gpt-5.2` 或 `claude-sonnet-4.5`
- `gpt-4o` 不建議再作為 Copilot Chat model

### Known Issues
- **ABAP editor 寫入驗證不足**
  - `set_editor_text()` 寫入 `GuiShell` editor 時，SAP GUI Frontend 可能回報插入例外
  - 實測流程仍可完成儲存、啟用與執行，但工具端無法可靠確認 editor 內容是否已完整寫入
  - 待處理：新增 editor read-back / compare 驗證，或在貼上後透過 SAP GUI 可讀 API、狀態列與啟用結果交叉確認

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
