"""
SAP GUI 背景監控器 (Polling-based)

在背景執行緒中定期快照 SAP 畫面狀態，比對差異來偵測使用者操作。
Stage 2 起優先使用 MCP (`mcp-sap-gui`) 擷取快照；若 MCP 不可用，
保留舊 pywin32 COM 快照作為 fallback。

偵測的事件類型：
- TCODE_CHANGE: T-Code 切換
- SCREEN_CHANGE: 同一交易中畫面跳轉
- FIELD_CHANGE: 可編輯欄位值改變
- STATUS_MESSAGE: 狀態列出現新訊息
"""

import threading
import time
import json
import hashlib
import os
import re
from datetime import datetime

try:
    import pythoncom
    import win32com.client
except Exception:  # pragma: no cover - Windows fallback dependency
    pythoncom = None
    win32com = None

from mcp_client import get_default_sync_client


MAX_WINDOW_SCAN = 6
MAX_GRID_ROWS = 100   # 每個 Grid 最多掃幾行
MAX_GRID_COLS = 30    # 每行最多掃幾欄
MAX_FIELD_SCAN_DEPTH = 12
MAX_MONITOR_FIELDS = int(os.getenv("SAP_MONITOR_MAX_FIELDS", "600"))


def env_enabled(name, default="true"):
    return str(os.getenv(name, default)).strip().lower() in {"1", "true", "yes", "y", "on"}


def env_list(name, default):
    value = os.getenv(name, default)
    return [item.strip() for item in value.split(",") if item.strip()]


MCP_MONITOR_ENABLED = env_enabled("MCP_SAP_MONITOR_ENABLED", os.getenv("MCP_SAP_ENABLED", "true"))
MCP_SESSION_INFO_TOOL_CANDIDATES = env_list(
    "MCP_SAP_SESSION_INFO_TOOLS",
    "sap_get_session_info,sap_get_current_session_info",
)
MCP_SCREEN_TOOL_CANDIDATES = env_list(
    "MCP_SAP_SCREEN_TOOLS",
    "sap_get_screen_elements,sap_get_screen,sap_scan_screen,sap_get_current_screen",
)
SAP_NATIVE_RECORD_EVENTS = env_enabled("SAP_NATIVE_RECORD_EVENTS", "true")
SAP_RECORD_CONTEXT_POLL_SECONDS = float(os.getenv("SAP_RECORD_CONTEXT_POLL_SECONDS", "0.5"))


def _patch_genpy_for_sap():
    """
    SAP GUI type library 包含非標準成員，使 gencache.EnsureModule 在
    生成 type stubs 時觸發 AssertionError（genpy.py DispatchItem.WriteClassBody）。

    策略：**只 patch gencache.EnsureModule**，讓它在遇到 AssertionError 時
    回傳 None（完全不產生 stubs），win32com 自動降級為 late-binding IDispatch。
    WithEvents 透過 IConnectionPoint 連接 SAP 事件仍可正常運作。

    不 patch genpy.DispatchItem.WriteClassBody：那樣會生成截斷的損壞 stubs
    並快取到磁碟，導致 session.findByID 等呼叫失敗。
    """
    try:
        import win32com.client.gencache as _gencache
        if getattr(_gencache, "_sap_ensure_patched", False):
            return True  # 已 patch，避免重複
        _orig_ensure = _gencache.EnsureModule
        def _safe_EnsureModule(*args, **kwargs):
            try:
                return _orig_ensure(*args, **kwargs)
            except AssertionError:
                # SAP type lib 有非標準 entry：完全放棄生成 stubs，
                # 讓 win32com 使用 late-binding（IDispatch），避免損壞快取。
                return None
        _gencache.EnsureModule = _safe_EnsureModule
        _gencache._sap_ensure_patched = True
        return True
    except Exception:
        return False



def _safe_get_attr(obj, attr, default=None):
    """安全地從 COM 物件取得屬性值（避免 hasattr 觸發 win32com 內部錯誤）"""
    try:
        return getattr(obj, attr)
    except Exception:
        return default


def _format_exception(exc):
    """保留 COM 例外在 str(exc) 為空時的診斷資訊。"""
    text = str(exc).strip()
    if text:
        return text
    args = getattr(exc, "args", ())
    return f"{type(exc).__name__}{args!r}" if args else type(exc).__name__


def _short_element_id(value):
    """將 /app/con[0]/ses[0]/wnd[1]/usr/... 正規化成 wnd[1]/usr/...。"""
    text = str(value or "")
    start = text.find("wnd[")
    if start < 0:
        return text
    return text[start:]


def _short_window_id(value):
    text = _short_element_id(value)
    end = text.find("]")
    if text.startswith("wnd[") and end >= 0:
        return text[:end + 1]
    return text


def _read_element_value(element, type_name):
    """讀取可監控元件的狀態值。"""
    if type_name in ("GuiTextField", "GuiCTextField", "GuiPasswordField", "GuiOkCodeField"):
        return _safe_get_attr(element, "Text", "")
    if type_name == "GuiComboBox":
        key = _safe_get_attr(element, "Key", "")
        text = _safe_get_attr(element, "Text", "")
        return f"{key}|{text}" if key else text
    if type_name in ("GuiCheckBox", "GuiRadioButton"):
        return str(bool(_safe_get_attr(element, "Selected", False)))
    if type_name == "GuiStatusbar":
        return _safe_get_attr(element, "Text", "")
    return None


def _is_okcode_field_id(element_id):
    return str(element_id or "").endswith("/tbar[0]/okcd") or str(element_id or "").endswith("/okcd")


def _strip_markdown_json(text):
    value = str(text or "").strip()
    if value.startswith("```"):
        value = re.sub(r"^```(?:json|text)?\s*", "", value, flags=re.IGNORECASE)
        value = re.sub(r"\s*```$", "", value)
    return value.strip()


def _try_parse_json(text):
    value = _strip_markdown_json(text)
    if not value:
        return None
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


def _iter_dicts(value):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _iter_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _iter_dicts(child)


def _first_nested_value(value, keys):
    wanted = {key.lower() for key in keys}
    for item in _iter_dicts(value):
        for key, item_value in item.items():
            if str(key).lower() in wanted and item_value not in (None, ""):
                return item_value
    return ""


def _find_first_list(value, keys):
    wanted = {key.lower() for key in keys}
    for item in _iter_dicts(value):
        for key, item_value in item.items():
            if str(key).lower() in wanted and isinstance(item_value, list):
                return item_value
    return []


def _mcp_flag_is_false(value):
    if isinstance(value, bool):
        return not value
    return str(value or "").strip().lower() in {"0", "false", "no", "off"}


def _mcp_bool_text(value):
    if isinstance(value, bool):
        return str(value)
    return "False" if str(value or "").strip().lower() in {"", "0", "false", "no", "off"} else "True"


def _is_actionable_mcp_element(item, element_id, type_name):
    short_id = _short_element_id(element_id)
    lowered_id = short_id.lower()
    lowered_type = str(type_name or "").lower()

    if item.get("visible") is not None and _mcp_flag_is_false(item.get("visible")):
        return False
    if item.get("enabled") is not None and _mcp_flag_is_false(item.get("enabled")):
        return False
    if item.get("changeable") is not None and _mcp_flag_is_false(item.get("changeable")):
        return False

    if any(fragment in lowered_id for fragment in (
        "/lbl", "/box", "/cntl", "/shellcont", "/tabs", "/tabp", "/sbar", "/titl",
    )):
        return "#r" in lowered_id

    if any(marker in lowered_type for marker in (
        "textfield", "combobox", "checkbox", "radiobutton", "okcode", "password",
    )):
        return True

    if any(marker in lowered_type for marker in (
        "label", "box", "container", "customcontrol", "shell", "button", "tab", "status",
    )):
        return "#r" in lowered_id

    leaf = lowered_id.rsplit("/", 1)[-1]
    return leaf == "okcd" or leaf.startswith(("ctxt", "txt", "pwd", "cmb", "chk", "rad"))


def _normalize_table_column_name(value):
    name = str(value or "").strip()
    lowered = name.lower()
    for prefix in ("ctxt", "txt", "pwd", "cmb", "chk", "rad"):
        if lowered.startswith(prefix):
            return name[len(prefix):]
    return name


def _parse_table_cell_id(element_id):
    short_id = _short_element_id(element_id)
    match = re.search(
        r"^(?P<table>.+?/tbl[^/]+?)/(?P<cell>[^/\[]+)\[(?P<col>\d+),(?P<row>\d+)\]$",
        short_id,
    )
    if not match:
        return None
    return {
        "table_id": match.group("table"),
        "cell_id": short_id,
        "column_index": int(match.group("col")),
        "row": int(match.group("row")),
        "column": _normalize_table_column_name(match.group("cell")),
    }


def _extract_mcp_field_values(value, max_fields=300, field_metadata=None):
    fields = {}
    candidates = []

    for key in ("fields", "elements", "screen_elements", "controls"):
        candidates.extend(_find_first_list(value, [key]))

    if isinstance(value, list):
        candidates.extend(value)

    for item in candidates:
        if len(fields) >= max_fields or not isinstance(item, dict):
            continue
        element_id = (
            item.get("id")
            or item.get("element_id")
            or item.get("elementId")
            or item.get("name")
            or ""
        )
        if not element_id:
            continue
        # 過濾 Tab 頁籤的 caption（tabsTAXI_TABSTRIP / tabpT）——這是頁籤文字，不是使用者輸入
        short_eid = _short_element_id(element_id)
        type_name = str(item.get("type") or item.get("control_type") or "")
        if not _is_actionable_mcp_element(item, short_eid, type_name):
            continue
        table_cell = _parse_table_cell_id(short_eid)
        field_id = (
            _table_cell_key(table_cell["table_id"], table_cell["row"], table_cell["column"])
            if table_cell
            else short_eid
        )

        if "value" in item and item.get("value") is not None:
            raw_value = item.get("value")
        elif "text" in item and item.get("text") is not None:
            raw_value = item.get("text")
        elif "selected" in item:
            raw_value = _mcp_bool_text(item.get("selected"))
        elif "key" in item and item.get("key") is not None:
            raw_value = item.get("key")
        else:
            raw_value = ""

        fields[field_id] = "" if raw_value is None else str(raw_value)
        if field_metadata is not None:
            field_metadata[field_id] = {
                "element_id": field_id,
                "cell_id": short_eid if table_cell else "",
                "control_type": type_name,
                "name": str(item.get("name") or ""),
                "label": str(item.get("label") or item.get("field_label") or ""),
                "tooltip": str(item.get("tooltip") or ""),
                "table_type": "GuiTableControl" if table_cell else "",
                "table_id": table_cell["table_id"] if table_cell else "",
                "row": table_cell["row"] if table_cell else None,
                "column_index": table_cell["column_index"] if table_cell else None,
                "column": table_cell["column"] if table_cell else "",
                "column_title": str(item.get("column_title") or item.get("label") or ""),
                "source": "mcp",
            }

    return fields


def _extract_mcp_window_titles(value):
    titles = {}
    windows = _find_first_list(value, ["windows", "window_titles"])
    for item in windows:
        if not isinstance(item, dict):
            continue
        window_id = item.get("id") or item.get("window_id") or item.get("windowId")
        title = item.get("title") or item.get("text") or item.get("name") or ""
        if window_id:
            titles[_short_window_id(window_id)] = str(title)
    popup = _first_nested_value(value, ["active_popup", "popup"])
    if isinstance(popup, dict):
        window_id = popup.get("id") or popup.get("window_id") or "wnd[1]"
        title = popup.get("title") or popup.get("text") or ""
        titles[_short_window_id(window_id)] = str(title)
    return titles


def _extract_from_text(patterns, text):
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return ""


class ScreenSnapshot:
    """SAP 畫面快照，用於比對前後差異"""

    def __init__(self):
        self.tcode = ""
        self.screen_number = ""
        self.title = ""
        self.program = ""
        self.active_window = ""
        self.focus_id = ""
        self.status_type = ""
        self.status_text = ""
        self.field_values = {}  # {element_id: text_value}
        self.field_metadata = {}  # {element_id: control/label/table metadata}
        self.window_titles = {}  # {wnd[n]: title}
        self.capture_error = ""

    def to_dict(self):
        return {
            "tcode": self.tcode,
            "screen_number": self.screen_number,
            "title": self.title,
            "program": self.program,
            "active_window": self.active_window,
            "focus_id": self.focus_id,
            "status_bar": {"type": self.status_type, "text": self.status_text},
            "field_values": self.field_values,
            "field_metadata": self.field_metadata,
            "window_titles": self.window_titles,
            "capture_error": self.capture_error,
        }

    @staticmethod
    def capture(session):
        """
        從 SAP Session 擷取當前畫面快照。

        Args:
            session: SAP Session COM 物件

        Returns:
            ScreenSnapshot 物件
        """
        snap = ScreenSnapshot()

        try:
            info = session.Info
            snap.tcode = _safe_get_attr(info, "Transaction", "") or ""
            snap.screen_number = str(_safe_get_attr(info, "ScreenNumber", "") or "")
            snap.title = _safe_get_attr(info, "Program", "") or ""
            snap.program = _safe_get_attr(info, "Program", "") or ""
        except Exception as e:
            snap.capture_error = f"session_info: {e}"

        try:
            window = session.FindById("wnd[0]")
            snap.title = _safe_get_attr(window, "Text", "") or ""
        except Exception as e:
            snap.capture_error = snap.capture_error or f"title: {e}"

        try:
            active_window = _safe_get_attr(session, "ActiveWindow", None)
            snap.active_window = _short_window_id(_safe_get_attr(active_window, "Id", "") or "")
        except Exception:
            pass

        try:
            focus = (
                _safe_get_attr(session, "GuiFocus", None)
                or _safe_get_attr(session, "SystemFocus", None)
            )
            snap.focus_id = _short_element_id(_safe_get_attr(focus, "Id", "") or "")
        except Exception:
            pass

        try:
            snap.window_titles = _capture_window_titles(session)
        except Exception:
            pass

        try:
            sbar = session.FindById("wnd[0]/sbar")
            snap.status_type = _safe_get_attr(sbar, "MessageType", "") or ""
            snap.status_text = _safe_get_attr(sbar, "Text", "") or ""
        except Exception as e:
            snap.capture_error = snap.capture_error or f"status_bar: {e}"

        # 擷取可編輯欄位的值（用於偵測 FIELD_CHANGE）
        try:
            snap.field_values = _capture_editable_fields(
                session, max_fields=MAX_MONITOR_FIELDS, field_metadata=snap.field_metadata
            )
        except Exception as e:
            snap.capture_error = snap.capture_error or f"fields: {e}"

        return snap

    @staticmethod
    def capture_mcp(mcp_client, tool_names=None, session=None):
        """
        從 MCP server 擷取當前畫面快照。

        mcp-sap-gui 的回傳格式可能依版本調整，因此這裡採寬鬆解析：
        JSON 優先；若只有文字，則用 regex 擷取 T-Code / screen / title。
        session 若提供，會額外用 COM 補捕 GuiGridView cell 值（MCP 不回傳 Grid 資料）。
        """
        snap = ScreenSnapshot()
        tool_names = set(tool_names or [])

        session_tool = next((name for name in MCP_SESSION_INFO_TOOL_CANDIDATES if name in tool_names), "")
        screen_tool = next((name for name in MCP_SCREEN_TOOL_CANDIDATES if name in tool_names), "")

        if not session_tool and not screen_tool:
            snap.capture_error = "MCP server did not expose supported snapshot tools"
            return snap

        raw_parts = []
        parsed_sources = []

        for tool_name in (session_tool, screen_tool):
            if not tool_name:
                continue
            try:
                raw = mcp_client.call_tool(tool_name, {})
            except Exception as e:
                snap.capture_error = snap.capture_error or f"{tool_name}: {e}"
                continue
            raw_text = str(raw or "")
            raw_parts.append(f"{tool_name}\n{raw_text}")
            parsed = _try_parse_json(raw_text)
            if parsed is not None:
                parsed_sources.append(parsed)

        raw_text = "\n\n".join(raw_parts)
        combined = parsed_sources if len(parsed_sources) != 1 else parsed_sources[0]

        if parsed_sources:
            snap.tcode = str(_first_nested_value(combined, [
                "tcode", "transaction", "transaction_code", "transactionCode",
            ]) or "")
            snap.screen_number = str(_first_nested_value(combined, [
                "screen_number", "screenNumber", "screen", "dynpro",
            ]) or "")
            snap.title = str(_first_nested_value(combined, [
                "title", "window_title", "windowTitle", "text",
            ]) or "")
            snap.program = str(_first_nested_value(combined, ["program", "program_name", "programName"]) or "")
            active_window_value = _first_nested_value(combined, [
                "active_window", "activeWindow", "window_id", "windowId",
            ])
            if isinstance(active_window_value, dict):
                active_window_value = active_window_value.get("id") or active_window_value.get("window_id") or ""
            snap.active_window = _short_window_id(active_window_value or "")

            focus_value = _first_nested_value(combined, [
                "focus_id", "focused_element", "focusedElement", "focus",
            ])
            if isinstance(focus_value, dict):
                focus_value = focus_value.get("id") or focus_value.get("element_id") or ""
            snap.focus_id = _short_element_id(focus_value or "")

            status = _first_nested_value(combined, ["status_bar", "statusBar", "status"])
            if isinstance(status, dict):
                snap.status_type = str(status.get("type") or status.get("message_type") or status.get("severity") or "")
                snap.status_text = str(status.get("text") or status.get("message") or "")
            else:
                snap.status_text = str(_first_nested_value(combined, [
                    "status_text", "statusText", "message", "status_message",
                ]) or "")
                snap.status_type = str(_first_nested_value(combined, [
                    "status_type", "statusType", "message_type",
                ]) or "")

            snap.field_values = _extract_mcp_field_values(
                combined, max_fields=MAX_MONITOR_FIELDS, field_metadata=snap.field_metadata
            )
            snap.window_titles = _extract_mcp_window_titles(combined)

        if raw_text and not snap.tcode:
            snap.tcode = _extract_from_text([
                r"T-?Code\s*[:=]\s*([A-Z0-9_/]+)",
                r"Transaction\s*[:=]\s*([A-Z0-9_/]+)",
                r"交易(?:代碼)?\s*[:=：]\s*([A-Z0-9_/]+)",
            ], raw_text)
        if raw_text and not snap.screen_number:
            snap.screen_number = _extract_from_text([
                r"Screen(?:Number)?\s*[:=]\s*([0-9]+)",
                r"畫面\s*[:=：]\s*([0-9]+)",
            ], raw_text)
        if raw_text and not snap.title:
            snap.title = _extract_from_text([
                r"Title\s*[:=]\s*(.+)",
                r"Window\s*Title\s*[:=]\s*(.+)",
                r"標題\s*[:=：]\s*(.+)",
            ], raw_text)

        if raw_text and not snap.program:
            # Internal fingerprint for debugging opaque text responses. Do not put
            # this into field_values or the recorder would treat it as user input.
            snap.program = "mcp:" + hashlib.sha1(
                raw_text.encode("utf-8", errors="ignore")
            ).hexdigest()[:12]

        # MCP 偶爾會短暫漏掉 session metadata；用既有 COM session 補齊，
        # 不額外建立連線，也不覆蓋 MCP 已取得的值。
        if session is not None:
            try:
                info = session.Info
                snap.tcode = snap.tcode or (_safe_get_attr(info, "Transaction", "") or "")
                snap.screen_number = snap.screen_number or str(
                    _safe_get_attr(info, "ScreenNumber", "") or ""
                )
                snap.program = snap.program or (_safe_get_attr(info, "Program", "") or "")
            except Exception:
                pass
            try:
                active_window = _safe_get_attr(session, "ActiveWindow", None)
                snap.active_window = snap.active_window or _short_window_id(
                    _safe_get_attr(active_window, "Id", "") or ""
                )
            except Exception:
                pass
            try:
                focus = (
                    _safe_get_attr(session, "GuiFocus", None)
                    or _safe_get_attr(session, "SystemFocus", None)
                )
                snap.focus_id = snap.focus_id or _short_element_id(
                    _safe_get_attr(focus, "Id", "") or ""
                )
            except Exception:
                pass
            try:
                com_metadata = {}
                com_fields = _capture_editable_fields(
                    session, max_fields=MAX_MONITOR_FIELDS, field_metadata=com_metadata
                )
                # COM 補充 MCP 沒有的可編輯欄位；MCP 已有的不覆蓋。
                for k, v in com_fields.items():
                    if k not in snap.field_values:
                        snap.field_values[k] = v
                    if k not in snap.field_metadata and k in com_metadata:
                        snap.field_metadata[k] = com_metadata[k]
            except Exception:
                pass

        return snap


def _capture_window_titles(session):
    titles = {}
    for wnd_idx in range(MAX_WINDOW_SCAN):
        window_id = f"wnd[{wnd_idx}]"
        try:
            window = session.FindById(window_id)
            titles[window_id] = _safe_get_attr(window, "Text", "") or ""
        except Exception:
            if wnd_idx > 0:
                break
    return titles


def _capture_editable_fields(session, max_fields=MAX_MONITOR_FIELDS, field_metadata=None):
    """Capture normal inputs plus editable Grid/TableControl cells."""
    fields = {}
    metadata = field_metadata if field_metadata is not None else {}

    for wnd_idx in range(MAX_WINDOW_SCAN):
        if len(fields) >= max_fields:
            break
        window_id = f"wnd[{wnd_idx}]"
        try:
            window = session.FindById(window_id)
            _collect_fields(window, fields, max_fields, metadata=metadata)
            _collect_tabular_cells(window, fields, max_fields, metadata=metadata)
        except Exception:
            if wnd_idx > 0:
                break

    return fields


def _column_title(table, column_index):
    try:
        columns = _safe_get_attr(table, "Columns", None)
        column = columns(column_index)
        return str(
            _safe_get_attr(column, "Title", "")
            or _safe_get_attr(column, "Tooltip", "")
            or ""
        )
    except Exception:
        return ""


def _table_cell_key(table_id, absolute_row, column_name):
    safe_column = str(column_name or "").replace("#", "_") or "column"
    return f"{table_id}#r{absolute_row}#{safe_column}"


def _read_grid_view_cells(grid, fields, max_fields, metadata=None):
    """Read editable ALV/Grid cells, including empty values used as baselines."""
    try:
        elem_id = _short_element_id(_safe_get_attr(grid, "Id", "") or "")
        if not elem_id:
            return
        row_count = int(_safe_get_attr(grid, "RowCount", 0) or 0)
        visible_rows = int(_safe_get_attr(grid, "VisibleRowCount", 0) or 0)
        first_visible_row = int(_safe_get_attr(grid, "FirstVisibleRow", 0) or 0)
        cols = list(_safe_get_attr(grid, "ColumnOrder", []) or [])
        scan_rows = visible_rows or min(row_count, MAX_GRID_ROWS)
        row_end = min(row_count, first_visible_row + min(scan_rows, MAX_GRID_ROWS))
        for row in range(first_visible_row, row_end):
            for col in cols[:MAX_GRID_COLS]:
                if len(fields) >= max_fields:
                    return
                try:
                    changeable_method = _safe_get_attr(grid, "GetCellChangeable", None)
                    changeable = True if not callable(changeable_method) else bool(changeable_method(row, col))
                    if not changeable:
                        continue
                    value = grid.GetCellValue(row, col)
                    key = _table_cell_key(elem_id, row, col)
                    fields[key] = "" if value is None else str(value)
                    if metadata is not None:
                        metadata[key] = {
                            "element_id": key,
                            "control_type": "GuiGridViewCell",
                            "table_type": "GuiGridView",
                            "table_id": elem_id,
                            "row": row,
                            "column": str(col),
                            "column_title": str(col),
                            "source": "com_grid",
                        }
                except Exception:
                    continue
    except Exception:
        pass


def _read_table_control_cells(table, fields, max_fields, metadata=None):
    """Read visible editable GuiTableControl cells using the same structure as /scan."""
    table_id = _short_element_id(_safe_get_attr(table, "Id", "") or "")
    if not table_id:
        return

    try:
        row_count = int(_safe_get_attr(table, "RowCount", 0) or 0)
    except Exception:
        row_count = 0
    try:
        visible_rows = int(_safe_get_attr(table, "VisibleRowCount", 0) or 0)
    except Exception:
        visible_rows = 0
    try:
        columns = _safe_get_attr(table, "Columns", None)
        column_count = int(_safe_get_attr(columns, "Count", 0) or 0)
    except Exception:
        column_count = 0
    try:
        scrollbar = _safe_get_attr(table, "VerticalScrollbar", None)
        row_offset = int(_safe_get_attr(scrollbar, "Position", 0) or 0)
    except Exception:
        row_offset = 0

    scan_rows = min(visible_rows or row_count, row_count or visible_rows, MAX_GRID_ROWS)
    for visible_row in range(max(scan_rows, 0)):
        absolute_row = row_offset + visible_row
        for column_index in range(min(column_count, MAX_GRID_COLS)):
            if len(fields) >= max_fields:
                return
            try:
                cell = table.GetCell(visible_row, column_index)
                type_name = _safe_get_attr(cell, "Type", "") or ""
                if type_name not in (
                    "GuiTextField", "GuiCTextField", "GuiPasswordField", "GuiComboBox",
                    "GuiCheckBox", "GuiRadioButton",
                ):
                    continue
                changeable = _safe_get_attr(cell, "Changeable", True)
                if not changeable and type_name not in ("GuiCheckBox", "GuiRadioButton"):
                    continue
                cell_name = _normalize_table_column_name(
                    _safe_get_attr(cell, "Name", "")
                    or _column_title(table, column_index)
                    or f"col_{column_index}"
                )
                value = _read_element_value(cell, type_name)
                key = _table_cell_key(table_id, absolute_row, cell_name)
                fields[key] = "" if value is None else str(value)
                if metadata is not None:
                    metadata[key] = {
                        "element_id": key,
                        "cell_id": _short_element_id(_safe_get_attr(cell, "Id", "") or ""),
                        "control_type": type_name,
                        "table_type": "GuiTableControl",
                        "table_id": table_id,
                        "row": absolute_row,
                        "visible_row": visible_row,
                        "column_index": column_index,
                        "column": cell_name,
                        "column_title": _column_title(table, column_index) or cell_name,
                        "source": "com_table_control",
                    }
            except Exception:
                continue


def _collect_tabular_cells(element, fields, max_fields, metadata=None, depth=0):
    """Recursively find GridView and TableControl cells for recording."""
    if depth > MAX_FIELD_SCAN_DEPTH or len(fields) >= max_fields:
        return
    try:
        children = element.Children
        count = children.Count
    except Exception:
        return
    for i in range(count):
        if len(fields) >= max_fields:
            return
        try:
            child = children(i)
            type_name = _safe_get_attr(child, "Type", "") or ""
            if type_name == "GuiGridView":
                _read_grid_view_cells(child, fields, max_fields, metadata=metadata)
                continue
            if type_name == "GuiTableControl":
                _read_table_control_cells(child, fields, max_fields, metadata=metadata)
                continue
            _collect_tabular_cells(
                child, fields, max_fields, metadata=metadata, depth=depth + 1
            )
        except Exception:
            continue

def _collect_fields(element, fields, max_fields, metadata=None, depth=0):
    """Recursively collect editable non-table fields at Scan-equivalent depth."""
    if depth > MAX_FIELD_SCAN_DEPTH or len(fields) >= max_fields:
        return

    try:
        children = element.Children
        count = children.Count
    except Exception:
        return

    for i in range(count):
        if len(fields) >= max_fields:
            return
        try:
            child = children(i)
            type_name = _safe_get_attr(child, "Type", "") or ""

            # Table/Grid cells are captured through stable row/column keys.
            if type_name in ("GuiGridView", "GuiTableControl"):
                continue

            if type_name in (
                "GuiTextField",
                "GuiCTextField",
                "GuiPasswordField",
                "GuiOkCodeField",
                "GuiComboBox",
                "GuiCheckBox",
                "GuiRadioButton",
            ):
                changeable = _safe_get_attr(child, "Changeable", True)
                if changeable or type_name in ("GuiCheckBox", "GuiRadioButton"):
                    elem_id = _short_element_id(_safe_get_attr(child, "Id", "") or "")
                    value = _read_element_value(child, type_name)
                    if elem_id and value is not None:
                        fields[elem_id] = str(value)
                        if metadata is not None:
                            metadata[elem_id] = {
                                "element_id": elem_id,
                                "control_type": type_name,
                                "name": str(_safe_get_attr(child, "Name", "") or ""),
                                "tooltip": str(_safe_get_attr(child, "Tooltip", "") or ""),
                                "source": "com_field",
                            }

            _collect_fields(
                child, fields, max_fields, metadata=metadata, depth=depth + 1
            )
        except Exception:
            continue

def _stabilize_snapshot(previous, current):
    """Carry forward transiently missing MCP metadata without merging screen fields."""
    if previous is None or current is None:
        return current

    same_tcode = not current.tcode or current.tcode == previous.tcode
    if not current.tcode:
        current.tcode = previous.tcode
    if not current.screen_number and same_tcode:
        current.screen_number = previous.screen_number
    if not current.title and same_tcode:
        current.title = previous.title
    if not current.program and same_tcode:
        current.program = previous.program
    if not current.active_window:
        current.active_window = previous.active_window
    if not current.focus_id:
        current.focus_id = previous.focus_id
    if not current.window_titles:
        current.window_titles = dict(previous.window_titles)
    return current


def _focus_matches_field(snapshot, field_id):
    focus_id = _short_element_id(getattr(snapshot, "focus_id", "") or "")
    field_id = _short_element_id(field_id)
    if not focus_id or not field_id:
        return False
    if focus_id == field_id:
        return True
    metadata = getattr(snapshot, "field_metadata", {}).get(field_id, {}) or {}
    cell_id = _short_element_id(metadata.get("cell_id", "") or "")
    if cell_id and cell_id == focus_id:
        return True
    if "#r" in field_id:
        table_id = field_id.split("#r", 1)[0]
        return focus_id == table_id or focus_id.startswith(f"{table_id}/")
    return False


def _is_save_success_status(status_type, status_text):
    """Return True only for a successful status message that confirms a save."""
    if str(status_type or "").strip().upper() != "S":
        return False
    text = str(status_text or "").strip().casefold()
    return any(marker in text for marker in ("已儲存", "saved", "gesichert"))


def _is_reset_value_change(old_value, new_value):
    """Detect SAP clearing a populated value or resetting a selected flag."""
    old_text = "" if old_value is None else str(old_value).strip()
    new_text = "" if new_value is None else str(new_value).strip()
    if not old_text:
        return False
    if not new_text:
        return True
    return old_text.casefold() == "true" and new_text.casefold() == "false"


def _is_save_button_id(element_id):
    short_id = _short_element_id(element_id).lower()
    return bool(re.search(r"(?:^|/)tbar\[0\]/btn\[11\]$", short_id))


def _diff_snapshots(old_snap, new_snap):
    """
    比對兩個畫面快照，產生事件清單。

    Args:
        old_snap: 前一次快照
        new_snap: 當前快照

    Returns:
        list[dict]: 偵測到的事件清單
    """
    events = []
    now = datetime.now().isoformat()

    # 1. T-Code 變更
    if old_snap.tcode and old_snap.tcode != new_snap.tcode and new_snap.tcode:
        events.append({
            "timestamp": now,
            "event_type": "TCODE_CHANGE",
            "details": {
                "from_tcode": old_snap.tcode,
                "to_tcode": new_snap.tcode,
                "screen_number": new_snap.screen_number,
                "title": new_snap.title,
            },
        })

    # 2. 畫面跳轉（同一 T-Code 內）
    elif (old_snap.screen_number != new_snap.screen_number
          and old_snap.screen_number
          and new_snap.screen_number
          and old_snap.tcode == new_snap.tcode):
        events.append({
            "timestamp": now,
            "event_type": "SCREEN_CHANGE",
            "details": {
                "tcode": new_snap.tcode,
                "from_screen": old_snap.screen_number,
                "to_screen": new_snap.screen_number,
                "title": new_snap.title,
            },
        })

    # 3. 活動視窗變更
    if old_snap.active_window != new_snap.active_window and new_snap.active_window:
        events.append({
            "timestamp": now,
            "event_type": "ACTIVE_WINDOW_CHANGE",
            "details": {
                "from_window": old_snap.active_window,
                "to_window": new_snap.active_window,
                "title": new_snap.window_titles.get(new_snap.active_window, ""),
                "tcode": new_snap.tcode,
                "screen_number": new_snap.screen_number,
            },
        })

    # 4. 彈窗開關/標題變化
    old_windows = set(old_snap.window_titles.keys())
    new_windows = set(new_snap.window_titles.keys())
    for window_id in sorted(new_windows - old_windows):
        events.append({
            "timestamp": now,
            "event_type": "WINDOW_OPEN",
            "details": {
                "window_id": window_id,
                "title": new_snap.window_titles.get(window_id, ""),
                "tcode": new_snap.tcode,
                "screen_number": new_snap.screen_number,
            },
        })
    for window_id in sorted(old_windows - new_windows):
        events.append({
            "timestamp": now,
            "event_type": "WINDOW_CLOSE",
            "details": {
                "window_id": window_id,
                "title": old_snap.window_titles.get(window_id, ""),
                "tcode": new_snap.tcode,
                "screen_number": new_snap.screen_number,
            },
        })

    # 5. 焦點變更（可記錄使用者點到哪個欄位/控制項）
    if old_snap.focus_id != new_snap.focus_id and new_snap.focus_id:
        events.append({
            "timestamp": now,
            "event_type": "FOCUS_CHANGE",
            "details": {
                "from_element": old_snap.focus_id,
                "to_element": new_snap.focus_id,
                "tcode": new_snap.tcode,
                "screen_number": new_snap.screen_number,
            },
        })

    save_success = (
        new_snap.status_text
        and new_snap.status_text != old_snap.status_text
        and _is_save_success_status(new_snap.status_type, new_snap.status_text)
    )

    # 6. 欄位值變更
    # 【方案 A】判斷是否偵測到導航事件（畫面跳轉 / T-Code 切換）
    navigation_event_seen = any(
        event.get("event_type") in {
            "TCODE_CHANGE",
            "SCREEN_CHANGE",
            "WINDOW_OPEN",
            "ACTIVE_WINDOW_CHANGE",
        }
        for event in events
    )
    field_events = []
    reset_changes = []
    for field_id, new_value in new_snap.field_values.items():
        old_value = old_snap.field_values.get(field_id, "")
        if old_value != new_value:
            is_new_field = field_id not in old_snap.field_values
            focus_matches = _focus_matches_field(new_snap, field_id)
            if navigation_event_seen:
                # 【方案 A 修正】畫面跳轉後：
                #   - 前次快照中「不存在」的新欄位 → 系統載入的預設值
                #   - 前次快照中「已存在」的舊欄位值變化 → 仍視為使用者操作
                #     （使用者可能在跳轉前填了值，polling 剛好在跳轉後才抓到）
                is_default_value = is_new_field and not focus_matches
            else:
                # 非跳轉 cycle：只有「不可操作的新欄位」才是系統預設值
                is_default_value = is_new_field and not _is_okcode_field_id(field_id)
            metadata = new_snap.field_metadata.get(field_id, {}) or {}
            details = {
                "element_id": metadata.get("cell_id") or field_id,
                "field_key": field_id,
                "from_value": old_value,
                "to_value": new_value,
                "tcode": new_snap.tcode,
                "screen_number": new_snap.screen_number,
                "system_default": is_default_value,
                "user_action": not is_default_value,
            }
            for key in (
                "cell_id", "control_type", "name", "label", "tooltip",
                "table_type", "table_id", "row", "visible_row",
                "column_index", "column", "column_title", "source",
            ):
                value = metadata.get(key)
                if value not in (None, ""):
                    details[key] = value
            field_event = {
                "timestamp": now,
                "event_type": "FIELD_DEFAULT" if is_default_value else "FIELD_CHANGE",
                "details": details,
            }
            if (
                save_success
                and field_event["event_type"] == "FIELD_CHANGE"
                and _is_reset_value_change(old_value, new_value)
            ):
                reset_details = dict(details)
                reset_details.update({
                    "system_default": True,
                    "user_action": False,
                })
                reset_changes.append(reset_details)
            else:
                field_events.append(field_event)

    events.extend(field_events)

    if save_success:
        events.append({
            "timestamp": now,
            "event_type": "SAVE_ACTION",
            "source": "polling_inferred",
            "details": {
                "action": "save",
                "method": "unknown",
                "inferred": True,
                "status_type": new_snap.status_type,
                "status_text": new_snap.status_text,
                "tcode": new_snap.tcode,
                "screen_number": new_snap.screen_number,
                "user_action": True,
            },
        })

    if reset_changes:
        events.append({
            "timestamp": now,
            "event_type": "SYSTEM_RESET",
            "source": "polling_system",
            "details": {
                "reason": "save_success",
                "changes": reset_changes,
                "change_count": len(reset_changes),
                "status_type": new_snap.status_type,
                "status_text": new_snap.status_text,
                "tcode": new_snap.tcode,
                "screen_number": new_snap.screen_number,
                "system_default": True,
                "user_action": False,
            },
        })

    # 7. 狀態列訊息（只在訊息變更且非空時觸發）
    if (new_snap.status_text
            and new_snap.status_text != old_snap.status_text):
        events.append({
            "timestamp": now,
            "event_type": "STATUS_MESSAGE",
            "details": {
                "type": new_snap.status_type,
                "text": new_snap.status_text,
                "tcode": new_snap.tcode,
                "screen_number": new_snap.screen_number,
            },
        })

    return events


def _as_native_sequence(value):
    if isinstance(value, (list, tuple)):
        return list(value)
    try:
        return list(value)
    except Exception:
        return [value]


def _native_command_lines(command_array):
    values = _as_native_sequence(command_array)
    if not values:
        return []
    if isinstance(values[0], str):
        return [values]
    lines = []
    for value in values:
        line = _as_native_sequence(value)
        if line:
            lines.append(line)
    return lines


def _native_value(value):
    if isinstance(value, bool):
        return str(value)
    if value is None:
        return ""
    return str(value)


def _vkey_name(vkey):
    names = {0: "Enter", 2: "F2", 3: "Back", 8: "F8/Execute", 11: "Ctrl+S/Save", 12: "Cancel"}
    try:
        key = int(vkey)
    except (TypeError, ValueError):
        return str(vkey)
    return names.get(key, f"VKey {key}")


def _is_context_monitor_event(event):
    return event.get("event_type") in {
        "SCREEN_CHANGE", "ACTIVE_WINDOW_CHANGE", "WINDOW_OPEN", "WINDOW_CLOSE",
        "STATUS_MESSAGE", "SYSTEM_RESET",
    }

def _native_component_details(component):
    element_id = _short_element_id(_safe_get_attr(component, "Id", "") or "")
    type_name = str(_safe_get_attr(component, "Type", "") or "")
    details = {
        "element_id": element_id,
        "control_type": type_name,
        "name": str(_safe_get_attr(component, "Name", "") or ""),
        "tooltip": str(_safe_get_attr(component, "Tooltip", "") or ""),
    }
    table_cell = _parse_table_cell_id(element_id)
    if table_cell:
        field_key = _table_cell_key(
            table_cell["table_id"], table_cell["row"], table_cell["column"]
        )
        details.update({
            "field_key": field_key,
            "cell_id": element_id,
            "table_type": "GuiTableControl",
            "table_id": table_cell["table_id"],
            "row": table_cell["row"],
            "column_index": table_cell["column_index"],
            "column": table_cell["column"],
            "column_title": table_cell["column"],
        })
    else:
        details["field_key"] = element_id
    return details


class _SAPSessionEventSink:
    """pywin32 event sink for SAP GuiSession.Record Change events."""

    _monitor = None

    def OnChange(self, Session, Component, CommandArray):
        if self._monitor is not None:
            self._monitor._handle_native_change(Session, Component, CommandArray)

class SAPMonitor:
    """
    SAP GUI 背景監控器

    在背景執行緒中定期輪詢 SAP 畫面狀態，
    偵測到變化時透過回調函數通知。

    使用範例：
        monitor = SAPMonitor(session)
        monitor.on_event = lambda event: print(event)
        monitor.start()
        # ... 使用者在 SAP 中操作 ...
        monitor.stop()
    """

    def __init__(self, session=None, poll_interval=0.3, connection_index=0, session_index=0):
        """
        初始化監控器。

        Args:
            session: SAP Session COM 物件
            poll_interval: 輪詢間隔（秒），預設 0.3 秒
        """
        self.session = session
        self.poll_interval = poll_interval
        self.connection_index = connection_index
        self.session_index = session_index
        self.on_event = None  # 事件回調函數: Callable[[dict], None]
        self._stop_event = threading.Event()
        self._thread = None
        self._last_snapshot = None
        self._is_running = False
        self._capture_failures = 0
        self.use_mcp = MCP_MONITOR_ENABLED
        self.mcp_client = get_default_sync_client() if self.use_mcp else None
        self._mcp_tool_names = set()
        self._mcp_poll_interval = float(os.getenv("MCP_SAP_MONITOR_POLL_SECONDS", "1.0"))
        self._native_pending_tcode = ""
        self._native_last_values = {}
        self._native_event_sink = None
        self._last_exact_save_at = 0.0
        self._marshaled_session_stream = None
        self._session_marshal_error = ""

    @property
    def is_running(self):
        """監控器是否正在執行"""
        return self._is_running

    def start(self):
        """啟動背景監控"""
        if self._is_running:
            print("\033[33m[Monitor] 監控器已在運行中\033[0m")
            return

        self._stop_event.clear()
        self._marshal_session_for_thread()

        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._is_running = True
        self._thread.start()
        print("\033[90m[Monitor] 背景監控已啟動\033[0m")

    def stop(self):
        """停止背景監控"""
        if not self._is_running:
            return

        self._stop_event.set()
        if self._thread:
            self._thread.join(timeout=2)
        self._is_running = False
        print("\033[90m[Monitor] 背景監控已停止\033[0m")

    def _monitor_loop(self):
        """背景監控主迴圈；統一管理 monitor thread 的 COM apartment。"""
        com_initialized = False
        thread_session = None
        try:
            if pythoncom is not None and win32com is not None:
                pythoncom.CoInitialize()
                com_initialized = True
                try:
                    thread_session = self._resolve_session_for_thread()
                    self.session = thread_session
                except Exception as exc:
                    detail = _format_exception(exc)
                    if self._session_marshal_error:
                        detail = f"{detail}; marshal={self._session_marshal_error}"
                    print(
                        "\033[33m[Monitor] 背景執行緒 SAP session 綁定失敗: "
                        f"{detail}\033[0m"
                    )

            if SAP_NATIVE_RECORD_EVENTS and thread_session is not None:
                if self._monitor_loop_native(thread_session):
                    return
                print(
                    "\033[33m[Monitor] SAP native record events 不可用，"
                    "切換 COM polling fallback\033[0m"
                )

            # COM polling 能以 0.3 秒頻率讀取深層欄位與表格；MCP-only
            # polling 預設為 1 秒，快速輸入後立即 Enter 時容易漏掉最終值。
            if thread_session is not None:
                self._monitor_loop_legacy(thread_session)
                return

            if self.use_mcp and self.mcp_client is not None:
                if self._monitor_loop_mcp():
                    return
                print("\033[33m[Monitor] MCP 監控不可用，且無可用 COM session\033[0m")
        finally:
            if com_initialized:
                pythoncom.CoUninitialize()

    def _native_context(self, session):
        try:
            info = session.Info
            return (
                str(_safe_get_attr(info, "Transaction", "") or ""),
                str(_safe_get_attr(info, "ScreenNumber", "") or ""),
            )
        except Exception:
            return "", ""

    def _dispatch_native_event(self, event_type, details):
        event = {
            "timestamp": datetime.now().isoformat(),
            "event_type": event_type,
            "details": details,
            "source": "sap_native_record",
        }
        if event_type == "SAVE_ACTION" and not details.get("inferred"):
            self._last_exact_save_at = time.monotonic()
        self._dispatch_event(event)

    def _dispatch_native_context_events(self, events):
        """Dispatch context polling results and deduplicate exact/inferred saves."""
        for event in events:
            event_type = event.get("event_type")
            if event_type == "SAVE_ACTION":
                exact_save_is_recent = (
                    self._last_exact_save_at
                    and time.monotonic() - self._last_exact_save_at <= 60.0
                )
                if exact_save_is_recent:
                    self._last_exact_save_at = 0.0
                    continue
                event.setdefault("source", "sap_native_context")
                self._dispatch_event(event)
                continue
            if _is_context_monitor_event(event):
                event.setdefault("source", "sap_native_context")
                self._dispatch_event(event)

    def _handle_native_change(self, session, component, command_array):
        tcode, screen_number = self._native_context(session)
        component_details = _native_component_details(component)
        element_id = component_details.get("element_id", "")
        current_target = dict(component_details)

        for line in _native_command_lines(command_array):
            if len(line) < 2:
                continue
            command_type = str(line[0] or "").upper()
            command_name = str(line[1] or "")
            params = line[2:]
            if len(params) == 1 and isinstance(params[0], (list, tuple)):
                params = list(params[0])
            lowered = command_name.lower()

            if command_type == "M" and lowered == "getabsoluterow" and params:
                try:
                    row = int(params[0])
                except (TypeError, ValueError):
                    row = 0
                table_id = element_id
                current_target.update({
                    "field_key": _table_cell_key(table_id, row, "selected"),
                    "table_type": "GuiTableControl",
                    "table_id": table_id,
                    "row": row,
                    "column": "selected",
                    "column_title": "選取",
                })
                continue

            # 【核心修正】處理 M findById 指令：
            # SAP native Record Events 的 CommandArray 結構為
            #   [["M","findById",["wnd[0]/usr/ctxtVBAK-AUART"]], ["SP","Text","OR1"]]
            # 或 [["M","findById",["wnd[0]/tbar[0]/btn[11]"]], ["M","press"]]
            # findById 必須被解析，後續 SP / M press 才能綁定到正確的 element_id。
            if command_type == "M" and lowered == "findbyid" and params:
                found_raw = str(params[0] if params else "")
                found_id = _short_element_id(found_raw)
                # 重建 current_target 為找到的元件
                current_target = {"element_id": found_id, "field_key": found_id}
                element_id = found_id  # 同步更新，供後續 sendVKey / press 使用
                # 偵測表格 cell 格式（table_id#rN#col）
                table_cell = _parse_table_cell_id(found_id)
                if table_cell:
                    fk = _table_cell_key(
                        table_cell["table_id"], table_cell["row"], table_cell["column"]
                    )
                    current_target.update({
                        "field_key": fk,
                        "cell_id": found_id,
                        "table_type": "GuiTableControl",
                        "table_id": table_cell["table_id"],
                        "row": table_cell["row"],
                        "column_index": table_cell["column_index"],
                        "column": table_cell["column"],
                        "column_title": table_cell["column"],
                    })
                continue

            if command_type == "SP":
                if lowered in {"record", "recordmode", "testtoolmode"}:
                    continue
                value = _native_value(params[0] if params else "")
                cur_element_id = current_target.get("element_id", "") or element_id
                if _is_okcode_field_id(cur_element_id) and lowered == "text":
                    self._native_pending_tcode = value.strip()
                    continue
                if lowered not in {"text", "key", "selected", "value"}:
                    continue
                field_key = current_target.get("field_key") or cur_element_id
                if not field_key:
                    continue  # element_id 仍為空，無法定位欄位，略過
                previous = self._native_last_values.get(field_key, "")
                details = dict(current_target)
                details["element_id"] = details.get("element_id") or cur_element_id
                details.update({
                    "from_value": previous,
                    "to_value": value,
                    "property": command_name,
                    "tcode": tcode,
                    "screen_number": screen_number,
                    "system_default": False,
                    "user_action": True,
                })
                self._native_last_values[field_key] = value
                self._dispatch_native_event("FIELD_CHANGE", details)
                continue

            if command_type != "M":
                continue

            if lowered == "sendvkey":
                raw_vkey = params[0] if params else 0
                try:
                    vkey = int(raw_vkey)
                except (TypeError, ValueError):
                    vkey = 0
                if self._native_pending_tcode and vkey == 0:
                    target_tcode = self._native_pending_tcode.lstrip("/n").strip().upper()
                    self._dispatch_native_event("TCODE_CHANGE", {
                        "from_tcode": tcode,
                        "to_tcode": target_tcode,
                        "element_id": "wnd[0]/tbar[0]/okcd",
                        "screen_number": screen_number,
                        "user_action": True,
                    })
                    self._native_pending_tcode = ""
                elif vkey == 11:
                    self._dispatch_native_event("SAVE_ACTION", {
                        "action": "save",
                        "method": "vkey",
                        "inferred": False,
                        "element_id": element_id or "wnd[0]",
                        "vkey": vkey,
                        "key_name": _vkey_name(vkey),
                        "tcode": tcode,
                        "screen_number": screen_number,
                        "user_action": True,
                    })
                else:
                    self._dispatch_native_event("KEY_PRESS", {
                        "element_id": element_id or "wnd[0]",
                        "vkey": vkey,
                        "key_name": _vkey_name(vkey),
                        "tcode": tcode,
                        "screen_number": screen_number,
                        "user_action": True,
                    })
                continue

            if lowered in {"press", "doubleclick", "select"}:
                cur_element_id = current_target.get("element_id", "") or element_id
                if lowered == "press" and _is_save_button_id(cur_element_id):
                    self._dispatch_native_event("SAVE_ACTION", {
                        "action": "save",
                        "method": "button",
                        "inferred": False,
                        "element_id": cur_element_id,
                        "tcode": tcode,
                        "screen_number": screen_number,
                        "user_action": True,
                    })
                    continue
                event_type = (
                    "TAB_SELECT" if lowered == "select" and "/tabp" in cur_element_id
                    else "BUTTON_CLICK"
                )
                details = dict(current_target)
                details["element_id"] = cur_element_id
                details.update({
                    "action": lowered,
                    "tcode": tcode,
                    "screen_number": screen_number,
                    "user_action": True,
                })
                self._dispatch_native_event(event_type, details)


    def _monitor_loop_native(self, thread_session):
        """Use SAP GuiSession.Record Change events; keep polling only for screen context."""
        try:
            # 【修正】Patch win32com genpy 以容忍 SAP type library 的非標準 enum entry
            # （SAP scripting type lib 有 desckind != FUNCDESC 的項目，導致 AssertionError）
            if not _patch_genpy_for_sap():
                print("\033[33m[Monitor] genpy patch 失敗，Native Record Events 可能無法啟用\033[0m")

            self._last_snapshot = ScreenSnapshot.capture(thread_session)
            self._native_last_values = dict(self._last_snapshot.field_values)
            self._native_event_sink = win32com.client.WithEvents(
                thread_session, _SAPSessionEventSink
            )
            self._native_event_sink._monitor = self
            thread_session.Record = True
            print("\033[90m[Monitor] SAP native record events 已啟用\033[0m")

            next_context_scan = time.monotonic() + SAP_RECORD_CONTEXT_POLL_SECONDS
            while not self._stop_event.is_set():
                pythoncom.PumpWaitingMessages()
                now = time.monotonic()
                if now >= next_context_scan:
                    new_snapshot = ScreenSnapshot.capture(thread_session)
                    if self._last_snapshot:
                        new_snapshot = _stabilize_snapshot(self._last_snapshot, new_snapshot)
                        self._dispatch_native_context_events(
                            _diff_snapshots(self._last_snapshot, new_snapshot)
                        )
                    self._last_snapshot = new_snapshot
                    next_context_scan = now + SAP_RECORD_CONTEXT_POLL_SECONDS
                self._stop_event.wait(0.03)

            # Turning Record off flushes the final edits that did not trigger a request.
            thread_session.Record = False
            for _ in range(5):
                pythoncom.PumpWaitingMessages()
                time.sleep(0.02)
            return True
        except AssertionError as exc:
            # 【方案 E 診斷】AssertionError 通常來自 win32com.client.WithEvents
            # 或 COM apartment 不相容；輸出完整 traceback 幫助排查。
            import traceback as _tb
            print(
                "\033[33m[Monitor] SAP native record events 啟動失敗: "
                f"{_format_exception(exc)}\033[0m"
            )
            print(f"\033[90m[Monitor] AssertionError 詳細:\n{_tb.format_exc().strip()}\033[0m")
            return False
        except Exception as exc:
            print(
                "\033[33m[Monitor] SAP native record events 啟動失敗: "
                f"{_format_exception(exc)}\033[0m"
            )
            return False
        finally:
            try:
                thread_session.Record = False
            except Exception:
                pass
            self._native_event_sink = None
    def _load_mcp_tool_names(self):
        tools = self.mcp_client.get_available_tools()
        self._mcp_tool_names = {
            item.get("function", {}).get("name", "")
            for item in tools
            if item.get("function", {}).get("name")
        }
        return self._mcp_tool_names

    def _monitor_loop_mcp(self):
        """MCP primary monitor loop. Returns False when legacy fallback should be used."""
        try:
            tool_names = self._load_mcp_tool_names()
            if not any(name in tool_names for name in MCP_SESSION_INFO_TOOL_CANDIDATES + MCP_SCREEN_TOOL_CANDIDATES):
                print("\033[33m[Monitor] MCP server 沒有可用的 session/screen snapshot 工具\033[0m")
                return False
            try:
                attach_result = self.mcp_client.ensure_sap_connected(list(tool_names))
                if attach_result.get("attempted") and not attach_result.get("attached"):
                    print(f"\033[33m[Monitor] MCP SAP session attach 警告: {attach_result}\033[0m")
            except Exception as attach_error:
                print(f"\033[33m[Monitor] MCP SAP session attach 警告: {attach_error}\033[0m")

            self._last_snapshot = ScreenSnapshot.capture_mcp(self.mcp_client, tool_names, session=self.session)
            if self._last_snapshot.capture_error and not (
                self._last_snapshot.tcode
                or self._last_snapshot.screen_number
                or self._last_snapshot.field_values
            ):
                print(f"\033[33m[Monitor] MCP 初始快照失敗: {self._last_snapshot.capture_error}\033[0m")
                return False

            field_count = len(self._last_snapshot.field_values)
            print(
                "\033[90m"
                f"[Monitor] MCP 初始快照: T-Code={self._last_snapshot.tcode or '-'}, "
                f"Screen={self._last_snapshot.screen_number or '-'}, "
                f"Fields={field_count}, Active={self._last_snapshot.active_window or '-'}"
                "\033[0m"
            )

            while not self._stop_event.is_set():
                try:
                    new_snapshot = ScreenSnapshot.capture_mcp(self.mcp_client, tool_names, session=self.session)
                    if new_snapshot.capture_error:
                        self._capture_failures += 1
                        if self._capture_failures in (1, 5, 20) or self._capture_failures % 100 == 0:
                            print(f"\033[33m[Monitor] MCP 快照擷取警告 #{self._capture_failures}: {new_snapshot.capture_error}\033[0m")
                    else:
                        self._capture_failures = 0

                    if self._last_snapshot:
                        new_snapshot = _stabilize_snapshot(self._last_snapshot, new_snapshot)
                        events = _diff_snapshots(self._last_snapshot, new_snapshot)
                        for event in events:
                            event.setdefault("source", "mcp")
                            self._dispatch_event(event)

                    self._last_snapshot = new_snapshot
                except Exception as e:
                    self._capture_failures += 1
                    if self._capture_failures in (1, 5, 20) or self._capture_failures % 100 == 0:
                        print(f"\033[33m[Monitor] MCP 背景監控讀取 SAP 失敗 #{self._capture_failures}: {e}\033[0m")

                self._stop_event.wait(max(self.poll_interval, self._mcp_poll_interval))

            return True
        except Exception as e:
            print(f"\033[33m[Monitor] MCP 監控啟動失敗: {e}\033[0m")
            return False

    def _monitor_loop_legacy(self, thread_session=None):
        """Legacy pywin32 COM monitor loop used as fallback."""
        if pythoncom is None or win32com is None:
            print("\033[31m[Monitor] legacy GUI fallback 不可用：pywin32 未安裝或無法匯入\033[0m")
            return

        try:
            thread_session = thread_session or self._resolve_session_for_thread()
            self.session = thread_session
            self._last_snapshot = ScreenSnapshot.capture(thread_session)
            field_count = len(self._last_snapshot.field_values)
            print(
                "\033[90m"
                f"[Monitor] 初始快照: T-Code={self._last_snapshot.tcode or '-'}, "
                f"Screen={self._last_snapshot.screen_number or '-'}, "
                f"Fields={field_count}, Active={self._last_snapshot.active_window or '-'}"
                "\033[0m"
            )

            # 【方案 B】跳轉緩衝計數：偵測到導航事件後，下一個 cycle
            # 暫不發送 FIELD_CHANGE，避免畫面載入中的欄位值被誤判為使用者輸入。
            _skip_field_diff_cycles = 0

            while not self._stop_event.is_set():
                try:
                    # 擷取新快照
                    new_snapshot = ScreenSnapshot.capture(thread_session)
                    if new_snapshot.capture_error:
                        self._capture_failures += 1
                        if self._capture_failures in (1, 5, 20) or self._capture_failures % 100 == 0:
                            print(f"\033[33m[Monitor] 快照擷取警告 #{self._capture_failures}: {new_snapshot.capture_error}\033[0m")
                    else:
                        self._capture_failures = 0

                    # 比對差異
                    if self._last_snapshot:
                        new_snapshot = _stabilize_snapshot(self._last_snapshot, new_snapshot)
                        events = _diff_snapshots(self._last_snapshot, new_snapshot)

                        # 【方案 B】偵測本 cycle 是否包含導航事件
                        has_nav_event = any(
                            e.get("event_type") in {
                                "TCODE_CHANGE", "SCREEN_CHANGE",
                                "WINDOW_OPEN", "ACTIVE_WINDOW_CHANGE",
                            }
                            for e in events
                        )
                        if has_nav_event:
                            # 下一個 cycle 的 FIELD_CHANGE 先緩衝，等畫面穩定
                            _skip_field_diff_cycles = 1

                        for event in events:
                            # 【方案 B】跳轉緩衝：在計數期間略過 FIELD_CHANGE
                            # （仍然保留導航與狀態事件讓 recorder 作為脈絡）
                            if _skip_field_diff_cycles > 0 and event.get("event_type") == "FIELD_CHANGE":
                                continue
                            # 【Polling 補強】在 SCREEN_CHANGE（同一 T-Code 內畫面跳轉）前
                            # 自動插入推斷的 Enter 鍵事件，補充 polling 無法直接偵測按鍵的缺陷。
                            # TCODE_CHANGE 由 okcd 欄位變化已有明確紀錄，不需推斷。
                            if event.get("event_type") == "SCREEN_CHANGE":
                                details = event.get("details", {})
                                self._dispatch_event({
                                    "timestamp": event.get("timestamp", datetime.now().isoformat()),
                                    "event_type": "KEY_PRESS",
                                    "source": "polling_inferred",
                                    "details": {
                                        "vkey": 0,
                                        "key_name": "Enter",
                                        "element_id": "wnd[0]",
                                        "tcode": details.get("tcode", ""),
                                        "screen_number": details.get("from_screen", ""),
                                        "user_action": True,
                                        "inferred": True,
                                    },
                                })
                            self._dispatch_event(event)

                        if _skip_field_diff_cycles > 0:
                            _skip_field_diff_cycles -= 1

                    self._last_snapshot = new_snapshot

                except Exception as e:
                    self._capture_failures += 1
                    if self._capture_failures in (1, 5, 20) or self._capture_failures % 100 == 0:
                        print(f"\033[33m[Monitor] 背景監控讀取 SAP 失敗 #{self._capture_failures}: {e}\033[0m")

                # 等待下一次輪詢
                self._stop_event.wait(self.poll_interval)
        except Exception as e:
            print(f"\033[31m[Monitor] 啟動背景監控失敗: {e}\033[0m")

    def _resolve_session_for_thread(self):
        """在 monitor thread 內取得 start() 時選定並 marshal 的 SAP session。"""
        if self._marshaled_session_stream is not None:
            stream = self._marshaled_session_stream
            self._marshaled_session_stream = None
            dispatch = pythoncom.CoGetInterfaceAndReleaseStream(
                stream, pythoncom.IID_IDispatch
            )
            return win32com.client.Dispatch(dispatch)

        # 相容未經 start()、直接呼叫 loop 的舊測試或整合程式。
        sap_gui = win32com.client.GetObject("SAPGUI")
        application = sap_gui.GetScriptingEngine
        connection = application.Children(self.connection_index)
        return connection.Children(self.session_index)

    def _marshal_session_for_thread(self):
        """將目前選定的 SAP session 安全傳入 monitor COM apartment。"""
        self._marshaled_session_stream = None
        self._session_marshal_error = ""
        if self.session is None or pythoncom is None or win32com is None:
            return
        try:
            dispatch = _safe_get_attr(self.session, "_oleobj_", self.session)
            self._marshaled_session_stream = (
                pythoncom.CoMarshalInterThreadInterfaceInStream(
                    pythoncom.IID_IDispatch, dispatch
                )
            )
        except Exception as exc:
            self._session_marshal_error = _format_exception(exc)

    def _dispatch_event(self, event):
        """分派事件到回調函數"""
        if self.on_event:
            try:
                self.on_event(event)
            except Exception as e:
                print(f"\033[33m[Monitor] 事件回調錯誤: {e}\033[0m")

    def get_current_snapshot(self):
        """取得最近一次的畫面快照"""
        return self._last_snapshot
