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
| 🔒 **Human-in-the-loop** | 敏感操作（儲存、刪除、過帳）強制人工確認，杜絕 AI 寫入錯誤資料 |
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
uv pip install pywin32 requests
```

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
| `/scan` | 掃描並顯示當前 SAP 畫面的結構化元件資訊 |
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
