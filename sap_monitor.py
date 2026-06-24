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


def _safe_get_attr(obj, attr, default=None):
    """安全地從 COM 物件取得屬性值（避免 hasattr 觸發 win32com 內部錯誤）"""
    try:
        return getattr(obj, attr)
    except Exception:
        return default


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


def _extract_mcp_field_values(value, max_fields=300):
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
        if "/tabs" in short_eid or "/tabp" in short_eid:
            continue
        type_name = str(item.get("type") or item.get("control_type") or "")
        if type_name and not any(
            marker in type_name
            for marker in ("TextField", "ComboBox", "CheckBox", "RadioButton", "OkCode")
        ):
            if not any(key in item for key in ("value", "text", "selected", "key")):
                continue
        raw_value = (
            item.get("value")
            if item.get("value") not in (None, "")
            else item.get("text")
        )
        if raw_value in (None, "") and "selected" in item:
            raw_value = str(bool(item.get("selected")))
        if raw_value in (None, "") and item.get("key") not in (None, ""):
            raw_value = item.get("key")
        if raw_value not in (None, ""):
            fields[_short_element_id(element_id)] = str(raw_value)

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
            snap.field_values = _capture_editable_fields(session)
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

            snap.field_values = _extract_mcp_field_values(combined)
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

        # MCP 不回傳深層欄位（如 VA01 的 KUNNR）及 Grid cell；用 COM 補抓
        if session is not None:
            try:
                com_fields = _capture_editable_fields(session, max_fields=300)
                # COM 補充 MCP 沒有的欄位；MCP 已有的不覆蓋（MCP 較準確）
                for k, v in com_fields.items():
                    if k not in snap.field_values:
                        snap.field_values[k] = v
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


def _capture_editable_fields(session, max_fields=200):
    """
    擷取當前畫面所有可編輯欄位的值。

    只擷取 GuiTextField, GuiCTextField 等輸入欄位，
    限制數量避免過度消耗效能。

    Args:
        session: SAP Session COM 物件
        max_fields: 最大欄位數量限制

    Returns:
        dict: {element_id: text_value}
    """
    fields = {}

    for wnd_idx in range(MAX_WINDOW_SCAN):
        if len(fields) >= max_fields:
            break
        window_id = f"wnd[{wnd_idx}]"
        try:
            window = session.FindById(window_id)
            _collect_fields(window, fields, max_fields)
            _collect_grid_cells(window, fields, max_fields)
        except Exception:
            if wnd_idx > 0:
                break

    return fields


def _read_grid_view_cells(grid, fields, max_fields):
    """讀取 GuiGridView (ALV Grid) 的所有可見 cell 值。"""
    try:
        elem_id = _short_element_id(_safe_get_attr(grid, "Id", "") or "")
        if not elem_id:
            return
        row_count = int(_safe_get_attr(grid, "RowCount", 0) or 0)
        cols = list(grid.ColumnOrder)
        for row in range(min(row_count, MAX_GRID_ROWS)):
            for col in cols[:MAX_GRID_COLS]:
                if len(fields) >= max_fields:
                    return
                try:
                    value = grid.GetCellValue(row, col)
                    if value:
                        fields[f"{elem_id}#r{row}#{col}"] = str(value)
                except Exception:
                    pass
    except Exception:
        pass


def _collect_grid_cells(element, fields, max_fields, depth=0):
    """遞迴找 GuiGridView 並捕捉 cell 值（COM 路徑專用）。"""
    if depth > 10 or len(fields) >= max_fields:
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
                _read_grid_view_cells(child, fields, max_fields)
            _collect_grid_cells(child, fields, max_fields, depth + 1)
        except Exception:
            continue


def _collect_fields(element, fields, max_fields, depth=0):
    """遞迴收集可編輯欄位"""
    if depth > 8 or len(fields) >= max_fields:
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

            # 收集可觀察的輸入/選擇狀態
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

            # 繼續遍歷子元件
            _collect_fields(child, fields, max_fields, depth + 1)
        except Exception:
            continue


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
    if old_snap.tcode != new_snap.tcode and new_snap.tcode:
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

    # 6. 欄位值變更
    navigation_event_seen = any(
        event.get("event_type") in {
            "TCODE_CHANGE",
            "SCREEN_CHANGE",
            "WINDOW_OPEN",
            "ACTIVE_WINDOW_CHANGE",
        }
        for event in events
    )
    for field_id, new_value in new_snap.field_values.items():
        old_value = old_snap.field_values.get(field_id, "")
        if old_value != new_value and new_value:
            is_new_field = field_id not in old_snap.field_values
            is_default_value = (
                navigation_event_seen
                and is_new_field
                and not _is_okcode_field_id(field_id)
            )
            events.append({
                "timestamp": now,
                "event_type": "FIELD_DEFAULT" if is_default_value else "FIELD_CHANGE",
                "details": {
                    "element_id": field_id,
                    "from_value": old_value,
                    "to_value": new_value,
                    "tcode": new_snap.tcode,
                    "screen_number": new_snap.screen_number,
                    "system_default": is_default_value,
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
        """背景監控主迴圈"""
        if self.use_mcp and self.mcp_client is not None:
            if self._monitor_loop_mcp():
                return
            print("\033[33m[Monitor] MCP 監控不可用，切換 legacy GUI fallback\033[0m")

        self._monitor_loop_legacy()

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
                        events = _diff_snapshots(self._last_snapshot, new_snapshot)
                        for event in events:
                            event["source"] = "mcp"
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

    def _monitor_loop_legacy(self):
        """Legacy pywin32 COM monitor loop used as fallback."""
        if pythoncom is None or win32com is None:
            print("\033[31m[Monitor] legacy GUI fallback 不可用：pywin32 未安裝或無法匯入\033[0m")
            return

        pythoncom.CoInitialize()
        try:
            thread_session = self._resolve_session_for_thread()
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
                        events = _diff_snapshots(self._last_snapshot, new_snapshot)
                        for event in events:
                            self._dispatch_event(event)

                    self._last_snapshot = new_snapshot

                except Exception as e:
                    self._capture_failures += 1
                    if self._capture_failures in (1, 5, 20) or self._capture_failures % 100 == 0:
                        print(f"\033[33m[Monitor] 背景監控讀取 SAP 失敗 #{self._capture_failures}: {e}\033[0m")

                # 等待下一次輪詢
                self._stop_event.wait(self.poll_interval)
        except Exception as e:
            print(f"\033[31m[Monitor] 啟動背景監控失敗: {e}\033[0m")
        finally:
            pythoncom.CoUninitialize()

    def _resolve_session_for_thread(self):
        """在 monitor thread 內重新取得 SAP session，避免跨 thread COM proxy 失效。"""
        try:
            sap_gui = win32com.client.GetObject("SAPGUI")
            application = sap_gui.GetScriptingEngine
            connection = application.Children(self.connection_index)
            return connection.Children(self.session_index)
        except Exception:
            if self.session is not None:
                return self.session
            raise

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
