"""
SAP GUI 背景監控器 (Polling-based)

在背景執行緒中定期快照 SAP 畫面狀態，比對差異來偵測使用者操作。
替代不穩定的 WithEvents 方案，提供穩定可靠的事件偵測。

偵測的事件類型：
- TCODE_CHANGE: T-Code 切換
- SCREEN_CHANGE: 同一交易中畫面跳轉
- FIELD_CHANGE: 可編輯欄位值改變
- STATUS_MESSAGE: 狀態列出現新訊息
"""

import threading
import time
import json
from datetime import datetime
from pywintypes import com_error


def _safe_get_attr(obj, attr, default=None):
    """安全地從 COM 物件取得屬性值（避免 hasattr 觸發 win32com 內部錯誤）"""
    try:
        return getattr(obj, attr)
    except Exception:
        return default


class ScreenSnapshot:
    """SAP 畫面快照，用於比對前後差異"""

    def __init__(self):
        self.tcode = ""
        self.screen_number = ""
        self.title = ""
        self.program = ""
        self.status_type = ""
        self.status_text = ""
        self.field_values = {}  # {element_id: text_value}

    def to_dict(self):
        return {
            "tcode": self.tcode,
            "screen_number": self.screen_number,
            "title": self.title,
            "program": self.program,
            "status_bar": {"type": self.status_type, "text": self.status_text},
            "field_values": self.field_values,
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
        except Exception:
            pass

        try:
            window = session.FindById("wnd[0]")
            snap.title = _safe_get_attr(window, "Text", "") or ""
        except Exception:
            pass

        try:
            sbar = session.FindById("wnd[0]/sbar")
            snap.status_type = _safe_get_attr(sbar, "MessageType", "") or ""
            snap.status_text = _safe_get_attr(sbar, "Text", "") or ""
        except Exception:
            pass

        # 擷取可編輯欄位的值（用於偵測 FIELD_CHANGE）
        try:
            snap.field_values = _capture_editable_fields(session)
        except Exception:
            pass

        return snap


def _capture_editable_fields(session, max_fields=50):
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

    try:
        window = session.FindById("wnd[0]")
        _collect_fields(window, fields, max_fields)
    except Exception:
        pass

    return fields


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

            # 只收集可編輯的輸入欄位
            if type_name in ("GuiTextField", "GuiCTextField", "GuiPasswordField"):
                changeable = _safe_get_attr(child, "Changeable", False)
                if changeable:
                    elem_id = _safe_get_attr(child, "Id", "") or ""
                    text = _safe_get_attr(child, "Text", "") or ""
                    if elem_id:
                        fields[elem_id] = str(text)

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

    # 3. 欄位值變更
    for field_id, new_value in new_snap.field_values.items():
        old_value = old_snap.field_values.get(field_id, "")
        if old_value != new_value and new_value:
            events.append({
                "timestamp": now,
                "event_type": "FIELD_CHANGE",
                "details": {
                    "element_id": field_id,
                    "from_value": old_value,
                    "to_value": new_value,
                    "tcode": new_snap.tcode,
                    "screen_number": new_snap.screen_number,
                },
            })

    # 4. 狀態列訊息（只在訊息變更且非空時觸發）
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

    def __init__(self, session, poll_interval=0.3):
        """
        初始化監控器。

        Args:
            session: SAP Session COM 物件
            poll_interval: 輪詢間隔（秒），預設 0.3 秒
        """
        self.session = session
        self.poll_interval = poll_interval
        self.on_event = None  # 事件回調函數: Callable[[dict], None]
        self._stop_event = threading.Event()
        self._thread = None
        self._last_snapshot = None
        self._is_running = False

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

        # 擷取初始快照
        try:
            self._last_snapshot = ScreenSnapshot.capture(self.session)
        except Exception:
            self._last_snapshot = ScreenSnapshot()

        self._thread = threading.Thread(target=self._monitor_loop, daemon=True)
        self._thread.start()
        self._is_running = True
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
        while not self._stop_event.is_set():
            try:
                # 擷取新快照
                new_snapshot = ScreenSnapshot.capture(self.session)

                # 比對差異
                if self._last_snapshot:
                    events = _diff_snapshots(self._last_snapshot, new_snapshot)
                    for event in events:
                        self._dispatch_event(event)

                self._last_snapshot = new_snapshot

            except Exception:
                # COM 錯誤（可能是 SAP 正在忙碌或畫面切換中），靜默忽略
                pass

            # 等待下一次輪詢
            self._stop_event.wait(self.poll_interval)

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
