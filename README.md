# 🤖 SAP GUI Copilot

> **用自然語言操作 SAP，告別繁瑣的 T-Code。**

SAP GUI Copilot 是一個以 Python 打造的 SAP GUI 智慧助手，透過 COM Interface / MCP 深度整合 SAP GUI，並可切換 GitHub Copilot 或 Codex LLM provider 來實現自然語言驅動的 ERP 操作自動化。

---

## ✨ 核心特色

| 特色 | 說明 |
|------|------|
| 🟣 **Auto Mode** | 用自然語言下指令，AI 自動操作 SAP 畫面（ReAct Loop：掃描 → 思考 → 執行 → 驗證） |
| 🟢 **Ask Mode** | 結合當前畫面狀態的 Context-Aware 問答，回答「這格該填什麼」「為何報錯」 |
| 🟡 **Solve Mode** | 專注當前錯誤、彈窗、狀態列與卡關情境，告訴使用者下一步如何處理；不執行操作 |
| 🔴 **Record Mode** | 背景錄製使用者的 SAP 操作流程，自動產生 JSON 格式的 SOP 腳本 |
| ▶ **Study Mode** | 使用 `/study [名稱]` 讀取既有 SOP / Skill；若無錄製依據會預設停止，需明確 `--draft` 才啟動探索草稿 |
| 🪟 **Popup-aware Scanner** | 可解析 SAP 多層彈窗、錯誤訊息、焦點欄位、下拉選單、Editor，以及 label ↔ input 對應 |
| 🔁 **畫面切換自癒** | 每次工具操作後重新掃描 SAP，避免沿用舊畫面元件 ID |
| 🔒 **Human-in-the-loop** | 敏感操作（儲存、刪除、過帳）強制人工確認，杜絕 AI 寫入錯誤資料 |
| 🚦 **LLM 節流保護** | 支援 429 retry/backoff、模型設定與對話歷史裁切 |
| 🔌 **Provider 可切換** | 支援 GitHub Copilot 與 Codex OAuth provider，可由 UI Connect 或 `/connect` 手動切換 |

---

## 🏗️ 系統架構

```
┌──────────────────────────────────────────────────────┐
│                   main.py (CLI REPL)                 │
│   🟣 Auto │ 🟢 Ask │ 🔴 Record │ ▶ Study          │
├──────────┬───────┴──────────┴───────┬────────────────┤
│          │                          │                │
│  llm_brain.py              sap_monitor.py           │
│  (LLM Agent)               (Background Polling)     │
│  ReAct Loop                 0.3s Snapshot Diff       │
│          │                          │                │
│          ▼                          ▼                │
│  mcp_client.py              sap_recorder.py         │
│  (MCP primary path)         (SOP JSON Manager)       │
│          │                                           │
│          ▼                                           │
│  mcp-sap-gui ──── SAP GUI Scripting ──── SAP        │
│          │                                           │
│          ▼ fallback during migration                │
│  sap_agent_tools.py / sap_core.py (legacy GUI COM)  │
│          │                                           │
│  sap_skill_library.py ── skills/ + recordings/       │
│          │                                           │
│  llm_provider.py ── Copilot / Codex API ── LLM      │
└──────────────────────────────────────────────────────┘
```

---

## 📦 技術棧

| 項目 | 技術 |
|------|------|
| 語言 | Python 3.10+ |
| SAP 整合 | Stage 2 起以 `mcp-sap-gui` / MCP 為主要路徑，`pywin32` COM Interface 暫作 fallback |
| LLM 後端 | GitHub Copilot API 或 Codex OAuth HTTP |
| 認證 | GitHub OAuth Device Flow 或 Codex/ChatGPT 瀏覽器 OAuth |
| 監控策略 | Polling-based Snapshot Diff（取代不穩定的 `WithEvents`） |
| SOP / Skill 儲存 | JSON 檔案 (`./skills/`, `./recordings/`) |
| 套件管理 | `uv` |

---

## 🚀 快速開始

### 前置需求

- **Windows** 作業系統（SAP GUI 僅支援 Windows）
- **SAP GUI** 已安裝且啟用 Scripting 功能
- **GitHub Copilot** 訂閱（Individual / Business / Enterprise），或可用的 ChatGPT/Codex 帳號
- **Python 3.10+**

### 安裝

```bash
# 1. Clone 專案
git clone https://github.com/your-repo/SAP_Copilot.git
cd SAP_Copilot

# 2. 建立虛擬環境
uv venv

# 3. 安裝套件
uv pip install pywin32 requests python-dotenv mcp

# 4. 自動取得 MCP SAP GUI server fork，並寫入 .env
python setup_mcp.py

# 5. 回到本專案啟動，並在 CLI/UI 輸入 /mcp 檢查 MCP SAP GUI server
python start.py
```

### 設定 `.env`

複製 `.env.example` 為 `.env`，填入 SAP 登入資訊與 LLM provider 設定：

```env
MANDT=
BNAME=
BCODE=
SAP_GUI_PATH=
connection=

LLM_PROVIDER=github_copilot

# Copilot model：目前建議 gpt-5-mini
COPILOT_MODEL=gpt-5-mini

# Codex OAuth；LLM_PROVIDER=codex 時使用
# Codex CLI 只作為瀏覽器登入 helper，不用於模型推理
CODEX_OAUTH_MODEL=gpt-5.5
CODEX_OAUTH_CHAT_URL=https://chatgpt.com/backend-api/codex/responses
CODEX_OAUTH_LOGIN_ON_CONNECT=true
CODEX_OAUTH_LOGIN_TIMEOUT_SECONDS=900

# Auto Mode 控制
COPILOT_MAX_ITERATIONS=6
COPILOT_HISTORY_LIMIT=14

# 429 / 暫時性錯誤 retry
COPILOT_MAX_RETRIES=4
COPILOT_RETRY_BASE_SECONDS=2
COPILOT_RETRY_MAX_SECONDS=60
EDITOR_CONTEXT_MAX_CHARS=12000

# Stage 2 MCP SAP GUI path
# setup_mcp.py 會自動填入此路徑
MCP_SAP_ENABLED=true
MCP_SAP_SERVER_DIR=C:\path\to\SAP_Copilot\external\mcp-sap-gui
MCP_SAP_LOCAL_COMMAND=uv
MCP_SAP_LOCAL_ARGS=run python -m mcp_sap_gui.server
MCP_SAP_ALLOW_PACKAGE_MODE=false
MCP_SAP_COMMAND=uvx
MCP_SAP_ARGS=--from mcp-sap-gui==0.2.0 mcp-sap-gui
MCP_SAP_UV_CACHE_DIR=C:\tmp\sap-copilot-uv-cache
MCP_SAP_FAILURE_COOLDOWN_SECONDS=30
MCP_TIMING_DEBUG=false
MCP_SAP_FAST_MODE=true
MCP_SAP_TOOL_PROFILE=core
MCP_FAST_SCREEN_MAX_DEPTH=2
MCP_FAST_SCREEN_CHANGEABLE_ONLY=false
MCP_EVIDENCE_SCREEN_MAX_DEPTH=5
MCP_EVIDENCE_SCREEN_TYPE_FILTER=GuiTextField,GuiCTextField,GuiPasswordField,GuiComboBox,GuiCheckBox,GuiRadioButton,GuiButton,GuiTab,GuiTableControl,GuiGridView,GuiShell,GuiLabel,GuiStatusbar,GuiOkCodeField
MCP_EVIDENCE_LEGACY_FALLBACK_ON_EMPTY=true
MCP_EVIDENCE_MIN_ELEMENT_COUNT=1
MCP_SCREEN_CACHE_ENABLED=true
MCP_SCREEN_CACHE_TTL_SECONDS=30
MCP_SCREEN_CACHE_MAX_WRITES=20
MCP_EXPOSE_DISCOVERY_TO_LLM=false
MCP_POPUP_USE_POPUP_TOOL_ONLY=true
MCP_ATTACH_ELEMENTS_AFTER_NAV=true
MCP_ATTACH_ELEMENTS_ON_FIELD_FAILURE=true
MCP_READ_TABLES_IN_CONTEXT=true
MCP_READ_SHELLS_IN_CONTEXT=true
MCP_TABLE_CONTEXT_MAX_TABLES=3
MCP_TABLE_CONTEXT_MAX_ROWS=25
MCP_TABLE_CONTEXT_MAX_COLUMNS=12
MCP_TABLE_CONTEXT_MAX_SCHEMA_COLUMNS=80
MCP_TABLE_CONTEXT_MAX_CELL_CHARS=160
MCP_TABLE_CONTEXT_DISCOVERY_DEPTH=6
MCP_TABLE_CONTEXT_SCHEMA_FIRST=true
MCP_TABLE_CONTEXT_BROAD_DISCOVERY_ON_EMPTY=true
MCP_TABLE_CONTEXT_BROAD_DISCOVERY_DEPTH=10
MCP_TABLE_CONTEXT_INSPECT_ON_EMPTY=true
MCP_TABLE_CONTEXT_INSPECT_INCLUDE_ROWS=false
MCP_TABLE_CONTEXT_MAX_CANDIDATES=12
MCP_TABLE_CONTEXT_TYPE_FILTER=GuiGridView,GuiTableControl,GuiShell
MCP_SHELL_CONTEXT_MAX_CHARS=4000
MCP_FAST_SCREEN_TYPE_FILTER=GuiTextField,GuiCTextField,GuiPasswordField,GuiComboBox,GuiCheckBox,GuiRadioButton,GuiButton,GuiTab,GuiTableControl,GuiShell,GuiOkCodeField
MCP_SAP_SCREEN_TOOLS=sap_get_screen_elements,sap_get_screen,sap_scan_screen,sap_get_current_screen
MCP_SAP_SESSION_INFO_TOOLS=sap_get_session_info,sap_get_current_session_info
MCP_SAP_SET_FOCUS_TOOLS=sap_set_focus,sap_focus_element
MCP_SAP_MONITOR_ENABLED=true
MCP_SAP_MONITOR_POLL_SECONDS=1.0

# Study Mode
STUDY_PREFIX_TCODE_OUTSIDE_START=true
STUDY_ADAPTIVE_MODE=true
STUDY_HUMAN_FIELD_INPUT=true
STUDY_FOCUS_HUMAN_FIELDS=true
STUDY_VISUALIZE_SECONDS=1.2
STUDY_SCREEN_CHANGE_TIMEOUT_SECONDS=8
STUDY_SCREEN_CHANGE_POLL_SECONDS=0.5
STUDY_PROMPT_FIELD_VALUES=true
STUDY_AUTOFILL_PROMPTED_VALUES=false
STUDY_INITIAL_TCODES=SESSION_MANAGER,S000
STUDY_REQUIRE_DRAFT_FLAG=false
STUDY_ALLOW_DRAFT=true
STUDY_SAVE_DRAFT_SKILL=false
STUDY_KNOWLEDGE_MAX_MATCHES=5
STUDY_WEB_SEARCH_ENABLED=false
STUDY_WEB_SEARCH_MAX_RESULTS=5
STUDY_WEB_SEARCH_TIMEOUT_SECONDS=8
STUDY_WEB_SEARCH_CACHE_TTL_SECONDS=86400

# Auto / Study Macro
SAP_MACRO_DIR=macros
SAP_MACRO_AUTOROUTE_ENABLED=true
SAP_MACRO_AUTOROUTE_MIN_SCORE=70
SAP_MACRO_STRICT_MATCH=true
SAP_MACRO_REQUIRE_BOUNDARIES=true
SAP_MACRO_SCREEN_MAX_DEPTH=5
SAP_MACRO_SCREEN_TYPE_FILTER=GuiTextField,GuiCTextField,GuiPasswordField,GuiComboBox,GuiCheckBox,GuiRadioButton,GuiButton,GuiTab,GuiTableControl,GuiGridView,GuiShell,GuiLabel,GuiStatusbar,GuiOkCodeField
SAP_MACRO_SCREEN_CHANGEABLE_ONLY=false
SAP_MACRO_STEP_SCAN_RETRIES=5
SAP_MACRO_STEP_SCAN_RETRY_SECONDS=0.4
```

#### LLM Provider 與模型建議

`LLM_PROVIDER=github_copilot` 時使用 `api.githubcopilot.com/chat/completions`，仍會走 GitHub Copilot OAuth Device Flow。`LLM_PROVIDER=codex` 時使用 Codex OAuth token 直連 Codex backend Responses endpoint；若尚未登入且 `CODEX_OAUTH_LOGIN_ON_CONNECT=true`，按 **Connect** 會觸發瀏覽器 OAuth 登入。Codex CLI 只作為登入 helper，不作為模型推理連線，也不會使用命令列推理。

也可手動用 `/login` 或下列指令開啟 Codex 瀏覽器登入：

```powershell
codex login
```

Codex OAuth 模型使用本機 Codex model cache 可用的模型 ID，例如 `CODEX_OAUTH_MODEL=gpt-5.5`。若要較輕量可改為 `gpt-5.4-mini`。

可在執行中手動切換：

```text
/connect github_copilot
/connect codex
```

UI 模式可按右上角 **Connect**，在 radio button 選單中選擇 GitHub Copilot 或 Codex OAuth；切換時會顯示對應連線方法、HTTP endpoint 與 model 欄位，再重新連線。

| 用途 | `.env` 代號 | 說明 |
|------|-------------|------|
| GitHub Copilot 預設推薦 | `COPILOT_MODEL=gpt-5-mini` | 快、成本低、適合高頻 ReAct 操作 |
| Codex OAuth 預設推薦 | `CODEX_OAUTH_MODEL=gpt-5.5` | 使用 Codex OAuth HTTP provider 時的模型 |
| Codex OAuth endpoint | `CODEX_OAUTH_CHAT_URL=https://chatgpt.com/backend-api/codex/responses` | Codex OAuth token 呼叫的 Codex backend Responses endpoint |
| 強推理 | `gpt-5.2` | 較強，但較可能增加用量與限流風險 |
| Claude 平衡 | `claude-sonnet-4.5` | 對複雜表單/彈窗推理表現穩定 |
| Claude 快速 | `claude-haiku-4.5` | 回應快，適合簡單操作 |
| Claude 強模型 | `claude-opus-4.5` | 推理強但較重 |

> 不建議使用 `gpt-4o`；Copilot Chat 已不適合作為預設模型，且容易遇到相容性或限流問題。

### 啟用 SAP GUI Scripting

> [!IMPORTANT]
> 必須在 SAP GUI 中啟用 Scripting 功能，否則程式無法連接。

1. 開啟 SAP GUI → **Options** → **Accessibility & Scripting** → **Scripting**
2. 勾選 **Enable Scripting**
3. 取消勾選 **Notify When a Script Attaches** 和 **Notify When a Script Opens a Connection**（建議）

### 使用

```bash
# 推薦：先檢查 SAP / LLM provider 登入，再啟動主程式
python start.py

# Phase 4 UI：啟動懸浮控制台
python start.py --ui

# Windows 也可直接執行
start.bat

# Windows UI 批次檔
start_ui.bat
```

`start.py` 會先確認 SAP GUI 是否已登入；若尚未登入，會執行 `sap_login.py`。接著依 `LLM_PROVIDER` 檢查 GitHub Copilot 授權或 Codex OAuth 設定，完成後預設啟動 `main.py`。若使用 `--ui`，則啟動 `ui_app.py` 的 Tkinter 懸浮控制台。使用 GitHub Copilot 且首次啟動時，程式會顯示 GitHub Device Flow 授權碼，在瀏覽器中完成授權後即可使用；使用 Codex OAuth 時可用 `/login` 觸發 Codex CLI 瀏覽器登入。

### Stage 2 MCP 遷移

Stage 2 的方向是以 `mcp-sap-gui` 作為主要 SAP GUI 操作路徑，舊有 `sap_core.py` / `sap_agent_tools.py` 的 pywin32 COM 操作保留為遷移期間的 fallback。

Phase 1 已新增 `mcp_client.py`，負責：

- 預設要求設定 `MCP_SAP_SERVER_DIR`，使用本機 clone 的 `MCP_SAP_LOCAL_COMMAND` / `MCP_SAP_LOCAL_ARGS`，預設為 `uv run python -m mcp_sap_gui.server`
- 可執行 `python setup_mcp.py` 自動 clone/update `tingjunchen425/mcp-sap-gui` 到 `external/mcp-sap-gui`，並更新 `.env` 的 MCP 設定
- `MCP_SAP_ALLOW_PACKAGE_MODE=true` 時才會嘗試 `uvx --from mcp-sap-gui==0.2.0 mcp-sap-gui`；目前實測 package registry 查無此 package，因此不作為預設
- 預設將 `UV_CACHE_DIR` 指到 `C:\tmp\sap-copilot-uv-cache`，降低 Windows 使用者目錄 cache 權限造成的啟動失敗
- `/mcp` 會顯示實際 command、cwd、cache、初始化結果、tool list、SAP attach 狀態與 server stderr tail
- 使用 `mcp.ClientSession` 初始化 session
- 將 MCP tools 轉成 GitHub Copilot / OpenAI tool calling JSON Schema
- 呼叫 MCP tool 並將結果整理成可放入 tool message 的字串
- 提供同步 bridge，讓目前同步 CLI / UI 架構可在 Phase 2 接上 MCP

Phase 2 已將 `llm_brain.py` 改為 MCP-first：

- Auto Mode 啟動時會優先讀取 MCP tool list，並把 MCP tools 動態提供給 Copilot tool calling
- 若 MCP SDK、MCP server 初始化、MCP tools 或單次 MCP tool call 失敗，會回到 legacy GUI COM tools
- Ask / Solve / Study 的畫面 context 會優先使用 MCP screen tools；MCP 失敗時仍使用原本 `scan_sap_screen()`
- Study Mode 的 `guide_user_action` 仍是本地 human-in-the-loop 工具；若 MCP 提供 `sap_set_focus`，會先用 MCP 聚焦欄位，再回到本地提示流程

Phase 2.1 新增 MCP speed mode：

- `MCP_SAP_TOOL_PROFILE=core` 預設只把常用 MCP tools 提供給 Copilot；需要完整 MCP tool 清單時可改為 `full`
- 若本機 `external/mcp-sap-gui` 提供 composite tools，Auto Mode 會優先使用 `sap_get_light_snapshot`、`sap_set_fields_and_enter`、`sap_select_popup_table_row_and_confirm`
- `sap_get_light_snapshot` 只讀 screen info / popup summary / fingerprint，不掃完整 elements，適合每輪狀態檢查
- `sap_set_fields_and_enter` 將多欄填值與 Enter validation 合併成一次 MCP call
- `sap_select_popup_table_row_and_confirm` 將彈窗 table row selection 與確認合併，適合 MM03 選擇檢視等流程
- `MCP_ATTACH_ELEMENTS_AFTER_NAV=true` 時，交易切換、畫面跳轉或 Enter 後的 screen change 會自動附上 filtered elements，避免下一輪猜欄位 ID
- `MCP_ATTACH_ELEMENTS_ON_FIELD_FAILURE=true` 時，欄位寫入失敗會自動讀一次 filtered elements 並放入 `field_write_recovery`，讓下一輪直接用正確 ID 重試
- `MCP_SAP_FAST_MODE=true` 時，初始畫面只抓 `sap_get_screen_info` 與 filtered `sap_get_screen_elements`
- `MCP_READ_TABLES_IN_CONTEXT=true` 時，Agent 會優先用 MCP 的 `sap_read_table` 讀取目前畫面中的 `GuiGridView` / `GuiTableControl` / ALV 報表，並把壓縮後的 `mcp_table_report_context` 放入 Ask / Solve / Study / Auto 的畫面 context
- `MCP_TABLE_CONTEXT_MAX_TABLES`、`MCP_TABLE_CONTEXT_MAX_ROWS`、`MCP_TABLE_CONTEXT_MAX_COLUMNS` 控制自動附上的表格數、列數與資料列欄數；大型報表只會附上前幾列，需要更多資料時再由 Agent 分頁呼叫 `sap_read_table(start_row=...)`
- `MCP_TABLE_CONTEXT_MAX_SCHEMA_COLUMNS=80` 會獨立控制欄位 schema 的保留上限；因此「顯示所有欄位」會優先列出完整 `columns` / `column_info`，但 `rows[].cells` 仍只保留前幾欄以控制 token。
- `MCP_TABLE_CONTEXT_SCHEMA_FIRST=true` 時，會先用 `sap_read_table(columns_only=true)` 讀表格欄位 schema，再讀 rows；因此即使 ME51N 項目概觀目前沒有可讀列資料，Ask Mode 仍可回答「有哪些欄位」
- `MCP_TABLE_CONTEXT_BROAD_DISCOVERY_ON_EMPTY=true` 時，若 filtered discovery 沒找到 `GuiTableControl` / `GuiShell`，會做一次較深的廣域候選掃描，從 element id / name / text 中找 table/grid/shell 特徵，改善 ME51N 項目概觀這類深層或非標準 type 控件
- `MCP_TABLE_CONTEXT_INSPECT_ON_EMPTY=true` 時，若一般 discovery 仍無法穩定定位表格，Agent 會優先呼叫 custom MCP tool `sap_inspect_tables`，用 focused control、parent id probing、broad discovery 與 schema-first 讀取來定位深層表格。
- 若 Ask Mode 仍顯示 `tables[]` 為空，可先在 SAP 表格任一儲存格或欄位標題點一下，再執行 `/inspect table` 或 UI 的 `Inspect` 按鈕；系統會列出 focused element、候選 table id、可讀欄位與錯誤原因。
- `MCP_READ_SHELLS_IN_CONTEXT=true` 時，若 `GuiShell` 不是表格但可讀文字或 HTML，會用 `sap_read_shell_content` 補充 shell/report 內容摘要
- `MCP_EVIDENCE_SCREEN_MAX_DEPTH=5` 時，Ask / Solve / Study 會用較深的 MCP elements 掃描並包含 `GuiLabel`，讓 ME51N、ME5A、ALV、Splitter Layout 等深層畫面能讀到欄位文字；Auto 仍使用 `MCP_FAST_SCREEN_MAX_DEPTH=2` 以維持速度
- `MCP_EVIDENCE_LEGACY_FALLBACK_ON_EMPTY=true` 時，若 MCP 只讀到交易碼/標題但沒有欄位、表格或 shell 內容，Ask / Solve / Study 會附上 `legacy_screen_summary_fallback`，避免回答「畫面元素無法讀到」後只給通用 SAP 教材
- `MCP_SCREEN_CACHE_ENABLED=true` 時，每輪只用 `sap_get_screen_info` 檢查畫面 fingerprint；同一畫面會復用上一輪 elements，避免重複讀完整畫面
- `MCP_SCREEN_CACHE_TTL_SECONDS` 控制 elements cache 有效時間；一般欄位寫入會另以 local write cache 補充本輪最新值
- `MCP_EXPOSE_DISCOVERY_TO_LLM=false` 時，`sap_get_screen_elements`、`sap_get_screen_info`、`sap_get_popup_window` 等讀畫面工具只由 agent 內部使用，不提供給 Copilot 主動呼叫
- `MCP_POPUP_USE_POPUP_TOOL_ONLY=true` 時，活動視窗是彈窗時只讀 `sap_get_popup_window`，不再額外掃彈窗 elements
- tool 執行後優先使用 MCP action response 內建的 `screen`，一般欄位寫入不重掃畫面，導航/彈窗類工具才補輕量 `sap_get_screen_info`
- 同畫面多欄輸入會提示模型優先使用 `sap_set_batch_fields`，減少逐欄 tool call 與重掃
- `MCP_TIMING_DEBUG=true` 可列印 LLM API、MCP call、screen scan 耗時，方便比對優化前後

Phase 3 已將 `sap_monitor.py` 改為 MCP-first polling：

- Record Mode 背景監控會優先使用 MCP session/screen tools 產生 snapshot
- 若 MCP 初始化或初始快照失敗，會自動切回舊 pywin32 COM monitor
- MCP monitor 預設每秒 polling，可用 `MCP_SAP_MONITOR_POLL_SECONDS` 調整

---

## 📖 指令參考

### 基礎指令

| 指令 | 說明 |
|------|------|
| `自然語言` | 直接輸入指令，AI 自動操作 SAP（Auto Mode）或回答問題（Ask Mode） |
| `/scan` | 掃描並顯示當前 SAP 畫面、彈窗、錯誤訊息、欄位摘要與可操作元件 |
| `/connect github_copilot` / `/connect codex` | 重新連線並手動切換 LLM provider；UI 可用 Connect 按鈕切換 |
| `/login` | 重新執行目前 LLM provider 授權流程；GitHub Copilot 走 GitHub device flow，Codex OAuth 走 Codex CLI 瀏覽器登入 |
| `/reset` | 重置 AI 對話歷史 |
| `/mcp` | 檢查 MCP SAP GUI server 啟動、工具清單、SAP session attach 與 stderr 診斷 |
| `/quit` | 結束程式 |

### 模式切換

| 指令 | 說明 |
|------|------|
| `/auto` | 切換至 🟣 Auto Mode — AI 可呼叫工具操作 SAP |
| `/ask` | 切換至 🟢 Ask Mode — AI 只回答問題，不執行操作 |
| `/solve` | 切換至 🟡 Solve Mode — AI 只診斷當前畫面/彈窗/錯誤並給處理建議，不執行操作 |

### 錄製管理

| 指令 | 說明 |
|------|------|
| `/record [名稱]` | 開始錄製 🔴 — 背景記錄使用者在 SAP 中的操作 |
| `/stop` | 停止錄製並儲存 SOP 檔案 |
| `/recordings` | 列出所有 SOP / Skill（`skills/` 優先，其次 `recordings/`） |
| `/play [名稱]` | 顯示指定 SOP / Skill 的完整操作步驟 |
| `/inspect table` | 以 MCP 優先、legacy fallback 檢查目前 SAP 畫面可讀的表格/ALV 欄位與候選 ID |
| `/study [名稱或目標]` | 優先執行指定 SOP / Skill；找不到時會建立 evidence pack 並進入受控探索草稿 |
| `/study --draft [目標]` | 明確啟動探索草稿；效果等同未知 `/study [目標]`，但語意更清楚 |
| `/study --save-draft [目標]` | 明確啟動探索草稿，完成後保存為 Markdown skill 草稿 |
| `/macros` / `/macro list` | 列出 `macros/` 目錄中的結構化 Markdown Macro |
| `/macro show <名稱>` | 顯示指定 Macro 的 inputs 與 steps |
| `/macro run <名稱> key=value ...` | 以 Auto 路徑直接執行 Macro；優先 MCP，失敗時走 legacy fallback |
| `/macro study <名稱> key=value ...` | 將同一份 Macro 轉成 Study Mode 互動式引導 |
| `/macro learn recordings` | 掃描 `recordings/*.json`，把穩定欄位 ID 納入 Macro learned index |
| `/macro audit <名稱>` | 顯示指定 Macro 的 primary ID、alternate IDs、learned IDs 與結果畫面 evidence |
| `/macro doctor <名稱>` | 只掃描目前畫面並診斷各 step 是否可匹配，不執行 SAP 動作 |
| `/study --macro <名稱> key=value ...` | 從 Study 指令直接啟動 Macro 引導 |
| `/knowledge import <path-or-url>` | 匯入官方文件、公司 SOP、Markdown、PDF、HTML、DOCX 或網頁，建立 knowledge index |
| `/knowledge search <query>` | 搜尋 knowledge / draft evidence |
| `/knowledge distill <path-or-query>` | 依來源資料蒸餾成 `module > business_cycle > document` 階層 draft skill |
| `/knowledge rebuild` | 依已保存文件重建 knowledge metadata |
| `/skills drafts` | 依 module → business cycle → document 顯示 draft skill |
| `/skills promote <draft>` | 將 draft skill 提升為正式 `skills/` skill 並更新索引 |

Record Mode 使用 polling snapshot diff，不依賴不穩定的 SAP COM events。監控器會在背景 thread 內重新取得 SAP session，並偵測：

- T-Code / screen 變化
- 活動視窗與彈窗開關
- 焦點欄位變化
- 文字欄位、下拉選單、checkbox、radio 變化
- 狀態列訊息
- 畫面跳轉後 SAP 自動帶出的非空欄位會標記為 `FIELD_DEFAULT`，保留在 raw events 供除錯，但不會成為 SOP 必填步驟

錄製檔會保留兩層資料：

- `raw_events`：原始 polling 事件，用於除錯
- `events`：壓縮後 SOP 步驟，用於 `/play`、Ask Mode 參考與後續引導；系統預設值不會被壓成使用者輸入步驟

Study Mode 透過 `sap_skill_library.py` 讀取 `skills/` 與 `recordings/` 中的 SOP，並以其內容作為互動式引導參考。若兩邊有同名項目，`skills/` 會優先，適合放置整理後的穩定教學流程。

若 `/study [名稱]` 找不到既有 SOP / Skill，v0.11 起會先建立 evidence pack，再進入受控探索草稿。教練必須標示 module、business_cycle、來源、信心與未知項目；若 evidence 不足，第一步會先請使用者確認 T-Code 或流程方向，而不是直接把推測當成正式流程。低信心情境會先提出 2-3 個候選方向讓使用者選擇，不會同時要求輸入 T-Code 與業務欄位。

若仍希望回到舊版「找不到 skill 就停止」的保守行為，可設定 `STUDY_REQUIRE_DRAFT_FLAG=true`，之後只有 `/study --draft [目標]` 或 `STUDY_ALLOW_DRAFT=true` 才會允許探索。草稿模式不會自動保存；若確定要把探索結果留作 skill，可使用 `/study --save-draft [目標]`，或在 `.env` 設定 `STUDY_SAVE_DRAFT_SKILL=true`。

### Macro 系統

Macro 是給 Auto / Study 共用的結構化操作描述，存放於 `macros/*.md`。它適合處理「GUI 元件 ID 已知、流程穩定、只有少數欄位值需要動態帶入」的操作，例如固定 T-Code、固定 checkbox/radio、固定按鈕，以及每次不同的物料號、單號、日期或公司代碼。

Macro 與 Recording / Skill 的定位不同：

- `/macro run` 會直接執行結構化步驟；不經過 LLM 重新決策，因此可減少掃描與 ReAct 輪數。
- `/macro study` 或 `/study --macro` 只把 Macro 轉成 Study Mode 參考資料；教練仍只會引導使用者，不會替使用者寫入 SAP。
- Auto Mode 收到自然語言時會先做 Macro pre-route：若 `SAP_MACRO_AUTOROUTE_ENABLED=true` 且匹配分數達 `SAP_MACRO_AUTOROUTE_MIN_SCORE`，會直接執行 Macro，不進入 ReAct；沒有高信心 Macro 才走原本 Auto agent。
- Auto pre-route 命中的 Macro 若在尚未寫入欄位、按按鈕、勾選或選列前就因畫面驗證失敗，會自動退回原本 Auto ReAct。明確使用 `/macro run` 時則保留失敗結果，方便修正 Macro。
- 固定值可直接寫在 `value`；動態值用 `{{變數名}}`，執行時用 `key=value` 提供，缺值時系統會提示輸入。
- Auto 執行採 MCP-first。實際順序是「掃描目前畫面 → 完整匹配 Start / step 元件 → 輸入值或執行動作 → 驗證 End」。文字欄位會優先使用 `sap_set_batch_fields` / `sap_set_fields_and_enter` 批次化；若 MCP 不可用，才退回 legacy GUI COM。
- Macro Markdown 必須有明確的 `## Start`、`## Steps`、`## End`。缺少 start/end 時，`/macro run` 會中止，避免在錯誤畫面直接操作。
- `wnd[0]/tbar[0]/okcd` 是全域命令欄，部分 MCP/GUI 掃描不會回傳工具列元素；若 active window 仍是 `wnd[0]`，Macro 不會只因 start check 掃不到 okcd 就中止。`tcode` / `key` 這類不需要 element ID 的步驟也不會額外做 step element 掃描。
- T-Code 切換後下一畫面尚未穩定時，Macro 會依 `SAP_MACRO_STEP_SCAN_RETRIES` / `SAP_MACRO_STEP_SCAN_RETRY_SECONDS` 重試 step target validation。
- `## Steps` 可加上 `alternate_ids` 欄位，以 `;`、`,` 或換行分隔候選 SAP GUI ID；主要 ID 找不到時，Macro 會依候選 ID 與欄位 label 做二次定位，定位成功後用實際 ID 執行。
- 若 `SAP_MACRO_LEARNING_ENABLED=true` 且 `SAP_MACRO_AUTO_WRITEBACK=true`，Macro 成功用替代 ID 執行後會自動更新該 macro 的 `alternate_ids`，並在 `macros/_backups/` 建立備份、在 `macros/_learned_index.json` 留 evidence。
- 同一替代 ID 連續成功達 `SAP_MACRO_PROMOTE_PRIMARY_AFTER` 次後，才會提升為 primary `element_id`；一次成功只會先加入 alternate IDs。
- 結果畫面 evidence 只記在 learned index，例如 MMBE screen 300 的 `IO_MATERIAL`，不會混入選擇畫面 step 的 `alternate_ids`。

Macro Markdown 格式：

```markdown
---
name: 查詢物料巨集
description: 進入 MM03 並查詢指定物料
mode: both
tags: MM,MM03,物料
---

# Macro: 查詢物料巨集

## Inputs
| name | label | default | required | description |
|---|---|---|---|---|
| material | 物料號 |  | true | 本次要查詢的物料號 |
| view_row | 檢視列索引 | 0 | false | MM03 選擇檢視彈窗的列索引 |

## Start
| condition | target | value | required | description |
|---|---|---|---|---|
| tcode |  | SESSION_MANAGER | true | 從起始畫面或可輸入 T-Code 的畫面開始 |
| element | wnd[0]/tbar[0]/okcd |  | true | 必須能找到 T-Code 欄位 |

## Steps
| step | action | element_id | value | label | element_type | expected_label | description |
|---|---|---|---|---|---|---|---|
| 1 | tcode | wnd[0]/tbar[0]/okcd | MM03 | 進入 MM03 | GuiOkCodeField |  | 開啟顯示物料交易 |
| 2 | input | wnd[0]/usr/ctxtRMMG1-MATNR | {{material}} | 物料 | GuiCTextField | 物料 | 輸入本次要查詢的物料號 |
| 3 | key |  | Enter | 送出 |  |  | 驗證物料號 |
| 4 | popup_table_confirm | wnd[1]/usr/tblSAPLMGMMTC_VIEW | {{view_row|default=0}} | 選擇檢視 | GuiTableControl |  | 選取檢視列並按繼續 |

## End
| condition | target | value | required | description |
|---|---|---|---|---|
| tcode |  | MM03 | true | 應停在 MM03 |
| title_contains |  | 顯示物料 | false | 標題應能識別為顯示物料流程 |
```

`## Start` / `## End` 支援的常用 condition：

| condition | 用途 |
|---|---|
| `tcode` | 比對目前交易碼 |
| `screen_number` | 比對目前 Dynpro screen number |
| `title` / `title_contains` | 比對畫面標題 |
| `active_window` | 比對目前活動視窗，例如 `wnd[0]` / `wnd[1]` |
| `element` | 確認指定 SAP GUI element ID 存在 |
| `status_text` / `status_type` | 比對狀態列訊息 |

`## Steps` 可加上 `element_type`、`expected_label`、`expected_text`、`expected_value` 等欄位。若設定了這些欄位，Macro 會在執行前掃描畫面並完整比對；比對失敗就停止，不會嘗試填值。

清單型查詢建議把非必要欄位設成 `skip_if_empty=true`。當該欄位的 `{{變數}}` 本次沒有提供值時，Macro 會略過該 step，不會填空值，也不會因為該欄位目前不可見而中止。若要讓 Macro 自動送出查詢，可加一個 key step，例如 `value={{execute_key}}` 並設定 `skip_if_empty=true`；使用時傳 `execute_key=Execute` 才會執行。

內建 Macro：

| Macro | 用途 |
|---|---|
| `va05_list_orders` | VA05 銷售訂單清單 |
| `va03_display_order` | VA03 顯示銷售訂單 |
| `vf05_list_billing` | VF05 請款單清單 |
| `mb51_material_docs` | MB51 物料憑證 |
| `mb52_stock_list` | MB52 庫存清單 |
| `mmbe_stock_overview` | MMBE 庫存總覽 |
| `me2m_po_by_material` | ME2M 採購單清單 |

Macro router 會先從自然語句抽取常見 SAP 代號，再決定是否直接執行 Macro：

- 庫存：`顯示所有庫存`、`庫存清單` 會走 `mb52_stock_list`；`查看物料MAT_001的庫存` 會走 `mmbe_stock_overview`，並自動抽取 `material=MAT_001`。
- 銷售訂單：`查看訂單500000001的情況`、`訂單號500000001查看情況`、`查看單號500000001的情況` 會走 `va03_display_order`，並自動抽取 `sales_order=500000001`；`訂單清單` 仍會走 `va05_list_orders`。
- 請款與客戶：`查詢付款人C0001的請款文件` 會走 `vf05_list_billing`，並自動抽取 `payer=C0001`；`查詢買方C0001的銷售訂單清單` 會走 `va05_list_orders`，並自動抽取 `sold_to=C0001`。
- 物料延伸查詢：`查詢物料MAT_001的採購單` 會走 `me2m_po_by_material`；`查詢物料MAT_001的物料憑證` 會走 `mb51_material_docs`。

若文字只有一串數字且沒有「訂單/單號/請款/採購」等上下文，router 會保持保守，避免把不同類型的 SAP 文件號誤判成 VA03。若文字出現 `請購單號`、`採購單號`、`請款單號` 這類非銷售文件語境，也不會強行導到 VA03。

支援的 `action`：

| action | 用途 |
|---|---|
| `tcode` | 執行 T-Code，優先用 `sap_execute_transaction` |
| `input` / `set_field` | 寫入一般文字欄位 |
| `checkbox` | 勾選或取消 checkbox，`value=true/false` |
| `radio` | 選取 radio button |
| `combo` | 依 key 或顯示文字選擇下拉選單 |
| `click` | 按下按鈕 |
| `tab` | 切換頁籤 |
| `key` | 送出 `Enter`、`Execute`、`Save`、`Back`、`Cancel` 或 F-key |
| `popup` | 呼叫 `sap_handle_popup`，`value=confirm/cancel/auto/read` |
| `table_row` | 依 row text 或 row index 選取表格列 |
| `popup_table_confirm` | 選取彈窗表格列並確認，適用 MM03 選擇檢視類流程 |
| `textedit` | 寫入多行文字 editor |
| `focus` | 將游標定位到指定元件 |
| `wait` | 等待指定秒數 |

使用範例：

```text
/macros
/macro show 查詢物料巨集
/macro run 查詢物料巨集 material=MAT_001
/macro study 查詢物料巨集 material=MAT_001
/macro learn recordings
/macro audit mmbe_stock_overview
/macro doctor mmbe_stock_overview
/study --macro 查詢物料巨集 material=MAT_001
```

### Knowledge 與階層式 Skill 蒸餾

無錄製 Study Mode 會先建立 evidence pack，而不是直接依模型常識教學。Evidence pack 會整理：

- 目前 SAP 畫面摘要
- 相似正式 skill / recording
- `skills/_drafts/` 中的階層式 draft
- `knowledge/_knowledge_index.json` 中的匯入文件
- 選擇性即時網搜結果（需 `STUDY_WEB_SEARCH_ENABLED=true`）

Evidence pack 會另外產生 `Evidence Summary`，包含 `evidence_level`、`best_confidence`、`should_confirm_flow` 與 `recommended_source`。Study Mode 會依這些欄位決定能否直接進入操作引導；若只有 `llm_prior_only` 或 `should_confirm_flow=true`，必須先做流程方向確認。

資料匯入後會以「所屬模組 → 業務循環 → 文件」建立節點，例如 `MM > Material Master > 物料查詢SOP.md`。索引會保存 `module`、`business_cycle`、`document_title`、`source_path/url`、`source_type`、`tags`、`tcode`、`confidence`、`content_hash` 等 metadata。若同一份文件跨多個模組或循環，會保留同一來源與 hash，但產生多個候選節點。

蒸餾流程不會直接寫入正式 `skills/`：

```bash
/knowledge import docs/mm03_material_master.md --type company --tags MM,MM03,物料
/knowledge distill 查詢物料
/skills drafts
/skills promote MM/Material_Master/mm03_material_master.md
```

`/knowledge distill` 會把草稿放在 `skills/_drafts/<module>/<business_cycle>/<document>.md`，內容包含目的、適用情境、前提條件、T-Code、操作步驟、使用者需確認資料、風險、來源、信心與未驗證項目。只有 `/skills promote` 後，草稿才會變成正式 Study skill 並被 `/study` 優先命中。

Skill 草稿保存會先清理自然語句，避免整句話直接變成檔名。例如 `/study --save-draft 教我如何查詢物料` 會保存為 `skills/查詢物料.md`，同時保留原始查詢作為別名；之後輸入 `/study 查詢物料` 或 `/study 教我如何查詢物料` 都會命中同一份 skill。`/recordings` 也會依正規化名稱去重，避免同一流程重複顯示。

Skill Library 會維護 `skills/_skill_index.json`，替每個 skill 建立 canonical name、tags、來源與檔案路徑。查詢 skill 時會綜合檔名、Markdown 標題、`Tags:` metadata、原始查詢別名與 SAP 關鍵詞做語意式匹配；例如 `查物料`、`教我查物料`、`查詢物料` 會對應到同一個 `查詢物料` skill。

若工具執行中途被 Ctrl+C 或例外中斷，LLM API 可能拒絕後續請求並回報 `assistant message with tool_calls must be followed by tool messages`。目前 Agent 會在送出下一次 API 前自動修復這類 dangling tool-call history；通常可直接重新輸入指令，不需要手動 `/reset`。

Study Mode 預設採互動式參考引導流程；錄製值與錄製畫面不是絕對準則，而是提示使用者理解流程的參考資料：

- T-Code 切換會重放 OKCode + Enter，或直接使用 `set_tcode()`
- 若目前已在目標 T-Code，T-Code / OKCode 參考步驟會自動略過
- 若目前不在起始畫面，T-Code 會自動改成 `/nTCODE` 格式，例如 `VF05` → `/nVF05`
- 下拉式選單、radio/checkbox 等制式選取會由 agent 自動處理
- 一般文字欄位會由 agent 先 focus/highlight 對應 SAP 欄位，再詢問本次要使用的值；高亮使用 `visualize_element()` / `Visualize(True)`
- 欄位提示會從目前畫面 scan 的 `fields` 解析 SAP label / tooltip / name，優先顯示「付款人」「請款文件開始」這類畫面文字，並附上目前值、欄位型別、畫面名稱與元件 ID
- 若 SAP 版面導致 label 沒配到，Study Mode 會再用座標尋找鄰近左側 label，最後才使用內建欄位字典或技術欄位名作 fallback
- 錄製值只作為參考值；若目前 SAP 欄位已有值，直接 Enter 會使用目前值作為本次預設，輸入新值可覆寫，例如日期區間、付款人代號
- AI 教練會優先檢查目前畫面 `fields[].value`；欄位已有值時，會引導確認沿用，而不是要求重新輸入 SOP 舊值
- 使用者在 SAP GUI 手動輸入後，agent 會讀回欄位值並確認是否符合本次值
- 若教練提示使用者選擇選項，`guide_user_action()` 會把輸入內容作為 `user_response` 回傳給 Study Mode；例如選擇「結束教學」會直接收斂並產生摘要，避免重複詢問
- `guide_user_action()` 會把「要做什麼」放在短 instruction，並把原因、來源、信心與候選選項拆成獨立欄位；UI 會自動壓縮過長內容，避免 Study Step 視窗變成大段推理文字。
- 輸入值類提示必須指向真實可寫欄位；若模型傳入 `wnd[0]/usr`、container、label 或 status bar，工具會拒絕彈窗並回傳 `target_actionable=false`，要求重新定位或先引導使用者揭露欄位
- 若 SAP 狀態列提示必填欄位，但目前 scan 沒有該欄位 ID，Study Mode 應引導使用者透過表格水平捲動、項目明細、版面/個人設定或欄位搜尋把欄位顯示出來，再進行填值
- 若驗證不符合，可選擇重試輸入、接受目前值、略過或中止
- 畫面跳轉事件代表錄製時偵測到頁面變化；若沒有更細的按鈕事件，Study Mode 會先嘗試 F8/Execute，若該 vkey 未啟用則改送 Enter
- 畫面跳轉送出後會掃描 SAP，確認 T-Code / screen 已到錄製目標；若仍停在原畫面、出現必填欄位彈窗或狀態列錯誤，會進入 retry/manual/skip/abort recovery
- 若目前畫面已離開起點但與錄製目標 screen 不同，Study Mode 會進入互動式 review，由使用者決定接受目前狀態、重試、手動完成、略過或中止
- Recovery 偵測到彈窗內有空白或必填欄位時，Enter / `g` 會啟動 guided popup recovery：逐欄高亮、提示填值、讀回驗證，最後送出彈窗並重新確認原 SOP 目標畫面
- 自動步驟失敗時可選擇 retry、manual、skip 或 abort，不會直接卡死
- 自動步驟完成後固定等待 5 秒；人工步驟由使用者按 Enter 控制節奏，不再額外等待
- Study Mode 不會在完成一輪教學回覆後自動切回 Auto Mode；要離開教學需明確輸入 `/auto`、`/ask`、`/solve`，或在 UI 按對應模式按鈕

`STUDY_ADAPTIVE_MODE=true` 是預設值，代表 SOP 是參考資料而不是絕對腳本。`STUDY_HUMAN_FIELD_INPUT=true` 目的是避免像 SE38 ABAP editor 這類 SAP GUI 特殊控制元件因格式不同而無法可靠讀寫。`STUDY_FOCUS_HUMAN_FIELDS=true` 會在提示前嘗試將游標移到欄位並高亮，`STUDY_VISUALIZE_SECONDS` 控制高亮停留秒數。`STUDY_PROMPT_FIELD_VALUES=true` 會把錄製值當成可覆寫的參考參數；`STUDY_AUTOFILL_PROMPTED_VALUES=false` 代表預設由使用者在 SAP GUI 手動輸入並由 agent 驗證。若要改回舊式全自動欄位重放，可將 human input 設為 `false`。

停止錄製時會自動合併同一欄位的連續輸入，只保留最後值。例如 `C → C00 → C0001` 會壓縮成 `C0001`。

若 `/stop` 後仍顯示 0 筆操作，先看終端機是否有 `[Monitor] 快照擷取警告` 或 `[Monitor] 背景監控讀取 SAP 失敗`。常見原因是 SAP GUI Scripting 未啟用、目前操作在另一個 SAP session、或 SAP 正在忙碌造成 COM 暫時不可讀。

### 使用範例

```
🟣 AUTO You > 幫我進入 VA01 建立銷售訂單
  [Agent] 正在掃描 SAP 畫面...
  [Agent] 呼叫工具: set_tcode({"tcode": "VA01"})

🟢 ASK  You > 訂單類型這格要填什麼？
  AI > 根據畫面狀態，「訂單類型」欄位 (ctxtVBAK-AUART) 常見值為：
       - OR: 標準訂單
       - RE: 退貨訂單
       - SO: 急件訂單
       請依據您的業務需求選填。

🔴 REC  You > （在 SAP 中操作，動作自動被錄製）
  📝 [  1] 🔄 交易切換  SESSION_MANAGER → VA01
  📝 [  2] ✏️ 欄位修改  ctxtVBAK-AUART = "OR"
```

### 彈窗與錯誤解析

Scanner 會將 SAP 畫面整理成 LLM 容易判斷的摘要：

- `active_window`：目前活動視窗，例如 `wnd[2]`
- `active_popup`：最上層彈窗，含 title、actions、messages、fields
- `messages`：彈窗中的錯誤/提示文字，例如「輸入一數值」
- `fields`：將畫面 label 與可編輯欄位配對，例如 `標題 → wnd[1]/usr/txt...`
- `dropdown=true`：標記 SAP `GuiComboBox` 下拉式選單，並盡量列出可選 `options`
- `editors`：列出 ABAP/text editor 這類 `GuiAbapEditor` / `GuiShell` 控件，例如 `wnd[0]/usr/cntlEDITOR/shellcont/shell`，並顯示可用的 editor API 能力
- `focused_element`：目前焦點或紅框欄位（若 SAP GUI COM 可讀取）

Auto Mode 每次工具執行後都會重新掃描並回傳 compact `screen_after`，避免畫面切換後仍使用舊的元件 ID。

下拉式選單會使用 `select_combo()`，可依 option `key` 或顯示文字選取；`set_text()` 遇到 `GuiComboBox` 時也會自動改走下拉選取邏輯。

Checkbox / radio 會使用 `read_checkbox()` / `set_checkbox()` 讀寫 `Selected` 狀態，不再把 `True` / `False` 當文字填入欄位。Scanner 的 `fields` 會將這類元件的 `value` 顯示為 `True` / `False`，並保留 `selected` 布林值，讓 Auto Mode 能先判斷目前狀態再決定是否切換。

SAP `GuiTableControl` 內的 checkbox/列選取則使用 `select_table_row()`，例如 MM03「選擇檢視」彈窗中的「基本資料 1」。這類畫面常只掃到文字 cell，但實際勾選框藏在 table 選取欄內；Scanner 會在 `active_popup.tables` 列出可見 row，Agent 應呼叫 `select_table_row(row_text="基本資料 1")` 後再按 Enter 或 Continue。

ABAP 原始碼編輯器通常是 `GuiAbapEditor` / `GuiShell`，不能假設可用一般 `.Text` 讀寫。Auto Mode 讀取程式碼會使用 `read_editor_text()`，優先走 `GetLineText` / `GetUnprotectedTextPart`；寫入程式碼會使用 `set_editor_text()`，優先走 `SelectAll + ReplaceSelection`、`SetSelectionIndexes + ReplaceSelection`、`SetUnprotectedTextPart`、`InsertText`，最後才用剪貼簿 fallback。Study Mode 預設仍會將程式碼內容輸入交給使用者手動完成。

Ask Mode 若偵測到目前畫面是 SE38/ABAP editor，會自動讀取 editor source 並放入 `editor_sources` context，因此可以直接詢問「這個程式是幹嘛的」。`EDITOR_CONTEXT_MAX_CHARS` 控制最多放入 LLM 的程式碼字元數。

Solve Mode 是獨立的操作入口，但底層沿用 Ask Mode 的唯讀畫面掃描流程。差異在 prompt 與 context：`/ask` 適合一般問答與程式碼說明，`/solve` 會額外提供 `solve_diagnostics`，優先彙整 `active_popup`、`status_bar`、`messages`、table row、focused element 與需要注意的欄位。回答時會先列出實際讀到的錯誤/訊息，再判斷原因與建議處理；若畫面沒有錯誤清單，不應先猜常見修法，而是要求使用者先打開或展開錯誤清單。

為避免 SAP GUI 將 ATC、Examples 等選單 `GuiShell` 誤判成程式碼 editor，`read_editor_text()` 會回傳 `looks_like_source`；Ask Mode 只會把看起來像 ABAP source 的內容交給 LLM，非 source 候選會以讀取失敗原因呈現。

---

## 📁 專案結構

```
SAP_Copilot/
├── main.py              # CLI 入口 (REPL 互動介面)
├── ui_app.py            # Phase 4 Tkinter 懸浮控制台
├── mcp_client.py        # Stage 2 MCP SAP GUI client，MCP primary path
├── setup_mcp.py         # 初始設定：clone/update MCP fork 並更新 .env
├── setup_mcp.bat        # Windows MCP 初始設定批次檔
├── start.py             # 啟動器：確認 SAP / Copilot 登入後啟動 CLI 或 UI
├── start.bat            # Windows 啟動批次檔
├── start_ui.bat         # Windows UI 啟動批次檔
├── llm_provider.py      # LLM provider adapter：GitHub Copilot / Codex OAuth
├── copilot_auth.py      # GitHub Copilot OAuth 認證
├── sap_core.py          # SAP GUI COM 連線管理
├── sap_agent_tools.py   # 畫面掃描 (Scanner) + 操作工具 (Actor)
├── llm_brain.py         # LLM Agent (ReAct Loop + Auto/Ask/Solve Mode)
├── sap_monitor.py       # 背景 Polling 監控器
├── sap_recorder.py      # SOP 錄製管理器
├── sap_skill_library.py # Phase 3 SOP / Skill Library
├── sap_macro_library.py # 結構化 Markdown Macro 解析與執行
├── sap_knowledge_library.py # Study evidence / knowledge / draft distillation
├── sap_login.py         # SAP GUI 自動登入腳本
├── plan.md              # 開發計劃藍圖
├── knowledge/           # 匯入文件、正規化文字與 knowledge index
├── macros/              # Auto / Study 共用 Markdown Macro
├── skills/              # 整理後的穩定教學 Skill；_drafts/ 存放蒸餾草稿
├── recordings/          # SOP 錄製檔案 (JSON)
└── .venv/               # Python 虛擬環境
```

---

## 🛡️ 安全設計

### Copilot 429 與節流

Auto Mode 是 ReAct loop，一個使用者指令可能觸發多次 LLM 呼叫。若模型較重、SAP 畫面摘要過大、或短時間連續操作，LLM provider 可能回傳 `429`。

目前已加入以下保護：

- 對 `429` / `500` / `502` / `503` / `504` 自動 retry
- 優先遵守 `Retry-After` header
- 未提供 `Retry-After` 時使用 exponential backoff
- `screen_after` 使用 compact summary，不再回傳完整 SAP DOM
- 使用 `COPILOT_HISTORY_LIMIT` 裁切跨輪對話歷史
- 使用 `COPILOT_MAX_ITERATIONS` 限制單次 Auto Mode 最大迭代

若仍頻繁 429，建議先調整：

```env
COPILOT_MODEL=gpt-5-mini
COPILOT_MAX_ITERATIONS=4
COPILOT_HISTORY_LIMIT=10
COPILOT_RETRY_MAX_SECONDS=90
```

### 敏感操作保護

以下操作會觸發 **Human-in-the-loop** 確認機制，AI 無法自行執行：

- 💾 **Save** / 儲存（`Ctrl+S`, VKey 11）
- 📮 **Post** / 過帳
- 🗑️ **Delete** / 刪除
- ✅ **Confirm** / 確認
- 📤 **Submit** / 提交
- 🔓 **Release** / 核發

```
⚠️ 偵測到敏感操作: Ctrl+S (Save)，需要使用者確認
工具: send_vkey, 參數: {'vkey': 11}

確認執行？(y/n): _
```

### 防禦性 COM 編程

- 所有 `session.FindById()` 呼叫均以 `try-except` 包裝
- 使用 `_safe_get_attr()` 取代 `hasattr()`（避免 `win32com` 內部 `IndexError`）
- 不快取 SAP UI 元件，每次操作即時重新取得

---

## 🗺️ 開發路線圖

- [x] **Phase 1** — 基礎建設與 Auto Mode 雛形 (CLI)
- [x] **Phase 2** — 監控系統與 Record / Ask Mode
- [x] **Phase 3** — 視覺化引導與 Study Mode（`.Visualize(True)` 高亮 + Skill Library + Guided Recovery）
- [x] **Phase 4** — UI 整合與最終封裝（Tkinter 懸浮控制台 + CLI fallback）

---

## 📄 授權

本專案目前為內部開發使用。
