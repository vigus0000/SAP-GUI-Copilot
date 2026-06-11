"""
LLM Agent 大腦 — GitHub Copilot 版

透過 GitHub Copilot API (api.githubcopilot.com) 進行意圖識別與 Tool Calling。
實作 ReAct Loop: Scan → Think → Act → Verify

注意：Copilot API 相容 OpenAI Chat Completions 格式，但回應可能有差異，
需要額外的錯誤處理。

架構解耦：
- LLM 呼叫邏輯與 SAP Tools 完全分離
- 未來如需切換 LLM，只需修改此檔案的 endpoint 與 headers
"""

import json
import os
import time
import requests

try:
    import dotenv
    dotenv.load_dotenv()
except Exception:
    pass

from copilot_auth import CopilotAuth
from sap_agent_tools import (
    scan_sap_screen,
    read_editor_text,
    TOOL_SCHEMAS,
    TOOL_FUNCTIONS,
    confirmed_click,
    confirmed_send_vkey,
    confirmed_handle_popup,
)
from mcp_client import MCPClientUnavailable, get_default_sync_client


def env_enabled(name, default="true"):
    return str(os.getenv(name, default)).strip().lower() in {"1", "true", "yes", "y", "on"}


def env_list(name, default):
    value = os.getenv(name, default)
    return [item.strip() for item in value.split(",") if item.strip()]

# Study Mode 只允許使用的工具名稱
STUDY_ALLOWED_TOOLS = {"guide_user_action", "visualize_element"}
STUDY_TOOL_SCHEMAS = [s for s in TOOL_SCHEMAS if s.get("function", {}).get("name") in STUDY_ALLOWED_TOOLS]

# GitHub Copilot Chat Completions API
COPILOT_CHAT_URL = "https://api.githubcopilot.com/chat/completions"

# 預設模型。可用 .env 的 COPILOT_MODEL 覆寫；不要再硬寫已退場的 gpt-4o。
DEFAULT_MODEL = os.getenv("COPILOT_MODEL", "gpt-5-mini")

# 最大 ReAct 迴圈次數（防止無限迴圈）
MAX_ITERATIONS = int(os.getenv("COPILOT_MAX_ITERATIONS", "6"))

# Copilot API 節流/重試設定
COPILOT_MAX_RETRIES = int(os.getenv("COPILOT_MAX_RETRIES", "4"))
COPILOT_RETRY_BASE_SECONDS = float(os.getenv("COPILOT_RETRY_BASE_SECONDS", "2"))
COPILOT_RETRY_MAX_SECONDS = float(os.getenv("COPILOT_RETRY_MAX_SECONDS", "60"))
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
MCP_SCREEN_CACHE_ENABLED = env_enabled("MCP_SCREEN_CACHE_ENABLED", "true")
MCP_SCREEN_CACHE_TTL_SECONDS = float(os.getenv("MCP_SCREEN_CACHE_TTL_SECONDS", "30"))
MCP_SCREEN_CACHE_MAX_WRITES = int(os.getenv("MCP_SCREEN_CACHE_MAX_WRITES", "20"))
MCP_EXPOSE_DISCOVERY_TO_LLM = env_enabled("MCP_EXPOSE_DISCOVERY_TO_LLM", "false")
MCP_POPUP_USE_POPUP_TOOL_ONLY = env_enabled("MCP_POPUP_USE_POPUP_TOOL_ONLY", "true")
MCP_ATTACH_ELEMENTS_AFTER_NAV = env_enabled("MCP_ATTACH_ELEMENTS_AFTER_NAV", "true")
MCP_ATTACH_ELEMENTS_ON_FIELD_FAILURE = env_enabled("MCP_ATTACH_ELEMENTS_ON_FIELD_FAILURE", "true")
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
        "sap_read_table",
        "sap_select_table_row",
        "sap_select_popup_table_row_and_confirm",
        "sap_select_multiple_rows",
        "sap_get_popup_window",
        "sap_handle_popup",
        "sap_get_toolbar_buttons",
        "sap_read_shell_content",
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
- 如果只需要填多個欄位但暫不送出，才使用 sap_set_batch_fields(fields={id: value, ...}, validate=false)，不要逐欄 sap_set_field
- 如果畫面是彈窗 table row 選擇後要按繼續，且有 sap_select_popup_table_row_and_confirm，優先一次呼叫它；不要拆成 sap_select_table_row + sap_handle_popup
- 不要把 save/post/delete/confirm/release 等敏感提交操作放進批次欄位動作；這些操作仍必須走確認或由使用者明確允許
- 如果工具結果的 screen_after 內含 screen_elements、field_write_recovery 或 instruction_to_agent，下一步必須優先使用其中列出的實際元件 ID 重試；不要要求使用者先列出元素 ID
- 如果欄位寫入回傳 Could not set field 或 failed>0，代表使用了錯誤/過期的 ID；先根據 screen_after.field_write_recovery 或 screen_after.screen_elements 找正確欄位重試，不要立刻改成手動教學
- 如果 fields 的 type 是 GuiCheckBox 或 GuiRadioButton，讀取狀態使用 read_checkbox，設定狀態使用 set_checkbox；不要用 set_text 寫入 True/False，也不要在狀態未知時盲目 click
- checkbox/radio 的 fields[].value 會是 "True" / "False"，selected 也會標示布林狀態；操作前先確認目前狀態，避免重複切換
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
2. 根據 SOP 的下一步，或在沒有 SOP 時根據 SAP 常識與目前畫面推斷下一步，找到畫面上對應的元件 ID
3. 使用 `guide_user_action(element_id, instruction)` 引導使用者
4. 使用者確認完成後，工具結果會包含 `user_response`；如果它不是空字串，代表使用者在教學中輸入了明確回答，你必須依該回答決定下一步，不能重複詢問同一個問題
5. 如果畫面狀態與 SOP 預期不同，分析原因並調整引導

## 沒有既有 SOP 時
- 如果 extra context 表明「目前沒有同名 SOP / skill」或「即席 Study 任務」，你仍然要教學，不要要求使用者先錄製 SOP
- 先依使用者目標判斷最可能的 SAP 流程；例如「查詢物料」通常可先考慮 MM03 或系統中的物料查詢交易，但要依目前畫面與狀態列調整
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
- instruction 要清楚說明：要操作什麼、填入什麼值、為什麼這樣做
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

    透過 GitHub Copilot API 實現自然語言操作 SAP 的能力。
    """

    def __init__(self, auth: CopilotAuth, model: str = DEFAULT_MODEL):
        """
        初始化 Agent。

        Args:
            auth: CopilotAuth 認證物件
            model: 使用的模型名稱 (預設 gpt-4o)
        """
        self.auth = auth
        self.model = model
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
        呼叫 GitHub Copilot Chat Completions API。

        Args:
            messages: 對話歷史
            tools: Function Calling 工具定義（可選）

        Returns:
            dict: API 回應

        Raises:
            RuntimeError: API 呼叫失敗
        """
        headers = {
            "Authorization": f"Bearer {self.auth.get_token()}",
            "Content-Type": "application/json",
            "Editor-Version": "vscode/1.100.0",
            "Editor-Plugin-Version": "copilot-chat/0.24.0",
            "Copilot-Integration-Id": "vscode-chat",
            "Openai-Intent": "conversation-panel",
        }

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.1,  # 低溫度，確保操作穩定
        }

        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        last_error = None
        for attempt in range(COPILOT_MAX_RETRIES + 1):
            try:
                started_at = time.perf_counter()
                resp = requests.post(
                    COPILOT_CHAT_URL,
                    headers=headers,
                    json=payload,
                    timeout=60,
                )
                self._timing_log("Copilot API call", started_at)

                if resp.status_code == 401:
                    # Token 過期，嘗試刷新後重試
                    print("\033[33m[Agent] Copilot Token 過期，正在刷新...\033[0m")
                    self.auth._refresh_copilot_token()
                    headers["Authorization"] = f"Bearer {self.auth.get_token()}"
                    continue

                if resp.status_code in (429, 500, 502, 503, 504):
                    last_error = self._format_api_error(resp)
                    if attempt < COPILOT_MAX_RETRIES:
                        wait_time = self._retry_delay_seconds(resp, attempt)
                        print(
                            "\033[33m"
                            f"[Agent] Copilot API {resp.status_code}，"
                            f"{wait_time:.1f} 秒後重試 "
                            f"({attempt + 1}/{COPILOT_MAX_RETRIES})，model={self.model}"
                            "\033[0m"
                        )
                        time.sleep(wait_time)
                        continue

                if resp.status_code != 200:
                    raise RuntimeError(self._format_api_error(resp))

                result = resp.json()

                # 偵錯：檢查回應結構
                if "choices" not in result:
                    print(f"\033[33m[Agent] API 回應缺少 choices 欄位: {json.dumps(result, ensure_ascii=False)[:500]}\033[0m")
                elif len(result["choices"]) == 0:
                    print(f"\033[33m[Agent] API 回應 choices 為空陣列\033[0m")

                return result

            except requests.Timeout:
                last_error = "Copilot API 回應逾時 (60s)"
                if attempt < COPILOT_MAX_RETRIES:
                    wait_time = self._retry_delay_seconds(None, attempt)
                    print(f"\033[33m[Agent] {last_error}，{wait_time:.1f} 秒後重試 ({attempt + 1}/{COPILOT_MAX_RETRIES})\033[0m")
                    time.sleep(wait_time)
                    continue
                raise RuntimeError(last_error)

            except requests.RequestException as e:
                last_error = f"網路請求失敗: {e}"
                if attempt < COPILOT_MAX_RETRIES:
                    wait_time = self._retry_delay_seconds(None, attempt)
                    print(f"\033[33m[Agent] {last_error}，{wait_time:.1f} 秒後重試 ({attempt + 1}/{COPILOT_MAX_RETRIES})\033[0m")
                    time.sleep(wait_time)
                    continue
                raise RuntimeError(last_error)

        raise RuntimeError(last_error or "Copilot API 呼叫失敗")

    def _retry_delay_seconds(self, resp, attempt):
        """依 Retry-After 或 exponential backoff 計算等待時間。"""
        if resp is not None:
            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                try:
                    return min(float(retry_after), COPILOT_RETRY_MAX_SECONDS)
                except ValueError:
                    pass

        delay = COPILOT_RETRY_BASE_SECONDS * (2 ** attempt)
        return min(delay, COPILOT_RETRY_MAX_SECONDS)

    def _format_api_error(self, resp):
        retry_after = resp.headers.get("Retry-After")
        retry_part = f"\nRetry-After: {retry_after}" if retry_after else ""
        return (
            f"Copilot API 呼叫失敗: {resp.status_code}"
            f"\nmodel: {self.model}"
            f"{retry_part}"
            f"\n{resp.text}"
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

    def _mcp_cached_elements_valid(self, fingerprint, container_id):
        if not MCP_SCREEN_CACHE_ENABLED:
            return False
        cache = self._mcp_screen_cache
        if not fingerprint or not cache.get("elements_text"):
            return False
        if cache.get("fingerprint") != fingerprint:
            return False
        if cache.get("container_id") != container_id:
            return False
        age = time.time() - float(cache.get("elements_at") or 0)
        return age <= MCP_SCREEN_CACHE_TTL_SECONDS

    def _remember_mcp_elements(self, fingerprint, container_id, elements_text):
        if not MCP_SCREEN_CACHE_ENABLED or not fingerprint or not elements_text:
            return
        self._mcp_screen_cache = {
            "fingerprint": fingerprint,
            "container_id": container_id,
            "elements_text": str(elements_text),
            "elements_at": time.time(),
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

        if not force and self._mcp_cached_elements_valid(fingerprint, container_id):
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
            self._remember_mcp_elements(fingerprint, container_id, raw_elements)
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

    def _mcp_fast_screen_context_text(self):
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
            if self._mcp_cached_elements_valid(fingerprint, container_id):
                raw_elements = self._mcp_screen_cache.get("elements_text", "")
                parts.append(f"### {elements_tool} fast filtered (cache hit)\n{raw_elements}")
            else:
                try:
                    raw_elements = self._call_mcp_tool_text(elements_tool, {
                        "container_id": container_id,
                        "max_depth": MCP_FAST_SCREEN_MAX_DEPTH,
                        "type_filter": MCP_FAST_SCREEN_TYPE_FILTER,
                        "changeable_only": MCP_FAST_SCREEN_CHANGEABLE_ONLY,
                    })
                    self._remember_mcp_elements(fingerprint, container_id, raw_elements)
                    parts.append(f"### {elements_tool} fast filtered\n{raw_elements}")
                except Exception:
                    cache = self._mcp_screen_cache
                    if cache.get("fingerprint") == fingerprint and cache.get("container_id") == container_id:
                        raw_elements = cache.get("elements_text", "")
                        parts.append(f"### {elements_tool} fast filtered (stale cache fallback)\n{raw_elements}")
                    else:
                        raise

        recent_writes = self._mcp_recent_writes_text()
        if recent_writes:
            parts.append(
                "### mcp_local_field_write_cache\n"
                "The screen elements above may contain older values for fields changed locally in this run.\n"
                f"{recent_writes}"
            )

        return "\n\n".join(parts) if parts else None

    def _mcp_screen_context_text(self):
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
                fast_text = self._mcp_fast_screen_context_text()
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
        mcp_text = self._mcp_screen_context_text()
        if mcp_text:
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
            item = {"id": table.get("id", ""), "rows": []}
            rows = table.get("rows", [])
            for row in rows[:max_rows]:
                row_item = {
                    "row": row.get("row"),
                    "text": row.get("text", ""),
                    "cells": row.get("cells", [])[:max_cells],
                }
                item["rows"].append(row_item)
            item["rows_truncated"] = len(rows) > max_rows
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
                return f"Copilot API 錯誤: {error_info.get('message', '')}"
            return "Copilot API 回應異常，請稍後重試。"

        message = choices[0].get("message", {})
        if not message:
            return "Copilot API 回應格式異常"

        self.conversation_history.append(message)
        return message.get("content", "(AI 未回傳訊息)")

    def _process_auto(self, session, user_message: str) -> str:
        """
        Auto Mode: 執行 ReAct Loop（可呼叫工具操作 SAP）。
        """
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
                    return f"Copilot API 錯誤: {error_info.get('message', json.dumps(error_info, ensure_ascii=False))}"
                return f"Copilot API 回應異常（無 choices），請稍後重試。"

            choice = choices[0]
            message = choice.get("message", {})
            finish_reason = choice.get("finish_reason", "")

            if not message:
                return "Copilot API 回應格式異常（空 message）"

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

    def _process_study(self, session, user_message: str, extra_context: str = "") -> str:
        """
        Study Mode: 使用 ReAct Loop 但只允許引導工具（guide_user_action, visualize_element）。
        """
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

        for iteration in range(MAX_ITERATIONS):
            print(f"\033[90m[Agent] Study ReAct 迭代 {iteration + 1}/{MAX_ITERATIONS}\033[0m")

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
                    return f"Copilot API 錯誤: {error_info.get('message', json.dumps(error_info, ensure_ascii=False))}"
                return "Copilot API 回應異常（無 choices），請稍後重試。"

            choice = choices[0]
            message = choice.get("message", {})
            finish_reason = choice.get("finish_reason", "")

            if not message:
                return "Copilot API 回應格式異常（空 message）"

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

                # Study Mode 安全檢查：只允許引導工具
                if tool_name not in STUDY_ALLOWED_TOOLS:
                    print(f"\033[33m[Agent] Study Mode 攔截了禁用工具: {tool_name}\033[0m")
                    tool_result = {
                        "success": False,
                        "error": f"Study Mode 禁止使用 {tool_name}。你只能使用 guide_user_action 和 visualize_element。",
                    }
                else:
                    print(f"\033[36m[Agent] 呼叫工具: {tool_name}({tool_args})\033[0m")
                    interrupted = False
                    try:
                        tool_result = self._execute_study_tool_call(session, tool_name, tool_args)
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
                if tool_name not in STUDY_ALLOWED_TOOLS:
                    interrupted = False
                    tool_result = self._attach_screen_after_tool(session, tool_result)
                print(f"\033[90m[Agent] 工具結果: {json.dumps({k: v for k, v in tool_result.items() if k != 'screen_after'}, ensure_ascii=False)}\033[0m")

                self.conversation_history.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": json.dumps(tool_result, ensure_ascii=False),
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
