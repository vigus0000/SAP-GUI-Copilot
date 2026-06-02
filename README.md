# 🤖 SAP GUI Copilot

> **用自然語言操作 SAP，告別繁瑣的 T-Code。**

SAP GUI Copilot 是一個以 Python 打造的 SAP GUI 智慧助手，透過 COM Interface 深度整合 SAP GUI，結合 GitHub Copilot LLM 實現自然語言驅動的 ERP 操作自動化。

---

## ✨ 核心特色

| 特色 | 說明 |
|------|------|
| 🟣 **Auto Mode** | 用自然語言下指令，AI 自動操作 SAP 畫面（ReAct Loop：掃描 → 思考 → 執行 → 驗證） |
| 🟢 **Ask Mode** | 結合當前畫面狀態的 Context-Aware 問答，回答「這格該填什麼」「為何報錯」 |
| 🔴 **Record Mode** | 背景錄製使用者的 SAP 操作流程，自動產生 JSON 格式的 SOP 腳本 |
| ▶ **Study Mode** | 使用 `/study [名稱]` 在 Auto Mode 重放錄製 SOP，每個 SOP 步驟間隔 5 秒 |
| 🪟 **Popup-aware Scanner** | 可解析 SAP 多層彈窗、錯誤訊息、焦點欄位、下拉選單、Editor，以及 label ↔ input 對應 |
| 🔁 **畫面切換自癒** | 每次工具操作後重新掃描 SAP，避免沿用舊畫面元件 ID |
| 🔒 **Human-in-the-loop** | 敏感操作（儲存、刪除、過帳）強制人工確認，杜絕 AI 寫入錯誤資料 |
| 🚦 **Copilot 節流保護** | 支援 429 retry/backoff、模型設定與對話歷史裁切 |
| 💰 **零額外費用** | 直接使用 GitHub Copilot 訂閱的 LLM 能力，無需額外 API Key |

---

## 🏗️ 系統架構

```
┌──────────────────────────────────────────────────────┐
│                   main.py (CLI REPL)                 │
│         🟣 Auto  │  🟢 Ask  │  🔴 Record            │
├──────────┬───────┴──────────┴───────┬────────────────┤
│          │                          │                │
│  llm_brain.py              sap_monitor.py           │
│  (LLM Agent)               (Background Polling)     │
│  ReAct Loop                 0.3s Snapshot Diff       │
│          │                          │                │
│          ▼                          ▼                │
│  sap_agent_tools.py         sap_recorder.py         │
│  (Scanner + Actor)          (SOP JSON Manager)       │
│          │                                           │
│          ▼                                           │
│  sap_core.py ──── SAP GUI COM Interface ──── SAP    │
│          │                                           │
│  copilot_auth.py ── GitHub Copilot API ── LLM       │
└──────────────────────────────────────────────────────┘
```

---

## 📦 技術棧

| 項目 | 技術 |
|------|------|
| 語言 | Python 3.10+ |
| SAP 整合 | `pywin32` (COM Interface) |
| LLM 後端 | GitHub Copilot API (`api.githubcopilot.com`) |
| 認證 | GitHub OAuth Device Flow |
| 監控策略 | Polling-based Snapshot Diff（取代不穩定的 `WithEvents`） |
| SOP 儲存 | JSON 檔案 (`./recordings/`) |
| 套件管理 | `uv` |

---

## 🚀 快速開始

### 前置需求

- **Windows** 作業系統（SAP GUI 僅支援 Windows）
- **SAP GUI** 已安裝且啟用 Scripting 功能
- **GitHub Copilot** 訂閱（Individual / Business / Enterprise）
- **Python 3.10+**

### 安裝

```bash
# 1. Clone 專案
git clone https://github.com/your-repo/SAP_Copilot.git
cd SAP_Copilot

# 2. 建立虛擬環境
uv venv

# 3. 安裝套件
uv pip install pywin32 requests python-dotenv
```

### 設定 `.env`

複製 `.env.example` 為 `.env`，填入 SAP 登入資訊與 Copilot 設定：

```env
MANDT=
BNAME=
BCODE=
SAP_GUI_PATH=
connection=

# Copilot model：目前建議 gpt-5-mini
COPILOT_MODEL=gpt-5-mini

# Auto Mode 控制
COPILOT_MAX_ITERATIONS=6
COPILOT_HISTORY_LIMIT=14

# 429 / 暫時性錯誤 retry
COPILOT_MAX_RETRIES=4
COPILOT_RETRY_BASE_SECONDS=2
COPILOT_RETRY_MAX_SECONDS=60

# Study Mode
STUDY_PREFIX_TCODE_OUTSIDE_START=true
STUDY_INITIAL_TCODES=SESSION_MANAGER,S000
```

#### Copilot 模型建議

本專案使用 `api.githubcopilot.com/chat/completions`。不同帳號、方案與組織政策可用模型可能不同，可用 `/models` 端點查詢實際清單。

| 用途 | `.env` 代號 | 說明 |
|------|-------------|------|
| 預設推薦 | `gpt-5-mini` | 快、成本低、適合高頻 ReAct 操作 |
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
# 1. 登入 SAP GUI（或手動登入）
python sap_login.py

# 2. 啟動 Copilot（首次會引導 GitHub 授權）
python main.py
```

首次啟動時，程式會顯示 GitHub Device Flow 授權碼，在瀏覽器中完成授權後即可使用。

---

## 📖 指令參考

### 基礎指令

| 指令 | 說明 |
|------|------|
| `自然語言` | 直接輸入指令，AI 自動操作 SAP（Auto Mode）或回答問題（Ask Mode） |
| `/scan` | 掃描並顯示當前 SAP 畫面、彈窗、錯誤訊息、欄位摘要與可操作元件 |
| `/login` | 重新執行 GitHub Copilot 授權流程 |
| `/reset` | 重置 AI 對話歷史 |
| `/quit` | 結束程式 |

### 模式切換

| 指令 | 說明 |
|------|------|
| `/auto` | 切換至 🟣 Auto Mode — AI 可呼叫工具操作 SAP |
| `/ask` | 切換至 🟢 Ask Mode — AI 只回答問題，不執行操作 |

### 錄製管理

| 指令 | 說明 |
|------|------|
| `/record [名稱]` | 開始錄製 🔴 — 背景記錄使用者在 SAP 中的操作 |
| `/stop` | 停止錄製並儲存 SOP 檔案 |
| `/recordings` | 列出所有已錄製的 SOP |
| `/play [名稱]` | 顯示指定 SOP 的完整操作步驟 |
| `/study [名稱]` | 在 Auto Mode 執行錄製 SOP，每個 SOP 步驟間隔 5 秒 |

Record Mode 使用 polling snapshot diff，不依賴不穩定的 SAP COM events。監控器會在背景 thread 內重新取得 SAP session，並偵測：

- T-Code / screen 變化
- 活動視窗與彈窗開關
- 焦點欄位變化
- 文字欄位、下拉選單、checkbox、radio 變化
- 狀態列訊息

錄製檔會保留兩層資料：

- `raw_events`：原始 polling 事件，用於除錯
- `events`：壓縮後 SOP 步驟，用於 `/play`、Ask Mode 參考與後續重放

Study Mode 使用 `events` 進行語意重放：

- T-Code 切換會重放 OKCode + Enter，或直接使用 `set_tcode()`
- 若目前不在起始畫面，T-Code 會自動改成 `/nTCODE` 格式，例如 `VF05` → `/nVF05`
- 一般欄位使用 `set_text()`，下拉式選單使用 `select_combo()`，radio/checkbox 會用 click/Select
- 畫面跳轉事件代表錄製時偵測到頁面變化；若沒有更細的按鈕事件，Study Mode 會先嘗試 F8/Execute，若該 vkey 未啟用則改送 Enter
- 每個 SOP 步驟完成後固定等待 5 秒，方便觀察 SAP GUI 狀態與彈窗

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
- `editors`：列出 ABAP/text editor 這類 `GuiShell` 控件，例如 `wnd[0]/usr/cntlEDITOR/shellcont/shell`
- `focused_element`：目前焦點或紅框欄位（若 SAP GUI COM 可讀取）

Auto Mode 每次工具執行後都會重新掃描並回傳 compact `screen_after`，避免畫面切換後仍使用舊的元件 ID。

下拉式選單會使用 `select_combo()`，可依 option `key` 或顯示文字選取；`set_text()` 遇到 `GuiComboBox` 時也會自動改走下拉選取邏輯。

ABAP 原始碼編輯器通常是 `GuiShell`，不能用一般 `.Text` 寫入。Auto Mode 會使用 `set_editor_text()`，優先嘗試 SAP editor 原生 API，失敗時再用剪貼簿貼上 fallback。

---

## 📁 專案結構

```
SAP_Copilot/
├── main.py              # CLI 入口 (REPL 互動介面)
├── copilot_auth.py      # GitHub Copilot OAuth 認證
├── sap_core.py          # SAP GUI COM 連線管理
├── sap_agent_tools.py   # 畫面掃描 (Scanner) + 操作工具 (Actor)
├── llm_brain.py         # LLM Agent (ReAct Loop + Auto/Ask Mode)
├── sap_monitor.py       # 背景 Polling 監控器
├── sap_recorder.py      # SOP 錄製管理器
├── sap_login.py         # SAP GUI 自動登入腳本
├── plan.md              # 開發計劃藍圖
├── recordings/          # SOP 錄製檔案 (JSON)
└── .venv/               # Python 虛擬環境
```

---

## 🛡️ 安全設計

### Copilot 429 與節流

Auto Mode 是 ReAct loop，一個使用者指令可能觸發多次 LLM 呼叫。若模型較重、SAP 畫面摘要過大、或短時間連續操作，GitHub Copilot 可能回傳 `429`。

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
- [ ] **Phase 3** — 視覺化引導與 Study Mode（`.Visualize(True)` 高亮 + 技能庫）
- [ ] **Phase 4** — UI 整合與最終封裝（PyQt6 / CustomTkinter 懸浮對話框）

---

## 📄 授權

本專案目前為內部開發使用。
