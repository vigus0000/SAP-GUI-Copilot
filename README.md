# 🤖 SAP GUI Copilot

> **用自然語言操作 SAP，告別繁瑣的 T-Code。**

SAP GUI Copilot 是一個以 Python 打造的 SAP GUI 智慧助手，透過 COM Interface 深度整合 SAP GUI，結合 GitHub Copilot LLM 實現自然語言驅動的 ERP 操作自動化。

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
| 🚦 **Copilot 節流保護** | 支援 429 retry/backoff、模型設定與對話歷史裁切 |
| 💰 **零額外費用** | 直接使用 GitHub Copilot 訂閱的 LLM 能力，無需額外 API Key |

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
│  sap_agent_tools.py         sap_recorder.py         │
│  (Scanner + Actor)          (SOP JSON Manager)       │
│          │                                           │
│  sap_skill_library.py ── skills/ + recordings/       │
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
| SOP / Skill 儲存 | JSON 檔案 (`./skills/`, `./recordings/`) |
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
EDITOR_CONTEXT_MAX_CHARS=12000

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
STUDY_ALLOW_DRAFT=false
STUDY_SAVE_DRAFT_SKILL=false
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
# 推薦：先檢查 SAP / Copilot 登入，再啟動主程式
python start.py

# Phase 4 UI：啟動懸浮控制台
python start.py --ui

# Windows 也可直接執行
start.bat

# Windows UI 批次檔
start_ui.bat
```

`start.py` 會先確認 SAP GUI 是否已登入；若尚未登入，會執行 `sap_login.py`。接著檢查 GitHub Copilot 授權，完成後預設啟動 `main.py`。若使用 `--ui`，則啟動 `ui_app.py` 的 Tkinter 懸浮控制台。首次啟動時，程式會顯示 GitHub Device Flow 授權碼，在瀏覽器中完成授權後即可使用。

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
| `/solve` | 切換至 🟡 Solve Mode — AI 只診斷當前畫面/彈窗/錯誤並給處理建議，不執行操作 |

### 錄製管理

| 指令 | 說明 |
|------|------|
| `/record [名稱]` | 開始錄製 🔴 — 背景記錄使用者在 SAP 中的操作 |
| `/stop` | 停止錄製並儲存 SOP 檔案 |
| `/recordings` | 列出所有 SOP / Skill（`skills/` 優先，其次 `recordings/`） |
| `/play [名稱]` | 顯示指定 SOP / Skill 的完整操作步驟 |
| `/study [名稱]` | 執行指定 SOP / Skill，找不到時預設停止，避免沒有錄製依據時亂教 |
| `/study --draft [目標]` | 明確啟動探索草稿；AI 只能依目前 SAP 畫面與使用者確認引導，不會自動保存 |
| `/study --save-draft [目標]` | 明確啟動探索草稿，完成後保存為 Markdown skill 草稿 |

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

若 `/study [名稱]` 找不到既有 SOP / Skill，系統預設不會啟動教學，避免 AI 在沒有錄製依據時把推測當成流程。建議先使用 `/record [名稱]` 錄製一次真實操作，或用 `/solve` 針對當前畫面卡關取得處理建議。

若只是要探索未知流程，必須明確使用 `/study --draft [目標]`。草稿模式會先標示不確定性，只依目前 SAP 畫面、狀態列、彈窗、可見欄位與使用者確認引導，不會把候選 T-Code 或 SAP 常識說成已驗證事實，也不會自動保存。若確定要把探索結果留作草稿 skill，可使用 `/study --save-draft [目標]`，或在 `.env` 設定 `STUDY_SAVE_DRAFT_SKILL=true`。

Skill 草稿保存會先清理自然語句，避免整句話直接變成檔名。例如 `/study --save-draft 教我如何查詢物料` 會保存為 `skills/查詢物料.md`，同時保留原始查詢作為別名；之後輸入 `/study 查詢物料` 或 `/study 教我如何查詢物料` 都會命中同一份 skill。`/recordings` 也會依正規化名稱去重，避免同一流程重複顯示。

Skill Library 會維護 `skills/_skill_index.json`，替每個 skill 建立 canonical name、tags、來源與檔案路徑。查詢 skill 時會綜合檔名、Markdown 標題、`Tags:` metadata、原始查詢別名與 SAP 關鍵詞做語意式匹配；例如 `查物料`、`教我查物料`、`查詢物料` 會對應到同一個 `查詢物料` skill。

若工具執行中途被 Ctrl+C 或例外中斷，Copilot API 可能拒絕後續請求並回報 `assistant message with tool_calls must be followed by tool messages`。目前 Agent 會在送出下一次 API 前自動修復這類 dangling tool-call history；通常可直接重新輸入指令，不需要手動 `/reset`。

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
- 若驗證不符合，可選擇重試輸入、接受目前值、略過或中止
- 畫面跳轉事件代表錄製時偵測到頁面變化；若沒有更細的按鈕事件，Study Mode 會先嘗試 F8/Execute，若該 vkey 未啟用則改送 Enter
- 畫面跳轉送出後會掃描 SAP，確認 T-Code / screen 已到錄製目標；若仍停在原畫面、出現必填欄位彈窗或狀態列錯誤，會進入 retry/manual/skip/abort recovery
- 若目前畫面已離開起點但與錄製目標 screen 不同，Study Mode 會進入互動式 review，由使用者決定接受目前狀態、重試、手動完成、略過或中止
- Recovery 偵測到彈窗內有空白或必填欄位時，Enter / `g` 會啟動 guided popup recovery：逐欄高亮、提示填值、讀回驗證，最後送出彈窗並重新確認原 SOP 目標畫面
- 自動步驟失敗時可選擇 retry、manual、skip 或 abort，不會直接卡死
- 自動步驟完成後固定等待 5 秒；人工步驟由使用者按 Enter 控制節奏，不再額外等待

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
├── start.py             # 啟動器：確認 SAP / Copilot 登入後啟動 CLI 或 UI
├── start.bat            # Windows 啟動批次檔
├── start_ui.bat         # Windows UI 啟動批次檔
├── copilot_auth.py      # GitHub Copilot OAuth 認證
├── sap_core.py          # SAP GUI COM 連線管理
├── sap_agent_tools.py   # 畫面掃描 (Scanner) + 操作工具 (Actor)
├── llm_brain.py         # LLM Agent (ReAct Loop + Auto/Ask/Solve Mode)
├── sap_monitor.py       # 背景 Polling 監控器
├── sap_recorder.py      # SOP 錄製管理器
├── sap_skill_library.py # Phase 3 SOP / Skill Library
├── sap_login.py         # SAP GUI 自動登入腳本
├── plan.md              # 開發計劃藍圖
├── skills/              # 整理後的穩定教學 Skill (JSON)
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
- [x] **Phase 3** — 視覺化引導與 Study Mode（`.Visualize(True)` 高亮 + Skill Library + Guided Recovery）
- [x] **Phase 4** — UI 整合與最終封裝（Tkinter 懸浮控制台 + CLI fallback）

---

## 📄 授權

本專案目前為內部開發使用。
