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

import time

from pywintypes import com_error

try:
    import win32clipboard
    import win32con
    import win32com.client
except Exception:
    win32clipboard = None
    win32con = None
    win32com = None


MAX_WINDOW_SCAN = 6


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


def _short_window_id(value):
    """將 /app/con[0]/ses[0]/wnd[1] 正規化成 wnd[1]。"""
    text = str(value or "")
    start = text.find("wnd[")
    if start < 0:
        return text

    end = text.find("]", start)
    if end < 0:
        return text[start:]

    return text[start:end + 1]


def _window_id(window):
    """取得 SAP 視窗 ID，失敗時回傳空字串。"""
    return _short_window_id(_safe_get_attr(window, "Id", "") or "")


def _normalize_text(value):
    """用於比對 UI 文字的簡單正規化。"""
    return str(value or "").strip().lower()


def _safe_find_by_id(session, element_id):
    """FindById 的薄包裝，讓呼叫端可以用 None 判斷找不到。"""
    try:
        return session.FindById(element_id)
    except Exception:
        return None


def _wait_for_session_ready(session, timeout=3.0):
    """等待 SAP 畫面切換完成，避免下一次 scan 讀到半切換狀態。"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if not _safe_get_attr(session, "Busy", False):
                return
        except Exception:
            return
        time.sleep(0.1)


def _get_window(session, window_id=""):
    """取得指定視窗；未指定時優先回傳 SAP 目前活動視窗。"""
    if window_id:
        window = _safe_find_by_id(session, window_id)
        if window:
            return window

    active_window = _safe_get_attr(session, "ActiveWindow", None)
    if active_window:
        return active_window

    return _safe_find_by_id(session, "wnd[0]")


def _get_active_window_id(session):
    """取得目前活動視窗 ID。SAP modal 彈窗通常會是 wnd[1]。"""
    window = _get_window(session)
    return _window_id(window)


def _find_open_windows(session):
    """列出目前可用的 SAP 視窗，包含主視窗與 modal 彈窗。"""
    windows = []
    active_id = _get_active_window_id(session)

    for wnd_idx in range(MAX_WINDOW_SCAN):
        window_id = f"wnd[{wnd_idx}]"
        window = _safe_find_by_id(session, window_id)
        if not window:
            if wnd_idx == 0:
                continue
            break

        windows.append({
            "id": window_id,
            "title": _safe_get_attr(window, "Text", "") or "",
            "type": _get_element_type_name(window),
            "active": window_id == active_id,
        })

    return windows


def _active_popup_window(session):
    """取得目前活動彈窗；若 ActiveWindow 不可靠，退回第一個存在的 wnd[1+]。"""
    active_window = _get_window(session)
    active_id = _window_id(active_window)
    if active_id.startswith("wnd[") and active_id != "wnd[0]":
        return active_window

    for wnd_idx in range(1, MAX_WINDOW_SCAN):
        popup = _safe_find_by_id(session, f"wnd[{wnd_idx}]")
        if popup:
            return popup

    return None


def _resolve_window_id(session, window_id=""):
    """回傳指定視窗 ID；未指定時優先使用活動彈窗，再退回活動視窗。"""
    if window_id:
        return window_id

    popup = _active_popup_window(session)
    if popup:
        return _window_id(popup)

    return _get_active_window_id(session) or "wnd[0]"

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


def _iter_collection(collection, max_items=50):
    """安全走訪 SAP COM collection，支援 Children/Entries 這類集合。"""
    if not collection:
        return

    try:
        count = min(int(_safe_get_attr(collection, "Count", 0) or 0), max_items)
    except Exception:
        return

    for index in range(count):
        try:
            yield collection(index)
        except Exception:
            continue


def _extract_combo_options(element, max_options=50):
    """讀取 GuiComboBox 的下拉選項，若 SAP GUI 不提供則回傳空 list。"""
    options = []
    seen = set()

    for collection_name in ("Entries", "Items"):
        collection = _safe_get_attr(element, collection_name, None)
        for item in _iter_collection(collection, max_options):
            key = _safe_get_attr(item, "Key", "") or ""
            value = (
                _safe_get_attr(item, "Value", "")
                or _safe_get_attr(item, "Text", "")
                or _safe_get_attr(item, "Name", "")
                or ""
            )
            item_id = _safe_get_attr(item, "Id", "") or ""
            option = {
                "key": str(key),
                "text": str(value),
            }
            if item_id:
                option["id"] = str(item_id)

            dedupe_key = (option["key"], option["text"])
            if dedupe_key in seen or (not option["key"] and not option["text"]):
                continue
            seen.add(dedupe_key)
            options.append(option)

    return options


def _is_editor_like_info(type_name, elem_id="", name="", tooltip=""):
    haystack = f"{type_name} {elem_id} {name} {tooltip}".lower()
    return (
        "guiabapeditor" in haystack
        or "guitextedit" in haystack
        or "editor" in haystack
        or "textedit" in haystack
        or "txeditor" in haystack
        or ("guishell" in haystack and "shellcont/shell" in haystack)
    )


def _is_never_editor_type(type_name):
    return str(type_name) in {
        "GuiButton",
        "GuiCheckBox",
        "GuiComboBox",
        "GuiLabel",
        "GuiMenu",
        "GuiMenubar",
        "GuiRadioButton",
        "GuiStatusbar",
        "GuiTextField",
        "GuiToolbar",
    }


def _callable_member(element, name):
    member = _safe_get_attr(element, name, None)
    return member if callable(member) else None


def _has_callable_member(element, *names):
    return any(_callable_member(element, name) for name in names)


def _detect_editor_capabilities(element):
    """回傳 editor 控制元件可用的讀寫能力。"""
    capability_names = (
        "LineCount",
        "GetLineText",
        "InsertText",
        "SelectAll",
        "ReplaceSelection",
        "SetSelectionIndexes",
        "GetUnprotectedTextPart",
        "SetUnprotectedTextPart",
    )
    capabilities = {}
    for name in capability_names:
        value = _safe_get_attr(element, name, None)
        capabilities[name] = callable(value) or value is not None
    return capabilities


def _is_abap_editor_control(element, elem_id="", name="", tooltip=""):
    type_name = _get_element_type_name(element)
    if _is_never_editor_type(type_name):
        return False

    haystack = f"{type_name} {elem_id} {name} {tooltip}".lower()
    if "guiabapeditor" in haystack or "guitextedit" in haystack:
        return True

    has_reader = _has_callable_member(
        element,
        "GetLineText",
        "GetUnprotectedTextPart",
    )
    has_writer = _has_callable_member(
        element,
        "InsertText",
        "ReplaceSelection",
        "SetUnprotectedTextPart",
    )
    line_count = _line_count(element)

    if _is_editor_like_info(type_name, elem_id, name, tooltip):
        return has_reader or has_writer or line_count is not None

    return bool(has_reader and (has_writer or line_count is not None))


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
        name = _safe_get_attr(element, "Name", "") or ""
        text = _safe_get_attr(element, "Text", "") or ""
        tooltip = _safe_get_attr(element, "Tooltip", "") or ""
        changeable = _safe_get_attr(element, "Changeable", False) or False
        icon_name = _safe_get_attr(element, "IconName", "") or ""
        value = _safe_get_attr(element, "Value", None)
        key = _safe_get_attr(element, "Key", None)
        required = _safe_get_attr(element, "Required", None)
        selected = _safe_get_attr(element, "Selected", None)
        left = _safe_get_attr(element, "Left", None)
        top = _safe_get_attr(element, "Top", None)
        width = _safe_get_attr(element, "Width", None)
        height = _safe_get_attr(element, "Height", None)

        # 過濾空白標籤（節省 Token）
        if type_name == "GuiLabel" and not str(text).strip() and not str(tooltip).strip():
            return None

        info = {
            "id": elem_id,
            "type": type_name,
        }

        if _is_abap_editor_control(element, elem_id, name, tooltip):
            info["role"] = "editor"
            info["editor_capabilities"] = _detect_editor_capabilities(element)
        if name:
            info["name"] = str(name)
        if text:
            info["text"] = str(text)
        if tooltip:
            info["tooltip"] = str(tooltip)
        if icon_name:
            info["icon_name"] = str(icon_name)
        if value is not None and value != "":
            info["value"] = str(value)
        if key is not None and key != "":
            info["key"] = str(key)
        if required is not None:
            info["required"] = bool(required)
        if selected is not None:
            info["selected"] = bool(selected)
        if type_name == "GuiComboBox":
            options = _extract_combo_options(element)
            if options:
                info["options"] = options
        if changeable:
            info["changeable"] = True
        if left is not None and top is not None:
            info["position"] = {
                "left": left,
                "top": top,
                "width": width,
                "height": height,
            }

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


def _action_keywords():
    """常見 SAP 彈窗按鈕語意對照。"""
    return {
        "save": {
            "save", "儲存", "保存", "sichern", "speichern", "icon_save",
            "btn[11]", "btns", "存檔",
        },
        "ok": {
            "ok", "enter", "continue", "繼續", "確認", "確定", "是",
            "yes", "accept", "green check", "勾選", "套用", "apply",
            "icon_okay", "icon_accept", "btn[0]",
        },
        "cancel": {
            "cancel", "取消", "中止", "否", "no", "放棄", "abort",
            "icon_cancel", "btn[12]",
        },
        "close": {
            "close", "關閉", "結束", "exit", "back", "返回",
            "icon_close", "btn[15]",
        },
    }


def _infer_element_action(element_info):
    """從元件 ID、文字、tooltip、icon 推斷按鈕語意。"""
    haystack = " ".join(str(element_info.get(key, "")) for key in (
        "id", "name", "text", "tooltip", "icon_name",
    )).lower()

    for action, keywords in _action_keywords().items():
        if any(keyword in haystack for keyword in keywords):
            return action

    return ""


def _infer_popup_actions(elements):
    """整理彈窗內可點擊元件的高階 action 提示。"""
    actions = []
    seen = set()

    for elem in elements:
        elem_id = elem.get("id", "")
        elem_type = elem.get("type", "")
        if not elem_id or ("Button" not in elem_type and "/btn" not in elem_id):
            continue

        action = _infer_element_action(elem)
        if not action:
            continue

        key = (action, elem_id)
        if key in seen:
            continue
        seen.add(key)

        label = elem.get("tooltip") or elem.get("text") or elem.get("icon_name") or action
        actions.append({
            "action": action,
            "id": elem_id,
            "label": label,
        })

    return actions


def _element_text(elem):
    """取出元件對人可讀的文字。"""
    return str(
        elem.get("text")
        or elem.get("value")
        or elem.get("tooltip")
        or elem.get("name")
        or ""
    ).strip()


def _elem_position(elem):
    return elem.get("position") or {}


def _field_sort_key(elem):
    pos = _elem_position(elem)
    return (pos.get("top", 9999), pos.get("left", 9999), elem.get("id", ""))


def _nearest_left_label(field, labels):
    """找同列左側最近 label，適合 SAP dialog 的 label + field 版面。"""
    field_pos = _elem_position(field)
    field_left = field_pos.get("left")
    field_top = field_pos.get("top")
    if field_left is None or field_top is None:
        return None

    best = None
    best_score = None
    for label in labels:
        text = _element_text(label)
        if not text:
            continue

        label_pos = _elem_position(label)
        label_left = label_pos.get("left")
        label_top = label_pos.get("top")
        label_width = label_pos.get("width") or 0
        if label_left is None or label_top is None:
            continue

        label_right = label_left + label_width
        if label_right > field_left + 2:
            continue

        row_gap = abs(label_top - field_top)
        if row_gap > 8:
            continue

        horizontal_gap = abs(field_left - label_right)
        vertical_penalty = row_gap * 100
        if label_top > field_top + 4:
            vertical_penalty += 500
        score = vertical_penalty + horizontal_gap
        if best_score is None or score < best_score:
            best = label
            best_score = score

    return best


def _extract_form_fields(elements, focused_id=""):
    """將可編輯元件整理成含 label/value/focus 的表單欄位摘要。"""
    labels = [elem for elem in elements if "Label" in elem.get("type", "")]
    fields = []

    for elem in sorted(elements, key=_field_sort_key):
        if not _is_editable_element(elem) or not elem.get("id"):
            continue

        label = _nearest_left_label(elem, labels)
        label_text = _element_text(label) if label else ""
        text_value = str(elem.get("text", elem.get("value", "")) or "")
        field = {
            "id": elem.get("id", ""),
            "type": elem.get("type", ""),
            "label": label_text,
            "value": text_value,
            "empty": text_value.strip() == "",
            "changeable": bool(elem.get("changeable")),
            "focused": elem.get("id") == focused_id,
            "dropdown": "ComboBox" in elem.get("type", ""),
        }

        for key in ("name", "tooltip", "key", "required", "selected", "position", "options"):
            if key in elem:
                field[key] = elem[key]

        fields.append(field)

    return fields


def _extract_window_messages(elements):
    """抽取彈窗裡的短訊息文字，避免錯誤 dialog 只剩一堆 GuiLabel。"""
    messages = []
    seen = set()

    for elem in elements:
        elem_type = elem.get("type", "")
        text = _element_text(elem)
        if not text:
            continue
        if elem_type not in {"GuiLabel", "GuiStatusbar"} and "Label" not in elem_type:
            continue
        if text in seen:
            continue
        seen.add(text)
        messages.append({
            "text": text,
            "id": elem.get("id", ""),
            "type": elem_type,
            "position": elem.get("position", {}),
        })

    return messages


def _extract_editors(elements):
    """抽取 ABAP/text editor 這類 GuiShell 控件。"""
    editors = []
    for elem in elements:
        if elem.get("role") != "editor":
            continue
        editor = {
            "id": elem.get("id", ""),
            "type": elem.get("type", ""),
            "name": elem.get("name", ""),
            "tooltip": elem.get("tooltip", ""),
            "position": elem.get("position", {}),
        }
        if "editor_capabilities" in elem:
            editor["editor_capabilities"] = elem["editor_capabilities"]
        editors.append(editor)
    return editors


def _get_focus_element(session):
    """盡量取得目前焦點元件；SAP GUI 版本不同，屬性名稱可能不同。"""
    containers = [session, _get_window(session)]
    active_window = _get_window(session)
    active_id = _window_id(active_window)
    if active_id:
        containers.append(_safe_find_by_id(session, active_id))
    containers.append(_safe_find_by_id(session, "wnd[0]"))

    for container in containers:
        if not container:
            continue
        for attr in ("GuiFocus", "SystemFocus", "Focus"):
            focus = _safe_get_attr(container, attr, None)
            if focus:
                info = _extract_element_info(focus)
                if info and info.get("id"):
                    info["id"] = _short_element_id(info["id"])
                    return info

    return None


def _short_element_id(value):
    """將 /app/con[0]/ses[0]/wnd[1]/usr/... 正規化成 wnd[1]/usr/...。"""
    text = str(value or "")
    start = text.find("wnd[")
    if start < 0:
        return text
    return text[start:]


def _normalize_element_infos(elements):
    for elem in elements:
        if elem.get("id"):
            elem["id"] = _short_element_id(elem["id"])


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
        "active_window": "",
        "windows": [],
        "focused_element": None,
        "active_popup": None,
        "fields": [],
        "messages": [],
        "editors": [],
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
        result["active_window"] = _get_active_window_id(session)
        result["windows"] = _find_open_windows(session)
        result["focused_element"] = _get_focus_element(session)
    except Exception:
        pass

    focused_id = (result.get("focused_element") or {}).get("id", "")

    try:
        # 遍歷 DOM 樹
        window = session.FindById("wnd[0]")
        _traverse_children(window, result["elements"])
        _normalize_element_infos(result["elements"])
        result["fields"] = _extract_form_fields(result["elements"], focused_id)
        result["messages"] = _extract_window_messages(result["elements"])
        result["editors"] = _extract_editors(result["elements"])
    except Exception as e:
        result["scan_error"] = f"DOM 遍歷失敗: {e}"

    # 同時掃描彈出視窗 (wnd[1], wnd[2], ...)
    popup_infos = []
    for wnd_idx in range(1, MAX_WINDOW_SCAN):
        try:
            window_id = f"wnd[{wnd_idx}]"
            popup = session.FindById(window_id)
            popup_elements = []
            _traverse_children(popup, popup_elements)
            _normalize_element_infos(popup_elements)
            popup_info = {
                "id": window_id,
                "title": _safe_get_attr(popup, "Text", ""),
                "active": result.get("active_window") == window_id,
                "elements": popup_elements,
                "actions": _infer_popup_actions(popup_elements),
                "fields": _extract_form_fields(popup_elements, focused_id),
                "messages": _extract_window_messages(popup_elements),
                "editors": _extract_editors(popup_elements),
                "focused_element": result.get("focused_element")
                if focused_id.startswith(f"{window_id}/")
                else None,
            }
            result[f"popup_wnd{wnd_idx}"] = popup_info
            popup_infos.append(popup_info)
        except Exception:
            break  # 沒有更多視窗了

    if popup_infos:
        active_popup = next((popup for popup in popup_infos if popup.get("active")), None)
        result["active_popup"] = active_popup or popup_infos[-1]

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


def _normalize_element_id(session, element_id):
    """允許工具使用相對於活動視窗的元件 ID。"""
    if not element_id:
        return element_id
    if element_id.startswith("wnd[") or element_id.startswith("/app/"):
        return element_id

    window_id = _resolve_window_id(session)
    if element_id.startswith("/"):
        return f"{window_id}{element_id}"
    return element_id


def _is_editable_element(element_info):
    """判斷掃描結果中的元件是否像可輸入欄位。"""
    elem_type = element_info.get("type", "")
    return (
        element_info.get("changeable")
        or "TextField" in elem_type
        or "CTextField" in elem_type
        or "PasswordField" in elem_type
        or "ComboBox" in elem_type
    )


def _scan_window_elements(window):
    """掃描單一視窗的子元件。"""
    elements = []
    _traverse_children(window, elements)
    return elements


def _find_field_by_label(elements, field_label=""):
    """
    依 label 找彈窗欄位。

    SAP 常見 dialog 會用 label + 右側 input 排版，欄位本身不一定有語意化名稱。
    因此先比對 id/name/text/tooltip，找不到再用 label 的座標找同列右側最近欄位。
    """
    editable = [elem for elem in elements if _is_editable_element(elem) and elem.get("id")]
    if not editable:
        return None

    label = _normalize_text(field_label)
    if not label:
        return editable[0]

    for elem in editable:
        haystack = _normalize_text(" ".join(str(elem.get(key, "")) for key in (
            "id", "name", "text", "tooltip",
        )))
        if label and label in haystack:
            return elem

    matching_labels = []
    for elem in elements:
        if "Label" not in elem.get("type", ""):
            continue
        haystack = _normalize_text(f"{elem.get('text', '')} {elem.get('tooltip', '')}")
        if label in haystack:
            matching_labels.append(elem)

    best = None
    best_score = None
    for label_elem in matching_labels:
        label_pos = label_elem.get("position") or {}
        label_left = label_pos.get("left")
        label_top = label_pos.get("top")
        if label_left is None or label_top is None:
            continue

        for field in editable:
            field_pos = field.get("position") or {}
            field_left = field_pos.get("left")
            field_top = field_pos.get("top")
            if field_left is None or field_top is None:
                continue

            same_row_penalty = abs(field_top - label_top)
            right_side_penalty = 0 if field_left >= label_left else 1000
            distance_penalty = abs(field_left - label_left)
            score = same_row_penalty * 10 + right_side_penalty + distance_penalty
            if best_score is None or score < best_score:
                best = field
                best_score = score

    return best or editable[0]


def _press_or_select(element):
    """SAP 元件有些支援 Press，有些只支援 Select。"""
    try:
        element.Press()
        return "press"
    except Exception:
        element.Select()
        return "select"


def _send_vkey_to_window(session, vkey, window_id=""):
    window = _get_window(session, window_id)
    if not window:
        raise RuntimeError("找不到可接收按鍵的 SAP 視窗")
    target_window = _window_id(window) or window_id or "wnd[0]"
    window.SendVKey(vkey)
    return target_window


def _find_popup_action_element(elements, action):
    """在彈窗元素中找出指定 action 的按鈕。"""
    wanted = _normalize_text(action)
    candidates = []

    for elem in elements:
        elem_id = elem.get("id", "")
        elem_type = elem.get("type", "")
        if not elem_id or ("Button" not in elem_type and "/btn" not in elem_id):
            continue
        inferred = _infer_element_action(elem)
        if inferred == wanted:
            return elem
        candidates.append(elem)

    # 使用者也可能傳「確定」「儲存」等 label。
    for elem in candidates:
        haystack = _normalize_text(" ".join(str(elem.get(key, "")) for key in (
            "id", "name", "text", "tooltip", "icon_name",
        )))
        if wanted and wanted in haystack:
            return elem

    return None


def _combo_option_matches(option, wanted):
    wanted_norm = _normalize_text(wanted)
    if not wanted_norm:
        return False

    key = _normalize_text(option.get("key", ""))
    text = _normalize_text(option.get("text", ""))
    return wanted_norm in {key, text} or wanted_norm in text


def _match_combo_option(element, desired):
    options = _extract_combo_options(element)
    for option in options:
        if _combo_option_matches(option, desired):
            return option, options
    return None, options


def _set_combo_value(element, desired):
    """選取 GuiComboBox 值，優先使用 option key。"""
    desired = str(desired or "").strip()
    if not desired:
        raise ValueError("ComboBox 選取值不可為空")

    matched_option, options = _match_combo_option(element, desired)
    candidates = []
    if matched_option:
        candidates.extend([
            matched_option.get("key", ""),
            matched_option.get("text", ""),
        ])
    candidates.append(desired)

    errors = []
    for candidate in [item for item in candidates if item != ""]:
        for attr in ("Key", "Text"):
            try:
                setattr(element, attr, candidate)
                return {
                    "selected_key": _safe_get_attr(element, "Key", "") or candidate,
                    "selected_text": _safe_get_attr(element, "Text", "") or candidate,
                    "matched_option": matched_option,
                    "options": options,
                    "method": attr,
                }
            except Exception as e:
                errors.append(f"{attr}={candidate}: {e}")

    raise RuntimeError("; ".join(errors) or "無法設定 ComboBox")


def _get_clipboard_text():
    if not win32clipboard or not win32con:
        return None

    try:
        win32clipboard.OpenClipboard()
        try:
            if win32clipboard.IsClipboardFormatAvailable(win32con.CF_UNICODETEXT):
                return win32clipboard.GetClipboardData(win32con.CF_UNICODETEXT)
            return None
        finally:
            win32clipboard.CloseClipboard()
    except Exception:
        return None


def _set_clipboard_text(text):
    if not win32clipboard or not win32con:
        raise RuntimeError("win32clipboard 不可用，無法使用剪貼簿貼上")

    win32clipboard.OpenClipboard()
    try:
        win32clipboard.EmptyClipboard()
        win32clipboard.SetClipboardData(win32con.CF_UNICODETEXT, str(text))
    finally:
        win32clipboard.CloseClipboard()


def _find_editor_id(session):
    screen = scan_sap_screen(session)
    active_popup = screen.get("active_popup") or {}
    if active_popup.get("editors"):
        return active_popup["editors"][0].get("id", "")
    if screen.get("editors"):
        return screen["editors"][0].get("id", "")
    return ""


def _focus_element(element):
    for method_name in ("SetFocus", "setFocus"):
        method = _safe_get_attr(element, method_name, None)
        if callable(method):
            try:
                method()
                return True
            except Exception:
                pass
    return False


def visualize_element(session, element_id: str, duration_seconds: float = 1.2, set_focus: bool = True) -> dict:
    """Focus and highlight a SAP GUI element for guided Study Mode."""
    element_id = _normalize_element_id(session, element_id)
    try:
        element = session.FindById(element_id)
        element_type = _get_element_type_name(element)

        focused = False
        focus_error = ""
        if set_focus:
            try:
                focused = _focus_element(element)
                if not focused:
                    focus_error = "SetFocus is not available or failed"
            except Exception as exc:
                focus_error = str(exc)

        caret_set = False
        try:
            text = str(_safe_get_attr(element, "Text", "") or "")
            setattr(element, "caretPosition", len(text))
            caret_set = True
        except Exception:
            try:
                text = str(_safe_get_attr(element, "Text", "") or "")
                setattr(element, "CaretPosition", len(text))
                caret_set = True
            except Exception:
                pass

        visualized = False
        visualize_error = ""
        try:
            element.Visualize(True)
            visualized = True
            if duration_seconds and duration_seconds > 0:
                time.sleep(float(duration_seconds))
        except Exception as exc:
            visualize_error = str(exc)

        return {
            "success": bool(focused or visualized),
            "action": "visualize_element",
            "element_id": element_id,
            "element_type": element_type,
            "focused": focused,
            "caret_set": caret_set,
            "visualized": visualized,
            "focus_error": focus_error,
            "visualize_error": visualize_error,
            "error": "" if focused or visualized else (visualize_error or focus_error or "SAP does not support focus/highlight for this element"),
        }
    except Exception as e:
        return {
            "success": False,
            "action": "visualize_element",
            "element_id": element_id,
            "error": f"定位或高亮失敗: {e}",
        }


def _line_count(element):
    for attr in ("LineCount", "lineCount"):
        value = _safe_get_attr(element, attr, None)
        if value is not None:
            try:
                return int(value)
            except Exception:
                pass
    for method_name in ("GetLineCount", "getLineCount"):
        method = _callable_member(element, method_name)
        if method:
            try:
                return int(method())
            except Exception:
                pass
    return None


def _read_abap_editor_native(element):
    """使用 GuiAbapEditor / GuiTextedit API 讀取 editor 文字。"""
    errors = []

    get_unprotected = _callable_member(element, "GetUnprotectedTextPart")
    if get_unprotected:
        try:
            parts = []
            for index in range(10000):
                try:
                    part = get_unprotected(index)
                except Exception:
                    break
                if part is None:
                    break
                parts.append(str(part))
            if parts:
                return "\n".join(parts), "GetUnprotectedTextPart"
        except Exception as e:
            errors.append(f"GetUnprotectedTextPart: {e}")

    get_line_text = _callable_member(element, "GetLineText")
    line_count = _line_count(element)
    if get_line_text and line_count is not None:
        for first_line in (0, 1):
            try:
                lines = []
                for line_no in range(first_line, first_line + line_count):
                    lines.append(str(get_line_text(line_no)))
                return "\n".join(lines), f"GetLineText[{first_line}]"
            except Exception as e:
                errors.append(f"GetLineText[{first_line}]: {e}")

    for attr in ("Text", "AccText"):
        try:
            value = _safe_get_attr(element, attr, None)
            if value is not None and str(value) != "":
                return str(value), attr
        except Exception as e:
            errors.append(f"{attr}: {e}")

    raise RuntimeError("; ".join(errors) or "沒有可用的 editor 讀取方法")


def _looks_like_abap_source(text):
    text = str(text or "")
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if not lines:
        return False

    upper_lines = [line.upper() for line in lines]
    source_prefixes = (
        "*",
        '"',
        "REPORT ",
        "REPORT:",
        "PROGRAM ",
        "INCLUDE ",
        "FUNCTION ",
        "CLASS ",
        "INTERFACE ",
        "FORM ",
        "MODULE ",
        "METHOD ",
        "DATA ",
        "DATA:",
        "TABLES ",
        "PARAMETERS ",
        "PARAMETERS:",
        "SELECT-OPTIONS ",
        "SELECT-OPTIONS:",
        "START-OF-SELECTION",
        "END-OF-SELECTION",
        "INITIALIZATION",
        "AT SELECTION-SCREEN",
        "WRITE ",
        "WRITE:",
        "SELECT ",
        "LOOP ",
        "IF ",
        "CASE ",
        "CALL FUNCTION",
        "CALL METHOD",
        "CREATE OBJECT",
        "TYPES ",
        "TYPES:",
        "CONSTANTS ",
        "CONSTANTS:",
    )
    source_contains = (
        " ENDCLASS ",
        " ENDFORM ",
        " ENDMETHOD ",
        " ENDIF ",
        " ENDLOOP ",
        " FROM ",
        " INTO ",
        " WHERE ",
    )

    source_hits = 0
    for line in upper_lines:
        padded = f" {line} "
        if any(line.startswith(prefix) for prefix in source_prefixes):
            source_hits += 1
        elif any(token in padded for token in source_contains):
            source_hits += 1

    lowered = "\n".join(lines).lower()
    menu_patterns = (
        "abap test cockpit",
        "abap examples",
        "with...",
    )
    if source_hits == 0 and any(pattern in lowered for pattern in menu_patterns):
        return False

    return source_hits > 0


def _write_abap_editor_native(element, text):
    """使用 GuiAbapEditor / GuiTextedit API 寫入 editor 文字。"""
    errors = []
    text = str(text)

    try:
        _focus_element(element)
    except Exception:
        pass

    select_all = _callable_member(element, "SelectAll")
    replace_selection = _callable_member(element, "ReplaceSelection")
    if select_all and replace_selection:
        try:
            select_all()
            replace_selection(text)
            return "SelectAll+ReplaceSelection"
        except Exception as e:
            errors.append(f"SelectAll+ReplaceSelection: {e}")

    set_selection = _callable_member(element, "SetSelectionIndexes")
    if set_selection and replace_selection:
        try:
            current_text, _ = _read_abap_editor_native(element)
            set_selection(0, len(current_text))
            replace_selection(text)
            return "SetSelectionIndexes+ReplaceSelection"
        except Exception as e:
            errors.append(f"SetSelectionIndexes+ReplaceSelection: {e}")

    set_unprotected = _callable_member(element, "SetUnprotectedTextPart")
    if set_unprotected:
        try:
            ok = set_unprotected(0, text)
            if ok is False:
                raise RuntimeError("SetUnprotectedTextPart returned False")
            return "SetUnprotectedTextPart"
        except Exception as e:
            errors.append(f"SetUnprotectedTextPart: {e}")

    insert_text = _callable_member(element, "InsertText")
    if insert_text:
        call_patterns = (
            (text, 1, 1),
            (text, 0, 0),
            (text,),
        )
        for args in call_patterns:
            try:
                insert_text(*args)
                return f"InsertText/{len(args)}args"
            except Exception as e:
                errors.append(f"InsertText{args}: {e}")

    raise RuntimeError("; ".join(errors) or "沒有可用的 GuiAbapEditor 寫入方法")


def _set_editor_text_native(element, text):
    """嘗試 SAP editor/shell 的原生文字 API。"""
    errors = []

    if _is_abap_editor_control(element):
        try:
            return _write_abap_editor_native(element, text)
        except Exception as e:
            errors.append(f"GuiAbapEditor: {e}")

    try:
        element.Text = text
        return "Text"
    except Exception as e:
        errors.append(f"Text: {e}")

    for method_name in (
        "SetText",
        "setText",
        "InsertText",
        "insertText",
        "SetTextAsR3Table",
        "setTextAsR3Table",
    ):
        method = _safe_get_attr(element, method_name, None)
        if not callable(method):
            continue
        try:
            if "R3Table" in method_name:
                method(str(text).splitlines())
            else:
                method(str(text))
            return method_name
        except Exception as e:
            errors.append(f"{method_name}: {e}")

    raise RuntimeError("; ".join(errors) or "沒有可用的 editor 原生寫入方法")


def _set_editor_text_clipboard(element, text):
    if not win32com:
        raise RuntimeError("win32com 不可用，無法使用 SendKeys 貼上")

    old_clipboard = _get_clipboard_text()
    _set_clipboard_text(text)

    try:
        _focus_element(element)
        shell = win32com.client.Dispatch("WScript.Shell")
        time.sleep(0.1)
        shell.SendKeys("^a")
        time.sleep(0.05)
        shell.SendKeys("{DEL}")
        time.sleep(0.05)
        shell.SendKeys("^v")
        time.sleep(0.2)
        return "clipboard"
    finally:
        if old_clipboard is not None:
            try:
                _set_clipboard_text(old_clipboard)
            except Exception:
                pass


def set_editor_text(session, text: str, element_id: str = "") -> dict:
    """
    將多行文字寫入 SAP editor / GuiShell 控件，例如 ABAP 原始碼編輯器。
    """
    try:
        if not element_id:
            element_id = _find_editor_id(session)
        if not element_id:
            return {
                "success": False,
                "action": "set_editor_text",
                "error": "找不到 editor 控件。請先確認畫面已進入 ABAP 原始碼編輯器，或提供 element_id。",
            }

        element_id = _normalize_element_id(session, element_id)
        element = session.FindById(element_id)
        element_type = _get_element_type_name(element)

        native_error = ""
        try:
            method = _set_editor_text_native(element, text)
        except Exception as e:
            native_error = str(e)
            method = _set_editor_text_clipboard(element, text)

        _wait_for_session_ready(session)
        verify = {"available": False}
        try:
            read_text, read_method = _read_abap_editor_native(element)
            verify = {
                "available": True,
                "success": read_text == str(text),
                "read_method": read_method,
                "read_length": len(read_text),
            }
            if not verify["success"]:
                verify["message"] = "寫入後讀回內容與輸入文字不完全相同"
        except Exception as e:
            verify = {"available": False, "error": str(e)}

        return {
            "success": True,
            "action": "set_editor_text",
            "element_id": element_id,
            "element_type": element_type,
            "method": method,
            "native_error": native_error,
            "line_count": len(str(text).splitlines()),
            "verify": verify,
            "status_bar": _read_status_bar(session),
        }
    except Exception as e:
        return {
            "success": False,
            "action": "set_editor_text",
            "element_id": element_id,
            "error": f"寫入 editor 失敗: {e}",
        }


def read_editor_text(session, element_id: str = "") -> dict:
    """讀取目前 SAP ABAP editor / text editor 內容。"""
    try:
        if not element_id:
            element_id = _find_editor_id(session)
        if not element_id:
            return {
                "success": False,
                "action": "read_editor_text",
                "error": "找不到 editor 控件。請先進入 SE38/ABAP 原始碼編輯器，或提供 element_id。",
            }

        element_id = _normalize_element_id(session, element_id)
        element = session.FindById(element_id)
        element_type = _get_element_type_name(element)
        text, method = _read_abap_editor_native(element)
        looks_like_source = _looks_like_abap_source(text)
        return {
            "success": True,
            "action": "read_editor_text",
            "element_id": element_id,
            "element_type": element_type,
            "method": method,
            "text": text,
            "line_count": len(text.splitlines()),
            "char_count": len(text),
            "looks_like_source": looks_like_source,
            "warning": "" if looks_like_source else "read text does not look like ABAP source; it may be a menu or non-editor shell",
        }
    except Exception as e:
        return {
            "success": False,
            "action": "read_editor_text",
            "element_id": element_id,
            "error": f"讀取 editor 失敗: {e}",
        }


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
    element_id = _normalize_element_id(session, element_id)
    try:
        element = session.FindById(element_id)
        element_type = _get_element_type_name(element)
        if _is_editor_like_info(
            element_type,
            element_id,
            _safe_get_attr(element, "Name", "") or "",
            _safe_get_attr(element, "Tooltip", "") or "",
        ):
            return {
                "success": False,
                "action": "set_text",
                "element_id": element_id,
                "element_type": element_type,
                "requires_tool": "set_editor_text",
                "error": "目標是 SAP editor / GuiShell 控件，不能用一般 set_text，請使用 set_editor_text 寫入多行原始碼。",
            }
        if element_type == "GuiComboBox":
            combo_result = _set_combo_value(element, value)
            _wait_for_session_ready(session)
            status = _read_status_bar(session)
            return {
                "success": True,
                "action": "set_text (combo)",
                "element_id": element_id,
                "value": value,
                "selected_key": combo_result.get("selected_key", ""),
                "selected_text": combo_result.get("selected_text", ""),
                "matched_option": combo_result.get("matched_option"),
                "status_bar": status,
                "message": "目標是下拉式選單，已改用 ComboBox 選取邏輯",
            }

        element.Text = value
        _wait_for_session_ready(session)
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


def select_combo(session, element_id: str, value: str = "", key: str = "", text: str = "") -> dict:
    """
    選取 SAP 下拉式選單 (GuiComboBox)。

    Args:
        session: SAP Session COM 物件
        element_id: ComboBox 元件 ID
        value: 通用選取值，可填 key 或顯示文字
        key: 明確指定 option key
        text: 明確指定顯示文字
    """
    element_id = _normalize_element_id(session, element_id)
    desired = key or text or value

    try:
        element = session.FindById(element_id)
        element_type = _get_element_type_name(element)
        if element_type != "GuiComboBox":
            return {
                "success": False,
                "action": "select_combo",
                "element_id": element_id,
                "element_type": element_type,
                "error": "目標元件不是 GuiComboBox，請改用 set_text 或 click",
            }

        combo_result = _set_combo_value(element, desired)
        _wait_for_session_ready(session)
        status = _read_status_bar(session)
        return {
            "success": True,
            "action": "select_combo",
            "element_id": element_id,
            "value": desired,
            "selected_key": combo_result.get("selected_key", ""),
            "selected_text": combo_result.get("selected_text", ""),
            "matched_option": combo_result.get("matched_option"),
            "status_bar": status,
        }
    except Exception as e:
        options = []
        try:
            options = _extract_combo_options(session.FindById(element_id))
        except Exception:
            pass
        return {
            "success": False,
            "action": "select_combo",
            "element_id": element_id,
            "value": desired,
            "options": options,
            "error": f"選取下拉式選單失敗: {e}",
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
    element_id = _normalize_element_id(session, element_id)
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

        _press_or_select(element)
        _wait_for_session_ready(session)
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
            _wait_for_session_ready(session)
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
    element_id = _normalize_element_id(session, element_id)
    try:
        element = session.FindById(element_id)
        _press_or_select(element)
        _wait_for_session_ready(session)
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
            _wait_for_session_ready(session)
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


def send_vkey(session, vkey: int, window_id: str = "") -> dict:
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
        window_id: 目標視窗 ID，未指定時自動使用目前活動視窗/彈窗

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
            "window_id": _resolve_window_id(session, window_id),
            "requires_confirmation": True,
            "message": f"⚠️ 偵測到敏感操作: Ctrl+S (Save)，需要使用者確認",
        }

    try:
        target_window = _send_vkey_to_window(session, vkey, window_id)
        _wait_for_session_ready(session)
        status = _read_status_bar(session)
        return {
            "success": True,
            "action": "send_vkey",
            "vkey": vkey,
            "vkey_name": vkey_names.get(vkey, f"VKey-{vkey}"),
            "window_id": target_window,
            "status_bar": status,
        }
    except (com_error, RuntimeError) as e:
        return {
            "success": False,
            "action": "send_vkey",
            "vkey": vkey,
            "window_id": _resolve_window_id(session, window_id),
            "error": f"發送按鍵失敗: {e}",
        }


def confirmed_send_vkey(session, vkey: int, window_id: str = "") -> dict:
    """
    強制發送虛擬按鍵（已獲得使用者確認後呼叫）。

    Args:
        session: SAP Session COM 物件
        vkey: 虛擬按鍵編號
        window_id: 目標視窗 ID，未指定時自動使用目前活動視窗/彈窗

    Returns:
        dict: 操作結果
    """
    try:
        target_window = _send_vkey_to_window(session, vkey, window_id)
        _wait_for_session_ready(session)
        status = _read_status_bar(session)
        return {
            "success": True,
            "action": "confirmed_send_vkey",
            "vkey": vkey,
            "window_id": target_window,
            "status_bar": status,
        }
    except (com_error, RuntimeError) as e:
        return {
            "success": False,
            "action": "confirmed_send_vkey",
            "vkey": vkey,
            "window_id": _resolve_window_id(session, window_id),
            "error": f"發送按鍵失敗: {e}",
        }


def handle_popup(
    session,
    action: str = "ok",
    field_label: str = "",
    value: str = "",
    window_id: str = "",
    confirmed: bool = False,
) -> dict:
    """
    處理 SAP 常見彈出視窗。

    可選擇先依 label 填入彈窗欄位，再執行 ok/save/cancel/close/enter。
    """
    action = _normalize_text(action or "ok")
    if action == "confirm":
        action = "ok"
    if action == "yes":
        action = "ok"
    if action in ("", "none", "input"):
        action = "none"

    popup = _safe_find_by_id(session, window_id) if window_id else _active_popup_window(session)
    if not popup:
        return {
            "success": False,
            "action": "handle_popup",
            "requested_action": action,
            "error": "目前沒有偵測到 SAP 彈出視窗",
        }

    target_window_id = _window_id(popup)
    elements = _scan_window_elements(popup)
    filled_field = None

    try:
        if value != "":
            target_field = _find_field_by_label(elements, field_label)
            if not target_field:
                return {
                    "success": False,
                    "action": "handle_popup",
                    "window_id": target_window_id,
                    "requested_action": action,
                    "error": f"彈窗內找不到可填寫欄位: {field_label or '(第一個可編輯欄位)'}",
                    "popup_title": _safe_get_attr(popup, "Text", "") or "",
                }

            element_id = target_field["id"]
            element = session.FindById(element_id)
            if _get_element_type_name(element) == "GuiComboBox":
                combo_result = _set_combo_value(element, value)
            else:
                element.Text = value
                combo_result = None
            _wait_for_session_ready(session)
            filled_field = {
                "id": element_id,
                "label": field_label,
                "value": value,
            }
            if combo_result:
                filled_field["selected_key"] = combo_result.get("selected_key", "")
                filled_field["selected_text"] = combo_result.get("selected_text", "")
                filled_field["matched_option"] = combo_result.get("matched_option")

        if action == "none":
            return {
                "success": True,
                "action": "handle_popup",
                "window_id": target_window_id,
                "requested_action": action,
                "filled_field": filled_field,
                "status_bar": _read_status_bar(session),
            }

        if action == "save" and not confirmed:
            return {
                "success": False,
                "action": "handle_popup",
                "window_id": target_window_id,
                "requested_action": action,
                "filled_field": filled_field,
                "requires_confirmation": True,
                "message": "⚠️ 偵測到彈窗儲存操作，需要使用者確認",
            }

        if action == "enter":
            popup.SendVKey(0)
        elif action == "cancel":
            action_elem = _find_popup_action_element(elements, "cancel")
            if action_elem:
                _press_or_select(session.FindById(action_elem["id"]))
            else:
                popup.SendVKey(12)
        elif action == "close":
            action_elem = _find_popup_action_element(elements, "close")
            if action_elem:
                _press_or_select(session.FindById(action_elem["id"]))
            else:
                try:
                    popup.Close()
                except Exception:
                    popup.SendVKey(12)
        else:
            action_elem = _find_popup_action_element(elements, action)
            if action_elem:
                _press_or_select(session.FindById(action_elem["id"]))
            elif action == "save":
                popup.SendVKey(11)
            else:
                popup.SendVKey(0)

        _wait_for_session_ready(session)
        return {
            "success": True,
            "action": "handle_popup",
            "window_id": target_window_id,
            "requested_action": action,
            "filled_field": filled_field,
            "status_bar": _read_status_bar(session),
        }

    except com_error as e:
        return {
            "success": False,
            "action": "handle_popup",
            "window_id": target_window_id,
            "requested_action": action,
            "filled_field": filled_field,
            "error": f"處理彈窗失敗: {e}",
        }


def confirmed_handle_popup(
    session,
    action: str = "ok",
    field_label: str = "",
    value: str = "",
    window_id: str = "",
) -> dict:
    """已獲使用者確認後處理彈窗敏感 action。"""
    return handle_popup(
        session=session,
        action=action,
        field_label=field_label,
        value=value,
        window_id=window_id,
        confirmed=True,
    )


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
        _wait_for_session_ready(session)
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
            "description": "在 SAP 畫面指定文字欄位中填入文字值。若目標是 GuiComboBox / dropdown，應優先使用 select_combo。",
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
            "name": "select_combo",
            "description": (
                "選取 SAP 下拉式選單 (GuiComboBox)。"
                "當 scan fields 顯示 dropdown=true、type=GuiComboBox 或欄位有 options 時，使用此工具，"
                "不要用 set_text。可用 key 或顯示文字選取。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "element_id": {
                        "type": "string",
                        "description": "GuiComboBox 元件 ID，例如 'wnd[1]/usr/cmbTYPE'。",
                    },
                    "value": {
                        "type": "string",
                        "description": "要選取的值，可填 option key 或顯示文字。",
                    },
                    "key": {
                        "type": "string",
                        "description": "可選。明確指定 option key，例如 '1' 或 'I'。",
                    },
                    "text": {
                        "type": "string",
                        "description": "可選。明確指定顯示文字，例如 '可執行程式'。",
                    },
                },
                "required": ["element_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "set_editor_text",
            "description": (
                "將多行文字寫入 SAP editor / GuiShell 控件，例如 ABAP 原始碼編輯器。"
                "當 scan 結果出現 role=editor、editors、editor_capabilities，或元件 type=GuiAbapEditor/GuiShell 且是程式碼編輯器時，"
                "使用此工具，不要用 set_text。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "要寫入 editor 的完整文字，可包含多行 ABAP 原始碼。",
                    },
                    "element_id": {
                        "type": "string",
                        "description": "可選。editor 元件 ID，例如 'wnd[0]/usr/cntlEDITOR/shellcont/shell'。未提供時自動尋找目前畫面的 editor。",
                    },
                },
                "required": ["text"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_editor_text",
            "description": (
                "讀取 SAP ABAP editor / text editor 內容。"
                "當 scan 結果出現 role=editor、editors、editor_capabilities 或 GuiAbapEditor 時使用。"
                "會優先使用 GetLineText / GetUnprotectedTextPart 等 editor API。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "element_id": {
                        "type": "string",
                        "description": "可選。editor 元件 ID。未提供時自動尋找目前畫面的 editor。",
                    },
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "visualize_element",
            "description": (
                "將 SAP GUI 元件 focus 並使用 Visualize(True) 高亮，適合 Study Mode 或需要引導使用者看見欄位時使用。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "element_id": {
                        "type": "string",
                        "description": "SAP 元件 ID，例如 'wnd[0]/usr/ctxtFACOM-KUNDE'。",
                    },
                    "duration_seconds": {
                        "type": "number",
                        "description": "可選。高亮後等待秒數，預設 1.2。",
                    },
                    "set_focus": {
                        "type": "boolean",
                        "description": "可選。是否先嘗試 SetFocus，預設 true。",
                    },
                },
                "required": ["element_id"],
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
                    "window_id": {
                        "type": "string",
                        "description": "可選。目標視窗 ID，例如 'wnd[1]'。未提供時會自動使用目前活動視窗或彈窗。",
                    },
                },
                "required": ["vkey"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "handle_popup",
            "description": (
                "處理 SAP 常見彈出視窗。可先依欄位標籤填值，再執行 ok/save/cancel/close/enter。"
                "當 scan 結果出現 active_popup 或 popup_wnd1 時，優先使用此工具處理彈窗。"
                "例如 ABAP Program Attributes 視窗可用 field_label='標題', value='...', action='save'。"
                "save 會需要使用者確認。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["ok", "save", "cancel", "close", "enter", "none"],
                        "description": "彈窗處理動作。none 表示只填值不按任何按鈕。",
                    },
                    "field_label": {
                        "type": "string",
                        "description": "可選。要填寫的欄位標籤，例如 '標題'、'描述'、'Transport Request'。",
                    },
                    "value": {
                        "type": "string",
                        "description": "可選。要填入彈窗欄位的值。",
                    },
                    "window_id": {
                        "type": "string",
                        "description": "可選。彈窗視窗 ID，例如 'wnd[1]'。未提供時使用目前活動彈窗。",
                    },
                },
                "required": ["action"],
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
    "select_combo": select_combo,
    "set_editor_text": set_editor_text,
    "read_editor_text": read_editor_text,
    "visualize_element": visualize_element,
    "click": click,
    "send_vkey": send_vkey,
    "handle_popup": handle_popup,
    "set_tcode": set_tcode,
}
