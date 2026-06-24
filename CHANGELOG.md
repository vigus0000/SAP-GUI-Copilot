# Changelog

本專案的所有重要變更都記錄在此檔案中。

格式基於 [Keep a Changelog](https://keepachangelog.com/zh-TW/1.1.0/)，版本號遵循 [Semantic Versioning](https://semver.org/lang/zh-TW/)。

---

## [0.15.0] - 2026-06-24

#### Added
- 新增 `sap_skill_library.py` SAP 模組知識庫（`SAP_MODULES`）：預建 MM（物料管理）、SD（銷售配銷）、FI（財務會計）模組的交易碼對應、關鍵詞與建議 skill 清單，供 `/guide`、`/study` 等入口依模組自動推薦學習路徑。
- 新增 `sop_step_parser.py` SOP 步驟解析器：`parse_sop_steps()`、`steps_confidence()`、`vkey_label()`、`clean_step_instruction()` 等工具，供 Study Mode 將 Markdown SOP 拆解為結構化 `StepItem`，支援信心度評估與步驟清單格式化。
- 新增 `sap_monitor.py` Grid cell 補抓：`_read_grid_view_cells()` 與 `_collect_grid_cells()`，可在 MCP monitor 模式下用 COM 補讀 `GuiGridView` 的儲存格內容（上限 `MAX_GRID_ROWS=100`、`MAX_GRID_COLS=30`），解決 VA01 等畫面 KUNNR 等深層欄位在 MCP 模式下遺漏的問題。
- 新增 `llm_brain.py` Study Mode 專用畫面快照方法 `_study_post_tool_screen()`：工具執行後只回傳 tcode / title / screen_number / status_bar 與錯誤時的前 10 個可改欄位，避免 context 膨脹；MCP path 優先使用 `sap_get_screen_info`，失敗時回退 legacy COM scan。
- 新增 `STUDY_MAX_ITERATIONS` 環境變數，控制 Study Mode 每輪最大迭代次數（預設 20），與 Auto Mode 獨立設定。
- 新增 `.mcp.json`、`PRESENTATION.md`、`PROJECT_OVERVIEW.md`、`UPDATES_v0.14.md` 文件。
- 新增 recordings：`ME21N.json`、`ME51N.json`、`ME5A.json`。
- 新增 skills：`F-28`、`F-53`、`FBL1N`、`FBL5N`、`MB51`、`ME21N`、`ME2L`、`ME2M`、`ME51N`、`ME5A`、`MIGO`、`MIR6`、`MIRO`、`MM01`、`MMBE`、`VA01`、`VA02`、`VA05`、`VF01`、`VF03`、`VF05`、`VL01N`、`VL02N`、`VL06F`。

#### Changed
- `mcp_client.py` Windows 中文路徑修正：在 Windows 且未設定 `MCP_SAP_LOCAL_COMMAND` 時，若 `external/mcp-sap-gui/.venv/Scripts/python.exe` 存在，改以 venv Python 搭配 `-X utf8` 直接啟動 server，繞過 `uv` 在 CJK 使用者名稱路徑下 site.py decode 崩潰的問題。
- `mcp_client.py` 新增 `_fix_venv_pth_files()`：將 uv 寫入的 editable-install `.pth` 絕對非 ASCII 路徑轉換為相對路徑，提升跨環境相容性。
- `mcp_client.py` `capture_mcp()` 新增 `session` 參數：MCP monitor 模式下同時用 COM 補讀 `capture_editable_fields()`，MCP 已有的欄位維持優先，COM 只補缺漏欄位，提升 monitor 欄位覆蓋率。
- `sap_recorder.py` SOP 壓縮邏輯優化：`FIELD_DEFAULT` 事件不再合入 SOP steps（`NOISY_EVENT_TYPES`），`system_default=True` 的事件也一律跳過，避免 SAP 系統預設值污染錄製內容；`FIELD_CHANGE` 與 `FIELD_DEFAULT` 分離處理，前者才顯示 `short_id = "value"` 格式。
- `sap_recorder.py` Grid row ID 解析改善：`#r` 後綴的 element ID 改以 `rsplit("#r", 1)` 取得列索引，提升 ALV Grid 錄製準確度。
- `sap_monitor.py` 畫面快照過濾：Tab 標籤元件（`/tabs`、`/tabp`）不再記入 `field_values`，避免 Tab caption 污染欄位差異偵測。
- `sap_agent_tools.py` `visualize_element()` 高亮停留時間從 1.2 秒縮短至 0.5 秒，加快 Study Mode 引導節奏；預設 `duration_seconds` 同步調整。
- `sap_agent_tools.py` `execute_transaction()` 導航邏輯強化：若目前不在起始畫面（非 `SESSION_MANAGER` / `S000`），且 T-Code 未以 `/` 開頭，自動加上 `/n` 前綴，避免在已開啟的交易中直接送裸 T-Code 造成跳轉失敗。
- `sap_login.py` 重構為 function-based 架構：新增 `_env()`、`_require_env()`、`_open_sap_logon()`、`_get_sap_application()`、`_open_connection()` 輔助函式；登入失敗時提供更詳細的錯誤訊息與 `.env` 設定提示；支援 `SAP_CONNECTION` 新欄位（相容舊 `connection=`）；SAP Logon 啟動改用 `subprocess.Popen([path])` 並驗證路徑存在。
- `llm_brain.py` Study Mode 新增 `from sop_step_parser import ...` 整合，利用解析器對 SOP 步驟做信心度評估與格式化，提升引導品質。

#### Fixed
- 修正 Windows CJK 路徑（如桌面含中文的使用者名稱）下，`uv` 啟動 MCP server 時 `site.py` 因 cp950 解碼失敗而崩潰的問題。
- 修正 `sap_login.py` 使用 `subprocess.Popen(os.getenv(...))` 傳入字串在路徑含空白時解析異常的問題，改為傳入 list。
- 修正 Monitor 在 MCP 模式下遺漏 VA01 客戶編號（KUNNR）等非 input 欄位的問題，透過 COM 補讀 Grid cells 解決。

## [0.14.0]

#### Added
- 新增 `sap_business_tools.py` 業務邏輯 MCP tool registry：集中管理流程級工具的 module、business_cycle、觸發詞、使用情境與 prompt 說明。
- 新增 `business_tools/README.md`，定義未來新增業務 MCP 工具的 metadata 格式、外部 JSON registry 格式與安全原則。
- Auto Mode core MCP allowlist 會自動納入 registry 中的業務工具；目前內建 `sap_get_order_overview` 與 `sap_analyze_delivery_block`，用於 VA03 銷售訂單全貌與出貨卡關診斷。

#### Changed
- Auto prompt 會根據 `sap_business_tools.py` 動態插入「業務邏輯 MCP 工具」說明；若 MCP server 沒提供對應工具或工具失敗，仍回退一般 ReAct GUI 操作。
- `.env.example` / `.env` 新增 `SAP_BUSINESS_TOOLS_ENABLED`、`SAP_BUSINESS_TOOLS_REGISTRY`、`SAP_BUSINESS_EXTRA_MCP_TOOLS`、`SAP_BUSINESS_DISABLED_MCP_TOOLS` 與 `FBL5N_COMPANY_CODE`。

## [0.13.1] - 2026-06-17

#### Changed
- 優化 Tkinter UI 視覺層級：header 顯示版本號、Connect 與 Top 控制移到頂部，狀態列改為更精簡的 Mode / SAP / Provider 顯示。
- 對話紀錄改為 role-based 視覺格式，將使用者輸入、AI 回覆、系統訊息、Macro 訊息、錯誤與提示分別套用不同標籤、背景與縮排，避免長文字混在同一個 log 區塊。
- 輸入區與送出按鈕改為更接近聊天介面的樣式，送出按鈕顯示 `發送 ↵`，提升 Auto / Ask / Solve / Study 對話可讀性。
- Auto pre-route Macro 成功後新增 `SAP_MACRO_POST_REACT_VERIFY=true` 驗證層：Macro strict flow 結束前會交回 Auto ReAct 依目前 SAP 畫面與原始使用者目標確認是否真正完成，必要時補按 Execute/F8 或補足安全步驟。
- UI 對話區改為純聊天紀錄格式，保留 `You:`、`AI:`、Mode 切換與 Connect 連線狀態；Macro、MCP、Scan 與其他流程 log 改輸出到啟動 UI 的終端機，避免非對話訊息污染聊天區。
- Macro field step 新增 `clear_if_empty=true` 語意：空值時仍會寫入空白以清除 SAP selection screen 記憶值；`mb52_stock_list` 已套用於物料、工廠、儲位、物料類型、物料群組與批次篩選欄位。
- Auto ReAct 新增 `AUTO_CLEAR_STALE_SELECTION_FIELDS=true` selection carryover guard：模型準備批次填欄時會合併本次未指定的殘留限制欄位清空；若直接按 Execute/F8/Enter，系統會先做 pre-clear，避免沿用上次查詢條件。

#### Fixed
- 修正 UI 長回覆只以單一純文字 log 呈現時，不易辨識 `You` / `AI` / 系統狀態來源的問題。
- 修正 Macro 只滿足弱 `## End` 條件時可能半完成的問題，例如停在 MB52 選擇畫面卻已宣告完成。
- 修正 MB52 查詢「工廠 1710 的所有庫存」時，未指定物料卻沿用 SAP 上次記憶的 `MAT_001`，造成查詢條件錯誤的問題。
- 修正同類 selection screen 記憶值污染也可能發生在一般 Auto ReAct 路徑的問題，不再只依賴 Macro 的 `clear_if_empty`。

## [0.13.0] - 2026-06-17

#### Added
- 新增 LLM provider 抽象層 `llm_provider.py`，支援 `github_copilot` 與 `codex` 兩種連線方式。
- 新增 Codex OAuth provider：`LLM_PROVIDER=codex` 時讀取 Codex/ChatGPT 瀏覽器 OAuth 快取，使用 OAuth access token 直接呼叫 Codex backend Responses endpoint，模型由 `CODEX_OAUTH_MODEL` 控制。
- 新增 Codex OAuth 設定：`CODEX_OAUTH_MODEL`、`CODEX_OAUTH_CHAT_URL`、`CODEX_OAUTH_LOGIN_ON_CONNECT`、`CODEX_OAUTH_LOGIN_TIMEOUT_SECONDS`。
- 新增 `codex_auth.py`，專責讀取 `~/.codex/auth.json` 與觸發 Codex 瀏覽器登入；Codex CLI 只作為登入 helper，不作為模型推理連線。
- CLI 新增 `/connect github_copilot` / `/connect codex`，可在執行中手動切換 LLM provider 並重建 agent；`/login` 在 Codex OAuth 模式下會觸發 `codex login` 瀏覽器登入。
- `CODEX_OAUTH_LOGIN_ON_CONNECT=true` 時，Connect 會先讀取本機 OAuth 快取；若 token 不存在或過期才觸發 Codex OAuth 瀏覽器登入。
- 新增 Auto / Study 共用的 Markdown Macro 系統：`sap_macro_library.py` 會讀取 `macros/*.md` 的 front matter、Inputs table 與 Steps table，支援 `{{variable}}` / `{{variable|default=value}}` 動態佔位符。
- CLI 新增 `/macros`、`/macro list`、`/macro show`、`/macro run`、`/macro study`，並支援 `/study --macro`；UI 新增 Macro 按鈕與 `/macro` 指令。
- Macro Auto 執行採 MCP-first，文字欄位可使用 `sap_set_batch_fields` / `sap_set_fields_and_enter` 批次化；MCP 不可用時退回 legacy GUI fallback。
- Macro 執行新增嚴格驗證：Markdown 必須有 `## Start` / `## End`，執行流程改為「掃描畫面 → 完整匹配 Start/step 元件 → 輸入值 → 驗證 End」，避免在錯誤畫面套用固定 element ID。
- 新增 Macro 驗證設定：`SAP_MACRO_STRICT_MATCH`、`SAP_MACRO_REQUIRE_BOUNDARIES`、`SAP_MACRO_SCREEN_MAX_DEPTH`、`SAP_MACRO_SCREEN_TYPE_FILTER`、`SAP_MACRO_SCREEN_CHANGEABLE_ONLY`。
- 新增 Macro 自我修復與自動寫回：`SAP_MACRO_LEARNING_ENABLED` / `SAP_MACRO_AUTO_WRITEBACK` 開啟時，成功用替代 ID 執行後會更新 macro md 的 `alternate_ids`，並保存 `macros/_backups/` 與 `macros/_learned_index.json`。
- 新增 Macro learning 設定：`SAP_MACRO_LEARNED_INDEX`、`SAP_MACRO_BACKUP_DIR`、`SAP_MACRO_RESOLVE_MIN_CONFIDENCE`、`SAP_MACRO_PROMOTE_PRIMARY_AFTER`。
- 新增 `/macro learn recordings`、`/macro audit <name>`、`/macro doctor <name>`；UI `/macro` 指令同步支援。
- 新增 7 個內建 Macro：`va05_list_orders`、`va03_display_order`、`vf05_list_billing`、`mb51_material_docs`、`mb52_stock_list`、`mmbe_stock_overview`、`me2m_po_by_material`。
- 新增 Auto Mode Macro pre-router：自然語言會先以 `SAP_MACRO_AUTOROUTE_ENABLED` / `SAP_MACRO_AUTOROUTE_MIN_SCORE` 判斷是否直接執行 Macro；命中時跳過 LLM ReAct 迭代，未命中才回到 Auto agent。

#### Changed
- UI 的 **Connect** 按鈕改為 radio button 選單，可在 GitHub Copilot 與 Codex OAuth 間切換；Codex OAuth 頁面顯示 HTTP endpoint、token cache 與 model，不再要求 API key 或 command。
- `codex` provider alias 現在正規化為 `codex_oauth`；v0.13.0 的 Codex 路徑只保留瀏覽器 OAuth + HTTP token 呼叫。
- `start.py` 啟動器改為依 `LLM_PROVIDER` 檢查授權；使用 `codex` 時不再強制執行 GitHub Copilot device login。
- Record Mode 生成 Markdown SOP 時改用目前 agent 的 LLM provider，不再硬綁 GitHub Copilot。

#### Fixed
- 移除 Codex OAuth provider 中的命令列推理路徑，避免 CLI 參數相容性錯誤與 command-backed inference；SAP_Copilot 現在不再用 `codex.exe` 執行模型呼叫。
- 修正 Codex OAuth 預設 endpoint/model：改用 `https://chatgpt.com/backend-api/codex/responses` 與 `gpt-5.5`，避免一般 platform API quota 與 `gpt-5-mini` 不支援問題。
- 修正 Codex backend 要求 streaming 的問題；Codex OAuth provider 現在以 `stream=true` 呼叫 Responses endpoint，並解析 SSE 事件後轉回既有 Chat Completions-like response。
- 修正 Codex OAuth streaming parser 在 `requests.iter_lines()` 回傳 bytes 時拋出 `a bytes-like object is required, not 'str'` 的問題。
- 修正 Codex OAuth streaming parser 收到空的 `response.completed` envelope 時覆蓋前面 `output_text.delta` / function-call item，導致 UI 顯示「AI 未回傳任何訊息」的問題。
- 修正 Macro 欄位批次分組：`skip_if_empty=true` 的空白 key step 不再被誤判為 Enter validation，避免清單型 Macro 在未提供 `execute_key` 時仍自動送出查詢。
- 修正 Macro strict flow 對 `wnd[0]/tbar[0]/okcd` 過度嚴格的問題：MCP/GUI 掃描未回傳工具列命令欄時，只要仍在主視窗 `wnd[0]`，start check 不再中止；`tcode` / `key` 步驟也不再做不必要的 element 掃描。
- 修正 Auto pre-route 命中 Macro 但 Macro 在尚未寫入欄位/按鈕前驗證失敗時沒有 fallback 的問題；即使已完成 T-Code 導航，也會回到原本 Auto ReAct，避免使用者指令被 Macro 卡死。
- 修正 T-Code 切換後下一畫面尚未穩定就驗證 step target 的問題；新增 `SAP_MACRO_STEP_SCAN_RETRIES` / `SAP_MACRO_STEP_SCAN_RETRY_SECONDS`，step target validation 會短暫重試。
- 新增 Macro `alternate_ids` 支援：主要 SAP GUI ID 找不到時，會依候選 ID 與欄位 label 二次定位，定位成功後改用實際 ID 執行；`mmbe_stock_overview` 已補入常見 MMBE 物料/工廠/儲位/批次欄位候選。
- 修正 Macro 無法利用工具結果 `screen_after.screen_elements` 的問題；導航/F8 後的 result screen evidence 現在會記入 learned index。
- 修正 MMBE 已在結果畫面時仍回頭找選擇畫面欄位的問題；若 screen 300 的 `IO_MATERIAL` 已等於本次物料，Macro 直接判定已達成。
- 改善 Macro router 的庫存語意判斷：`查看物料MAT_001的庫存` 會命中 `mmbe_stock_overview` 並抽取 `material=MAT_001`；`顯示所有庫存` / `庫存清單` 會命中 `mb52_stock_list`。
- 改善 Macro router 的銷售訂單語意判斷：`查看訂單500000001的情況` 會命中 `va03_display_order` 並抽取 `sales_order=500000001`；`訂單清單` 仍會命中 `va05_list_orders`。
- 改善 Macro router 對自然語句中夾帶代號的抽取：可從 `查看單號500000001的情況`、`查詢付款人C0001的請款文件`、`查詢買方C0001的銷售訂單清單`、`查詢物料MAT_001的採購單`、`查詢物料MAT_001的物料憑證` 直接推斷對應 Macro 與 runtime values；若語境是請購/採購/請款等非銷售文件，則不會把 generic 單號誤導到 VA03。

## [0.12.1] - 2026-06-16

#### Added
- 新增 `sap_inspect_tables` MCP 診斷工具：結合目前焦點元素、父層 ID 探測、深層 element discovery 與 schema-first 讀取，用於 ME51N 項目概觀這類一般 scan 抓不到的表格/ALV。
- 新增 `/inspect table` CLI/UI 診斷入口：優先呼叫 MCP `sap_inspect_tables`，若不可用則走 legacy COM fallback，輸出 focused element、候選 table id、可讀欄位與錯誤原因。
- 新增 `MCP_TABLE_CONTEXT_INSPECT_ON_EMPTY`、`MCP_TABLE_CONTEXT_INSPECT_INCLUDE_ROWS` 設定；當一般 MCP table discovery 為空時，Ask / Solve / Study / Auto 會自動嘗試深層表格檢查。
- 新增 MCP-first 表格/報表讀取 context：畫面有 `GuiGridView`、`GuiTableControl` 或 ALV/`GuiShell` 時，Agent 會優先用 `sap_read_table` 讀取前幾列並輸出 `mcp_table_report_context`，讓 Ask / Solve / Study / Auto 能直接看到報表資料。
- 新增表格 schema-first 讀取：`MCP_TABLE_CONTEXT_SCHEMA_FIRST=true` 時會先呼叫 `sap_read_table(columns_only=true)` 取得欄位 metadata，再讀 rows，支援「項目概觀有哪些欄位」這類欄位查詢。
- 新增 broad table discovery：filtered MCP discovery 找不到表格候選時，會用 `MCP_TABLE_CONTEXT_BROAD_DISCOVERY_DEPTH` 做一次較深廣域掃描，從 element id / name / text 補抓 table/grid/shell 候選。
- 新增 shell/report 內容補充讀取：非表格 `GuiShell` 可由 `sap_read_shell_content` 讀取文字、URL 或 HTML preview，作為報表或 HTMLViewer 類畫面的 fallback。
- 新增表格讀取上限設定：`MCP_READ_TABLES_IN_CONTEXT`、`MCP_READ_SHELLS_IN_CONTEXT`、`MCP_TABLE_CONTEXT_MAX_TABLES`、`MCP_TABLE_CONTEXT_MAX_ROWS`、`MCP_TABLE_CONTEXT_MAX_COLUMNS`、`MCP_TABLE_CONTEXT_MAX_SCHEMA_COLUMNS`、`MCP_TABLE_CONTEXT_DISCOVERY_DEPTH`、`MCP_SHELL_CONTEXT_MAX_CHARS`。

#### Changed
- legacy GUI fallback scanner 現在也會嘗試讀取 ALV-like `GuiShell` / `GuiGridView` 前 25 列、12 欄，避免 MCP 不可用時完全看不到報表內容。
- legacy GUI fallback scanner 現在會保留 `GuiTableControl.Columns` schema，即使目前沒有 rows，也能把項目概觀欄位名稱放進 screen summary。
- `MCP_TABLE_CONTEXT_DISCOVERY_DEPTH` 預設由 4 提高到 6，但仍使用表格/shell type filter，改善 ME51N 項目概觀等深層表格偵測。

#### Fixed
- 修正表格欄位清單被 `MCP_TABLE_CONTEXT_MAX_COLUMNS=12` 截斷的問題；現在 `columns` / `column_info` 由 `MCP_TABLE_CONTEXT_MAX_SCHEMA_COLUMNS` 獨立控制，資料列 `rows[].cells` 才維持低欄數上限。
- Ask Mode 詢問表格欄位時，若 `rows` 空但 `columns` / `column_info` 已讀到，現在會直接依欄位 schema 回答，不再誤判為「表格內容讀不到」。

## [0.12.0] - 2026-06-16

#### Changed
- Ask / Solve / Study 的 MCP 畫面讀取改為 evidence scan：使用較深的 `MCP_EVIDENCE_SCREEN_MAX_DEPTH` 並包含 `GuiLabel`，避免 ME51N、ME5A、ALV 或 Splitter Layout 只讀到交易碼/標題。

#### Fixed
- `main.py --ui` 現在會直接啟動 Tkinter UI；先前只有 `start.py --ui` 會解析 UI 參數，導致 `uv run main.py --ui` 仍進入 CLI。
- 修正 Ask Mode 在 MCP 輕量掃描沒有讀到欄位/表格時仍直接回覆通用教材的問題；現在會附上 `legacy_screen_summary_fallback`，模型必須先使用 fallback 的 `fields`、`tables`、`status_bar`。
- Study Mode 現在會拒絕把 `wnd[0]/usr`、container、label 或 status bar 當成輸入欄位；若必填欄位存在但目前畫面沒有讀到精確欄位 ID，會回報 `target_actionable=false` 並要求改為揭露欄位，而不是彈窗要求使用者填不存在的欄位。
- 修正 Study Mode 執行完一輪後 CLI / UI 會在 `finally` 自動切回 Auto Mode 的惡性狀態機 bug；現在 Study 會保持啟用，只有使用者明確 `/auto`、`/ask`、`/solve` 或按模式按鈕時才切換。

## [0.11.0] - 2026-06-12

#### Added
- **Study Mode 階層式 Knowledge / Skill 蒸餾**
  - 新增 `sap_knowledge_library.py`，可將本機文件或 URL 匯入 `knowledge/`，並以 `module -> business_cycle -> document` 建立 evidence index。
  - 新增 `/knowledge import`、`/knowledge search`、`/knowledge distill`、`/knowledge rebuild` 指令。
  - 新增 `/skills drafts` 與 `/skills promote`，蒸餾結果預設只保存到 `skills/_drafts/<module>/<business_cycle>/`，必須 promote 才會成為正式 Study skill。
  - Knowledge index 保存 `module`、`business_cycle`、`document_title`、`source_path/url`、`source_type`、`tags`、`tcode`、`confidence`、`content_hash`。
  - `/study --draft` 找不到正式 skill 時會建立 evidence pack，引用目前 SAP 畫面、正式 skill、draft、knowledge 與可選網搜結果，再要求使用者確認第一個關鍵流程方向。

#### Changed
- Study prompt 現在明確要求 no-recording Study 依 evidence priority 回答，不保存 raw COT，只輸出 structured rationale、來源、信心與未知項目。
- Study Mode 無錄製教學改成「候選流程確認 -> 單一步驟引導」：當 evidence 只有 LLM prior 或信心不足時，第一個 dialog 只詢問使用者確認 T-Code / 流程方向，不再把完整候選分析、未知項目與欄位輸入混在同一個提示窗。
- `guide_user_action` 新增 `reason`、`confidence`、`source`、`choices`、`expected_response_type` 欄位，UI 會把教學指令壓縮成短提示，並把來源/信心/選項拆開顯示，降低 Study Step 視窗資訊過載。
- `.env.example` 新增 `STUDY_WEB_SEARCH_ENABLED`、搜尋上限、timeout 與 knowledge match 上限；即時網搜預設關閉，需要時手動啟用。

#### Fixed
- `/study [未知目標]` 不再因舊設定 `STUDY_ALLOW_DRAFT=false` 直接停止；v0.11 預設會進入 evidence-driven draft。若要恢復舊版必須明確加 `STUDY_REQUIRE_DRAFT_FLAG=true`。
- 驗證過程確認 mock 測試嚴重卡住的原因是 `tempfile.mkdtemp(dir='C:\\tmp')` 在目前 Windows 環境會停在暫存目錄建立；改以工作區短生命測試目錄驗證 knowledge import / distill / promote 流程。
- Evidence pack 新增 `Evidence Summary`，提供 `evidence_level`、`best_confidence`、`should_confirm_flow` 與 `recommended_source`，讓 Study prompt 能依據實際證據強度決定是否先做流程確認。

## [0.10.0] - 2026-06-12

#### Added
- **MCP composite tools local extension**
  - 在 `external/mcp-sap-gui` 新增 `sap_get_light_snapshot`，用於快速取得 screen info、active window、fingerprint 與 popup summary，不掃完整 elements。
  - 新增 `sap_set_fields_and_enter`，將 batch field fill 與 Enter validation 合併成單一 MCP call。
  - 新增 `sap_select_popup_table_row_and_confirm`，將彈窗 table row selection 與 confirm 合併成單一 MCP call，優先支援 MM03「選擇檢視」類流程。
- **MCP 初始設定腳本**
  - 新增 `setup_mcp.py` / `setup_mcp.bat`，可自動 clone/update `tingjunchen425/mcp-sap-gui` 到 `external/mcp-sap-gui`，執行 `uv sync --extra screenshots`，並更新 `.env` 的 MCP local server 設定。

#### Changed
- Auto Mode core MCP profile 會在 custom tools 存在時提供 composite tools，並在 prompt 中要求優先使用。
- MCP fast screen context 會優先使用 `sap_get_light_snapshot`；若 custom MCP tools 不存在，維持 `0.9.6` 的 fallback 行為。
- `sap_get_light_snapshot` 預設改由 agent 內部管理，不再讓 LLM 把它當成使用者可見的補救動作。
- MCP navigation / screen change 結果會自動附 filtered elements；欄位寫入失敗會附 `field_write_recovery`，避免 MM03 這類初始畫面因猜錯欄位 ID 後轉向詢問使用者。
- MCP 未設定時的錯誤提示改為優先建議執行 `python setup_mcp.py`。

## [0.9.6] - 2026-06-11

#### Changed
- **MCP screen discovery 改為內部事件驅動**
  - `sap_get_screen_elements`、`sap_get_screen_info`、`sap_get_popup_window` 等讀畫面工具預設不再暴露給 Copilot tool schema，避免模型主動要求完整畫面掃描。
  - 若模型仍因舊上下文呼叫 discovery tool，agent 會回傳內部 cached context 提示，不再 fallback 到 legacy `scan_sap_screen()`。
  - MCP context 會依 `sap_get_screen_info` 產生 `TCODE_CHANGE`、`SCREEN_CHANGE`、`ACTIVE_WINDOW_CHANGE`、`WINDOW_OPEN`、`WINDOW_CLOSE` 事件，復用 Record/Monitor 的畫面切換語意。
  - 活動視窗是彈窗時，預設只讀 `sap_get_popup_window`，不再同時掃 `wnd[1]/usr` elements。

#### Added
- `.env.example` / README 新增 `MCP_EXPOSE_DISCOVERY_TO_LLM` 與 `MCP_POPUP_USE_POPUP_TOOL_ONLY`。

## [0.9.5] - 2026-06-11

#### Changed
- **MCP screen context cache**
  - Auto/Ask/Solve/Study 的 MCP fast screen context 每輪仍讀 `sap_get_screen_info`，但會用 `active_window + transaction + program + screen_number + title` 建立 fingerprint。
  - fingerprint 未變且 cache 未過期時，復用上一輪 `sap_get_screen_elements` 結果，避免同一畫面反覆完整掃描。
  - 一般欄位寫入與 batch fields 成功後會記錄 local field write cache，下一輪 context 會提示最新寫入值，降低因 elements cache 仍含舊值造成的誤判。
  - `sap_set_batch_fields(validate=true)` 現在會直接使用 `validation.screen` 作為 tool 後畫面狀態，不再額外補一次 screen info。

#### Added
- `.env.example` / README 新增 `MCP_SCREEN_CACHE_ENABLED`、`MCP_SCREEN_CACHE_TTL_SECONDS`、`MCP_SCREEN_CACHE_MAX_WRITES`。

## [0.9.4] - 2026-06-11

#### Changed
- **MCP GUI 操作速度優化**
  - `mcp_client.py` 會自行載入 `.env`，獨立測試與不同入口都能讀到 MCP local server 設定。
  - `SyncMCPSAPClient` 改為單一背景 async worker 執行 MCP connect/list/call/close，避免 stdio session 在不同 task 關閉造成 cancel-scope 錯誤。
  - Auto Mode 預設使用 `MCP_SAP_TOOL_PROFILE=core`，只提供常用 MCP tools；`full` 才提供完整工具清單。
  - `MCP_SAP_FAST_MODE=true` 時，初始畫面使用 `sap_get_screen_info` + filtered `sap_get_screen_elements`，降低畫面 payload 與掃描時間。
  - MCP tool 執行後採 smart scan：優先使用 action response 內建 screen，一般欄位寫入不重掃，導航/彈窗類工具才補輕量 screen info。
  - Auto prompt 明確要求同畫面多欄輸入優先使用 `sap_set_batch_fields`。

#### Added
- 新增 `MCP_TIMING_DEBUG`，可列印 Copilot API、MCP call、screen scan 耗時。
- README 與 `.env.example` 補上 `MCP_SAP_FAST_MODE`、`MCP_SAP_TOOL_PROFILE` 與 filtered screen element 設定。

## [0.9.3] - 2026-06-10

#### Fixed
- **MCP 設定與 stderr capture 測試修正**
  - 修正 `mcp_client.py` 的 stderr capture：改用具有真實 file descriptor 的 temporary file，避免 Windows/anyio 啟動 MCP 子程序時出現 `fileno`。
  - 實測 `uvx --from mcp-sap-gui==0.2.0 mcp-sap-gui` 目前無法從 package registry 解析，因此預設不再硬啟動 package mode。
  - 未設定 `MCP_SAP_SERVER_DIR` 時，`/mcp` 會快速回報需要本機 clone，而不是等待 uvx 解析失敗後才 fallback。
  - 保留 `MCP_SAP_ALLOW_PACKAGE_MODE=true` 作為明確 opt-in 的 package mode。
  - `/mcp` 輸出 error / stderr tail 時會做 console-safe 轉碼，避免 Windows cp950 無法列印 uv 錯誤字元。

#### Changed
- README 與 `.env.example` 改為官方建議的 local clone 啟動方式：`MCP_SAP_SERVER_DIR` + `uv run python -m mcp_sap_gui.server`。

## [0.9.2] - 2026-06-10

#### Fixed
- **MCP server 啟動診斷與 fallback 穩定性**
  - `mcp_client.py` 預設改用 `uvx --from mcp-sap-gui==0.2.0 mcp-sap-gui`，避免只執行舊版或錯誤 package 名稱造成 stdio server 直接關閉。
  - 新增 `MCP_SAP_SERVER_DIR` 與 `MCP_SAP_LOCAL_COMMAND` / `MCP_SAP_LOCAL_ARGS`，可切換到本機 clone 的 `uv run python -m mcp_sap_gui.server` 路徑。
  - 新增 `MCP_SAP_UV_CACHE_DIR`，預設指向 `C:\tmp\sap-copilot-uv-cache`，降低 Windows 使用者目錄 uv cache 權限問題。
  - MCP 初始化、`list_tools()`、`call_tool()` 失敗時會保留 command、cwd、cache 與 stderr tail，避免只看到 `Connection closed`。
  - Auto / Ask / Solve / Study 與 Record monitor 會在 MCP tool list 載入後嘗試 `sap_connect_existing` attach SAP session；失敗時仍回退 legacy GUI fallback。

#### Added
- CLI 與 Tkinter UI 新增 `/mcp` 診斷入口，可檢查 MCP prerequisites、初始化狀態、SAP attach、tool list 與 server stderr tail。
- `.env.example` 與 README 補上 pinned MCP 啟動設定、local server dir 設定與 `/mcp` 使用方式。

## [0.9.1] - 2026-06-10

#### Changed
- **Stage 2 Phase 2 LLM Brain MCP-first routing**
  - `llm_brain.py` 會優先從 `mcp_client.py` 取得 MCP tool list，動態提供給 Copilot tool calling。
  - Auto Mode 的工具呼叫優先轉發到 MCP；若 MCP 不可用或單次 tool call 失敗，會嘗試回退到既有 `sap_agent_tools.py` legacy GUI COM 工具。
  - Ask / Solve / Study 的畫面 context 優先使用 MCP screen tools，失敗時回退到原本 `scan_sap_screen()`。
  - Study Mode 保留本地 `guide_user_action` human-in-the-loop 工具；若 MCP 提供 focus tool，會先用 MCP 聚焦欄位，再交由本地提示流程。

#### Added
- **Stage 2 Phase 3 MCP-first Record monitor**
  - `sap_monitor.py` 新增 MCP snapshot 路徑，優先使用 `mcp-sap-gui` session/screen tools 進行 polling。
  - MCP snapshot 採寬鬆解析，支援 JSON 與文字格式，會擷取 T-Code、screen、title、status、field values 與 window titles。
  - MCP monitor 啟動或初始快照失敗時，自動回退到原本 pywin32 COM monitor。
  - `.env.example` 新增 MCP screen/session/focus tool candidates 與 `MCP_SAP_MONITOR_ENABLED`、`MCP_SAP_MONITOR_POLL_SECONDS`。

## [0.9.0] - 2026-06-10

#### Added
- **Stage 2 Phase 1 MCP client**
  - 新增 `mcp_client.py`，以 `mcp.client.stdio.stdio_client` 與 `mcp.ClientSession` 管理 `uvx mcp-sap-gui` 連線。
  - 實作 `connect()`、`get_available_tools()`、`call_tool()` 三個核心 async 方法。
  - `get_available_tools()` 會將 MCP tool 轉成 GitHub Copilot / OpenAI tool calling 相容 JSON Schema。
  - `call_tool()` 會將 MCP tool result 整理成可寫回 tool message 的字串。
  - 新增 `SyncMCPSAPClient`，讓目前同步 CLI / UI 架構可在 Phase 2 優先接 MCP，並保留既有 GUI COM 工具作 fallback。

#### Changed
- README 與 `.env.example` 新增 Stage 2 MCP 設定：`MCP_SAP_ENABLED`、`MCP_SAP_COMMAND`、`MCP_SAP_ARGS`。
- 專案架構說明調整為 MCP primary path，舊 `sap_core.py` / `sap_agent_tools.py` 暫作遷移期 fallback。

## [0.8.1] - 2026-06-10

#### Changed
- **Study Mode 無錄製依據保護**
  - `/study [名稱]` 找不到既有 SOP / Skill 時，預設不再啟動即席教學，避免 AI 在沒有錄製依據時把推測當成流程。
  - 新增明確草稿入口：`/study --draft [目標]` 只做探索式引導，不自動保存；`/study --save-draft [目標]` 才會保存為 skill 草稿。
  - 新增 `.env` 控制：`STUDY_ALLOW_DRAFT` 與 `STUDY_SAVE_DRAFT_SKILL`，預設皆為 `false`。
  - CLI 與 Phase 4 UI 採用同一套 Study Mode 安全規則。

## [0.8.0] - 2026-06-10

#### Added
- **Phase 4 Tkinter 懸浮 UI (`ui_app.py`)**
  - 新增標準庫 Tkinter 懸浮控制台，不額外要求 PyQt6 / CustomTkinter 安裝。
  - UI 提供 Auto / Ask / Solve / Study 模式切換、自然語言輸入、回覆 log、Scan、Reset、Record / Stop、SOP 清單與 `/command` 路由。
  - SAP COM 與 LLM 呼叫集中在背景 worker thread 執行，UI thread 只負責渲染與接收訊息，避免長時間呼叫卡住視窗。
  - UI 替 Auto Mode 敏感操作確認與 Study Mode `guide_user_action()` 提供 Tkinter dialog bridge，避免沿用 CLI `input()` 造成 worker 卡死。
  - Record Mode 在 UI 中可啟動 `SAPMonitor`，停止錄製後會保存 JSON 並嘗試產生 AI SOP。
- **UI 啟動入口**
  - `start.py` 新增 `--ui` / `ui` / `/ui` 參數，登入檢查完成後可啟動 `ui_app.py`。
  - 新增 `start_ui.bat`，Windows 可直接啟動 Phase 4 UI；原 `start.bat` 保留 CLI 行為。

#### Changed
- CLI banner 更新為 `V0.8.0`。
- README 與 plan.md 更新 Phase 4 完成狀態與 UI 啟動方式。

## [0.7.1] - 2026-06-10

#### Changed
- **Solve Mode evidence-first 診斷**
  - Solve Mode 送入 LLM 的畫面 context 新增 `solve_diagnostics`，會彙整狀態列、主視窗/彈窗訊息、表格列文字、目前焦點與需要注意的欄位。
  - Solve prompt 改為先列出實際讀到的錯誤/訊息，再做原因判斷與處理建議；若畫面沒有錯誤清單，會要求使用者先打開或展開錯誤清單，而不是先猜常見 ABAP 修法。
  - 改善 Activate / Syntax Check 失敗時容易根據程式碼片段推測問題、未先讀完整錯誤清單的情況。

## [0.7.0] - 2026-06-09

#### Added
- **Solve Mode 問題排解入口**
  - 新增 `/solve`，作為獨立模式入口，專門用來判讀當前 SAP 畫面、彈窗、狀態列錯誤、必填欄位與卡關情境。
  - Solve Mode 底層沿用 Ask Mode 的唯讀掃描流程，不呼叫 SAP 操作工具；差異在 system prompt 會優先輸出「目前判斷」與「建議處理」。
  - CLI mode indicator 新增 `🟡 SOLVE`，未知指令提示與 README 指令表同步補上 `/solve`。

#### Design
- 以操作方便性來看，Solve Mode 保持獨立入口較合適：使用者遇到錯誤時可直接 `/solve`，不用在 Ask Mode 額外描述「請幫我排錯」；程式實作仍與 Ask Mode 共用掃描管線，避免增加操作風險與維護成本。

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
