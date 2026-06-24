"""
LLM Agent 大腦

透過可切換的 LLM provider 進行意圖識別與 Tool Calling。
實作 ReAct Loop: Scan → Think → Act → Verify

目前支援 GitHub Copilot 與 Codex/OpenAI-compatible Chat Completions。

架構解耦：
- LLM 呼叫邏輯與 SAP Tools 完全分離
- 未來如需切換 LLM，只需修改此檔案的 endpoint 與 headers
"""

import json
import os
import re
import time

try:
    import dotenv
    dotenv.load_dotenv()
except Exception:
    pass

from copilot_auth import CopilotAuth
from llm_provider import (
    default_model_for_provider,
    create_llm_provider,
    normalize_provider_name,
)
from sap_agent_tools import (
    scan_sap_screen,
    read_editor_text,
    TOOL_SCHEMAS,
    TOOL_FUNCTIONS,
    confirmed_click,
    confirmed_send_vkey,
    confirmed_handle_popup,
)
from sop_step_parser import parse_sop_steps, steps_confidence, vkey_label, clean_step_instruction, StepItem
from mcp_client import MCPClientUnavailable, get_default_sync_client


def env_enabled(name, default="true"):
    return str(os.getenv(name, default)).strip().lower() in {"1", "true", "yes", "y", "on"}


def env_list(name, default):
    value = os.getenv(name, default)
    return [item.strip() for item in value.split(",") if item.strip()]

# Study Mode 只允許使用的工具名稱
STUDY_ALLOWED_TOOLS = {"guide_user_action", "visualize_element"}
STUDY_TOOL_SCHEMAS = [s for s in TOOL_SCHEMAS if s.get("function", {}).get("name") in STUDY_ALLOWED_TOOLS]

DEFAULT_LLM_PROVIDER = normalize_provider_name(os.getenv("LLM_PROVIDER", "github_copilot"))
DEFAULT_MODEL = default_model_for_provider(DEFAULT_LLM_PROVIDER)

# 最大 ReAct 迴圈次數（防止無限迴圈）
MAX_ITERATIONS = int(os.getenv("COPILOT_MAX_ITERATIONS", "6"))
# Study Mode 允許更多步驟，且與 Auto Mode 分開設定
STUDY_MAX_ITERATIONS = int(os.getenv("STUDY_MAX_ITERATIONS", "20"))

CONVERSATION_HISTORY_LIMIT = int(os.getenv("COPILOT_HISTORY_LIMIT", "14"))
EDITOR_CONTEXT_MAX_CHARS = int(os.getenv("EDITOR_CONTEXT_MAX_CHARS", "12000"))

# Stage 2 MCP settings. MCP is the primary SAP operation path; the legacy
# sap_agent_tools.py/pywin32 path remains as fallback during migration.
MCP_SAP_ENABLED = env_enabled("MCP_SAP_ENABLED", "true")
MCP_SCREEN_TOOL_CANDIDATES = env_list(
    "MCP_SAP_SCREEN_TOOLS",
    "sap_get_screen_elements,sap_get_screen,sap_scan_screen,sap_get_current_screen",
)
MCP_SESSION_INFO_TOOL_CANDIDATES = env_list(
    "MCP_SAP_SESSION_INFO_TOOLS",
    "sap_get_session_info,sap_get_current_session_info",
)
MCP_SET_FOCUS_TOOL_CANDIDATES = env_list(
    "MCP_SAP_SET_FOCUS_TOOLS",
    "sap_set_focus,sap_focus_element",
)
MCP_FAILURE_COOLDOWN_SECONDS = float(os.getenv("MCP_SAP_FAILURE_COOLDOWN_SECONDS", "30"))
MCP_TIMING_DEBUG = env_enabled("MCP_TIMING_DEBUG", "false")
MCP_SAP_FAST_MODE = env_enabled("MCP_SAP_FAST_MODE", "true")
MCP_SAP_TOOL_PROFILE = os.getenv("MCP_SAP_TOOL_PROFILE", "core").strip().lower()
MCP_FAST_SCREEN_MAX_DEPTH = int(os.getenv("MCP_FAST_SCREEN_MAX_DEPTH", "2"))
MCP_FAST_SCREEN_CHANGEABLE_ONLY = env_enabled("MCP_FAST_SCREEN_CHANGEABLE_ONLY", "false")
MCP_EVIDENCE_SCREEN_MAX_DEPTH = int(os.getenv("MCP_EVIDENCE_SCREEN_MAX_DEPTH", "5"))
MCP_EVIDENCE_SCREEN_TYPE_FILTER = os.getenv(
    "MCP_EVIDENCE_SCREEN_TYPE_FILTER",
    ",".join([
        "GuiTextField",
        "GuiCTextField",
        "GuiPasswordField",
        "GuiComboBox",
        "GuiCheckBox",
        "GuiRadioButton",
        "GuiButton",
        "GuiTab",
        "GuiTableControl",
        "GuiGridView",
        "GuiShell",
        "GuiLabel",
        "GuiStatusbar",
        "GuiOkCodeField",
    ]),
)
MCP_EVIDENCE_LEGACY_FALLBACK_ON_EMPTY = env_enabled("MCP_EVIDENCE_LEGACY_FALLBACK_ON_EMPTY", "true")
MCP_EVIDENCE_MIN_ELEMENT_COUNT = int(os.getenv("MCP_EVIDENCE_MIN_ELEMENT_COUNT", "1"))
MCP_SCREEN_CACHE_ENABLED = env_enabled("MCP_SCREEN_CACHE_ENABLED", "true")
MCP_SCREEN_CACHE_TTL_SECONDS = float(os.getenv("MCP_SCREEN_CACHE_TTL_SECONDS", "30"))
MCP_SCREEN_CACHE_MAX_WRITES = int(os.getenv("MCP_SCREEN_CACHE_MAX_WRITES", "20"))
MCP_EXPOSE_DISCOVERY_TO_LLM = env_enabled("MCP_EXPOSE_DISCOVERY_TO_LLM", "false")
MCP_POPUP_USE_POPUP_TOOL_ONLY = env_enabled("MCP_POPUP_USE_POPUP_TOOL_ONLY", "true")
MCP_ATTACH_ELEMENTS_AFTER_NAV = env_enabled("MCP_ATTACH_ELEMENTS_AFTER_NAV", "true")
MCP_ATTACH_ELEMENTS_ON_FIELD_FAILURE = env_enabled("MCP_ATTACH_ELEMENTS_ON_FIELD_FAILURE", "true")
MCP_READ_TABLES_IN_CONTEXT = env_enabled("MCP_READ_TABLES_IN_CONTEXT", "true")
MCP_READ_SHELLS_IN_CONTEXT = env_enabled("MCP_READ_SHELLS_IN_CONTEXT", "true")
MCP_TABLE_CONTEXT_MAX_TABLES = int(os.getenv("MCP_TABLE_CONTEXT_MAX_TABLES", "3"))
MCP_TABLE_CONTEXT_MAX_ROWS = int(os.getenv("MCP_TABLE_CONTEXT_MAX_ROWS", "25"))
MCP_TABLE_CONTEXT_MAX_COLUMNS = int(os.getenv("MCP_TABLE_CONTEXT_MAX_COLUMNS", "12"))
MCP_TABLE_CONTEXT_MAX_SCHEMA_COLUMNS = int(os.getenv("MCP_TABLE_CONTEXT_MAX_SCHEMA_COLUMNS", "80"))
MCP_TABLE_CONTEXT_MAX_CELL_CHARS = int(os.getenv("MCP_TABLE_CONTEXT_MAX_CELL_CHARS", "160"))
MCP_TABLE_CONTEXT_DISCOVERY_DEPTH = int(os.getenv("MCP_TABLE_CONTEXT_DISCOVERY_DEPTH", "6"))
MCP_TABLE_CONTEXT_SCHEMA_FIRST = env_enabled("MCP_TABLE_CONTEXT_SCHEMA_FIRST", "true")
MCP_TABLE_CONTEXT_BROAD_DISCOVERY_ON_EMPTY = env_enabled("MCP_TABLE_CONTEXT_BROAD_DISCOVERY_ON_EMPTY", "true")
MCP_TABLE_CONTEXT_BROAD_DISCOVERY_DEPTH = int(os.getenv("MCP_TABLE_CONTEXT_BROAD_DISCOVERY_DEPTH", "10"))
MCP_TABLE_CONTEXT_INSPECT_ON_EMPTY = env_enabled("MCP_TABLE_CONTEXT_INSPECT_ON_EMPTY", "true")
MCP_TABLE_CONTEXT_INSPECT_INCLUDE_ROWS = env_enabled("MCP_TABLE_CONTEXT_INSPECT_INCLUDE_ROWS", "false")
MCP_TABLE_CONTEXT_MAX_CANDIDATES = int(os.getenv("MCP_TABLE_CONTEXT_MAX_CANDIDATES", "12"))
MCP_SHELL_CONTEXT_MAX_CHARS = int(os.getenv("MCP_SHELL_CONTEXT_MAX_CHARS", "4000"))
MCP_TABLE_CONTEXT_TYPE_FILTER = os.getenv(
    "MCP_TABLE_CONTEXT_TYPE_FILTER",
    "GuiGridView,GuiTableControl,GuiShell",
)
MCP_FAST_SCREEN_TYPE_FILTER = os.getenv(
    "MCP_FAST_SCREEN_TYPE_FILTER",
    ",".join([
        "GuiTextField",
        "GuiCTextField",
        "GuiPasswordField",
        "GuiComboBox",
        "GuiCheckBox",
        "GuiRadioButton",
        "GuiButton",
        "GuiTab",
        "GuiTableControl",
        "GuiShell",
        "GuiOkCodeField",
    ]),
)
MCP_CORE_TOOL_NAMES = set(env_list(
    "MCP_SAP_CORE_TOOLS",
    ",".join([
        "sap_connect",
        "sap_connect_existing",
        "sap_list_connections",
        "sap_get_session_info",
        "sap_get_screen_info",
        "sap_get_light_snapshot",
        "sap_get_screen_elements",
        "sap_execute_transaction",
        "sap_send_key",
        "sap_read_field",
        "sap_set_field",
        "sap_set_batch_fields",
        "sap_set_fields_and_enter",
        "sap_press_button",
        "sap_select_menu",
        "sap_select_checkbox",
        "sap_select_radio_button",
        "sap_select_combobox_entry",
        "sap_get_combobox_entries",
        "sap_select_tab",
        "sap_read_textedit",
        "sap_set_textedit",
        "sap_set_focus",
        "sap_inspect_tables",
        "sap_read_table",
        "sap_select_table_row",
        "sap_select_popup_table_row_and_confirm",
        "sap_select_multiple_rows",
        "sap_get_popup_window",
        "sap_handle_popup",
        "sap_get_toolbar_buttons",
        "sap_read_shell_content",
        "sap_analyze_delivery_block",
        "sap_screenshot",
        "sap_disconnect",
    ]),
))
MCP_DYNAMIC_TOOL_GROUPS = {
    "alv": {
        "sap_get_alv_toolbar",
        "sap_press_alv_toolbar_button",
        "sap_select_alv_context_menu_item",
        "sap_double_click_cell",
        "sap_modify_cell",
        "sap_set_current_cell",
        "sap_get_column_info",
        "sap_get_current_cell",
        "sap_get_cell_info",
        "sap_press_column_header",
        "sap_select_all_rows",
    },
    "table": {
        "sap_scroll_table_control",
        "sap_get_table_control_row_info",
        "sap_select_all_table_control_columns",
        "sap_double_click_cell",
        "sap_modify_cell",
        "sap_set_current_cell",
        "sap_get_column_info",
        "sap_get_current_cell",
    },
    "tree": {
        "sap_read_tree",
        "sap_expand_tree_node",
        "sap_collapse_tree_node",
        "sap_select_tree_node",
        "sap_double_click_tree_node",
        "sap_double_click_tree_item",
        "sap_click_tree_link",
        "sap_find_tree_node_by_path",
        "sap_search_tree_nodes",
        "sap_get_tree_node_children",
    },
}
MCP_FIELD_WRITE_TOOL_NAMES = {
    "sap_set_field",
    "sap_set_batch_fields",
    "sap_set_fields_and_enter",
    "sap_select_checkbox",
    "sap_select_radio_button",
    "sap_select_combobox_entry",
    "sap_set_textedit",
    "sap_set_focus",
}
MCP_NAVIGATION_TOOL_NAMES = {
    "sap_execute_transaction",
    "sap_send_key",
    "sap_press_button",
    "sap_select_menu",
    "sap_select_tab",
    "sap_handle_popup",
    "sap_select_table_row",
    "sap_select_popup_table_row_and_confirm",
    "sap_select_multiple_rows",
    "sap_double_click_cell",
    "sap_press_alv_toolbar_button",
    "sap_select_alv_context_menu_item",
    "sap_expand_tree_node",
    "sap_collapse_tree_node",
    "sap_select_tree_node",
    "sap_double_click_tree_node",
    "sap_double_click_tree_item",
    "sap_click_tree_link",
}
MCP_DISCOVERY_TOOL_NAMES = {
    "sap_get_session_info",
    "sap_get_current_session_info",
    "sap_get_screen_info",
    "sap_get_light_snapshot",
    "sap_get_screen_elements",
    "sap_inspect_tables",
    "sap_get_screen",
    "sap_scan_screen",
    "sap_get_current_screen",
    "sap_get_popup_window",
    "sap_screenshot",
}

# System Prompt - Auto Mode (可執行操作)
SYSTEM_PROMPT_AUTO = """你是一個專業的 SAP GUI 操作助手。你可以透過工具來操作 SAP 系統。

## 你的能力
1. 閱讀 SAP 畫面的結構化 JSON，理解當前畫面的狀態、欄位、按鈕
2. 使用本輪 API 實際提供的工具操作 SAP。Stage 2 預設工具來源是 MCP (`mcp-sap-gui`)，工具名稱可能以 `sap_` 開頭；若 MCP 不可用，系統才會提供舊版 GUI COM fallback 工具 (set_text, select_combo, click, send_vkey 等)
3. 根據狀態列訊息判斷操作是否成功

## 工作流程 (ReAct Loop)
1. **觀察 (Scan)**：閱讀提供的畫面 JSON，理解當前在哪個交易、有哪些欄位和按鈕
2. **思考 (Think)**：分析使用者的需求，決定需要執行哪些操作
3. **行動 (Act)**：呼叫適當的工具執行操作
4. **驗證 (Verify)**：根據工具回傳的結果判斷是否成功

## 重要規則
- 操作前先仔細閱讀畫面 JSON，確認元件 ID 正確
- 不要呼叫本輪工具清單沒有提供的工具；若工具名稱與舊版不同，依工具 description 與 parameters 判斷用途
- 如果畫面 JSON 有 active_popup 或 popup_wnd1，代表目前有 SAP 彈出視窗；請先處理彈窗，再操作主視窗
- 處理彈窗時優先使用 handle_popup；例如填寫彈窗「標題」後按儲存，使用 handle_popup(action="save", field_label="標題", value="...")
- 如果 active_popup 的 title 是「錯誤」或 messages 有錯誤文字，先讀 messages 判斷原因；通常要先 handle_popup(action="ok") 關閉最上層錯誤，再依下一層彈窗的 fields 補齊空白/焦點欄位
- screen JSON 中的 fields 會把欄位 label 與元件 ID 配對；填欄位時優先使用 fields 裡的 id 或 handle_popup(field_label=...)
- 如果 fields 的 type 是 GuiComboBox、dropdown=true 或含 options，代表下拉式選單；必須使用 select_combo，或在彈窗中用 handle_popup 依 label 選值，不要把它當一般文字欄位 set_text
- 下拉式選單若有 options，優先用 option key；沒有 key 時才用顯示文字
- 使用 MCP 工具時，如果有 sap_set_fields_and_enter，且同一畫面要填欄位後按 Enter 驗證，優先一次呼叫 sap_set_fields_and_enter(fields={id: value, ...})；不要拆成 sap_set_batch_fields + sap_send_key
- 需要切換 SAP 交易時，優先使用 sap_execute_transaction(tcode)。不要用 sap_set_field/set_text 直接把裸 T-Code 寫進 OK Code；若必須使用 OK Code 且目前不在起始畫面，交易碼必須加 `/n`，例如 `/nVL10A`。
- 如果只需要填多個欄位但暫不送出，才使用 sap_set_batch_fields(fields={id: value, ...}, validate=false)，不要逐欄 sap_set_field
- 如果畫面是彈窗 table row 選擇後要按繼續，且有 sap_select_popup_table_row_and_confirm，優先一次呼叫它；不要拆成 sap_select_table_row + sap_handle_popup
- 不要把 save/post/delete/confirm/release 等敏感提交操作放進批次欄位動作；這些操作仍必須走確認或由使用者明確允許
- 如果工具結果的 screen_after 內含 screen_elements、field_write_recovery 或 instruction_to_agent，下一步必須優先使用其中列出的實際元件 ID 重試；不要要求使用者先列出元素 ID
- 如果欄位寫入回傳 Could not set field 或 failed>0，代表使用了錯誤/過期的 ID；先根據 screen_after.field_write_recovery 或 screen_after.screen_elements 找正確欄位重試，不要立刻改成手動教學
- 如果 fields 的 type 是 GuiCheckBox 或 GuiRadioButton，讀取狀態使用 read_checkbox，設定狀態使用 set_checkbox；不要用 set_text 寫入 True/False，也不要在狀態未知時盲目 click
- checkbox/radio 的 fields[].value 會是 "True" / "False"，selected 也會標示布林狀態；操作前先確認目前狀態，避免重複切換
- 如果畫面 context 有 `mcp_table_report_context`，代表系統已用 MCP 讀到 ALV/Grid/TableControl 報表資料；回答或操作前優先使用其中的 `tables[].rows`、`columns`、`column_info`、`total_rows`，不要要求使用者手動貼出表格。
- 如果使用者問「有哪些欄位」，即使 `tables[].rows` 是空的，只要 `tables[].columns` 或 `column_info` 有資料，就必須直接列出欄位；不要因 rows 空就說表格讀不到。
- 大型報表若 `total_rows > rows_returned`，只在使用者需要更多資料時才分頁呼叫 `sap_read_table(start_row=...)`；不要一次讀完整大表。
- 如果 active_popup.tables 或 tables 顯示 GuiTableControl 列資料，且任務是勾選/選擇某一列（例如 MM03「選擇檢視」彈窗中的「基本資料 1」），優先使用 select_table_row(row_text=...)；不要只 click/highlight 文字 cell，因為 checkbox 可能藏在 table 選取欄內
- 選完 table row 後通常還需要按彈窗的 ok/continue/Enter；先用 select_table_row，再用 handle_popup(action="ok") 或 send_vkey(0, window_id="wnd[1]")
- 如果 scan 結果有 editors、role=editor、editor_capabilities、type=GuiAbapEditor 或 type=GuiShell 的 ABAP editor，代表程式碼編輯器；讀取程式碼必須使用 read_editor_text，寫入 ABAP 原始碼必須使用 set_editor_text，不要用 set_text
- read_editor_text / set_editor_text 可不傳 element_id，工具會自動尋找目前畫面的 editor；如果 screen_after.editors 有 id，優先傳該 id
- 若需要引導使用者看見某個欄位或按鈕，可使用 visualize_element 高亮該元件；這是視覺提示，不代表已填值或點擊
- 對彈窗發送 Enter/F12 等按鍵時，若不用 handle_popup，send_vkey 會自動送到活動彈窗，也可明確指定 window_id="wnd[1]"
        - 每次工具結果都可能包含 screen_after；後續操作必須以 screen_after 的最新元件 ID 為準，不要沿用舊畫面的 ID 或猜測不存在的 ID
- 如果操作失敗，嘗試分析原因並提出替代方案
- 遇到不確定的情況，向使用者詢問
- 使用繁體中文回覆使用者
- 回覆時簡潔明確，說明你做了什麼、結果如何

## SAP 基礎知識
- T-Code (Transaction Code) 是 SAP 交易代碼，如 VA01=建立銷售訂單, MM01=建立物料主檔
- wnd[0] 是主視窗，wnd[1] 是彈出視窗
- sbar 是狀態列，顯示操作結果訊息
- VKey 0=Enter, 3=F3(返回), 8=F8(執行), 11=Ctrl+S(儲存), 12=F12(取消)
"""

# System Prompt - Ask Mode (僅回答問題，不執行操作)
SYSTEM_PROMPT_ASK = """你是一個專業的 SAP GUI 問答助手。你**只回答問題，絕對不執行任何操作**。

## 你的能力
1. 閱讀 SAP 畫面的結構化 JSON，理解當前畫面的狀態
2. 結合 SAP 知識，回答使用者關於操作流程的問題
3. 解讀狀態列的錯誤訊息，提供解決方案
4. 參考已錄製的 SOP 操作紀錄，提供步驟引導

## 重要規則
- **你不能呼叫任何工具**，只能用文字回答
- 根據當前畫面 JSON 分析「這格該填什麼」「為何報錯」等問題
- 如果畫面 JSON 有 `editor_sources` 且 success=true，代表已讀到 SE38/ABAP editor 的程式碼；回答程式用途時必須根據 `editor_sources[].text` 分析，不要再要求使用者貼程式碼
- 如果畫面 JSON 或 MCP context 有 `mcp_table_report_context`，代表已讀到目前 ALV/Grid/TableControl 報表或表格資料；回答表格、清單、報表內容時必須根據 `tables[].rows`、`columns` 與 `column_info`，不要要求使用者再貼表格。
- 如果問題是詢問表格欄位、欄位標題或目前顯示哪些 columns，`tables[].columns` / `column_info` 本身就是答案；即使 rows 為空，也不要回答「表格內容讀不到」。
- 如果 context 有 `legacy_screen_summary_fallback`，代表 MCP 輕量讀取不足但 legacy scanner 讀到了補充資料；必須優先使用 fallback 內的 `fields`、`tables`、`status_bar`，不要再宣稱「畫面元素無法讀到」。
- 如果 `editor_sources` 讀取失敗，說明讀取失敗原因，並請使用者切到程式碼 editor 或貼出程式碼
- 如果提供了 SOP 紀錄，參考其步驟來引導使用者
- 使用繁體中文回覆
- 回覆時結構清晰，使用列表或步驟說明

## SAP 基礎知識
- T-Code (Transaction Code) 是 SAP 交易代碼
- 狀態列類型: S=成功, W=警告, E=錯誤, I=資訊, A=中止
- 常見欄位: VBELN=單號, MATNR=物料, BUKRS=公司代碼, WERKS=工廠
"""

# System Prompt - Solve Mode (問題排解：只診斷，不操作)
SYSTEM_PROMPT_SOLVE = """你是一個專業的 SAP GUI 問題排解助手。你只根據目前 SAP 畫面與使用者描述，告訴使用者當下應該如何處理；你絕對不執行任何 SAP 操作，也不呼叫工具。

## 任務目標
1. 先完整閱讀目前畫面 JSON 的 `solve_diagnostics`、active_popup、status_bar、messages、tables、fields、focused_element、editor_sources。
2. 先整理「實際讀到的錯誤/警告/提示訊息」，再根據這些訊息判斷原因；不要先猜常見問題。
3. 給出可立即照做的處理步驟，優先說明「現在先做什麼」，再說明原因。
4. 若資訊不足，只問一個最關鍵的確認問題，並明確說明目前缺少哪個錯誤訊息或畫面證據。

## 回答規則
- 使用繁體中文，簡潔、具體、可操作。
- 回答必須先給「讀到的錯誤/訊息」，再給「目前判斷」，最後給「建議處理」。
- 如果 `solve_diagnostics.messages` 或 table rows 有多筆錯誤，必須先逐條列出或分組歸納，再判斷優先修正順序。
- 如果 context 有 `mcp_table_report_context`，必須優先使用其中的表格/報表列與欄位 schema 作為證據；若只讀到欄位 schema 而沒有 rows，可以回答欄位問題，但需說明目前未讀到列資料。
- 如果 context 有 `legacy_screen_summary_fallback`，代表 MCP 輕量讀取不足但 legacy scanner 讀到了補充資料；必須先使用 fallback 的 `fields`、`tables`、`messages`，不要直接回答畫面讀不到。
- 不要在沒有實際錯誤訊息佐證時提出具體程式碼修法；可以說「目前畫面未提供足夠錯誤文字，請先打開/展開錯誤清單」。
- 若使用者提到 ABAP Activate / Syntax Check 失敗，必須先尋找語法錯誤清單、狀態列錯誤、彈窗訊息或 table row。只有在讀到實際錯誤文字後，才給出對應 ABAP 修法。
- 若有 active_popup，優先說明彈窗標題、錯誤/提示文字、必填欄位、可按的按鈕，並建議使用者如何填寫或關閉。
- 若 status_bar type 是 E/W/A，先引用狀態列原文，再說要修正哪個欄位或回到哪個畫面。
- 若畫面有紅框必填欄位，指出欄位 label、目前值與建議輸入內容；不要只回技術 ID。
- 若畫面是 SE38/ABAP editor 且 editor_sources 可用，可以根據程式碼協助排查；若無 source，不要猜測程式功能。
- 若問題涉及資料修改、刪除、過帳、啟用、儲存或送出，提醒使用者確認環境與資料正確性。
- 不要說你可以幫使用者點擊或輸入；Solve Mode 只提供處理建議。
"""

# System Prompt - Study Mode (Agentic 教練：只能引導，不能寫入)
SYSTEM_PROMPT_STUDY = """你是一個 SAP GUI 操作教練。你的任務是根據提供的 SOP 指南或使用者的學習目標，一步一步引導使用者完成 SAP 操作。

## 核心原則
你**絕對不能**替使用者執行任何寫入操作。你唯一能做的是：
1. 使用 `guide_user_action` 工具高亮 SAP 元件並顯示操作指引，等待使用者確認完成
2. 使用 `visualize_element` 工具高亮元件讓使用者知道要操作的位置
3. Stage 2 會優先用 MCP 取得畫面狀態；若 MCP 不可用才會回退到舊 GUI COM 掃描

## 嚴格禁止使用的工具
- set_text、select_combo、set_editor_text、click、send_vkey、set_tcode、handle_popup
- 這些工具會直接修改 SAP 畫面，你**絕對不能**呼叫它們

## 工作流程
1. 閱讀提供的 SOP 指南、即席教學目標和當前 SAP 畫面狀態
2. 若 extra context 包含 `Study Evidence Pack`，先依 evidence pack 的證據優先級判斷，不要直接用 SAP 常識補齊
3. 根據 SOP 的下一步，或在沒有 SOP 時根據 evidence pack、目前畫面與使用者確認推斷下一步，找到畫面上對應的元件 ID
4. 使用 `guide_user_action(element_id, instruction, reason, confidence, source, choices, expected_response_type)` 引導使用者
5. 使用者確認完成後，工具結果會包含 `user_response`；如果它不是空字串，代表使用者在教學中輸入了明確回答，你必須依該回答決定下一步，不能重複詢問同一個問題
6. 如果畫面狀態與 SOP 預期不同，分析原因並調整引導

## Evidence Pack 使用規則
- 證據優先級固定為：正式 skill / recording > promoted 公司 skill > 同 module / business cycle draft > 匯入 knowledge 文件 > 即時官方搜尋 > 社群或一般搜尋 > LLM prior。
- 每個候選流程都要標示 module、business_cycle、來源與信心；信心低或來源不足時，先請使用者確認第一個關鍵 T-Code / 流程方向。
- 不要輸出 raw chain-of-thought；只輸出 structured rationale：依據來源、信心、未知項目、下一個安全引導步驟。
- web search 或 community evidence 只能作為低信心參考，不能直接變成正式操作步驟。
- 如果 Evidence Summary 顯示 `evidence_level: llm_prior_only` 或 `should_confirm_flow: true`，第一個 `guide_user_action` 必須是候選流程確認，不得要求使用者同時輸入 T-Code 或業務欄位。
- 低信心候選確認可使用 `source="LLM prior"`、`confidence="low"`、`expected_response_type="choice"`、`choices=[...]`。使用者直接按 OK 視為接受第一個建議；輸入其他 T-Code 或流程名稱時，後續必須改用使用者指定方向。

## 元件定位規則
- `guide_user_action` 的 `element_id` 必須是目前畫面中精確、可見、可操作的元件 ID。
- 不要把 `wnd[0]/usr`、`wnd[1]/usr`、`GuiUserArea`、container、label 或 status bar 當成要輸入資料的欄位。
- 要求使用者輸入值時，`expected_response_type` 必須是 `value`，且目標必須是實際輸入欄位、combo、checkbox 或 radio。
- 如果狀態列提示必填欄位（例如「輸入 採購群組」），但目前 screen context 沒有該欄位 ID，請先說明「目前未在可見畫面讀到此欄位」，並引導使用者揭露欄位（表格水平捲動、項目明細、版面/個人設定、SAP 欄位搜尋），不要直接要求填入不存在的欄位。
- 如果工具回傳 `target_actionable=false`，下一輪不得重複同一個 element_id；必須改找精確欄位，或改成引導使用者揭露欄位。

## 沒有既有 SOP 時
- 如果 extra context 表明「目前沒有同名 SOP / skill」或「即席 Study 任務」，你仍然要教學，不要要求使用者先錄製 SOP
- 先讀 `Study Evidence Pack`；若有正式 skill、draft 或 knowledge 命中，必須引用其 module、business_cycle、來源與信心來提出候選流程
- 如果 evidence pack 沒有足夠證據，可以提出低信心候選，但第一步只能要求使用者確認候選流程，不要直接教完整流程
- 如果需要進入 T-Code，請用 `guide_user_action` 高亮 T-Code 欄位，指示使用者輸入交易代碼並按 Enter
- 如果目標太模糊，先問一個最小必要問題；若已有合理預設流程，先提出建議並引導第一步
- 完成教學時，回覆一段簡短、可重用的 SOP 摘要，方便系統保存成 skill 草稿

## SOP 與目前畫面的關係
- SOP 是參考資料，不是絕對腳本；你必須優先依照目前 SAP 畫面狀態引導
- 在引導任何欄位輸入前，先檢查目前畫面 JSON 的 `fields` / `active_popup.fields`
- 如果目標欄位已經有非空 `value`，不要要求使用者重新輸入 SOP 中的舊值；請引導使用者「確認沿用目前值」，只有在目前值不符合本次需求時才請使用者修改
- 如果 SOP 或事件中出現 FIELD_DEFAULT / system_default，代表 SAP 畫面跳轉後自動帶出的預設值，通常不需要使用者輸入
- 如果 SOP 值與目前畫面值不同，明確說明「SOP 參考值是 X，目前值是 Y」，並讓使用者決定是否沿用目前值或改成其他值
- 日期、組織、客戶、付款人等欄位常會有系統預設值；不要把這些預設值當成必須重打的步驟

## 引導風格
- 每次只引導一個步驟，等使用者確認完成後再繼續
- `instruction` 只寫本步要做什麼，最多 5 行；不要把候選流程、完整分析、未知項目清單塞進 `instruction`
- `reason` 只寫一行為什麼；`source` 與 `confidence` 放來源與信心；需要使用者選擇時用 `choices`
- 如果 SOP 中有具體值（如 T-Code、欄位值），在 instruction 中把它稱為「參考值」；目前畫面已有值時，優先提示確認目前值
- 如果你用 `guide_user_action` 詢問選項，下一輪必須讀取工具結果的 `user_response` 並處理該選項；例如使用者回覆 `4` 且你的選項 4 是結束教學，就要停止呼叫工具並回覆 SOP 摘要
- 不要連續對同一元件提出語意相同的選項問題；若使用者已回答，必須收斂或換下一步
- 遇到彈窗或錯誤，先分析原因再引導使用者處理
- 使用繁體中文

## SAP 基礎知識
- T-Code 欄位 ID 通常是 wnd[0]/tbar[0]/okcd
- VKey 0=Enter, 3=F3(返回), 8=F8(執行), 11=Ctrl+S(儲存), 12=F12(取消)
- wnd[0] 是主視窗，wnd[1] 是彈出視窗
"""


class SAPAgent:
    """
    SAP GUI AI Agent

    透過可切換 LLM provider 實現自然語言操作 SAP 的能力。
    """

    def __init__(self, auth: CopilotAuth = None, model: str = None, provider_name: str = None):
        """
        初始化 Agent。

        Args:
            auth: GitHub Copilot provider 使用的認證物件
            model: 使用的模型名稱
            provider_name: github_copilot 或 codex
        """
        self.provider_name = normalize_provider_name(provider_name or DEFAULT_LLM_PROVIDER)
        self.provider = create_llm_provider(self.provider_name, auth=auth, model=model)
        self.auth = getattr(self.provider, "auth", auth)
        self.model = self.provider.model
        self._mode = "auto"  # "auto", "ask", "solve", or "study"
        self.mcp_client = get_default_sync_client() if MCP_SAP_ENABLED else None
        self._mcp_all_tool_schemas = None
        self._mcp_all_tool_names = set()
        self._mcp_tool_schemas = None
        self._mcp_tool_names = set()
        self._mcp_unavailable_reason = ""
        self._mcp_last_failure_at = 0.0
        self._mcp_tool_hints = set()
        self._last_screen_context_text = ""
        self._last_screen_backend = ""
        self._mcp_screen_cache = {
            "fingerprint": "",
            "container_id": "",
            "elements_text": "",
            "elements_at": 0.0,
        }
        self._mcp_last_screen_info = None
        self._mcp_last_screen_fingerprint = ""
        self._mcp_last_screen_events = []
        self._mcp_recent_field_writes = {}
        self.conversation_history = [
            {"role": "system", "content": SYSTEM_PROMPT_AUTO}
        ]

    @property
    def mode(self):
        """當前模式: 'auto', 'ask', 'solve' 或 'study'"""
        return self._mode

    def set_mode(self, mode: str):
        """
        切換 Agent 模式。

        Args:
            mode: 'auto', 'ask', 'solve' 或 'study'
        """
        if mode not in ("auto", "ask", "solve", "study"):
            raise ValueError(f"不支援的模式: {mode}，請使用 'auto', 'ask', 'solve' 或 'study'")

        self._mode = mode
        prompt = {
            "auto": SYSTEM_PROMPT_AUTO,
            "ask": SYSTEM_PROMPT_ASK,
            "solve": SYSTEM_PROMPT_SOLVE,
            "study": SYSTEM_PROMPT_STUDY,
        }[mode]
        self.conversation_history = [
            {"role": "system", "content": prompt}
        ]
        mode_names = {
            "auto": "🟣 Auto Mode（自動代操）",
            "ask": "🟢 Ask Mode（問答模式）",
            "solve": "🟡 Solve Mode（問題排解）",
            "study": "📘 Study Mode（教練引導）",
        }
        print(f"\033[90m[Agent] 已切換至 {mode_names[mode]}\033[0m")

    def _call_copilot_api(self, messages, tools=None):
        """
        呼叫目前 LLM provider 的 Chat Completions API。

        Args:
            messages: 對話歷史
            tools: Function Calling 工具定義（可選）

        Returns:
            dict: API 回應

        Raises:
            RuntimeError: API 呼叫失敗
        """
        return self.provider.chat_completions(
            messages,
            tools=tools,
            timing_callback=self._timing_log,
        )

    def _timing_log(self, label, started_at):
        if MCP_TIMING_DEBUG:
            elapsed = time.perf_counter() - started_at
            print(f"\033[90m[Timing] {label}: {elapsed:.3f}s\033[0m")

    def _parse_mcp_json_text(self, text):
        value = str(text or "").strip()
        if not value:
            return None
        if value.startswith("```"):
            lines = value.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            value = "\n".join(lines).strip()
        try:
            return json.loads(value)
        except Exception:
            pass
        first = value.find("{")
        last = value.rfind("}")
        if first >= 0 and last > first:
            try:
                return json.loads(value[first:last + 1])
            except Exception:
                return None
        return None

    def _extract_screen_from_mcp_payload(self, payload):
        if not isinstance(payload, dict):
            return None
        if isinstance(payload.get("screen"), dict):
            return payload["screen"]
        if isinstance(payload.get("screen_info"), dict):
            return payload["screen_info"]
        validation = payload.get("validation")
        if isinstance(validation, dict) and isinstance(validation.get("screen"), dict):
            return validation["screen"]
        text = payload.get("text")
        if isinstance(text, str):
            parsed_text = self._parse_mcp_json_text(text)
            if isinstance(parsed_text, dict):
                return self._extract_screen_from_mcp_payload(parsed_text)
        content = payload.get("content")
        if isinstance(content, list):
            for item in content:
                screen = self._extract_screen_from_mcp_payload(item)
                if screen:
                    return screen
        return None

    def _mcp_screen_fingerprint(self, screen_info):
        if not isinstance(screen_info, dict):
            return ""
        keys = ("active_window", "transaction", "program", "screen_number", "title")
        return "|".join(str(screen_info.get(key, "") or "") for key in keys)

    def _mcp_screen_events_from_info(self, previous, current):
        if not isinstance(previous, dict) or not isinstance(current, dict):
            return []
        events = []
        prev_tcode = str(previous.get("transaction", "") or "")
        curr_tcode = str(current.get("transaction", "") or "")
        prev_screen = str(previous.get("screen_number", "") or "")
        curr_screen = str(current.get("screen_number", "") or "")
        prev_window = str(previous.get("active_window", "") or "")
        curr_window = str(current.get("active_window", "") or "")
        title = str(current.get("title", "") or "")

        if curr_tcode and prev_tcode != curr_tcode:
            events.append({
                "event_type": "TCODE_CHANGE",
                "from_tcode": prev_tcode,
                "to_tcode": curr_tcode,
                "screen_number": curr_screen,
                "title": title,
            })
        elif curr_screen and prev_screen != curr_screen and prev_tcode == curr_tcode:
            events.append({
                "event_type": "SCREEN_CHANGE",
                "tcode": curr_tcode,
                "from_screen": prev_screen,
                "to_screen": curr_screen,
                "title": title,
            })

        if curr_window and prev_window != curr_window:
            events.append({
                "event_type": "ACTIVE_WINDOW_CHANGE",
                "from_window": prev_window,
                "to_window": curr_window,
                "title": title,
                "tcode": curr_tcode,
                "screen_number": curr_screen,
            })
            if curr_window != "wnd[0]":
                events.append({
                    "event_type": "WINDOW_OPEN",
                    "window_id": curr_window,
                    "title": title,
                    "tcode": curr_tcode,
                    "screen_number": curr_screen,
                })
            elif prev_window and prev_window != "wnd[0]":
                events.append({
                    "event_type": "WINDOW_CLOSE",
                    "window_id": prev_window,
                    "title": str(previous.get("title", "") or ""),
                    "tcode": curr_tcode,
                    "screen_number": curr_screen,
                })
        return events

    def _remember_mcp_screen_info(self, screen_info):
        fingerprint = self._mcp_screen_fingerprint(screen_info)
        if not fingerprint:
            return ""
        if (
            self._mcp_last_screen_fingerprint
            and fingerprint != self._mcp_last_screen_fingerprint
        ):
            self._mcp_recent_field_writes = {}
        self._mcp_last_screen_info = screen_info
        self._mcp_last_screen_fingerprint = fingerprint
        return fingerprint

    def _mcp_cached_elements_valid(self, fingerprint, container_id, min_depth=0, type_filter=""):
        if not MCP_SCREEN_CACHE_ENABLED:
            return False
        cache = self._mcp_screen_cache
        if not fingerprint or not cache.get("elements_text"):
            return False
        if cache.get("fingerprint") != fingerprint:
            return False
        if cache.get("container_id") != container_id:
            return False
        if min_depth and int(cache.get("max_depth") or 0) < int(min_depth):
            return False
        if type_filter and cache.get("type_filter") != type_filter:
            return False
        age = time.time() - float(cache.get("elements_at") or 0)
        return age <= MCP_SCREEN_CACHE_TTL_SECONDS

    def _remember_mcp_elements(self, fingerprint, container_id, elements_text, max_depth=0, type_filter=""):
        if not MCP_SCREEN_CACHE_ENABLED or not fingerprint or not elements_text:
            return
        self._mcp_screen_cache = {
            "fingerprint": fingerprint,
            "container_id": container_id,
            "elements_text": str(elements_text),
            "elements_at": time.time(),
            "max_depth": int(max_depth or 0),
            "type_filter": type_filter,
        }

    def _mcp_filtered_elements_for_screen(self, screen_info, reason, force=False):
        """Read filtered elements internally and package them for a tool result."""
        elements_tool = self._first_available_mcp_tool([
            "sap_get_screen_elements",
            *MCP_SCREEN_TOOL_CANDIDATES,
        ])
        if not elements_tool or not isinstance(screen_info, dict):
            return None

        active_window = str(screen_info.get("active_window") or "wnd[0]")
        container_id = f"{active_window}/usr" if active_window.startswith("wnd[") else "wnd[0]/usr"
        fingerprint = self._mcp_screen_fingerprint(screen_info)

        if not force and self._mcp_cached_elements_valid(
            fingerprint,
            container_id,
            min_depth=MCP_FAST_SCREEN_MAX_DEPTH,
            type_filter=MCP_FAST_SCREEN_TYPE_FILTER,
        ):
            return {
                "backend": "mcp_internal_elements",
                "tool": elements_tool,
                "container_id": container_id,
                "reason": reason,
                "cache_hit": True,
                "raw": self._mcp_screen_cache.get("elements_text", ""),
                "instruction_to_agent": (
                    "Use these discovered element IDs for the next action. "
                    "Do not guess IDs and do not ask the user to list elements."
                ),
            }

        try:
            depth = MCP_FAST_SCREEN_MAX_DEPTH
            if reason == "field_write_failed":
                depth = max(depth, 3)
            raw_elements = self._call_mcp_tool_text(elements_tool, {
                "container_id": container_id,
                "max_depth": depth,
                "type_filter": MCP_FAST_SCREEN_TYPE_FILTER,
                "changeable_only": MCP_FAST_SCREEN_CHANGEABLE_ONLY,
            })
            self._remember_mcp_elements(
                fingerprint,
                container_id,
                raw_elements,
                max_depth=depth,
                type_filter=MCP_FAST_SCREEN_TYPE_FILTER,
            )
            return {
                "backend": "mcp_internal_elements",
                "tool": elements_tool,
                "container_id": container_id,
                "reason": reason,
                "cache_hit": False,
                "raw": raw_elements,
                "instruction_to_agent": (
                    "Use these discovered element IDs for the next action. "
                    "Do not guess IDs and do not ask the user to list elements."
                ),
            }
        except Exception as e:
            return {
                "backend": "mcp_internal_elements",
                "reason": reason,
                "error": str(e),
            }

    def _mcp_elements_from_text(self, raw_text):
        parsed = self._parse_mcp_json_text(raw_text)
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
        if isinstance(parsed, dict):
            for key in ("elements", "data", "items", "result"):
                value = parsed.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
            content = parsed.get("content")
            if isinstance(content, list):
                elements = []
                for item in content:
                    if isinstance(item, dict):
                        text = item.get("text") or item.get("content")
                        if isinstance(text, str):
                            elements.extend(self._mcp_elements_from_text(text))
                return elements
        return []

    def _mcp_context_sections(self, text):
        sections = []
        current_title = ""
        current_lines = []
        for line in str(text or "").splitlines():
            if line.startswith("### "):
                if current_title:
                    sections.append((current_title, "\n".join(current_lines).strip()))
                current_title = line[4:].strip()
                current_lines = []
            else:
                current_lines.append(line)
        if current_title:
            sections.append((current_title, "\n".join(current_lines).strip()))
        return sections

    def _mcp_text_has_useful_screen_evidence(self, text):
        element_count = 0
        table_rows = 0
        table_columns = 0
        shell_content = False

        for title, body in self._mcp_context_sections(text):
            if "screen_elements" in title or "fast filtered" in title:
                element_count += len(self._mcp_elements_from_text(body))
                continue
            if title == "mcp_table_report_context":
                parsed = self._parse_mcp_json_text(body)
                if not isinstance(parsed, dict):
                    continue
                for table in parsed.get("tables") or []:
                    if isinstance(table, dict):
                        table_rows += len(table.get("rows") or [])
                        table_columns += len(table.get("columns") or [])
                for shell in parsed.get("shells") or []:
                    if not isinstance(shell, dict):
                        continue
                    if shell.get("text") or shell.get("html_preview") or shell.get("url"):
                        shell_content = True

        return (
            element_count >= MCP_EVIDENCE_MIN_ELEMENT_COUNT
            or table_rows > 0
            or table_columns > 0
            or shell_content
        )

    def _mcp_table_candidates_from_elements(self, elements):
        candidates = []
        seen = set()
        table_types = {"GuiGridView", "GuiTableControl", "GuiShell", "GuiCtrlGridView"}
        table_id_patterns = (
            "/tbl",
            "tbl",
            "grid",
            "alv",
            "shellcont/shell",
            "cntl",
        )
        table_text_patterns = (
            "table",
            "grid",
            "item overview",
            "項目概觀",
            "項目總覽",
            "項目明細",
        )

        for element in elements or []:
            element_id = str(
                element.get("id")
                or element.get("element_id")
                or element.get("Id")
                or ""
            ).strip()
            element_type = str(element.get("type") or element.get("Type") or "").strip()
            element_name = str(element.get("name") or element.get("Name") or "").strip()
            element_text = str(element.get("text") or element.get("Text") or "").strip()
            lowered_blob = " ".join([element_id, element_name, element_text]).lower()
            looks_like_table = (
                element_type in table_types
                or any(pattern in lowered_blob for pattern in table_id_patterns)
                or any(pattern in lowered_blob for pattern in table_text_patterns)
            )
            if not element_id or element_id in seen or not looks_like_table:
                continue

            # GuiShell covers ALV grids, trees, HTML viewers and editors. Keep it
            # as a candidate, but table reads will be best-effort and may fall
            # back to shell content.
            seen.add(element_id)
            candidates.append({
                "id": element_id,
                "type": element_type,
                "name": element_name,
                "text": element_text,
            })
            if len(candidates) >= MCP_TABLE_CONTEXT_MAX_CANDIDATES:
                break

        priority = {"GuiGridView": 0, "GuiCtrlGridView": 0, "GuiTableControl": 1, "GuiShell": 2}
        candidates.sort(key=lambda item: (priority.get(item.get("type"), 7), item.get("id", "")))
        return candidates

    def _shorten_mcp_cell(self, value):
        if value is None:
            return ""
        text = str(value)
        if len(text) > MCP_TABLE_CONTEXT_MAX_CELL_CHARS:
            return text[:MCP_TABLE_CONTEXT_MAX_CELL_CHARS].rstrip() + "..."
        return text

    def _compact_mcp_table_result(self, payload, candidate):
        if not isinstance(payload, dict):
            return None
        if payload.get("error") and "table_type" not in payload:
            return None

        columns = list(payload.get("columns") or [])
        column_info = list(payload.get("column_info") or [])
        if not columns and column_info:
            columns = [str(item.get("name") or item.get("title") or "") for item in column_info]
            columns = [item for item in columns if item]

        if not columns and isinstance(payload.get("data"), list) and payload["data"]:
            first_row = payload["data"][0]
            if isinstance(first_row, dict):
                columns = [
                    key for key in first_row.keys()
                    if not str(key).startswith("_")
                ]

        schema_limit = max(MCP_TABLE_CONTEXT_MAX_SCHEMA_COLUMNS, MCP_TABLE_CONTEXT_MAX_COLUMNS)
        schema_columns = columns[:schema_limit]
        row_columns = columns[:MCP_TABLE_CONTEXT_MAX_COLUMNS]
        info_by_name = {}
        for item in column_info:
            if isinstance(item, dict):
                name = str(item.get("name") or "")
                if name:
                    info_by_name[name] = {
                        "name": name,
                        "title": item.get("title", ""),
                        "tooltip": item.get("tooltip", ""),
                    }

        rows = []
        for row in list(payload.get("data") or [])[:MCP_TABLE_CONTEXT_MAX_ROWS]:
            if isinstance(row, dict):
                row_index = row.get("_absolute_row_index")
                compact_row = {"row": row_index, "cells": {}}
                for column in row_columns:
                    compact_row["cells"][column] = self._shorten_mcp_cell(row.get(column))
                if row_index is None:
                    compact_row.pop("row", None)
                rows.append(compact_row)
            elif isinstance(row, list):
                rows.append({
                    "cells": [
                        self._shorten_mcp_cell(value)
                        for value in row[:MCP_TABLE_CONTEXT_MAX_COLUMNS]
                    ]
                })

        return {
            "table_id": payload.get("table_id") or candidate.get("id", ""),
            "source_element_type": candidate.get("type", ""),
            "table_type": payload.get("table_type", candidate.get("type", "")),
            "total_rows": payload.get("total_rows"),
            "rows_returned": payload.get("rows_returned", len(rows)),
            "start_row": payload.get("start_row", payload.get("first_visible_row", 0)),
            "visible_rows": payload.get("visible_rows"),
            "columns": schema_columns,
            "column_count": len(columns),
            "column_info": [info_by_name.get(column, {"name": column}) for column in schema_columns],
            "row_columns": row_columns,
            "rows": rows,
            "columns_only": bool(payload.get("columns_only")),
            "rows_truncated": len(payload.get("data") or []) > len(rows),
            "columns_truncated": len(columns) > len(schema_columns),
            "row_columns_truncated": len(columns) > len(row_columns),
        }

    def _compact_mcp_shell_result(self, payload, candidate):
        if not isinstance(payload, dict):
            return None
        if payload.get("error"):
            return {
                "shell_id": candidate.get("id", ""),
                "source_element_type": candidate.get("type", ""),
                "error": payload.get("error", ""),
            }
        text = str(payload.get("text") or "")
        html = str(payload.get("inner_html") or "")
        return {
            "shell_id": payload.get("shell_id") or candidate.get("id", ""),
            "source_element_type": candidate.get("type", ""),
            "type": payload.get("type", ""),
            "sub_type": payload.get("sub_type", ""),
            "url": payload.get("url", ""),
            "text": text[:MCP_SHELL_CONTEXT_MAX_CHARS],
            "text_truncated": len(text) > MCP_SHELL_CONTEXT_MAX_CHARS,
            "html_preview": html[:MCP_SHELL_CONTEXT_MAX_CHARS] if not text else "",
            "html_truncated": bool(html and len(html) > MCP_SHELL_CONTEXT_MAX_CHARS),
        }

    def _mcp_table_report_context_text(self, active_window, elements_tool, raw_elements):
        if not MCP_READ_TABLES_IN_CONTEXT and not MCP_READ_SHELLS_IN_CONTEXT:
            return ""

        read_table_tool = self._first_available_mcp_tool(["sap_read_table"])
        read_shell_tool = self._first_available_mcp_tool(["sap_read_shell_content"])
        inspect_table_tool = self._first_available_mcp_tool(["sap_inspect_tables"])
        if not MCP_READ_TABLES_IN_CONTEXT:
            read_table_tool = ""
            inspect_table_tool = ""
        if not MCP_READ_SHELLS_IN_CONTEXT:
            read_shell_tool = ""
        if not read_table_tool and not read_shell_tool and not inspect_table_tool:
            return ""

        elements = self._mcp_elements_from_text(raw_elements)
        table_candidates = self._mcp_table_candidates_from_elements(elements)

        # The normal fast element scan runs at depth 2. Nested ALV/report shells
        # are often deeper, so do one narrow, read-only, type-filtered discovery
        # pass when needed.
        if elements_tool and len(table_candidates) < MCP_TABLE_CONTEXT_MAX_TABLES:
            container_id = f"{active_window}/usr" if str(active_window).startswith("wnd[") else "wnd[0]/usr"
            try:
                raw_table_elements = self._call_mcp_tool_text(elements_tool, {
                    "container_id": container_id,
                    "max_depth": MCP_TABLE_CONTEXT_DISCOVERY_DEPTH,
                    "type_filter": MCP_TABLE_CONTEXT_TYPE_FILTER,
                    "changeable_only": False,
                })
                extra_elements = self._mcp_elements_from_text(raw_table_elements)
                seen = {item.get("id") for item in table_candidates}
                for item in self._mcp_table_candidates_from_elements(extra_elements):
                    if item.get("id") not in seen:
                        table_candidates.append(item)
                        seen.add(item.get("id"))
                        if len(table_candidates) >= MCP_TABLE_CONTEXT_MAX_CANDIDATES:
                            break
            except Exception as exc:
                table_candidates.append({
                    "id": "",
                    "type": "discovery_error",
                    "error": str(exc),
                })

        if (
            elements_tool
            and MCP_TABLE_CONTEXT_BROAD_DISCOVERY_ON_EMPTY
            and not [item for item in table_candidates if item.get("id")]
        ):
            container_id = f"{active_window}/usr" if str(active_window).startswith("wnd[") else "wnd[0]/usr"
            try:
                raw_broad_elements = self._call_mcp_tool_text(elements_tool, {
                    "container_id": container_id,
                    "max_depth": MCP_TABLE_CONTEXT_BROAD_DISCOVERY_DEPTH,
                    "type_filter": "",
                    "changeable_only": False,
                })
                broad_elements = self._mcp_elements_from_text(raw_broad_elements)
                seen = {item.get("id") for item in table_candidates}
                for item in self._mcp_table_candidates_from_elements(broad_elements):
                    if item.get("id") not in seen:
                        item["candidate_source"] = "broad_discovery"
                        table_candidates.append(item)
                        seen.add(item.get("id"))
                        if len(table_candidates) >= MCP_TABLE_CONTEXT_MAX_CANDIDATES:
                            break
            except Exception as exc:
                table_candidates.append({
                    "id": "",
                    "type": "broad_discovery_error",
                    "error": str(exc),
                })

        tables = []
        shells = []
        errors = []
        inspect_report = {}

        if (
            inspect_table_tool
            and MCP_TABLE_CONTEXT_INSPECT_ON_EMPTY
            and len([item for item in table_candidates if item.get("id")]) < MCP_TABLE_CONTEXT_MAX_TABLES
        ):
            container_id = f"{active_window}/usr" if str(active_window).startswith("wnd[") else "wnd[0]/usr"
            try:
                raw_inspect = self._call_mcp_tool_text(inspect_table_tool, {
                    "container_id": container_id,
                    "max_depth": MCP_TABLE_CONTEXT_BROAD_DISCOVERY_DEPTH,
                    "max_rows": MCP_TABLE_CONTEXT_MAX_ROWS,
                    "include_rows": MCP_TABLE_CONTEXT_INSPECT_INCLUDE_ROWS,
                    "use_focus": True,
                })
                parsed_inspect = self._parse_mcp_json_text(raw_inspect)
                if isinstance(parsed_inspect, dict):
                    inspect_report = parsed_inspect
                    seen = {item.get("id") for item in table_candidates}
                    for item in parsed_inspect.get("candidates") or []:
                        if not isinstance(item, dict):
                            continue
                        item_id = item.get("id", "")
                        if item_id and item_id not in seen:
                            item["candidate_source"] = item.get("candidate_source", "inspect_tables")
                            table_candidates.append(item)
                            seen.add(item_id)
                    for raw_table in parsed_inspect.get("tables") or []:
                        if not isinstance(raw_table, dict):
                            continue
                        candidate = {
                            "id": raw_table.get("table_id") or raw_table.get("id", ""),
                            "type": raw_table.get("table_type") or raw_table.get("source_element_type", ""),
                        }
                        compact = self._compact_mcp_table_result(raw_table, candidate)
                        if compact and compact.get("columns"):
                            compact["source"] = "sap_inspect_tables"
                            tables.append(compact)
                    for item in parsed_inspect.get("errors") or []:
                        if isinstance(item, dict):
                            item = dict(item)
                            item["tool"] = inspect_table_tool
                            errors.append(item)
                else:
                    errors.append({
                        "tool": inspect_table_tool,
                        "phase": "inspect_tables",
                        "error": "unexpected inspect response",
                    })
            except Exception as exc:
                errors.append({
                    "tool": inspect_table_tool,
                    "phase": "inspect_tables",
                    "error": str(exc),
                })

        loaded_table_ids = {
            table.get("table_id")
            for table in tables
            if table.get("table_id")
        }
        for candidate in table_candidates:
            if len(tables) >= MCP_TABLE_CONTEXT_MAX_TABLES:
                break
            table_id = candidate.get("id", "")
            if not table_id:
                if candidate.get("error"):
                    errors.append(candidate)
                continue
            if table_id in loaded_table_ids:
                continue

            table_read_ok = False
            if MCP_READ_TABLES_IN_CONTEXT and read_table_tool:
                schema_compact = None
                if MCP_TABLE_CONTEXT_SCHEMA_FIRST:
                    try:
                        raw_schema = self._call_mcp_tool_text(read_table_tool, {
                            "table_id": table_id,
                            "max_rows": 1,
                            "columns": "",
                            "columns_only": True,
                            "start_row": 0,
                        })
                        parsed_schema = self._parse_mcp_json_text(raw_schema)
                        schema_compact = self._compact_mcp_table_result(parsed_schema, candidate)
                        if isinstance(parsed_schema, dict) and parsed_schema.get("error"):
                            errors.append({
                                "table_id": table_id,
                                "type": candidate.get("type", ""),
                                "tool": read_table_tool,
                                "phase": "schema",
                                "error": str(parsed_schema.get("error") or ""),
                            })
                    except Exception as exc:
                        errors.append({
                            "table_id": table_id,
                            "type": candidate.get("type", ""),
                            "tool": read_table_tool,
                            "phase": "schema",
                            "error": str(exc),
                        })
                try:
                    raw_table = self._call_mcp_tool_text(read_table_tool, {
                        "table_id": table_id,
                        "max_rows": MCP_TABLE_CONTEXT_MAX_ROWS,
                        "columns": "",
                        "columns_only": False,
                        "start_row": 0,
                    })
                    parsed_table = self._parse_mcp_json_text(raw_table)
                    compact = self._compact_mcp_table_result(parsed_table, candidate)
                    if compact:
                        if schema_compact and not compact.get("columns"):
                            compact["columns"] = schema_compact.get("columns", [])
                            compact["column_info"] = schema_compact.get("column_info", [])
                            compact["column_count"] = schema_compact.get("column_count", 0)
                        tables.append(compact)
                        table_read_ok = True
                    elif isinstance(parsed_table, dict) and parsed_table.get("error"):
                        errors.append({
                            "table_id": table_id,
                            "type": candidate.get("type", ""),
                            "tool": read_table_tool,
                            "error": str(parsed_table.get("error") or ""),
                        })
                except Exception as exc:
                    errors.append({
                        "table_id": table_id,
                        "type": candidate.get("type", ""),
                        "tool": read_table_tool,
                        "phase": "rows",
                        "error": str(exc),
                    })
                if not table_read_ok and schema_compact and schema_compact.get("columns"):
                    tables.append(schema_compact)
                    table_read_ok = True

            if (
                not table_read_ok
                and MCP_READ_SHELLS_IN_CONTEXT
                and read_shell_tool
                and candidate.get("type") == "GuiShell"
            ):
                try:
                    raw_shell = self._call_mcp_tool_text(read_shell_tool, {"shell_id": table_id})
                    parsed_shell = self._parse_mcp_json_text(raw_shell)
                    compact_shell = self._compact_mcp_shell_result(parsed_shell, candidate)
                    if compact_shell and (
                        compact_shell.get("text")
                        or compact_shell.get("html_preview")
                        or compact_shell.get("url")
                        or compact_shell.get("error")
                    ):
                        shells.append(compact_shell)
                except Exception as exc:
                    errors.append({
                        "shell_id": table_id,
                        "type": candidate.get("type", ""),
                        "tool": read_shell_tool,
                        "error": str(exc),
                    })

        if not tables and not shells and not errors:
            return ""

        context = {
            "backend": "mcp_table_report_context",
            "read_tools": {
                "table": read_table_tool,
                "shell": read_shell_tool,
                "inspect": inspect_table_tool,
            },
            "inspect_focus": (inspect_report.get("focused_element") if isinstance(inspect_report, dict) else None),
            "candidate_count": len([item for item in table_candidates if item.get("id")]),
            "candidates": [
                {
                    "id": item.get("id", ""),
                    "type": item.get("type", ""),
                    "name": item.get("name", ""),
                    "text": item.get("text", ""),
                    "source": item.get("candidate_source", "filtered_discovery"),
                }
                for item in table_candidates
                if item.get("id")
            ][:MCP_TABLE_CONTEXT_MAX_CANDIDATES],
            "tables": tables,
            "shells": shells[:MCP_TABLE_CONTEXT_MAX_TABLES],
            "errors": errors[:8],
            "instruction_to_agent": (
                "Use tables[].columns and column_info for column/schema questions, even when rows is empty. "
                "tables[].columns may include more schema columns than each row; row_columns lists the subset included in rows[].cells. "
                "Use tables[].rows for SAP report/list/table data answers. "
                "For ALV reports, total_rows may exceed rows_returned; request pagination "
                "with sap_read_table(start_row=...) only if the user needs more rows."
            ),
        }
        return "### mcp_table_report_context\n" + json.dumps(context, ensure_ascii=False, indent=2)

    def _mcp_field_write_failed(self, tool_result):
        if tool_result.get("backend") != "mcp":
            return False
        if tool_result.get("action", "") not in MCP_FIELD_WRITE_TOOL_NAMES:
            return False

        payload = tool_result.get("mcp_payload")
        if not isinstance(payload, dict):
            return not tool_result.get("success", True)

        try:
            if int(payload.get("failed") or 0) > 0:
                return True
        except Exception:
            pass

        results = payload.get("results")
        if isinstance(results, dict):
            for status in results.values():
                text = str(status or "").lower()
                if text.startswith("error") or "could not set field" in text:
                    return True

        validation = payload.get("validation")
        if isinstance(validation, dict):
            reason = str(validation.get("reason", "") or "").lower()
            if "no fields were set" in reason:
                return True

        return False

    def _remember_mcp_field_writes(self, tool_result):
        if not tool_result.get("success", True):
            return
        action = tool_result.get("action", "")
        args = tool_result.get("_tool_args") or {}
        writes = {}

        if action in {"sap_set_batch_fields", "sap_set_fields_and_enter"}:
            fields = args.get("fields") if isinstance(args, dict) else None
            payload = tool_result.get("mcp_payload")
            result_statuses = payload.get("results", {}) if isinstance(payload, dict) else {}
            if isinstance(fields, dict):
                for field_id, value in fields.items():
                    status = result_statuses.get(field_id)
                    if status is None or status == "success":
                        writes[str(field_id)] = value
        elif action == "sap_set_field":
            field_id = (
                args.get("field_id")
                or args.get("element_id")
                or args.get("id")
            )
            value = args.get("value")
            if field_id is not None:
                writes[str(field_id)] = value
        elif action == "sap_select_checkbox":
            field_id = args.get("checkbox_id") or args.get("element_id") or args.get("id")
            selected = args.get("selected")
            if field_id is not None:
                writes[str(field_id)] = selected
        elif action in {"sap_select_radio_button", "sap_select_combobox_entry"}:
            field_id = (
                args.get("element_id")
                or args.get("field_id")
                or args.get("combobox_id")
                or args.get("radio_id")
                or args.get("id")
            )
            value = args.get("value", args.get("key", True))
            if field_id is not None:
                writes[str(field_id)] = value

        if not writes:
            return
        self._mcp_recent_field_writes.update(writes)
        if len(self._mcp_recent_field_writes) > MCP_SCREEN_CACHE_MAX_WRITES:
            items = list(self._mcp_recent_field_writes.items())[-MCP_SCREEN_CACHE_MAX_WRITES:]
            self._mcp_recent_field_writes = dict(items)

    def _mcp_recent_writes_text(self):
        if not self._mcp_recent_field_writes:
            return ""
        return json.dumps(
            self._mcp_recent_field_writes,
            ensure_ascii=False,
            indent=2,
        )

    def _detect_mcp_tool_hints(self, screen_text):
        lowered = str(screen_text or "").lower()
        hints = set()
        if any(marker in lowered for marker in ("guigridview", "alv", "grid_id")):
            hints.add("alv")
        if any(marker in lowered for marker in ("guitablecontrol", "table_id", "tablecontrol")):
            hints.add("table")
        if any(marker in lowered for marker in ("guitree", "simpletree", "columntree", "tree_id")):
            hints.add("tree")
        return hints

    def _filter_mcp_tool_schemas(self, tools):
        if MCP_SAP_TOOL_PROFILE == "full":
            filtered = list(tools)
            if not MCP_EXPOSE_DISCOVERY_TO_LLM:
                filtered = [
                    item for item in filtered
                    if item.get("function", {}).get("name", "") not in MCP_DISCOVERY_TOOL_NAMES
                ]
            return filtered

        allowed = set(MCP_CORE_TOOL_NAMES)
        for hint in self._mcp_tool_hints:
            allowed.update(MCP_DYNAMIC_TOOL_GROUPS.get(hint, set()))
        if not MCP_EXPOSE_DISCOVERY_TO_LLM:
            allowed.difference_update(MCP_DISCOVERY_TOOL_NAMES)

        filtered = [
            item for item in tools
            if item.get("function", {}).get("name", "") in allowed
        ]
        return filtered or ([] if not MCP_EXPOSE_DISCOVERY_TO_LLM else list(tools))

    def _get_mcp_tool_schemas(self, refresh=False, quiet=False):
        """Return dynamic MCP tools, or an empty list when fallback should be used."""
        if not self.mcp_client:
            self._mcp_unavailable_reason = "MCP_SAP_ENABLED=false"
            return []

        if (
            self._mcp_last_failure_at
            and not refresh
            and time.time() - self._mcp_last_failure_at < MCP_FAILURE_COOLDOWN_SECONDS
        ):
            if not quiet:
                print(
                    "\033[33m"
                    f"[Agent] MCP 暫停重試中，改用 legacy GUI fallback: {self._mcp_unavailable_reason}"
                    "\033[0m"
                )
            return []

        if self._mcp_all_tool_schemas is not None and not refresh:
            tools = self._filter_mcp_tool_schemas(self._mcp_all_tool_schemas)
            self._mcp_all_tool_names = {
                item.get("function", {}).get("name", "")
                for item in self._mcp_all_tool_schemas
                if item.get("function", {}).get("name")
            }
            self._mcp_tool_schemas = tools
            self._mcp_tool_names = {
                item.get("function", {}).get("name", "")
                for item in tools
                if item.get("function", {}).get("name")
            }
            return list(tools)

        try:
            started_at = time.perf_counter()
            all_tools = self.mcp_client.get_available_tools(refresh=refresh)
            self._timing_log("MCP list_tools", started_at)
            self._mcp_all_tool_schemas = all_tools
            self._mcp_all_tool_names = {
                item.get("function", {}).get("name", "")
                for item in all_tools
                if item.get("function", {}).get("name")
            }
            tools = self._filter_mcp_tool_schemas(all_tools)
            self._mcp_tool_schemas = tools
            self._mcp_tool_names = {
                item.get("function", {}).get("name", "")
                for item in tools
                if item.get("function", {}).get("name")
            }
            try:
                all_tool_names = [
                    item.get("function", {}).get("name", "")
                    for item in all_tools
                    if item.get("function", {}).get("name")
                ]
                self.mcp_client.ensure_sap_connected(all_tool_names)
            except Exception as attach_error:
                if not quiet:
                    print(f"\033[33m[Agent] MCP SAP session attach 警告: {attach_error}\033[0m")
            self._mcp_unavailable_reason = ""
            self._mcp_last_failure_at = 0.0
            if tools and not quiet:
                print(
                    "\033[90m"
                    f"[Agent] MCP tools loaded: {len(tools)} "
                    f"(profile={MCP_SAP_TOOL_PROFILE}, all={len(all_tools)})"
                    "\033[0m"
                )
            return list(tools)
        except (MCPClientUnavailable, TimeoutError, Exception) as e:
            self._mcp_all_tool_schemas = None
            self._mcp_all_tool_names = set()
            self._mcp_tool_schemas = None
            self._mcp_tool_names = set()
            self._mcp_unavailable_reason = str(e)
            self._mcp_last_failure_at = time.time()
            last_error = getattr(self.mcp_client, "last_error", "")
            detail = last_error or str(e)
            if not quiet:
                print(f"\033[33m[Agent] MCP 不可用，改用 legacy GUI fallback: {detail}\033[0m")
            return []

    def _tool_schemas_for_auto(self, screen_text=""):
        if screen_text:
            self._mcp_tool_hints = self._detect_mcp_tool_hints(screen_text)
        mcp_tools = self._get_mcp_tool_schemas()
        return mcp_tools or TOOL_SCHEMAS

    def _mcp_tool_available(self, name):
        if not self._mcp_tool_names:
            self._get_mcp_tool_schemas(quiet=True)
        return name in self._mcp_tool_names

    def _first_available_mcp_tool(self, candidates, include_hidden=True):
        if not self._mcp_all_tool_names and not self._mcp_tool_names:
            self._get_mcp_tool_schemas(quiet=True)
        available = self._mcp_all_tool_names if include_hidden else self._mcp_tool_names
        for name in candidates:
            if name in available:
                return name
        return ""

    def _call_mcp_tool_text(self, tool_name, arguments=None):
        if not self.mcp_client:
            raise MCPClientUnavailable("MCP_SAP_ENABLED=false")
        try:
            started_at = time.perf_counter()
            result = self.mcp_client.call_tool(tool_name, arguments or {})
            self._timing_log(f"MCP call {tool_name}", started_at)
            return result
        except Exception as e:
            self._mcp_unavailable_reason = str(e)
            self._mcp_last_failure_at = time.time()
            raise

    def _hidden_discovery_tool_result(self, tool_name):
        screen_after = {
            "backend": "mcp_internal_context",
            "message": (
                "Screen discovery tools are managed internally. "
                "Use the provided screen context and action tool results instead of rescanning."
            ),
        }
        if self._mcp_last_screen_info:
            screen_after["screen"] = self._mcp_last_screen_info
        return {
            "success": True,
            "backend": "mcp_internal_context",
            "action": tool_name,
            "message": "Discovery tool call skipped to avoid a full screen rescan.",
            "screen_after": screen_after,
            "screen_summary": screen_after,
        }

    def _mcp_fast_screen_context_text(self, purpose="auto"):
        parts = []
        snapshot_tool = self._first_available_mcp_tool(["sap_get_light_snapshot"])
        screen_info_tool = self._first_available_mcp_tool([
            "sap_get_screen_info",
            *MCP_SESSION_INFO_TOOL_CANDIDATES,
        ])
        elements_tool = self._first_available_mcp_tool([
            "sap_get_screen_elements",
            *MCP_SCREEN_TOOL_CANDIDATES,
        ])
        popup_tool = self._first_available_mcp_tool(["sap_get_popup_window"])

        screen_info = None
        active_window = "wnd[0]"
        fingerprint = ""
        screen_events = []
        popup_already_read = False
        raw_elements_for_tables = ""
        evidence_mode = purpose in ("ask", "solve", "study")
        element_scan_depth = (
            max(MCP_FAST_SCREEN_MAX_DEPTH, MCP_EVIDENCE_SCREEN_MAX_DEPTH)
            if evidence_mode
            else MCP_FAST_SCREEN_MAX_DEPTH
        )
        element_type_filter = (
            MCP_EVIDENCE_SCREEN_TYPE_FILTER
            if evidence_mode
            else MCP_FAST_SCREEN_TYPE_FILTER
        )
        if snapshot_tool:
            raw_snapshot = self._call_mcp_tool_text(snapshot_tool, {})
            parts.append(f"### {snapshot_tool}\n{raw_snapshot}")
            snapshot = self._parse_mcp_json_text(raw_snapshot)
            if isinstance(snapshot, dict):
                if isinstance(snapshot.get("screen"), dict):
                    screen_info = snapshot["screen"]
                else:
                    screen_info = snapshot
                active_window = str(
                    snapshot.get("active_window")
                    or screen_info.get("active_window")
                    or "wnd[0]"
                )
                screen_events = self._mcp_screen_events_from_info(
                    self._mcp_last_screen_info,
                    screen_info,
                )
                self._mcp_last_screen_events = screen_events
                remembered_fingerprint = self._remember_mcp_screen_info(screen_info)
                fingerprint = str(snapshot.get("fingerprint") or "") or remembered_fingerprint
                if snapshot.get("popup"):
                    popup_already_read = True
        elif screen_info_tool:
            raw_info = self._call_mcp_tool_text(screen_info_tool, {})
            parts.append(f"### {screen_info_tool}\n{raw_info}")
            screen_info = self._parse_mcp_json_text(raw_info)
            if isinstance(screen_info, dict):
                screen_events = self._mcp_screen_events_from_info(
                    self._mcp_last_screen_info,
                    screen_info,
                )
                self._mcp_last_screen_events = screen_events
                fingerprint = self._remember_mcp_screen_info(screen_info)
                active_window = str(screen_info.get("active_window") or "wnd[0]")

        if screen_events:
            parts.append(
                "### mcp_screen_events\n"
                f"{json.dumps(screen_events, ensure_ascii=False, indent=2)}"
            )

        skip_elements = False
        if popup_tool and active_window != "wnd[0]" and not popup_already_read:
            raw_popup = self._call_mcp_tool_text(popup_tool, {})
            parts.append(f"### {popup_tool}\n{raw_popup}")
            skip_elements = MCP_POPUP_USE_POPUP_TOOL_ONLY
        elif active_window != "wnd[0]" and popup_already_read:
            skip_elements = MCP_POPUP_USE_POPUP_TOOL_ONLY

        if elements_tool and not skip_elements:
            container_id = f"{active_window}/usr" if active_window.startswith("wnd[") else "wnd[0]/usr"
            if self._mcp_cached_elements_valid(
                fingerprint,
                container_id,
                min_depth=element_scan_depth,
                type_filter=element_type_filter,
            ):
                raw_elements = self._mcp_screen_cache.get("elements_text", "")
                raw_elements_for_tables = raw_elements
                parts.append(f"### {elements_tool} fast filtered (cache hit)\n{raw_elements}")
            else:
                try:
                    raw_elements = self._call_mcp_tool_text(elements_tool, {
                        "container_id": container_id,
                        "max_depth": element_scan_depth,
                        "type_filter": element_type_filter,
                        "changeable_only": MCP_FAST_SCREEN_CHANGEABLE_ONLY,
                    })
                    self._remember_mcp_elements(
                        fingerprint,
                        container_id,
                        raw_elements,
                        max_depth=element_scan_depth,
                        type_filter=element_type_filter,
                    )
                    raw_elements_for_tables = raw_elements
                    parts.append(f"### {elements_tool} fast filtered\n{raw_elements}")
                except Exception:
                    cache = self._mcp_screen_cache
                    if cache.get("fingerprint") == fingerprint and cache.get("container_id") == container_id:
                        raw_elements = cache.get("elements_text", "")
                        raw_elements_for_tables = raw_elements
                        parts.append(f"### {elements_tool} fast filtered (stale cache fallback)\n{raw_elements}")
                    else:
                        raise

        table_context = self._mcp_table_report_context_text(
            active_window=active_window,
            elements_tool=elements_tool,
            raw_elements=raw_elements_for_tables,
        )
        if table_context:
            parts.append(table_context)

        if purpose in ("ask", "solve", "study"):
            quality = {
                "purpose": purpose,
                "element_scan_depth": element_scan_depth,
                "element_count": len(self._mcp_elements_from_text(raw_elements_for_tables)),
                "has_table_report_context": bool(table_context),
                "usable_evidence": self._mcp_text_has_useful_screen_evidence("\n\n".join(parts)),
            }
            parts.append(
                "### mcp_context_quality\n"
                f"{json.dumps(quality, ensure_ascii=False, indent=2)}"
            )

        recent_writes = self._mcp_recent_writes_text()
        if recent_writes:
            parts.append(
                "### mcp_local_field_write_cache\n"
                "The screen elements above may contain older values for fields changed locally in this run.\n"
                f"{recent_writes}"
            )

        return "\n\n".join(parts) if parts else None

    def _mcp_screen_context_text(self, purpose="auto"):
        """Get screen context from MCP as raw text for LLM consumption."""
        if not self.mcp_client:
            return None

        tools = self._get_mcp_tool_schemas(quiet=True)
        if not tools:
            return None

        parts = []
        session_tool = self._first_available_mcp_tool(MCP_SESSION_INFO_TOOL_CANDIDATES)
        screen_tool = self._first_available_mcp_tool(MCP_SCREEN_TOOL_CANDIDATES)

        try:
            started_at = time.perf_counter()
            if MCP_SAP_FAST_MODE:
                fast_text = self._mcp_fast_screen_context_text(purpose=purpose)
                if fast_text:
                    self._last_screen_context_text = fast_text
                    self._last_screen_backend = "mcp"
                    self._mcp_tool_hints = self._detect_mcp_tool_hints(fast_text)
                    self._timing_log("MCP fast screen scan", started_at)
                    return fast_text

            if session_tool:
                parts.append(
                    f"### {session_tool}\n"
                    f"{self._call_mcp_tool_text(session_tool, {})}"
                )
            if screen_tool:
                parts.append(
                    f"### {screen_tool}\n"
                    f"{self._call_mcp_tool_text(screen_tool, {})}"
                )
        except Exception as e:
            self._mcp_unavailable_reason = str(e)
            self._mcp_last_failure_at = time.time()
            print(f"\033[33m[Agent] MCP 畫面掃描失敗，改用 legacy GUI fallback: {e}\033[0m")
            return None

        if not parts:
            self._mcp_unavailable_reason = "MCP server did not expose a supported screen tool"
            return None

        text = "\n\n".join(parts)
        self._last_screen_context_text = text
        self._last_screen_backend = "mcp"
        self._mcp_tool_hints = self._detect_mcp_tool_hints(text)
        return text

    def _local_screen_context_text(self, session, purpose="auto"):
        screen_state = scan_sap_screen(session)
        if purpose in ("ask", "solve"):
            screen_state = self._attach_editor_text_context(session, screen_state)
        context = self._solve_context(screen_state) if purpose == "solve" else self._screen_context(screen_state)
        return json.dumps(context, ensure_ascii=False, indent=2), screen_state

    def _screen_context_text(self, session, purpose="auto"):
        """
        Prefer MCP optimized screen output; fallback to legacy scanner.

        Returns:
            tuple[str, str] -> (context_text, backend)
        """
        started_at = time.perf_counter()
        mcp_text = self._mcp_screen_context_text(purpose=purpose)
        if mcp_text:
            if (
                purpose in ("ask", "solve", "study")
                and MCP_EVIDENCE_LEGACY_FALLBACK_ON_EMPTY
                and not self._mcp_text_has_useful_screen_evidence(mcp_text)
            ):
                try:
                    local_text, _screen_state = self._local_screen_context_text(session, purpose=purpose)
                    mcp_text = (
                        f"{mcp_text}\n\n"
                        "### legacy_screen_summary_fallback\n"
                        f"{local_text}"
                    )
                except Exception as exc:
                    mcp_text = (
                        f"{mcp_text}\n\n"
                        "### legacy_screen_summary_fallback_error\n"
                        f"{exc}"
                    )
            self._last_screen_context_text = mcp_text
            self._last_screen_backend = "mcp"
            self._timing_log(f"screen context ({purpose}, mcp)", started_at)
            return mcp_text, "mcp"

        local_text, _screen_state = self._local_screen_context_text(session, purpose=purpose)
        self._timing_log(f"screen context ({purpose}, legacy)", started_at)
        return local_text, "legacy"

    def _legacy_tool_name_for_mcp(self, tool_name):
        aliases = {
            "sap_set_text": "set_text",
            "sap_set_field": "set_text",
            "sap_enter_text": "set_text",
            "sap_select_combo": "select_combo",
            "sap_select_dropdown": "select_combo",
            "sap_click": "click",
            "sap_press_button": "click",
            "sap_send_vkey": "send_vkey",
            "sap_send_key": "send_vkey",
            "sap_set_tcode": "set_tcode",
            "sap_start_transaction": "set_tcode",
            "sap_handle_popup": "handle_popup",
            "sap_read_checkbox": "read_checkbox",
            "sap_set_checkbox": "set_checkbox",
            "sap_select_table_row": "select_table_row",
            "sap_get_screen_elements": "scan_sap_screen",
            "sap_get_screen": "scan_sap_screen",
            "sap_scan_screen": "scan_sap_screen",
            "sap_read_editor_text": "read_editor_text",
            "sap_set_editor_text": "set_editor_text",
            "sap_visualize_element": "visualize_element",
            "sap_set_focus": "visualize_element",
        }
        return aliases.get(tool_name, tool_name)

    def _normalize_legacy_tool_args(self, legacy_tool_name, args):
        normalized = dict(args or {})

        if "id" in normalized and "element_id" not in normalized:
            normalized["element_id"] = normalized.pop("id")
        if "element" in normalized and "element_id" not in normalized:
            normalized["element_id"] = normalized.pop("element")
        if "field_id" in normalized and "element_id" not in normalized:
            normalized["element_id"] = normalized.pop("field_id")

        if legacy_tool_name == "set_text":
            if "text" in normalized and "value" not in normalized:
                normalized["value"] = normalized.pop("text")
        elif legacy_tool_name == "select_combo":
            if "text" in normalized and "value" not in normalized and "key" not in normalized:
                normalized["value"] = normalized["text"]
        elif legacy_tool_name == "send_vkey":
            if "key" in normalized and "vkey" not in normalized:
                normalized["vkey"] = normalized.pop("key")
        elif legacy_tool_name == "set_tcode":
            if "transaction" in normalized and "tcode" not in normalized:
                normalized["tcode"] = normalized.pop("transaction")
            if "transaction_code" in normalized and "tcode" not in normalized:
                normalized["tcode"] = normalized.pop("transaction_code")
        elif legacy_tool_name == "set_checkbox":
            if "checked" in normalized and "selected" not in normalized:
                normalized["selected"] = normalized.pop("checked")

        return normalized

    def _execute_tool_call(self, session, tool_name, tool_args):
        """
        執行 LLM 回傳的 Tool Call。

        Args:
            session: SAP Session COM 物件
            tool_name: 工具名稱
            tool_args: 工具參數 dict

        Returns:
            dict: 工具執行結果
        """
        if tool_name not in TOOL_FUNCTIONS:
            return {"success": False, "error": f"未知的工具: {tool_name}"}

        tool_func = TOOL_FUNCTIONS[tool_name]

        # 呼叫工具函數（所有工具的第一個參數都是 session）
        return tool_func(session, **tool_args)

    def _execute_primary_tool_call(self, session, tool_name, tool_args):
        """Execute MCP tool first; fallback to legacy GUI tool when possible."""
        if (
            tool_name in MCP_DISCOVERY_TOOL_NAMES
            and not MCP_EXPOSE_DISCOVERY_TO_LLM
            and self.mcp_client
        ):
            return self._hidden_discovery_tool_result(tool_name)

        if self._mcp_tool_available(tool_name):
            try:
                result_text = self._call_mcp_tool_text(tool_name, tool_args)
                parsed = self._parse_mcp_json_text(result_text)
                success = not str(result_text).startswith("MCP tool error")
                if isinstance(parsed, dict) and parsed.get("error"):
                    success = False
                result = {
                    "success": success,
                    "backend": "mcp",
                    "action": tool_name,
                    "_tool_args": tool_args,
                    "result": result_text,
                }
                if parsed is not None:
                    result["mcp_payload"] = parsed
                    screen = self._extract_screen_from_mcp_payload(parsed)
                    if screen:
                        result["mcp_screen"] = screen
                return result
            except Exception as e:
                legacy_name = self._legacy_tool_name_for_mcp(tool_name)
                if legacy_name in TOOL_FUNCTIONS:
                    print(
                        "\033[33m"
                        f"[Agent] MCP tool {tool_name} 失敗，改用 legacy fallback {legacy_name}: {e}"
                        "\033[0m"
                    )
                    legacy_args = self._normalize_legacy_tool_args(legacy_name, tool_args)
                    result = self._execute_tool_call(session, legacy_name, legacy_args)
                    result["backend"] = "legacy_fallback"
                    result["mcp_error"] = str(e)
                    result["_legacy_tool_name"] = legacy_name
                    result["_legacy_tool_args"] = legacy_args
                    return result

                return {
                    "success": False,
                    "backend": "mcp",
                    "action": tool_name,
                    "error": f"MCP tool failed and no legacy fallback is available: {e}",
                }

        legacy_name = self._legacy_tool_name_for_mcp(tool_name)
        legacy_args = self._normalize_legacy_tool_args(legacy_name, tool_args)
        result = self._execute_tool_call(session, legacy_name, legacy_args)
        result["backend"] = "legacy"
        result["_legacy_tool_name"] = legacy_name
        result["_legacy_tool_args"] = legacy_args
        return result

    def _execute_study_tool_call(self, session, tool_name, tool_args):
        """
        Execute local Study tools.

        For guide_user_action, try MCP focus first when available, then use the
        existing local guide/visualize implementation as fallback and prompt UI.
        """
        if tool_name == "guide_user_action":
            element_id = str((tool_args or {}).get("element_id", "") or "")
            focus_tool = self._first_available_mcp_tool(MCP_SET_FOCUS_TOOL_CANDIDATES)
            if element_id and focus_tool:
                try:
                    self._call_mcp_tool_text(focus_tool, {"element_id": element_id})
                except Exception:
                    try:
                        self._call_mcp_tool_text(focus_tool, {"id": element_id})
                    except Exception as e:
                        print(f"\033[33m[Agent] MCP focus 失敗，改用 legacy guide fallback: {e}\033[0m")

        result = self._execute_tool_call(session, tool_name, tool_args)
        result["backend"] = "local_study"
        return result

    def _screen_summary(self, screen_state):
        """產生給終端機看的簡短畫面摘要。"""
        active_popup = screen_state.get("active_popup") or {}
        return {
            "tcode": screen_state.get("tcode", ""),
            "title": screen_state.get("title", ""),
            "screen_number": screen_state.get("screen_number", ""),
            "active_window": screen_state.get("active_window", ""),
            "focused_element": screen_state.get("focused_element"),
            "status_bar": screen_state.get("status_bar", {}),
            "fields": self._compact_fields(screen_state.get("fields", [])),
            "tables": self._compact_tables(screen_state.get("tables", [])),
            "messages": screen_state.get("messages", [])[:10],
            "editors": screen_state.get("editors", [])[:10],
            "editor_sources": screen_state.get("editor_sources", []),
            "active_popup": {
                "id": active_popup.get("id", ""),
                "title": active_popup.get("title", ""),
                "actions": active_popup.get("actions", []),
                "messages": active_popup.get("messages", [])[:10],
                "fields": self._compact_fields(active_popup.get("fields", [])),
                "tables": self._compact_tables(active_popup.get("tables", [])),
                "editors": active_popup.get("editors", [])[:10],
                "focused_element": active_popup.get("focused_element"),
                "element_count": len(active_popup.get("elements", [])),
            } if active_popup else None,
            "element_count": len(screen_state.get("elements", [])),
        }

    def _solve_context(self, screen_state):
        """Solve Mode context keeps diagnostic evidence before giving advice."""
        context = self._screen_summary(screen_state)
        context["solve_diagnostics"] = self._build_solve_diagnostics(screen_state)
        return context

    def _build_solve_diagnostics(self, screen_state):
        active_popup = screen_state.get("active_popup") or {}
        status_bar = screen_state.get("status_bar") or {}
        focused_element = active_popup.get("focused_element") or screen_state.get("focused_element")

        messages = []
        seen_messages = set()

        def add_message(source, item):
            if not item:
                return
            if isinstance(item, str):
                text = item.strip()
                msg = {"source": source, "text": text}
            else:
                text = str(item.get("text") or item.get("message") or "").strip()
                msg = {
                    "source": source,
                    "text": text,
                    "id": item.get("id", ""),
                    "type": item.get("type", ""),
                }
                if item.get("position"):
                    msg["position"] = item.get("position")
            if not text:
                return
            key = (source, text)
            if key in seen_messages:
                return
            seen_messages.add(key)
            messages.append(msg)

        if status_bar.get("text"):
            messages.append({
                "source": "status_bar",
                "text": status_bar.get("text", ""),
                "severity": status_bar.get("type", ""),
            })
            seen_messages.add(("status_bar", status_bar.get("text", "")))

        for item in screen_state.get("messages", []):
            add_message("main_window", item)
        for item in active_popup.get("messages", []):
            add_message("active_popup", item)

        table_evidence = []

        def add_tables(source, tables):
            for table in tables[:6]:
                rows = []
                for row in table.get("rows", [])[:80]:
                    cells = row.get("cells", [])[:12]
                    text = str(row.get("text") or "").strip()
                    if not text:
                        text = " | ".join(
                            str(cell.get("text", "")).strip()
                            for cell in cells
                            if str(cell.get("text", "")).strip()
                        )
                    if not text and not cells:
                        continue
                    rows.append({
                        "row": row.get("row"),
                        "text": text,
                        "cells": cells,
                    })
                if rows:
                    table_evidence.append({
                        "source": source,
                        "id": table.get("id", ""),
                        "row_count": len(table.get("rows", [])),
                        "rows": rows,
                    })

        add_tables("main_window", screen_state.get("tables", []))
        add_tables("active_popup", active_popup.get("tables", []))

        fields_need_attention = []
        focus_id = focused_element.get("id") if isinstance(focused_element, dict) else ""

        def truthy(value):
            if isinstance(value, bool):
                return value
            if isinstance(value, str):
                return value.strip().lower() in {"true", "x", "1", "yes", "y"}
            return bool(value)

        def add_fields(source, fields):
            for field in fields[:80]:
                value = str(field.get("value", "") or "")
                field_id = field.get("id", "")
                is_required = truthy(field.get("required"))
                is_focused = bool(focus_id and field_id == focus_id)
                is_empty_input = (
                    truthy(field.get("changeable"))
                    and not value.strip()
                    and any(kind in str(field.get("type", "")) for kind in ("TextField", "CTextField", "ComboBox"))
                )
                if not (is_required or is_focused or is_empty_input):
                    continue
                fields_need_attention.append({
                    "source": source,
                    "id": field_id,
                    "label": field.get("label") or field.get("name") or field.get("text") or "",
                    "type": field.get("type", ""),
                    "value": value,
                    "required": is_required,
                    "focused": is_focused,
                    "changeable": truthy(field.get("changeable")),
                })

        add_fields("main_window", screen_state.get("fields", []))
        add_fields("active_popup", active_popup.get("fields", []))

        return {
            "evidence_first": True,
            "status_bar": status_bar,
            "active_popup_title": active_popup.get("title", ""),
            "message_count": len(messages),
            "messages": messages[:80],
            "tables": table_evidence,
            "fields_need_attention": fields_need_attention[:40],
            "focused_element": focused_element,
            "instruction": (
                "先根據 messages、tables.rows 與 status_bar 的實際文字判斷；"
                "若這裡沒有錯誤文字，不要猜測修法，請要求使用者先打開或展開錯誤清單。"
            ),
        }

    def _compact_fields(self, fields, max_fields=20, max_options=12):
        compacted = []
        for field in fields[:max_fields]:
            item = dict(field)
            if "options" in item:
                item["options"] = item.get("options", [])[:max_options]
                item["options_truncated"] = len(field.get("options", [])) > max_options
            compacted.append(item)
        return compacted

    def _compact_tables(self, tables, max_tables=4, max_rows=24, max_cells=8):
        compacted = []
        for table in tables[:max_tables]:
            columns = list(table.get("columns", []) or [])
            schema_limit = max(MCP_TABLE_CONTEXT_MAX_SCHEMA_COLUMNS, MCP_TABLE_CONTEXT_MAX_COLUMNS)
            schema_columns = columns[:schema_limit]
            row_columns = columns[:MCP_TABLE_CONTEXT_MAX_COLUMNS]
            item = {
                "id": table.get("id", ""),
                "table_type": table.get("table_type", ""),
                "columns": schema_columns,
                "column_count": table.get("column_count", len(columns)),
                "total_rows": table.get("total_rows"),
                "visible_rows": table.get("visible_rows"),
                "columns_only": bool(table.get("columns_only")),
                "row_columns": row_columns,
                "rows": [],
            }
            if table.get("column_info"):
                item["column_info"] = list(table.get("column_info") or [])[:schema_limit]
            rows = table.get("rows", [])
            for row in rows[:max_rows]:
                row_item = {
                    "row": row.get("row"),
                    "text": row.get("text", ""),
                    "cells": row.get("cells", [])[:max_cells],
                }
                item["rows"].append(row_item)
            item["rows_truncated"] = len(rows) > max_rows
            item["columns_truncated"] = len(columns) > len(item["columns"])
            item["row_columns_truncated"] = len(columns) > len(row_columns)
            compacted.append(item)
        return compacted

    def _screen_context(self, screen_state):
        """給 LLM 的畫面上下文，保留可操作摘要，避免傳完整 SAP DOM。"""
        return self._screen_summary(screen_state)

    def _attach_editor_text_context(self, session, screen_state):
        """Ask Mode 讀取目前 editor 內容，讓 LLM 能解釋程式用途。"""
        editors = list(screen_state.get("editors", []))
        active_popup = screen_state.get("active_popup") or {}
        editors.extend(active_popup.get("editors", []))
        if not editors:
            return screen_state

        valid_sources = []
        rejected_sources = []
        seen_ids = set()
        for editor in editors[:8]:
            element_id = editor.get("id", "")
            if element_id in seen_ids:
                continue
            seen_ids.add(element_id)
            read_result = read_editor_text(session, element_id=element_id)
            item = {
                "element_id": element_id,
                "success": bool(read_result.get("success")),
                "method": read_result.get("method", ""),
                "line_count": read_result.get("line_count", 0),
                "char_count": read_result.get("char_count", 0),
                "looks_like_source": bool(read_result.get("looks_like_source")),
            }
            if read_result.get("success"):
                text = str(read_result.get("text", ""))
                if read_result.get("looks_like_source"):
                    item["text"] = text[:EDITOR_CONTEXT_MAX_CHARS]
                    item["truncated"] = len(text) > EDITOR_CONTEXT_MAX_CHARS
                else:
                    item["success"] = False
                    item["error"] = read_result.get("warning") or "read text does not look like ABAP source"
                    item["preview"] = text[:500]
            else:
                item["error"] = read_result.get("error", "")
            if item.get("success"):
                valid_sources.append(item)
                if len(valid_sources) >= 3:
                    break
            else:
                rejected_sources.append(item)

        editor_sources = valid_sources or rejected_sources[:3]
        if editor_sources:
            enriched = dict(screen_state)
            enriched["editor_sources"] = editor_sources
            return enriched
        return screen_state

    def _study_post_tool_screen(self, session) -> dict:
        """Study Mode 專用：工具執行後的輕量畫面快照。

        只取 tcode / title / screen_number / status_bar，出現錯誤時才附帶
        可輸入欄位清單（最多 10 筆），避免完整掃描導致 context 膨脹。
        """
        # MCP 路徑：用 session info 工具（速度最快，不掃 elements）
        session_tool = self._first_available_mcp_tool(
            ["sap_get_screen_info", *MCP_SESSION_INFO_TOOL_CANDIDATES]
        )
        if session_tool and self.mcp_client:
            try:
                raw = self._call_mcp_tool_text(session_tool, {})
                parsed = self._parse_mcp_json_text(raw)
                if isinstance(parsed, dict):
                    status = parsed.get("status_bar") or parsed.get("statusbar") or {}
                    has_error = str(status.get("type", "")).upper() in ("E", "W", "A")
                    result: dict = {
                        "tcode": parsed.get("tcode", ""),
                        "title": parsed.get("title", ""),
                        "screen_number": parsed.get("screen_number", ""),
                        "status_bar": status,
                        "backend": "mcp_session_info",
                    }
                    if has_error:
                        result["fields"] = [
                            f for f in parsed.get("fields", [])
                            if f.get("changeable") or f.get("required")
                        ][:10]
                    return result
            except Exception:
                pass

        # Legacy 退路：完整掃一次，但只保留最小欄位
        try:
            screen = scan_sap_screen(session)
            status = screen.get("status_bar", {})
            has_error = str(status.get("type", "")).upper() in ("E", "W", "A")
            result = {
                "tcode": screen.get("tcode", ""),
                "title": screen.get("title", ""),
                "screen_number": screen.get("screen_number", ""),
                "status_bar": status,
                "backend": "legacy_minimal",
            }
            if has_error:
                result["fields"] = [
                    f for f in screen.get("fields", [])
                    if f.get("changeable") or f.get("required")
                ][:10]
            return result
        except Exception as exc:
            return {"backend": "scan_failed", "error": str(exc)}

    def _attach_screen_after_tool(self, session, tool_result):
        """工具執行後重新掃描 SAP，讓下一輪推理看到最新畫面。"""
        action = tool_result.get("action", "")
        if tool_result.get("backend") == "mcp" and MCP_SAP_FAST_MODE:
            if tool_result.get("mcp_screen"):
                if action in MCP_FIELD_WRITE_TOOL_NAMES:
                    self._remember_mcp_field_writes(tool_result)
                screen_events = self._mcp_screen_events_from_info(
                    self._mcp_last_screen_info,
                    tool_result["mcp_screen"],
                )
                self._mcp_last_screen_events = screen_events
                self._remember_mcp_screen_info(tool_result["mcp_screen"])
                screen_after = {
                    "backend": "mcp_result",
                    "screen": tool_result["mcp_screen"],
                }
                if screen_events:
                    screen_after["events"] = screen_events
                if (
                    (action in MCP_NAVIGATION_TOOL_NAMES or screen_events)
                    and MCP_ATTACH_ELEMENTS_AFTER_NAV
                ):
                    elements_context = self._mcp_filtered_elements_for_screen(
                        tool_result["mcp_screen"],
                        "screen_changed" if screen_events else "navigation_result",
                    )
                    if elements_context:
                        screen_after["screen_elements"] = elements_context
                if self._mcp_field_write_failed(tool_result) and MCP_ATTACH_ELEMENTS_ON_FIELD_FAILURE:
                    recovery_context = self._mcp_filtered_elements_for_screen(
                        tool_result["mcp_screen"],
                        "field_write_failed",
                        force=True,
                    )
                    if recovery_context:
                        screen_after["field_write_recovery"] = recovery_context
                tool_result["screen_after"] = screen_after
                tool_result["screen_summary"] = screen_after
                return tool_result

            if action in MCP_FIELD_WRITE_TOOL_NAMES:
                self._remember_mcp_field_writes(tool_result)
                screen_after = {
                    "backend": "mcp_fast_cached",
                    "message": "Screen was not rescanned after a non-navigation write.",
                    "action": action,
                    "arguments": {
                        key: value
                        for key, value in (tool_result.get("_tool_args") or {}).items()
                        if key not in {"password", "BCODE"}
                    },
                }
                if self._mcp_field_write_failed(tool_result) and MCP_ATTACH_ELEMENTS_ON_FIELD_FAILURE:
                    recovery_context = self._mcp_filtered_elements_for_screen(
                        self._mcp_last_screen_info,
                        "field_write_failed",
                        force=True,
                    )
                    if recovery_context:
                        screen_after["field_write_recovery"] = recovery_context
                tool_result["screen_after"] = screen_after
                tool_result["screen_summary"] = screen_after
                return tool_result

            if action in MCP_NAVIGATION_TOOL_NAMES:
                screen_tool = self._first_available_mcp_tool(["sap_get_screen_info"])
                if screen_tool:
                    try:
                        started_at = time.perf_counter()
                        screen_text = self._call_mcp_tool_text(screen_tool, {})
                        self._timing_log("MCP smart screen_after", started_at)
                        screen_after = {
                            "backend": "mcp_screen_info",
                            "raw": screen_text,
                        }
                        parsed = self._parse_mcp_json_text(screen_text)
                        if parsed is not None:
                            if isinstance(parsed, dict):
                                screen_events = self._mcp_screen_events_from_info(
                                    self._mcp_last_screen_info,
                                    parsed,
                                )
                                self._mcp_last_screen_events = screen_events
                                self._remember_mcp_screen_info(parsed)
                                if screen_events:
                                    screen_after["events"] = screen_events
                            screen_after["screen"] = parsed
                        tool_result["screen_after"] = screen_after
                        tool_result["screen_summary"] = screen_after
                        return tool_result
                    except Exception as e:
                        tool_result["screen_after_error"] = f"MCP 輕量掃描失敗: {e}"
                        return tool_result

            tool_result["screen_after"] = {
                "backend": "mcp_fast_skipped",
                "message": "Screen scan skipped for this MCP tool result.",
            }
            tool_result["screen_summary"] = tool_result["screen_after"]
            return tool_result

        if tool_result.get("backend") == "mcp":
            mcp_text = self._mcp_screen_context_text()
            if mcp_text:
                tool_result["screen_after"] = {
                    "backend": "mcp",
                    "raw": mcp_text,
                }
                tool_result["screen_summary"] = tool_result["screen_after"]
                return tool_result

        try:
            started_at = time.perf_counter()
            screen_state = scan_sap_screen(session)
            self._timing_log("legacy screen_after scan", started_at)
            screen_summary = self._screen_summary(screen_state)
            tool_result["screen_after"] = screen_summary
            tool_result["screen_summary"] = screen_summary
        except Exception as e:
            tool_result["screen_after_error"] = f"工具執行後重新掃描失敗: {e}"
        return tool_result

    def _trim_conversation_history(self):
        """跨使用者請求時裁切歷史，丟掉舊 tool trace，避免 payload 不斷膨脹。"""
        self._repair_tool_call_history()

        if len(self.conversation_history) <= CONVERSATION_HISTORY_LIMIT:
            return

        system_messages = [
            msg for msg in self.conversation_history
            if msg.get("role") == "system"
        ][:1]

        compactable_messages = [
            msg for msg in self.conversation_history
            if (
                msg.get("role") != "system"
                and msg.get("role") != "tool"
                and not msg.get("tool_calls")
            )
        ]
        recent_messages = [
            msg for msg in compactable_messages
        ][-(CONVERSATION_HISTORY_LIMIT - len(system_messages)):]
        self.conversation_history = system_messages + recent_messages

    def _repair_tool_call_history(self):
        """
        Remove invalid dangling tool-call messages before sending history to the API.

        Chat Completions requires every assistant message containing tool_calls to be
        followed immediately by tool messages for every tool_call_id. If a previous
        run was interrupted between the assistant tool call and tool result append,
        the next API request will fail with invalid_request_body unless we repair it.
        """
        repaired = []
        messages = self.conversation_history
        i = 0
        repaired_any = False

        while i < len(messages):
            message = messages[i]
            role = message.get("role")

            if role == "assistant" and message.get("tool_calls"):
                expected_ids = [
                    tool_call.get("id")
                    for tool_call in message.get("tool_calls", [])
                    if tool_call.get("id")
                ]
                expected = set(expected_ids)
                tool_messages = []
                seen = set()
                j = i + 1

                while j < len(messages) and messages[j].get("role") == "tool":
                    tool_message = messages[j]
                    tool_call_id = tool_message.get("tool_call_id")
                    if tool_call_id in expected:
                        tool_messages.append(tool_message)
                        seen.add(tool_call_id)
                    else:
                        repaired_any = True
                    j += 1

                if expected and expected.issubset(seen):
                    repaired.append(message)
                    repaired.extend(tool_messages)
                else:
                    repaired_any = True
                i = j
                continue

            if role == "tool":
                repaired_any = True
                i += 1
                continue

            repaired.append(message)
            i += 1

        if repaired_any:
            self.conversation_history = repaired

    def _request_study_prompt(self, title: str, message: str, warn: str = "") -> str:
        """
        Study Mode 無 element_id 步驟 / fallback 的提示。
        預設：terminal print/input；UI 模式由 ui_app.py 覆寫為 dialog。

        Returns:
            使用者輸入的字串（"" = 確認，"/skip" = 略過，"/done" = 結束）
        """
        print(f"\n\033[1;33m  📌 {title}\033[0m")
        for line in message.splitlines():
            if line.strip():
                print(f"\033[1;37m     {line}\033[0m")
        if warn:
            print(f"\033[33m  ⚠ {warn}\033[0m")
        print()
        return input(
            "\033[1;36m  ✅ 完成後按 Enter；可輸入問題；/skip 略過；/done 結束 > \033[0m"
        ).strip()

    def _handle_confirmation(self, session, tool_result, tool_name, tool_args):
        """
        處理需要使用者確認的敏感操作。

        Args:
            session: SAP Session COM 物件
            tool_result: 工具回傳的結果（包含 requires_confirmation）
            tool_name: 工具名稱
            tool_args: 工具參數

        Returns:
            dict: 確認後的執行結果，或取消的結果
        """
        message = tool_result.get("message", "需要確認的操作")
        print(f"\n\033[1;33m  {message}\033[0m")
        print(f"\033[1;33m  工具: {tool_name}, 參數: {tool_args}\033[0m")

        while True:
            user_input = input("\n\033[1;33m  確認執行？(y/n): \033[0m").strip().lower()
            if user_input in ("y", "yes", "是"):
                # 使用者確認，執行對應的 confirmed 版本
                if tool_name == "click":
                    return confirmed_click(session, tool_args["element_id"])
                elif tool_name == "send_vkey":
                    return confirmed_send_vkey(
                        session,
                        tool_args["vkey"],
                        tool_args.get("window_id", ""),
                    )
                elif tool_name == "handle_popup":
                    return confirmed_handle_popup(
                        session,
                        action=tool_args.get("action", "ok"),
                        field_label=tool_args.get("field_label", ""),
                        value=tool_args.get("value", ""),
                        window_id=tool_args.get("window_id", ""),
                    )
                else:
                    # 其他工具直接執行
                    return self._execute_tool_call(session, tool_name, tool_args)
            elif user_input in ("n", "no", "否"):
                return {
                    "success": False,
                    "action": tool_name,
                    "message": "使用者取消了操作",
                }
            else:
                print("\033[33m  請輸入 y 或 n\033[0m")

    def process_message(self, session, user_message: str, extra_context: str = "") -> str:
        """
        處理使用者的自然語言訊息。

        根據當前模式決定行為：
        - Auto Mode: 執行 ReAct Loop（可呼叫工具操作 SAP）
        - Ask Mode: 僅回答問題（不呼叫工具）
        - Study Mode: 執行 ReAct Loop 但只能用引導工具

        Args:
            session: SAP Session COM 物件
            user_message: 使用者輸入的自然語言指令
            extra_context: 額外的上下文資訊（如 SOP 紀錄摘要）

        Returns:
            str: AI 的最終回應文字
        """
        if self._mode in ("ask", "solve"):
            return self._process_ask(session, user_message, extra_context)
        elif self._mode == "study":
            return self._process_study(session, user_message, extra_context)
        else:
            return self._process_auto(session, user_message)

    def _extract_delivery_block_request(self, user_message: str) -> str:
        """Return sales order number for delivery/credit/material block diagnostics."""
        text = str(user_message or "").strip()
        if not text:
            return ""

        lowered = text.lower()
        has_order_intent = any(
            marker in lowered
            for marker in ("銷售訂單", "sales order", "so", "訂單")
        )
        has_block_intent = any(
            marker in lowered
            for marker in ("卡關", "不出貨", "不能出貨", "無法出貨", "出不了貨", "為什麼不出貨", "信用", "credit", "缺料", "庫存", "md04", "vkm1", "vkm3", "凍結", "block", "診斷")
        )
        if not (has_order_intent and has_block_intent):
            return ""

        match = re.search(r"(\d{6,})", text)
        return match.group(1) if match else ""

    def _try_direct_delivery_block_analysis(self, user_message: str) -> str | None:
        """Use the dedicated MCP delivery block analyzer without an LLM loop."""
        order_number = self._extract_delivery_block_request(user_message)
        if not order_number:
            return None

        tool_name = "sap_analyze_delivery_block"
        if not self._mcp_tool_available(tool_name):
            available = []
            try:
                available = sorted(self.mcp_client.tool_names()) if self.mcp_client else []
            except Exception:
                available = []
            hint = ""
            if available:
                hint = "\n目前 MCP tools 前幾個: " + ", ".join(available[:12])
            return (
                f"已偵測到銷售訂單 {order_number} 卡關診斷，但 MCP 工具 `{tool_name}` 目前不可用。\n"
                "我已停止自動迭代，避免 LLM 改跑 VL10A 或其他非預期流程。\n"
                "請先按 UI 的 MCP 診斷確認工具清單，並重啟 MCP server / SAP-GUI-Copilot，讓新版 mcp-sap-gui 載入。"
                f"{hint}"
            )

        print(
            "\033[90m"
            f"[Agent] 偵測到訂單卡關診斷，直接呼叫 MCP 工具 {tool_name}"
            "\033[0m"
        )
        try:
            result_text = self._call_mcp_tool_text(tool_name, {"order_number": order_number})
        except Exception as exc:
            return (
                f"已偵測到銷售訂單 {order_number} 卡關診斷，但 MCP 工具 `{tool_name}` 執行失敗：{exc}\n"
                "這通常代表 MCP server 尚未重啟、SAP 尚未連上，或 VA03/VKM1/MD04 欄位 ID 與工具內候選 ID 不一致。"
            )

        payload = self._parse_mcp_json_text(result_text)
        if not isinstance(payload, dict):
            return f"銷售訂單 {order_number} 卡關診斷完成，但工具回傳格式無法解析：\n{result_text}"
        if payload.get("error"):
            return f"銷售訂單 {order_number} 卡關診斷失敗：{payload.get('error')}"

        findings = payload.get("findings") or []
        finding_lines = []
        for finding in findings[:5]:
            if isinstance(finding, dict):
                finding_lines.append(f"- [{finding.get('severity', 'info')}] {finding.get('message', '')}")

        checks = payload.get("checks") or {}
        va03 = checks.get("va03") or {}
        credit = checks.get("vkm1_credit") or {}
        stock = checks.get("md04_stock") or {}
        customer_ar = checks.get("fbl5n_ar") or {}

        if stock.get("shortage"):
            stock_text = "疑似缺料"
        elif not stock.get("checked"):
            stock_text = f"未執行（{stock.get('reason', '原因未回傳')}）"
        else:
            stock_text = "未抓到明確缺料"

        if customer_ar.get("overdue_signal"):
            ar_text = "疑似有逾期/到期未清帳款"
        elif customer_ar.get("open_item_signal"):
            ar_text = "有未清項目，但未抓到明確逾期訊號"
        elif not customer_ar.get("checked"):
            ar_text = f"未執行（{customer_ar.get('reason', '原因未回傳')}）"
        else:
            ar_text = "未抓到明確逾期或未清帳款訊號"

        if credit.get("blocked"):
            if credit.get("filter_basis") == "customer_credit_account":
                credit_text = "疑似凍結（以客戶信用帳戶篩選）"
            else:
                credit_text = "疑似凍結"
        elif credit.get("checked") and not credit.get("filter_set"):
            credit_text = "未完成篩選（VKM1 欄位未對上）"
        elif credit.get("filter_basis") == "customer_credit_account":
            credit_text = "未抓到明確信用凍結（已用客戶信用帳戶篩選）"
        else:
            credit_text = "未抓到明確信用凍結"

        has_delivery_block = any(
            isinstance(finding, dict) and finding.get("type") == "delivery_block"
            for finding in findings
        )
        va03_status_text = "發現交貨凍結/出貨卡關訊號" if has_delivery_block else "未抓到明確交貨凍結訊號"

        if credit.get("blocked") and not stock.get("shortage"):
            conclusion_text = "這張單最可能卡在信用凍結，不是缺料。"
        elif stock.get("shortage") and not credit.get("blocked"):
            conclusion_text = "這張單最可能卡在缺料或庫存可用量不足。"
        elif credit.get("blocked") and stock.get("shortage"):
            conclusion_text = "這張單同時有信用凍結與缺料風險，需要兩邊一起處理。"
        else:
            conclusion_text = payload.get("conclusion", "未取得明確結論")

        customer = va03.get("customer", "N/A")
        if credit.get("blocked"):
            recommendations = [
                f"1. 請信用管理/財務人員檢查客戶 {customer} 的信用狀態。",
                "2. 請依 FBL5N 應收帳款檢查結果確認是否有逾期或未清項目需要處理。",
                "3. 若確認可放行，請至 VKM3/VKM4 或公司既有信用釋放流程處理。",
                "4. 信用釋放後，再重新建立或處理出貨。",
            ]
        elif stock.get("shortage"):
            recommendations = [
                "1. 請物管或生管確認 MD04 的可用量與補貨日期。",
                "2. 若有替代料或可調撥庫存，先處理供給來源。",
                "3. 庫存滿足後，再重新執行出貨流程。",
            ]
        else:
            recommendations = [
                "1. 請先依 VA03 狀態文字確認交貨凍結原因。",
                "2. 若信用與庫存都不是主因，請再檢查交貨排程、揀配、運輸或出貨單建立流程。",
            ]

        return (
            f"銷售訂單 {payload.get('order_number', order_number)} 卡關診斷完成。\n"
            f"\n結論: {conclusion_text}\n"
            "\n基本資料:\n"
            f"- 買方: {customer}\n"
            f"- 物料: {va03.get('material') or 'N/A'}\n"
            f"- 工廠: {va03.get('plant') or 'N/A'}\n"
            "\n檢查結果:\n"
            f"- VA03 狀態: {va03_status_text}\n"
            f"- VKM1 信用檢查: {credit_text}\n"
            f"- MD04 庫存檢查: {stock_text}\n"
            f"- FBL5N 應收帳款檢查: {ar_text}\n"
            "\n發現:\n"
            + ("\n".join(finding_lines) if finding_lines else "- 無明確卡關訊號")
            + "\n\n建議處理:\n"
            + "\n".join(recommendations)
        )

    def _process_ask(self, session, user_message: str, extra_context: str = "") -> str:
        """
        Ask Mode: 結合畫面狀態回答問題（不執行操作）。
        """
        # 掃描當前畫面
        print("\033[90m[Agent] 正在掃描 SAP 畫面...\033[0m")
        screen_text, screen_backend = self._screen_context_text(session, purpose=self._mode)

        # 組合訊息
        combined_parts = [
            f"## 當前 SAP 畫面狀態（{screen_backend}）\n```text\n{screen_text}\n```",
        ]

        if extra_context:
            combined_parts.append(f"\n## 相關 SOP 參考資料\n{extra_context}")

        combined_parts.append(f"\n## 使用者問題\n{user_message}")

        combined_message = "\n".join(combined_parts)
        self._trim_conversation_history()
        self.conversation_history.append({"role": "user", "content": combined_message})

        # 呼叫 LLM（不傳 tools，禁止操作）
        try:
            response = self._call_copilot_api(
                messages=self.conversation_history,
                tools=None,  # Ask Mode 不提供工具
            )
        except RuntimeError as e:
            return f"LLM 呼叫失敗: {e}"

        choices = response.get("choices", [])
        if not choices:
            error_info = response.get("error", {})
            if error_info:
                return f"LLM API 錯誤: {error_info.get('message', '')}"
            return "LLM API 回應異常，請稍後重試。"

        message = choices[0].get("message", {})
        if not message:
            return "LLM API 回應格式異常"

        self.conversation_history.append(message)
        return message.get("content", "(AI 未回傳訊息)")

    def _process_auto(self, session, user_message: str) -> str:
        """
        Auto Mode: 執行 ReAct Loop（可呼叫工具操作 SAP）。
        """
        direct_block_result = self._try_direct_delivery_block_analysis(user_message)
        if direct_block_result is not None:
            return direct_block_result

        # Step 1: 掃描當前畫面
        print("\033[90m[Agent] 正在掃描 SAP 畫面...\033[0m")
        screen_text, screen_backend = self._screen_context_text(session, purpose="auto")
        tool_schemas = self._tool_schemas_for_auto(screen_text)
        tool_backend = "mcp" if tool_schemas != TOOL_SCHEMAS else "legacy"

        # 組合訊息：畫面狀態 + 使用者指令
        combined_message = (
            f"## 當前 SAP 畫面狀態（{screen_backend}）\n"
            f"```text\n{screen_text}\n```\n\n"
            f"## 工具來源\n{tool_backend}\n\n"
            f"## 使用者指令\n{user_message}"
        )

        # 加入對話歷史
        self._trim_conversation_history()
        self.conversation_history.append({"role": "user", "content": combined_message})

        # Step 2-4: ReAct Loop (Think → Act → Verify)
        for iteration in range(MAX_ITERATIONS):
            print(f"\033[90m[Agent] ReAct 迭代 {iteration + 1}/{MAX_ITERATIONS}\033[0m")

            # 呼叫 LLM
            try:
                response = self._call_copilot_api(
                    messages=self.conversation_history,
                    tools=tool_schemas,
                )
            except RuntimeError as e:
                error_msg = f"LLM 呼叫失敗: {e}"
                print(f"\033[31m[Agent] {error_msg}\033[0m")
                return error_msg

            # 解析 LLM 回應
            choices = response.get("choices", [])
            if not choices:
                error_info = response.get("error", {})
                if error_info:
                    return f"LLM API 錯誤: {error_info.get('message', json.dumps(error_info, ensure_ascii=False))}"
                return f"LLM API 回應異常（無 choices），請稍後重試。"

            choice = choices[0]
            message = choice.get("message", {})
            finish_reason = choice.get("finish_reason", "")

            if not message:
                return "LLM API 回應格式異常（空 message）"

            self.conversation_history.append(message)

            # 情況 A：LLM 直接回應文字
            if finish_reason == "stop" or not message.get("tool_calls"):
                final_text = message.get("content", "")
                if final_text:
                    return final_text
                return "(AI 未回傳任何訊息)"

            # 情況 B：LLM 要求呼叫工具
            tool_calls = message.get("tool_calls", [])
            for tool_call in tool_calls:
                func = tool_call.get("function", {})
                tool_name = func.get("name", "")
                tool_call_id = tool_call.get("id", "")

                try:
                    tool_args = json.loads(func.get("arguments", "{}"))
                except json.JSONDecodeError:
                    tool_args = {}

                print(f"\033[36m[Agent] 呼叫工具: {tool_name}({tool_args})\033[0m")

                interrupted = False
                try:
                    tool_result = self._execute_primary_tool_call(session, tool_name, tool_args)

                    if tool_result.get("requires_confirmation"):
                        confirm_tool_name = tool_result.get("_legacy_tool_name") or tool_result.get("action") or tool_name
                        confirm_tool_args = tool_result.get("_legacy_tool_args") or tool_args
                        tool_result = self._handle_confirmation(
                            session, tool_result, confirm_tool_name, confirm_tool_args
                        )

                    tool_result = self._attach_screen_after_tool(session, tool_result)
                except KeyboardInterrupt:
                    interrupted = True
                    tool_result = {
                        "success": False,
                        "action": tool_name,
                        "error": "使用者中斷工具執行",
                    }
                except Exception as e:
                    tool_result = {
                        "success": False,
                        "action": tool_name,
                        "error": f"工具執行例外: {e}",
                    }

                console_result = dict(tool_result)
                if "screen_after" in console_result:
                    console_result["screen_after"] = console_result.get("screen_summary")
                print(f"\033[90m[Agent] 工具結果: {json.dumps(console_result, ensure_ascii=False)}\033[0m")

                self.conversation_history.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": json.dumps(tool_result, ensure_ascii=False),
                })

                if interrupted:
                    raise KeyboardInterrupt

        return "⚠️ 已達最大操作次數限制，請確認當前畫面狀態是否正確。"

    def _study_tool_requested_finish(self, tool_name: str, tool_result: dict) -> bool:
        """Detect explicit Study Mode finish responses before another LLM turn can loop."""
        if tool_name != "guide_user_action":
            return False
        if tool_result.get("finish_requested"):
            return True

        user_response = str(tool_result.get("user_response", "") or "").strip().lower()
        if not user_response:
            return False

        instruction = str(tool_result.get("instruction", "") or "")
        finish_words = ("結束教學", "結束", "我已學會", "完成教學", "sop 摘要", "SOP 摘要")
        if user_response == "4" and any(word in instruction for word in finish_words):
            return True
        return False

    def _study_finish_summary(self, tool_result: dict) -> str:
        """Return a deterministic Study Mode finish message to avoid repeated choice loops."""
        screen = tool_result.get("screen_after") or tool_result.get("screen_summary") or {}
        tcode = screen.get("tcode") or "N/A"
        title = screen.get("title") or "N/A"
        screen_number = screen.get("screen_number") or "N/A"
        current_value = str(tool_result.get("current_value", "") or "").strip()

        lines = [
            "✅ Study Mode 已依你的選擇結束。",
            "",
            "## 本次教學摘要",
            f"- 目前交易: {tcode}",
            f"- 目前畫面: {title} / Screen {screen_number}",
        ]
        if current_value:
            lines.append(f"- 目前聚焦欄位值: {current_value}")
        lines.extend([
            "",
            "## 下次可重用的 SOP 草稿",
            "1. 進入對應查詢交易或延續目前 SAP 畫面。",
            "2. 依畫面提示輸入必要查詢條件，若欄位已有系統預設值則先確認是否沿用。",
            "3. 進入查詢結果或主資料顯示畫面後，依需求查看對應頁籤或欄位。",
            "4. 若已取得所需資訊，即可結束教學。",
        ])
        return "\n".join(lines)

    def _one_shot_ask(self, context: str, question: str) -> str:
        """Step Runner 內嵌 Q&A：單次 LLM 呼叫回答使用者問題。"""
        try:
            response = self._call_copilot_api(
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT_ASK},
                    {"role": "user", "content": f"{context}\n\n## 使用者問題\n{question}"},
                ],
                tools=None,
            )
            choices = response.get("choices", [])
            if choices:
                return choices[0].get("message", {}).get("content", "(AI 未回傳內容)")
        except Exception as e:
            return f"(問 AI 失敗：{e})"
        return "(無回應)"

    def _run_sop_step_runner(
        self,
        session,
        steps: list[StepItem],
        sop_name: str,
        extra_context: str = "",
    ) -> str:
        """
        直接依 SOP 步驟引導，不在步驟間呼叫 LLM。

        LLM 僅在以下情況呼叫：
        - 使用者在某步驟輸入了問題（非空白、非快捷指令）
        - （未來）元件找不到需要 AI 輔助定位

        快捷指令：
        - Enter（空）       → 確認完成，進入下一步
        - /skip 或 s        → 略過此步驟
        - /done 或 結束     → 提早結束教學
        - 任何其他文字      → 視為問題，呼叫 AI 回答後繼續
        """
        total = len(steps)
        print(f"\n\033[90m[Study] 步驟引導模式：共 {total} 步（Enter=下一步 / /skip=略過 / /done=結束）\033[0m\n")

        completed = 0
        for step in steps:
            print(f"\n\033[1;34m{'─' * 54}\033[0m")
            print(f"\033[1;34m  步驟 {step.n}/{total}: {step.header}\033[0m")
            print(f"\033[1;34m{'─' * 54}\033[0m")

            if step.element_id:
                # guide_user_action 負責：MCP focus → 高亮 → 顯示說明 → 等待輸入
                tool_args = {
                    "element_id": step.element_id,
                    "instruction": clean_step_instruction(step),
                    "reason": f"步驟 {step.n}/{total}",
                    "confidence": "high",
                    "source": f"SOP: {sop_name}",
                    "expected_response_type": "confirm",
                }
                try:
                    result = self._execute_study_tool_call(
                        session, "guide_user_action", tool_args
                    )
                except Exception as exc:
                    print(f"\033[33m  ⚠ 元件高亮失敗: {exc}\033[0m")
                    result = {"success": False}

                # 元件不在目前畫面（尚未切換、子表單未展開等）→ 退回純文字 dialog
                if not result.get("success") and not result.get("finish_requested"):
                    body = clean_step_instruction(step)
                    if step.vkey is not None:
                        body += f"\n\n快捷鍵：{vkey_label(step.vkey)}"
                    warn_msg = "無法定位元件，請手動操作後確認。" if result.get("error") else ""
                    raw = self._request_study_prompt(
                        f"步驟 {step.n}/{total}: {step.header}",
                        body,
                        warn=warn_msg,
                    )
                    lower = raw.lower()
                    result = {
                        "success": True,
                        "user_response": raw,
                        "finish_requested": lower in ("/done", "done", "結束", "finish"),
                        "skipped": lower in ("/skip", "skip", "s"),
                    }
            else:
                # 無 element_id（如 F8 執行、確認畫面）
                # 通過可覆寫的 hook：terminal 模式 → print/input，UI 模式 → dialog
                body = clean_step_instruction(step)
                if step.vkey is not None:
                    body += f"\n\n快捷鍵：{vkey_label(step.vkey)}"
                raw = self._request_study_prompt(
                    f"步驟 {step.n}/{total}: {step.header}",
                    body,
                )
                lower = raw.lower()
                result = {
                    "success": True,
                    "user_response": raw,
                    "finish_requested": lower in ("/done", "done", "結束", "finish"),
                    "skipped": lower in ("/skip", "skip", "s"),
                }

            finish_requested = result.get("finish_requested", False)
            user_response = str(result.get("user_response", "") or "")
            skipped = result.get("skipped", False)

            if finish_requested:
                print(f"\n\033[32m  ✅ 教學已依您要求結束（完成 {completed}/{total} 步）。\033[0m")
                break

            if skipped:
                print(f"\033[90m  已略過步驟 {step.n}\033[0m")
                continue

            completed += 1

            # 使用者輸入了問題（非空白、非常見確認詞）
            _confirm_words = {"ok", "好", "是", "y", "ye", "yes", "完成", ""}
            if user_response.lower() not in _confirm_words:
                print(f"\033[90m[Study] 問 AI...\033[0m")
                screen_text, _ = self._screen_context_text(session, purpose="ask")
                step_ctx = (
                    f"## 當前 SOP 步驟 ({step.n}/{total}): {step.header}\n"
                    f"{step.detail[:600]}\n\n"
                    f"## SOP 說明\n{extra_context[:1500]}\n\n"
                    f"## 當前 SAP 畫面\n{screen_text[:3000]}"
                )
                answer = self._one_shot_ask(step_ctx, user_response)
                self._request_study_prompt(
                    f"AI 回覆（步驟 {step.n}/{total}）",
                    answer,
                )

        # 結束時輕量掃描
        compact = self._study_post_tool_screen(session)
        tcode = compact.get("tcode", "")
        title = compact.get("title", "")
        screen_n = compact.get("screen_number", "")
        status_text = (compact.get("status_bar") or {}).get("text", "")
        lines = [
            f"✅ Study Mode 完成（{completed}/{total} 步）。",
            f"最終畫面：{tcode} / {title}（screen {screen_n}）",
        ]
        if status_text:
            lines.append(f"狀態列：{status_text}")
        return "\n".join(lines)

    def _process_study(self, session, user_message: str, extra_context: str = "") -> str:
        """
        Study Mode 入口。

        Fast path（Step Runner）：
          若 extra_context（SOP 文字）能解析出帶有 element_id 的步驟，
          直接循序執行，步驟間無 LLM API 呼叫。

        Slow path（LLM ReAct Loop）：
          步驟解析不足時（element_id 覆蓋率 < 30% 或步驟數 < 2），
          回退到原本的 LLM 驅動引導模式。
        """
        # ── Fast path: SOP Step Runner ──────────────────────────────────────
        if extra_context:
            steps = parse_sop_steps(extra_context)
            confidence = steps_confidence(steps)
            if len(steps) >= 2 and confidence >= 0.3:
                sop_name_m = re.search(r'#\s*SOP[：:]\s*(.+)', extra_context)
                sop_name = sop_name_m.group(1).strip() if sop_name_m else "SOP"
                print(
                    f"\033[90m[Study] 解析出 {len(steps)} 個步驟"
                    f"（element_id 覆蓋率 {int(confidence * 100)}%）"
                    f"，啟用步驟引導模式\033[0m"
                )
                return self._run_sop_step_runner(session, steps, sop_name, extra_context)

        # ── Slow path: LLM ReAct Loop ────────────────────────────────────────
        print("\033[90m[Study] 步驟解析不足，改用 LLM 引導模式\033[0m")
        print("\033[90m[Agent] 正在掃描 SAP 畫面...\033[0m")
        screen_text, screen_backend = self._screen_context_text(session, purpose="study")

        combined_parts = [
            f"## 當前 SAP 畫面狀態（{screen_backend}）\n```text\n{screen_text}\n```",
        ]
        if extra_context:
            combined_parts.append(f"\n## SOP 操作指南\n{extra_context}")
        combined_parts.append(f"\n## 使用者指令\n{user_message}")
        combined_message = "\n".join(combined_parts)

        self._trim_conversation_history()
        self.conversation_history.append({"role": "user", "content": combined_message})

        for iteration in range(STUDY_MAX_ITERATIONS):
            print(f"\033[90m[Agent] Study ReAct 迭代 {iteration + 1}/{STUDY_MAX_ITERATIONS}\033[0m")

            try:
                response = self._call_copilot_api(
                    messages=self.conversation_history,
                    tools=STUDY_TOOL_SCHEMAS,
                )
            except RuntimeError as e:
                error_msg = f"LLM 呼叫失敗: {e}"
                print(f"\033[31m[Agent] {error_msg}\033[0m")
                return error_msg

            choices = response.get("choices", [])
            if not choices:
                error_info = response.get("error", {})
                if error_info:
                    return f"LLM API 錯誤: {error_info.get('message', json.dumps(error_info, ensure_ascii=False))}"
                return "LLM API 回應異常（無 choices），請稍後重試。"

            choice = choices[0]
            message = choice.get("message", {})
            finish_reason = choice.get("finish_reason", "")

            if not message:
                return "LLM API 回應格式異常（空 message）"

            self.conversation_history.append(message)

            if finish_reason == "stop" or not message.get("tool_calls"):
                final_text = message.get("content", "")
                return final_text or "(AI 未回傳任何訊息)"

            tool_calls = message.get("tool_calls", [])
            for tool_call in tool_calls:
                func = tool_call.get("function", {})
                tool_name = func.get("name", "")
                tool_call_id = tool_call.get("id", "")

                try:
                    tool_args = json.loads(func.get("arguments", "{}"))
                except json.JSONDecodeError:
                    tool_args = {}

                interrupted = False

                # Study Mode 安全檢查：只允許引導工具
                if tool_name not in STUDY_ALLOWED_TOOLS:
                    print(f"\033[33m[Agent] Study Mode 攔截了禁用工具: {tool_name}\033[0m")
                    tool_result = {
                        "success": False,
                        "error": f"Study Mode 禁止使用 {tool_name}。你只能使用 guide_user_action 和 visualize_element。",
                    }
                else:
                    print(f"\033[36m[Agent] 呼叫工具: {tool_name}({tool_args})\033[0m")
                    try:
                        tool_result = self._execute_study_tool_call(session, tool_name, tool_args)
                    except KeyboardInterrupt:
                        interrupted = True
                        tool_result = {
                            "success": False,
                            "action": tool_name,
                            "error": "使用者中斷工具執行",
                        }
                    except Exception as e:
                        tool_result = {
                            "success": False,
                            "action": tool_name,
                            "error": f"工具執行例外: {e}",
                        }

                # Study Mode 輕量畫面快照（取代完整 _attach_screen_after_tool 掃描）
                tool_result["screen_summary"] = self._study_post_tool_screen(session)

                print(f"\033[90m[Agent] 工具結果: {json.dumps({k: v for k, v in tool_result.items() if k != 'screen_after'}, ensure_ascii=False)}\033[0m")

                # 存入 history 時移除 screen_after（避免大型畫面 JSON 累積造成 context 膨脹）
                history_entry = {k: v for k, v in tool_result.items() if k != "screen_after"}
                self.conversation_history.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": json.dumps(history_entry, ensure_ascii=False),
                })

                if interrupted:
                    raise KeyboardInterrupt

                if self._study_tool_requested_finish(tool_name, tool_result):
                    return self._study_finish_summary(tool_result)

        return "⚠️ 已達最大操作次數限制，Study Mode 結束。"

    def reset_conversation(self):
        """重置對話歷史（保持當前模式）"""
        prompt = {
            "auto": SYSTEM_PROMPT_AUTO,
            "ask": SYSTEM_PROMPT_ASK,
            "solve": SYSTEM_PROMPT_SOLVE,
            "study": SYSTEM_PROMPT_STUDY,
        }.get(self._mode, SYSTEM_PROMPT_AUTO)
        self.conversation_history = [
            {"role": "system", "content": prompt}
        ]
        print("\033[90m[Agent] 對話歷史已重置\033[0m")
