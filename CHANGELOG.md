# Changelog

本專案的所有重要變更都記錄在此檔案中。

格式基於 [Keep a Changelog](https://keepachangelog.com/zh-TW/1.1.0/)，版本號遵循 [Semantic Versioning](https://semver.org/lang/zh-TW/)。

---

## [0.6.5] - 2026-06-08

#### Added
- **SAP TableControl 列選取工具**
  - 新增 `select_table_row()`，可依 `row_text` 或 `row_index` 選取 `GuiTableControl` 內的列。
  - 針對 MM03「選擇檢視」這類彈窗，會自動從文字 cell 推導常見 checkbox ID，例如 `chkMSICHTAUSW-KZSEL[0,row]`，解決只點到文字但沒有勾選檢視的問題。
  - Scanner 新增 `tables` 摘要，`active_popup.tables[].rows[]` 會列出 table row 文字，讓 LLM 可直接使用 `select_table_row(row_text="基本資料 1")`。

#### Changed
- Auto prompt 明確要求遇到彈窗 table 內 checkbox/選取列時優先使用 `select_table_row()`，選取後再按 Enter/Continue。
- `/scan` 顯示彈窗時會列出 table rows，方便除錯 table checkbox 類畫面。

## [0.6.4] - 2026-06-08

#### Added
- **Checkbox / Radio 專用工具**
  - 新增 `read_checkbox()` 與 `set_checkbox()`，可穩定讀寫 `GuiCheckBox` / `GuiRadioButton` 的 `Selected` 狀態。
  - `set_text()` 遇到 checkbox/radio 時會自動轉用 `set_checkbox()`，避免把 True/False 當文字寫入。
  - `handle_popup()` 依 label 填值時若目標是 checkbox/radio，也會改用 `Selected` 狀態設定。
- **Skill Tag Index**
  - 新增 `skills/_skill_index.json`，索引每個 skill 的 canonical name、tags、來源與路徑。
  - Skill 自動保存時會寫入 `Tags:` metadata，查詢時會綜合檔名、標題、別名、tags 與 SAP 關鍵詞做語意式匹配。

#### Changed
- Scanner 的 `fields` 對 checkbox/radio 會將 `value` 顯示為 `True` / `False`，並保留 `selected` 布林狀態，方便 LLM 判斷目前是否已勾選。
- Skill 查詢會將 `查物料` 正規化為 `查詢物料`，避免短口語另存成重複 skill。

## [0.6.3] - 2026-06-05

#### Fixed
- **Copilot tool call 歷史修復**
  - 修正前一次工具呼叫被中斷後，對話歷史中殘留 assistant `tool_calls` 但缺少對應 `tool` response，導致後續每次呼叫 Copilot API 都回 400 `invalid_request_body`。
  - 送出 API 前會自動清理 dangling tool-call history，讓使用者不必手動 `/reset` 才能恢復。
  - Auto / Study Mode 的工具執行若遇到例外或 Ctrl+C 中斷，會補上一筆 tool error response，降低再次污染 conversation history 的機率。

## [0.6.2] - 2026-06-04

#### Changed
- **Skill 命名與查詢規則**
  - `/study` 即席教學自動保存 skill 時，會將自然語句正規化為穩定名稱，例如 `教我如何查詢物料` → `查詢物料`。
  - Skill 查詢支援原始使用者說法、正規化名稱、Markdown 標題與 `原始查詢` / `查詢別名` metadata。
  - `/recordings` 清單會依正規化名稱去重，避免同一個流程同時顯示 `教我如何查詢物料` 與 `查詢物料`。

#### Fixed
- 移除自動保存造成的重複物料查詢 skill，保留 canonical 的 `查詢物料.md`。

## [0.6.1] - 2026-06-04

#### Fixed
- **Study Mode 選項回覆卡住**
  - `guide_user_action()` 現在會把使用者在提示中輸入的內容回傳為 `user_response`，避免使用者輸入 `4`、`完成` 等回覆後 LLM 看不到答案而重複詢問。
  - Study prompt 明確要求模型處理 `user_response`，不能對同一元件連續提出語意相同的選項問題。
  - Study ReAct 新增結束選項保護：若使用者選擇結束教學，會直接收斂並回傳 SOP 摘要，不再進入下一輪重複工具呼叫。

## [0.6.0] - 2026-06-04

#### Added
- **Study Mode 即席教學**
  - `/study [名稱或目標]` 找不到既有 SOP / Skill 時，不再中止；會改以目前 SAP 畫面與 SAP 常識啟動探索式教學。
  - 即席 Study Mode 仍維持 Human-in-the-loop：只能高亮元件與顯示指引，不會替使用者寫入 SAP。
  - 即席教學完成後會自動整理本次引導步驟，保存為 `skills/[名稱].md`，下次可直接用同名 `/study` 載入。
- **Skill Library 寫入能力**
  - 新增 `save_markdown_skill()`，支援將 AI 教學結果保存為 Markdown skill。

#### Changed
- Study prompt 現在明確支援「沒有 SOP」的教學情境，可依使用者目標、目前畫面與 SAP 常識推斷第一步。
- CLI banner 更新為 `V0.6.0`。

## [0.5.1] - 2026-06-04

#### Changed
- **Study Mode 預設值處理**
  - Study prompt 明確規定 SOP 是參考資料，必須優先依照目前 SAP 畫面狀態引導。
  - 欄位已有非空目前值時，AI 教練應引導使用者確認沿用，不再要求重新輸入 SOP 中的舊值。
  - `guide_user_action()` 會讀取並顯示目標元件目前值，讓使用者能直接判斷是否需要修改。
- **Record Mode 預設值辨識**
  - Monitor 會將畫面跳轉後新出現的非空欄位標記為 `FIELD_DEFAULT`，代表 SAP 系統預設值。
  - Recorder 會保留 `FIELD_DEFAULT` raw event 供除錯，但不會壓入 SOP steps，避免 Study Mode 把系統預設值當作使用者必填輸入。
- **Scanner 欄位摘要修正**
  - `fields` 摘要排除 menu / button / toolbar 等非表單輸入元件，避免真正欄位被前 20 筆 menu 擠掉。

## [0.5.0] - 2026-06-03

#### Added
- **AI 互動引導工具 (`sap_agent_tools.py`)**
  - 新增 `guide_user_action(element_id, instruction)`，透過高亮元件與 CLI 印出指示，並等待使用者按 Enter 確認，實現真正的 Human-in-the-loop (HITL) 互動。
- **Study Mode 升級為 AI 教練 (`llm_brain.py`)**
  - 新增專屬的 `SYSTEM_PROMPT_STUDY`，嚴格禁止 AI 呼叫寫入工具（如 `set_text`, `click`）。
  - 新增 `_process_study()`，專門處理教練模式的 ReAct 迴圈，且具備安全機制攔截非法工具呼叫。
- **自然語言 SOP 生成 (`sap_recorder.py`)**
  - 錄製結束 (`/stop`) 時，新增 `generate_sop_with_llm()`，自動將繁雜的 JSON 軌跡送到 GitHub Copilot API 總結為繁體中文的 Markdown SOP，並儲存至 `skills/` 目錄。

#### Changed
- **移除了基於巨集回放的舊代碼 (`main.py`)**
  - 刪除超過 1,200 行的舊版 `run_study` 迭代迴圈與各種硬編碼的欄位對齊、錯誤處理邏輯。
  - 移除已不再需要的 `STUDY_*` 系列環境變數。
- **`/study` 指令重構**
  - `/study` 現在會直接讀取 Markdown SOP 文本，並交由 `agent.process_message()` 以 `extra_context` 形式啟動全新的 Agentic 教練流程。
- **技能庫支援 Markdown (`sap_skill_library.py`)**
  - 優先讀取 `.md` 格式的 SOP 檔案，並與現有的 JSON SOP 保持向下相容。
- CLI banner 與模組 docstring 更新為 `V0.5.0 (Phase 4 — Agentic Coach)`。
- CLI 模式指示器新增 `📘 STUDY` 狀態。

## [0.4.1] - 2026-06-03

### Changed
- **Study Mode 改為互動式參考引導**
  - `recordings/` 與 `skills/` 的 SOP JSON 現在是流程參考資料，不再被視為必須逐字逐畫面重放的絕對腳本。
  - 新增 `STUDY_ADAPTIVE_MODE=true` 預設設定。
  - 欄位步驟若目前 SAP 欄位已有值，直接 Enter 會使用目前值作為本次預設；錄製值只顯示為參考值。
  - 若目前已在目標 T-Code，OKCode / T-Code 參考步驟會自動略過，避免重送 Enter 造成流程跳動。
  - 畫面跳轉若已離開起始畫面但未到錄製目標 screen，會進入互動式 review，由使用者決定接受目前狀態、重試、手動完成、略過或中止。

## [0.4.0] - 2026-06-03

### Added
- **Phase 3 開發啟動**
  - 版本進入 `V0.4.0 (Phase 3)`。
  - Auto / Ask / Record / Study mode 已完成實測，Phase 3 轉向視覺化引導與技能庫。
  - Phase 3 實作完成：Study Mode 已具備高亮引導、Skill Library、欄位語意提示、畫面跳轉驗證與 guided popup recovery。
- **Study Mode 視覺化引導**
  - 新增 `visualize_element()` 工具，封裝 SAP GUI `Visualize(True)` 與 `SetFocus`。
  - Study Mode 欄位定位改用共用高亮工具，並由 `STUDY_VISUALIZE_SECONDS` 控制高亮停留秒數。
  - `visualize_element()` 已加入工具 schema，後續 Auto Mode / UI 也可重用。
  - 畫面跳轉步驟送出 F8/Enter 後會掃描驗證是否到達錄製目標 screen；若出現必填欄位彈窗、狀態列錯誤或仍停在原畫面，會進入 recovery，不再把最後一步誤判為完成。
  - 新增 `STUDY_SCREEN_CHANGE_TIMEOUT_SECONDS` / `STUDY_SCREEN_CHANGE_POLL_SECONDS` 設定。
  - Study Mode 欄位步驟會掃描目前畫面 `fields`，優先顯示 SAP label / tooltip / name，並附上目前值、欄位型別、畫面名稱與元件 ID，降低使用者只看到技術 ID 的情況。
  - 放寬 scanner 的 label 對齊容忍度，並在 Study Mode 新增 near-label 二次推斷與常見 SAP 欄位字典 fallback，改善 `FACOM-KUNDE`、radio 等欄位偶爾抓不到 label 的情況。
  - 新增 guided popup recovery：Study Mode 若被必填彈窗擋住，可逐欄高亮彈窗欄位、提示填值、讀回驗證，送出彈窗後再驗證原 SOP 目標畫面是否達成。
- **Skill Library**
  - 新增 `sap_skill_library.py`，統一讀取 SOP / Skill JSON。
  - 新增 `skills/` 目錄，作為整理後的穩定教學流程存放位置。
  - `/recordings`、`/play`、`/study` 改為透過 Skill Library 讀取；`skills/` 優先，其次 `recordings/`。

### Changed
- CLI banner 更新為 `V0.4.0 (Phase 3)`。
- README / plan 更新 Phase 3 狀態與 Skill Library 架構。

## [0.3.1] - 2026-06-03

### Added
- **啟動器 (`start.py`)**
  - 新增一個統一啟動入口，先確認 SAP GUI 是否已有登入完成的 session。
  - 若尚未登入 SAP，會執行 `sap_login.py`，並在登入後重新確認 session 狀態。
  - 啟動 `main.py` 前會檢查 GitHub Copilot 授權；未登入或 token 失效時會自動進入授權流程。
- **Windows 啟動批次檔 (`start.bat`)**
  - 可直接在 Windows 以批次檔呼叫 `python start.py`。
- **Study Mode human-in-the-loop**
  - 新增 `STUDY_HUMAN_FIELD_INPUT` 設定，預設開啟。
  - 新增 `STUDY_FOCUS_HUMAN_FIELDS` 設定，提示使用者輸入前會嘗試 focus/highlight 對應 SAP 欄位。
  - 新增 `STUDY_PROMPT_FIELD_VALUES` 設定，錄製欄位值會作為本次執行的預設值，可在執行時覆寫。
  - 新增 `STUDY_AUTOFILL_PROMPTED_VALUES` 設定，可選擇是否由 agent 嘗試自動填入欄位；預設為手動輸入後驗證。
  - `/study` 現在會自動執行 T-Code、Enter、畫面跳轉、下拉式選單、radio/checkbox 等制式操作。
  - 一般文字欄位會提示使用者輸入本次值；直接 Enter 使用錄製值，輸入新值會覆寫本次執行。
  - 使用者在 SAP GUI 完成文字欄位輸入後，Study Mode 會讀回欄位值並確認是否符合本次值。
- **SE38 / ABAP editor 專用讀寫**
  - 新增 `read_editor_text()` 工具，可從目前 ABAP editor 讀取程式碼。
  - `set_editor_text()` 會優先使用 `GuiAbapEditor` / `GuiTextedit` API 寫入，包含 `SelectAll + ReplaceSelection`、`SetSelectionIndexes + ReplaceSelection`、`SetUnprotectedTextPart`、`InsertText`。
  - 寫入後會嘗試讀回比對，回傳 `verify` 結果。
  - `/scan` 的 `editors` 會顯示 editor capabilities，方便確認 SAP GUI 是否暴露 `GetLineText`、`InsertText` 等方法。
  - Ask Mode 偵測到 editor 時會自動讀取 source code 放入 `editor_sources`，可直接回答「這個程式是幹嘛的」。
  - Editor 偵測不再只因元件文字含有 `ABAP` 就判定為 editor，避免 ATC、Examples 等 SAP 選單被誤判為程式碼來源。
  - `read_editor_text()` 新增 `looks_like_source` 判斷；Ask Mode 會忽略不像 ABAP source 的 shell 內容，並回報候選元件讀取原因。
  - 新增 `EDITOR_CONTEXT_MAX_CHARS` 設定，控制放入 LLM context 的 editor source 長度。

### Changed
- 從本版開始版本號調整為 `V0.3.1`。
- README 的啟動方式改為推薦使用 `python start.py` 或 `start.bat`。
- Study Mode 改為引導式半自動流程，降低 SAP GUI 特殊控制元件讀寫失敗造成 SOP 中斷的風險。
- Study Mode 自動步驟失敗時可選擇 retry、manual、skip 或 abort，不再直接停止。
- Study Mode 文字欄位驗證失敗時可選擇重新輸入、接受目前值、略過或中止。
- 人工輸入步驟完成後不再額外等待 5 秒；只有自動步驟保留固定延遲。

### Known Issues
- **SE38 / ABAP editor 實機相容性仍需驗證**
  - 已加入 `GuiAbapEditor` / `GuiTextedit` 專用讀寫路徑，但不同 SAP GUI / SAP_BASIS 版本可能暴露不同方法。
  - 若 editor API 不可用，`set_editor_text()` 仍會 fallback 到剪貼簿貼上；Study Mode 中程式碼與一般文字欄位內容預設仍由使用者手動輸入並驗證。

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
