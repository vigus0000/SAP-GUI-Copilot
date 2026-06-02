"""
SAP GUI Scanner & Actor 工具集

為 LLM Agent 提供 Function Calling 的工具介面：
- Scanner: 遞迴遍歷 SAP GUI DOM 樹，輸出結構化 JSON
- Actor: set_text, click, send_vkey, set_tcode 等操作工具

遵循 plan.md 指引：
- 過濾無用排版元件，只保留可互動元件（Token 優化）
- 每個操作都帶防禦性 try-except
- 敏感操作（Save/Post）標記需要人工確認
"""

import json
from pywintypes import com_error


def _safe_get_attr(obj, attr, default=None):
    """
    安全地從 COM 物件取得屬性值。

    win32com 的動態 dispatch 物件不支援 hasattr()，
    因為 __getattr__ 內部的 _make_method_ 可能在解析
    型別資訊時觸發 IndexError (BuildCallList: fdesc[6])。

    此函數用 try-except 捕獲所有異常，取代 hasattr + getattr 模式。
    """
    try:
        return getattr(obj, attr)
    except Exception:
        return default

# ===== 元件類型過濾設定 =====

# 需要保留的可互動元件類型
INTERACTIVE_TYPES = {
    "GuiTextField",
    "GuiCTextField",
    "GuiPasswordField",
    "GuiButton",
    "GuiTab",
    "GuiCheckBox",
    "GuiRadioButton",
    "GuiComboBox",
    "GuiLabel",
    "GuiStatusbar",
    "GuiShell",        # 表格控件 (ALV Grid 等)
    "GuiTableControl",
    "GuiOkCodeField",  # T-Code 輸入欄
    "GuiMenu",
    "GuiMenubar",
    "GuiToolbar",
    "GuiTitlebar",
}

# 需要略過的容器/排版元件類型
SKIP_TYPES = {
    "GuiContainerShell",
    "GuiCustomControl",
    "GuiScrollContainer",
    "GuiUserArea",
    "GuiSplitterContainer",
    "GuiSplitterShell",
    "GuiDockShell",
    "GuiSimpleContainer",
    "GuiBoxShell",
    "GuiDialogShell",
}

# 敏感按鈕關鍵字（觸發 Human-in-the-loop 確認）
SENSITIVE_KEYWORDS = {
    "save", "儲存", "post", "過帳", "delete", "刪除",
    "execute", "執行", "release", "核發", "confirm", "確認",
    "submit", "提交", "approve", "批准",
}


# ===== Scanner: 畫面掃描 =====

def _get_element_type_name(element):
    """安全地取得元件類型名稱"""
    try:
        result = _safe_get_attr(element, "Type")
        return result if result is not None else str(type(element).__name__)
    except Exception:
        return "Unknown"


def _extract_element_info(element):
    """
    從單一 SAP UI 元件中提取結構化資訊。

    Returns:
        dict 或 None（如果元件不值得保留）
    """
    try:
        type_name = _get_element_type_name(element)

        # 取得基本屬性（全部使用 _safe_get_attr，避免 hasattr 觸發 COM 內部錯誤）
        elem_id = _safe_get_attr(element, "Id", "") or ""
        text = _safe_get_attr(element, "Text", "") or ""
        tooltip = _safe_get_attr(element, "Tooltip", "") or ""
        changeable = _safe_get_attr(element, "Changeable", False) or False

        # 過濾空白標籤（節省 Token）
        if type_name == "GuiLabel" and not str(text).strip() and not str(tooltip).strip():
            return None

        info = {
            "id": elem_id,
            "type": type_name,
        }

        if text:
            info["text"] = str(text)
        if tooltip:
            info["tooltip"] = str(tooltip)
        if changeable:
            info["changeable"] = True

        return info

    except Exception:
        return None


def _traverse_children(element, elements_list, max_depth=10, current_depth=0):
    """
    遞迴遍歷 SAP GUI DOM 樹，收集可互動元件。

    Args:
        element: 當前 SAP UI 元件
        elements_list: 收集結果的 list
        max_depth: 最大遞迴深度（防止無限遞迴）
        current_depth: 當前深度
    """
    if current_depth > max_depth:
        return

    try:
        children = element.Children
        count = children.Count
    except Exception:
        return

    for i in range(count):
        try:
            child = children(i)
            type_name = _get_element_type_name(child)

            # 跳過純容器類型但繼續遍歷其子元件
            if type_name in SKIP_TYPES:
                _traverse_children(child, elements_list, max_depth, current_depth + 1)
                continue

            # 提取可互動元件的資訊
            info = _extract_element_info(child)
            if info:
                elements_list.append(info)

            # 繼續向下遍歷（Tab、Toolbar 等也有子元件）
            _traverse_children(child, elements_list, max_depth, current_depth + 1)

        except Exception:
            continue


def scan_sap_screen(session):
    """
    掃描當前 SAP 畫面，將 UI 元件轉換為結構化 JSON。

    會自動過濾排版元件，只保留可互動元件（Token 優化）。

    Args:
        session: SAP Session COM 物件

    Returns:
        dict: 結構化的畫面狀態 JSON
            {
                "tcode": "VA01",
                "title": "Create Sales Order: Initial Screen",
                "screen_number": "100",
                "status_bar": {"type": "S", "text": ""},
                "elements": [...]
            }
    """
    result = {
        "tcode": "",
        "title": "",
        "screen_number": "",
        "status_bar": {"type": "", "text": ""},
        "elements": [],
    }

    try:
        # 取得 Session 資訊
        info = session.Info
        result["tcode"] = _safe_get_attr(info, "Transaction", "")
        result["screen_number"] = str(_safe_get_attr(info, "ScreenNumber", ""))
    except Exception:
        pass

    try:
        # 取得視窗標題
        window = session.FindById("wnd[0]")
        result["title"] = _safe_get_attr(window, "Text", "")
    except Exception:
        pass

    try:
        # 讀取狀態列
        sbar = session.FindById("wnd[0]/sbar")
        result["status_bar"] = {
            "type": _safe_get_attr(sbar, "MessageType", ""),
            "text": _safe_get_attr(sbar, "Text", ""),
        }
    except Exception:
        pass

    try:
        # 遍歷 DOM 樹
        window = session.FindById("wnd[0]")
        _traverse_children(window, result["elements"])
    except Exception as e:
        result["scan_error"] = f"DOM 遍歷失敗: {e}"

    # 同時掃描彈出視窗 (wnd[1], wnd[2], ...)
    for wnd_idx in range(1, 5):
        try:
            popup = session.FindById(f"wnd[{wnd_idx}]")
            popup_elements = []
            _traverse_children(popup, popup_elements)
            if popup_elements:
                result[f"popup_wnd{wnd_idx}"] = {
                    "title": _safe_get_attr(popup, "Text", ""),
                    "elements": popup_elements,
                }
        except Exception:
            break  # 沒有更多視窗了

    return result


# ===== Actor: 操作工具 =====

def _read_status_bar(session):
    """讀取狀態列（操作完成後的回饋）"""
    try:
        sbar = session.FindById("wnd[0]/sbar")
        return {
            "type": _safe_get_attr(sbar, "MessageType", ""),
            "text": _safe_get_attr(sbar, "Text", ""),
        }
    except Exception:
        return {"type": "", "text": ""}


def _is_sensitive_action(element_id, text=""):
    """
    判斷是否為敏感操作（需要人工確認）。

    檢查按鈕的 ID 或文字是否包含敏感關鍵字。
    """
    check_str = f"{element_id} {text}".lower()
    return any(keyword in check_str for keyword in SENSITIVE_KEYWORDS)


def set_text(session, element_id: str, value: str) -> dict:
    """
    在 SAP 畫面指定欄位中填入文字值。

    Args:
        session: SAP Session COM 物件
        element_id: SAP 元件 ID (例如 "wnd[0]/usr/ctxtVBAK-AUART")
        value: 要填入的文字值

    Returns:
        dict: 操作結果
    """
    try:
        element = session.FindById(element_id)
        element.Text = value
        status = _read_status_bar(session)
        return {
            "success": True,
            "action": "set_text",
            "element_id": element_id,
            "value": value,
            "status_bar": status,
        }
    except com_error as e:
        return {
            "success": False,
            "action": "set_text",
            "element_id": element_id,
            "error": f"填值失敗: {e}。可能原因: 元件 ID 不存在或欄位不可編輯。",
        }


def click(session, element_id: str) -> dict:
    """
    點擊 SAP 畫面上的按鈕或選擇項目。

    如果偵測到敏感操作（Save/Delete 等），會標記 requires_confirmation。

    Args:
        session: SAP Session COM 物件
        element_id: SAP 元件 ID (例如 "wnd[0]/tbar[0]/btn[11]")

    Returns:
        dict: 操作結果
    """
    try:
        element = session.FindById(element_id)
        element_text = ""
        element_tooltip = ""

        element_text = _safe_get_attr(element, "Text", "") or ""
        element_tooltip = _safe_get_attr(element, "Tooltip", "") or ""

        # 檢查是否為敏感操作
        if _is_sensitive_action(element_id, f"{element_text} {element_tooltip}"):
            return {
                "success": False,
                "action": "click",
                "element_id": element_id,
                "element_text": element_text,
                "element_tooltip": element_tooltip,
                "requires_confirmation": True,
                "message": f"⚠️ 偵測到敏感操作: {element_tooltip or element_text}，需要使用者確認",
            }

        element.Press()
        status = _read_status_bar(session)
        return {
            "success": True,
            "action": "click",
            "element_id": element_id,
            "element_text": element_text,
            "status_bar": status,
        }

    except com_error as e:
        # 有些元件用 Select() 而非 Press()
        try:
            element = session.FindById(element_id)
            element.Select()
            status = _read_status_bar(session)
            return {
                "success": True,
                "action": "click (select)",
                "element_id": element_id,
                "status_bar": status,
            }
        except com_error:
            return {
                "success": False,
                "action": "click",
                "element_id": element_id,
                "error": f"點擊失敗: {e}。可能原因: 元件 ID 不存在或按鈕不可用。",
            }


def confirmed_click(session, element_id: str) -> dict:
    """
    強制執行點擊（已獲得使用者確認後呼叫）。

    跳過敏感操作檢查，直接執行點擊。

    Args:
        session: SAP Session COM 物件
        element_id: SAP 元件 ID

    Returns:
        dict: 操作結果
    """
    try:
        element = session.FindById(element_id)
        element.Press()
        status = _read_status_bar(session)
        return {
            "success": True,
            "action": "confirmed_click",
            "element_id": element_id,
            "status_bar": status,
        }
    except com_error as e:
        try:
            element = session.FindById(element_id)
            element.Select()
            status = _read_status_bar(session)
            return {
                "success": True,
                "action": "confirmed_click (select)",
                "element_id": element_id,
                "status_bar": status,
            }
        except com_error:
            return {
                "success": False,
                "action": "confirmed_click",
                "element_id": element_id,
                "error": f"點擊失敗: {e}",
            }


def send_vkey(session, vkey: int) -> dict:
    """
    發送虛擬按鍵到 SAP 畫面。

    常用按鍵：
        0  = Enter
        2  = F2
        3  = F3 (Back)
        8  = F8 (Execute)
        11 = Ctrl+S (Save)
        12 = F12 (Cancel)

    Args:
        session: SAP Session COM 物件
        vkey: 虛擬按鍵編號

    Returns:
        dict: 操作結果
    """
    vkey_names = {
        0: "Enter", 2: "F2", 3: "F3 (Back)", 4: "F4 (Help)",
        5: "F5", 6: "F6", 7: "F7", 8: "F8 (Execute)",
        11: "Ctrl+S (Save)", 12: "F12 (Cancel)",
    }

    # Save (vkey=11) 為敏感操作
    if vkey == 11:
        return {
            "success": False,
            "action": "send_vkey",
            "vkey": vkey,
            "vkey_name": vkey_names.get(vkey, f"VKey-{vkey}"),
            "requires_confirmation": True,
            "message": f"⚠️ 偵測到敏感操作: Ctrl+S (Save)，需要使用者確認",
        }

    try:
        window = session.FindById("wnd[0]")
        window.SendVKey(vkey)
        status = _read_status_bar(session)
        return {
            "success": True,
            "action": "send_vkey",
            "vkey": vkey,
            "vkey_name": vkey_names.get(vkey, f"VKey-{vkey}"),
            "status_bar": status,
        }
    except com_error as e:
        return {
            "success": False,
            "action": "send_vkey",
            "vkey": vkey,
            "error": f"發送按鍵失敗: {e}",
        }


def confirmed_send_vkey(session, vkey: int) -> dict:
    """
    強制發送虛擬按鍵（已獲得使用者確認後呼叫）。

    Args:
        session: SAP Session COM 物件
        vkey: 虛擬按鍵編號

    Returns:
        dict: 操作結果
    """
    try:
        window = session.FindById("wnd[0]")
        window.SendVKey(vkey)
        status = _read_status_bar(session)
        return {
            "success": True,
            "action": "confirmed_send_vkey",
            "vkey": vkey,
            "status_bar": status,
        }
    except com_error as e:
        return {
            "success": False,
            "action": "confirmed_send_vkey",
            "vkey": vkey,
            "error": f"發送按鍵失敗: {e}",
        }


def set_tcode(session, tcode: str) -> dict:
    """
    在 T-Code 欄位輸入交易代碼並按 Enter 執行。

    Args:
        session: SAP Session COM 物件
        tcode: 交易代碼 (例如 "VA01", "SE11")

    Returns:
        dict: 操作結果
    """
    try:
        okcode = session.FindById("wnd[0]/tbar[0]/okcd")
        okcode.Text = tcode
        window = session.FindById("wnd[0]")
        window.SendVKey(0)  # Enter
        status = _read_status_bar(session)
        return {
            "success": True,
            "action": "set_tcode",
            "tcode": tcode,
            "status_bar": status,
        }
    except com_error as e:
        return {
            "success": False,
            "action": "set_tcode",
            "tcode": tcode,
            "error": f"執行 T-Code 失敗: {e}",
        }


# ===== Tool Schema: OpenAI Function Calling 格式 =====

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "set_text",
            "description": "在 SAP 畫面指定欄位中填入文字值。使用 scan 結果中的 element id 來指定目標欄位。",
            "parameters": {
                "type": "object",
                "properties": {
                    "element_id": {
                        "type": "string",
                        "description": "SAP 元件 ID，例如 'wnd[0]/usr/ctxtVBAK-AUART'。從畫面掃描結果的 id 欄位取得。",
                    },
                    "value": {
                        "type": "string",
                        "description": "要填入的文字值",
                    },
                },
                "required": ["element_id", "value"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "click",
            "description": "點擊 SAP 畫面上的按鈕或選擇項目。敏感操作（如 Save、Delete）會需要使用者確認。",
            "parameters": {
                "type": "object",
                "properties": {
                    "element_id": {
                        "type": "string",
                        "description": "SAP 按鈕的元件 ID，例如 'wnd[0]/tbar[0]/btn[11]'",
                    },
                },
                "required": ["element_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "send_vkey",
            "description": (
                "發送虛擬按鍵到 SAP 畫面。"
                "常用按鍵: 0=Enter, 3=F3(Back), 8=F8(Execute), 11=Ctrl+S(Save), 12=F12(Cancel)。"
                "Save (vkey=11) 會需要使用者確認。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "vkey": {
                        "type": "integer",
                        "description": "虛擬按鍵編號 (0=Enter, 3=F3/Back, 8=F8/Execute, 11=Ctrl+S/Save, 12=F12/Cancel)",
                    },
                },
                "required": ["vkey"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_tcode",
            "description": "在 T-Code 欄位輸入交易代碼並按 Enter 執行。用於切換 SAP 交易畫面。",
            "parameters": {
                "type": "object",
                "properties": {
                    "tcode": {
                        "type": "string",
                        "description": "SAP 交易代碼，例如 'VA01' (建立銷售訂單), 'SE11' (資料字典)",
                    },
                },
                "required": ["tcode"],
            },
        },
    },
]

# Tool 名稱到函數的映射（供 LLM Agent 呼叫）
TOOL_FUNCTIONS = {
    "set_text": set_text,
    "click": click,
    "send_vkey": send_vkey,
    "set_tcode": set_tcode,
}
