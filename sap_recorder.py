"""
SAP GUI 操作紀錄管理器

負責管理 Record Mode 產生的操作紀錄：
- 錄製使用者的 SAP 操作流程
- 儲存為 JSON 格式的 SOP 檔案
- 讀取與列出已錄製的 SOP
- 產生摘要給 LLM 使用（Ask Mode 上下文）
"""

import json
import os
import re
from datetime import datetime

from llm_provider import create_llm_provider, normalize_provider_name


NOISY_EVENT_TYPES = {"FOCUS_CHANGE", "FIELD_DEFAULT"}
USER_OPERATION_EVENT_TYPES = {
    "TCODE_CHANGE",
    "FIELD_CHANGE",
    "BUTTON_CLICK",
    "TAB_SELECT",
    "KEY_PRESS",
    "CHECKBOX_CHANGE",
    "RADIO_CHANGE",
    "COMBO_CHANGE",
    "TABLE_SELECTION",
    "SAVE_ACTION",
}
CONTEXT_EVENT_TYPES = {
    "SCREEN_CHANGE",
    "ACTIVE_WINDOW_CHANGE",
    "WINDOW_OPEN",
    "WINDOW_CLOSE",
    "STATUS_MESSAGE",
    "SYSTEM_RESET",
}
NON_ACTIONABLE_FIELD_ID_FRAGMENTS = (
    "/lbl",
    "/box",
    "/cntl",
    "/shellcont",
    "/tabs",
    "/tabp",
    "/sbar",
    "/titl",
)


def _is_actionable_field_id(element_id):
    value = str(element_id or "")
    lowered = value.lower()
    if not value:
        return False
    if "#r" in lowered or re.search(r"/tbl[^/]+/[^/\[]+\[\d+,\d+\]$", lowered):
        return True
    if any(fragment in lowered for fragment in NON_ACTIONABLE_FIELD_ID_FRAGMENTS):
        return False
    leaf = lowered.rsplit("/", 1)[-1]
    return leaf == "okcd" or leaf.startswith(("ctxt", "txt", "pwd", "cmb", "chk", "rad"))


def _is_user_operation_event(event):
    event_type = str(event.get("event_type", ""))
    details = event.get("details", {}) or {}
    if event_type not in USER_OPERATION_EVENT_TYPES:
        return False
    if details.get("system_default") or details.get("user_action") is False:
        return False

    if event_type == "FIELD_CHANGE":
        if not _is_actionable_field_id(details.get("element_id", "")):
            return False
        if str(event.get("source", "")).lower() == "mcp":
            if not details.get("tcode") and not details.get("screen_number"):
                return False

    if event_type == "TCODE_CHANGE":
        from_tcode = str(details.get("from_tcode", "") or "").strip()
        to_tcode = str(details.get("to_tcode", "") or "").strip()
        if not to_tcode or from_tcode == to_tcode:
            return False
        if str(event.get("source", "")).lower() == "mcp" and not from_tcode:
            return False

    return True


def _is_context_event(event):
    return str(event.get("event_type", "")) in CONTEXT_EVENT_TYPES

def format_field_target(details):
    details = details or {}
    element_id = str(details.get("element_id", "?") or "?")
    table_id = str(details.get("table_id", "") or "")
    if table_id or "#r" in element_id:
        if not table_id:
            table_id = element_id.split("#r", 1)[0]
        row = details.get("row")
        column = str(
            details.get("column_title")
            or details.get("column")
            or (element_id.rsplit("#", 1)[-1] if "#" in element_id else "欄位")
        )
        try:
            row_label = int(row) + 1
        except (TypeError, ValueError):
            try:
                row_label = int(element_id.rsplit("#r", 1)[1].split("#", 1)[0]) + 1
            except Exception:
                row_label = "?"
        table_name = table_id.split("/")[-1] if table_id else "表格"
        return f"{table_name} 第{row_label}列/{column}"

    return str(
        details.get("label")
        or details.get("name")
        or (element_id.split("/")[-1] if "/" in element_id else element_id)
    )



# 錄製檔案儲存目錄
RECORDINGS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recordings")
SKILLS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "skills")


# 事件類型的中文描述
EVENT_TYPE_LABELS = {
    "TCODE_CHANGE": "🔄 交易切換",
    "SCREEN_CHANGE": "📄 畫面跳轉",
    "ACTIVE_WINDOW_CHANGE": "🪟 活動視窗",
    "WINDOW_OPEN": "🪟 彈窗開啟",
    "WINDOW_CLOSE": "🪟 彈窗關閉",
    "FOCUS_CHANGE": "🎯 焦點移動",
    "FIELD_CHANGE": "✏️ 欄位修改",
    "FIELD_DEFAULT": "🔹 系統預設值",
    "STATUS_MESSAGE": "💬 狀態訊息",
    "SAVE_ACTION": "💾 儲存",
    "SYSTEM_RESET": "🔄 系統重置",
}


class SAPRecorder:
    """
    SAP 操作紀錄管理器

    使用範例：
        recorder = SAPRecorder()
        recorder.start_recording("建立銷售訂單")
        recorder.add_event(event)  # 由 Monitor 回調觸發
        recorder.stop_recording()
        recordings = recorder.list_recordings()
    """

    def __init__(self):
        self._current_recording = None
        self._recording_name = None
        self._recording_start_time = None
        self._is_recording = False
        self._recording_start_ts = ""   # ISO timestamp 並列比對，過濾早於錄跟開始的事件
        self._recording_stop_ts = ""    # 停止錄製後記錄的時間，過濾歘留事件

        # 確保錄製目錄存在
        os.makedirs(RECORDINGS_DIR, exist_ok=True)

    @property
    def is_recording(self):
        """是否正在錄製"""
        return self._is_recording

    @property
    def recording_name(self):
        """當前錄製的名稱"""
        return self._recording_name

    def start_recording(self, name: str):
        """
        開始錄製新的 SOP。

        Args:
            name: SOP 名稱（會成為檔案名稱）

        Raises:
            RuntimeError: 如果已有錄製正在進行
        """
        if self._is_recording:
            raise RuntimeError(
                f"已有錄製正在進行: '{self._recording_name}'，"
                "請先執行 /stop 停止當前錄製"
            )

        self._recording_name = name
        self._recording_start_time = datetime.now()
        self._recording_start_ts = self._recording_start_time.isoformat()
        self._recording_stop_ts = ""  # 清空上次停止時間
        self._current_recording = {
            "name": name,
            "created_at": self._recording_start_time.isoformat(),
            "duration_seconds": 0,
            "event_count": 0,
            "raw_event_count": 0,
            "raw_events": [],
            "events": [],
            "context_event_count": 0,
            "context_events": [],
            "summary": "",
        }
        self._is_recording = True

        print(f"\033[1;31m  🔴 開始錄製: {name}\033[0m")
        print(f"\033[90m     在 SAP GUI 中操作，所有動作將被記錄\033[0m")
        print(f"\033[90m     輸入 /stop 結束錄製\033[0m")

    def stop_recording(self) -> str:
        """
        停止錄製並儲存。

        Returns:
            str: 儲存的檔案路徑

        Raises:
            RuntimeError: 如果沒有錄製正在進行
        """
        if not self._is_recording:
            raise RuntimeError("目前沒有正在進行的錄製")

        # 計算持續時間
        duration = (datetime.now() - self._recording_start_time).total_seconds()
        raw_events = self._current_recording["raw_events"]
        context_events = self._compact_context_events(
            self._current_recording.get("context_events", [])
        )
        compacted_events = self._compact_events(raw_events)

        self._current_recording["duration_seconds"] = round(duration, 1)
        self._current_recording["raw_event_count"] = len(raw_events)
        self._current_recording["events"] = compacted_events
        self._current_recording["event_count"] = len(compacted_events)
        self._current_recording["context_events"] = context_events
        self._current_recording["context_event_count"] = len(context_events)

        # 自動產生摘要
        self._current_recording["summary"] = self._generate_summary(
            self._current_recording["events"]
        )

        # 儲存到檔案
        filepath = self._get_filepath(self._recording_name)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self._current_recording, f, ensure_ascii=False, indent=2)

        event_count = self._current_recording["event_count"]
        raw_event_count = self._current_recording["raw_event_count"]
        context_count = self._current_recording["context_event_count"]
        print(f"\n\033[1;32m  ⏹️ 錄製完成: {self._recording_name}\033[0m")
        print(
            f"\033[90m     共記錄 {event_count} 個使用者操作"
            f"（原始操作 {raw_event_count} 個、畫面脈絡 {context_count} 個），"
            f"持續 {duration:.1f} 秒\033[0m"
        )
        print(f"\033[90m     已儲存至: {filepath}\033[0m")

        # 重置狀態（先記錄 stop 時間戳，再清空）
        self._recording_stop_ts = datetime.now().isoformat()  # 【方案 C】記錄停止時間
        self._is_recording = False
        self._current_recording = None
        name = self._recording_name
        self._recording_name = None
        self._recording_start_time = None

        return filepath

    def add_event(self, event: dict):
        """
        新增一筆操作紀錄（由 Monitor 的 on_event 回調觸發）。

        Args:
            event: 事件資料 dict
        """
        if not self._is_recording or not self._current_recording:
            return False

        # 【方案 C】timestamp guard：過濾停止錄製後才抵達的殘留事件
        # （monitor 尚未完全停止時，最後一次 poll 可能在 stop_recording 後才發送）
        event_ts = str(event.get("timestamp", "") or "")
        if event_ts and self._recording_stop_ts and event_ts > self._recording_stop_ts:
            return False
        event_type = event.get("event_type", "UNKNOWN")
        details = event.get("details", {})

        # 畫面跳轉/狀態訊息只作為 Skill 生成的脈絡，不算使用者操作。
        if _is_context_event(event):
            context_events = self._current_recording["context_events"]
            if not context_events or self._context_signature(context_events[-1]) != self._context_signature(event):
                context_events.append(event)
            return False

        # 僅保存能確認是使用者輸入、選取或按鍵的事件。
        if event_type in NOISY_EVENT_TYPES or not _is_user_operation_event(event):
            return False

        self._current_recording["raw_events"].append(event)

        # 即時在 CLI 顯示偵測到的事件
        label = EVENT_TYPE_LABELS.get(event_type, f"❓ {event_type}")

        if event_type == "TCODE_CHANGE":
            detail_str = f"{details.get('from_tcode', '?')} → {details.get('to_tcode', '?')}"
        elif event_type == "SCREEN_CHANGE":
            detail_str = f"畫面 {details.get('from_screen', '?')} → {details.get('to_screen', '?')}"
        elif event_type == "FIELD_CHANGE":
            elem_id = details.get("element_id", "?")
            short_id = elem_id.split("/")[-1] if "/" in elem_id else elem_id
            detail_str = f"{short_id} = \"{details.get('to_value', '')}\""
        elif event_type == "SAVE_ACTION":
            method = details.get("method", "unknown")
            suffix = "（由成功狀態推定）" if details.get("inferred") else ""
            detail_str = f"method={method}{suffix}"
        elif event_type in ("ACTIVE_WINDOW_CHANGE", "WINDOW_OPEN", "WINDOW_CLOSE"):
            detail_str = f"{details.get('window_id', details.get('to_window', '?'))} {details.get('title', '')}"
        elif event_type == "FOCUS_CHANGE":
            elem_id = details.get("to_element", "?")
            short_id = elem_id.split("/")[-1] if "/" in elem_id else elem_id
            detail_str = short_id
        elif event_type == "STATUS_MESSAGE":
            detail_str = f"[{details.get('type', '?')}] {details.get('text', '')}"
        else:
            detail_str = json.dumps(details, ensure_ascii=False)[:60]

        count = len(self._current_recording["raw_events"])
        print(f"\033[31m  📝 [{count:3d}] {label}  {detail_str}\033[0m")
        return True

    def list_recordings(self) -> list:
        """
        列出所有已錄製的 SOP。

        Returns:
            list[dict]: 錄製摘要清單
        """
        recordings = []

        if not os.path.exists(RECORDINGS_DIR):
            return recordings

        for filename in sorted(os.listdir(RECORDINGS_DIR)):
            if not filename.endswith(".json"):
                continue

            filepath = os.path.join(RECORDINGS_DIR, filename)
            try:
                data = self.load_recording(os.path.splitext(filename)[0])
                recordings.append({
                    "name": data.get("name", filename),
                    "created_at": data.get("created_at", ""),
                    "duration_seconds": data.get("duration_seconds", 0),
                    "event_count": data.get("event_count", 0),
                    "raw_event_count": data.get("raw_event_count", data.get("event_count", 0)),
                    "summary": data.get("summary", ""),
                    "filepath": filepath,
                })
            except (json.JSONDecodeError, IOError):
                continue

        return recordings

    def load_recording(self, name: str) -> dict:
        """
        載入指定的 SOP 錄製資料。

        Args:
            name: SOP 名稱

        Returns:
            dict: 完整的錄製資料

        Raises:
            FileNotFoundError: 找不到指定的錄製
        """
        filepath = self._get_filepath(name)
        if not os.path.exists(filepath):
            raise FileNotFoundError(f"找不到錄製: '{name}'")

        with open(filepath, "r", encoding="utf-8") as f:
            data = json.load(f)

        # 每次載入都重新清理，讓舊錄製也不會把系統載入值送進 Skill。
        data = dict(data)
        raw_events = data.get("raw_events")
        if raw_events is None:
            raw_events = data.get("events", [])
        derived_context = self._extract_context_events(raw_events)
        context_events = self._compact_context_events(
            list(data.get("context_events", [])) + derived_context
        )
        compacted_events = self._compact_events(raw_events)
        data["raw_events"] = raw_events
        data["raw_event_count"] = len(raw_events)
        data["events"] = compacted_events
        data["event_count"] = len(compacted_events)
        data["context_events"] = context_events
        data["context_event_count"] = len(context_events)
        data["summary"] = self._generate_summary(compacted_events)
        return data

    def get_recording_summary(self, name: str) -> str:
        """
        取得 SOP 的文字摘要（給 LLM 使用）。

        Args:
            name: SOP 名稱

        Returns:
            str: 摘要文字
        """
        try:
            data = self.load_recording(name)
        except FileNotFoundError:
            return f"找不到錄製: '{name}'"

        lines = [f"## SOP 錄製: {data.get('name', name)}"]
        lines.append(f"錄製時間: {data.get('created_at', 'N/A')}")
        lines.append(f"操作數量: {data.get('event_count', 0)}")
        if data.get("context_event_count"):
            lines.append(f"畫面脈絡數量: {data.get('context_event_count', 0)}")
        if data.get("raw_event_count") and data.get("raw_event_count") != data.get("event_count"):
            lines.append(f"原始事件數量: {data.get('raw_event_count')}")
        lines.append(f"持續時間: {data.get('duration_seconds', 0)} 秒")
        lines.append("")

        if data.get("summary"):
            lines.append(f"### 摘要")
            lines.append(data["summary"])
            lines.append("")

        lines.append("### 操作步驟")
        for i, event in enumerate(data.get("events", []), 1):
            event_type = event.get("event_type", "")
            details = event.get("details", {})
            timestamp = event.get("timestamp", "")

            if event_type == "TCODE_CHANGE":
                step = f"切換交易: {details.get('from_tcode', '?')} → {details.get('to_tcode', '?')}"
            elif event_type == "SCREEN_CHANGE":
                step = f"畫面跳轉: {details.get('from_screen', '?')} → {details.get('to_screen', '?')} ({details.get('title', '')})"
            elif event_type == "FIELD_CHANGE":
                target = format_field_target(details)
                action = "設定表格欄位" if details.get("table_id") or "#r" in str(details.get("element_id", "")) else "填入欄位"
                step = f"{action}: {target} = \"{details.get('to_value', '')}\""
            elif event_type == "FIELD_DEFAULT":
                elem_id = details.get("element_id", "?")
                short_id = elem_id.split("/")[-1] if "/" in elem_id else elem_id
                step = f"確認系統預設值: {short_id} = \"{details.get('to_value', '')}\""
            elif event_type == "KEY_PRESS":
                step = f"按鍵: {details.get('key_name', details.get('vkey', '?'))} ({details.get('element_id', 'wnd[0]')})"
            elif event_type == "BUTTON_CLICK":
                step = f"按下按鈕: {details.get('element_id', '?')} ({details.get('action', 'press')})"
            elif event_type == "SAVE_ACTION":
                method = details.get("method", "unknown")
                if details.get("inferred"):
                    step = "儲存文件（已由 SAP 成功狀態確認；實際觸發方式未記錄）"
                elif method == "button":
                    step = f"按下儲存按鈕: {details.get('element_id', '?')}"
                else:
                    step = f"執行儲存快捷鍵: {details.get('key_name', 'Ctrl+S/Save')}"
            elif event_type == "TAB_SELECT":
                step = f"選取頁籤: {details.get('element_id', '?')}"
            elif event_type == "ACTIVE_WINDOW_CHANGE":
                step = f"活動視窗變更: {details.get('from_window', '?')} → {details.get('to_window', '?')} ({details.get('title', '')})"
            elif event_type == "WINDOW_OPEN":
                step = f"彈窗開啟: {details.get('window_id', '?')} ({details.get('title', '')})"
            elif event_type == "WINDOW_CLOSE":
                step = f"彈窗關閉: {details.get('window_id', '?')} ({details.get('title', '')})"
            elif event_type == "FOCUS_CHANGE":
                elem_id = details.get("to_element", "?")
                short_id = elem_id.split("/")[-1] if "/" in elem_id else elem_id
                step = f"焦點移動: {short_id}"
            elif event_type == "STATUS_MESSAGE":
                step = f"狀態訊息: [{details.get('type', '?')}] {details.get('text', '')}"
            else:
                step = f"{event_type}: {json.dumps(details, ensure_ascii=False)[:50]}"

            lines.append(f"{i}. {step}")

        return "\n".join(lines)

    def save_recording(self, recording: dict) -> str:
        """
        將外部已建好的 recording dict（如 VBS 解析結果）直接儲存到 recordings/ 目錄。

        Returns:
            str: 儲存的檔案路徑
        """
        recording = dict(recording)
        raw_events = recording.get("raw_events", recording.get("events", []))
        context_events = self._compact_context_events(
            list(recording.get("context_events", [])) + self._extract_context_events(raw_events)
        )
        events = self._compact_events(raw_events)
        recording["raw_events"] = raw_events
        recording["raw_event_count"] = len(raw_events)
        recording["events"] = events
        recording["event_count"] = len(events)
        recording["context_events"] = context_events
        recording["context_event_count"] = len(context_events)
        recording["summary"] = self._generate_summary(events)

        name = recording.get("name", "import")
        filepath = self._get_filepath(name)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(recording, f, ensure_ascii=False, indent=2)
        return filepath

    def _get_filepath(self, name: str) -> str:
        """產生錄製檔案的完整路徑"""
        # 清理檔案名稱（移除不合法字元）
        safe_name = "".join(c for c in name if c.isalnum() or c in (" ", "_", "-", ".", "（", "）"))
        safe_name = safe_name.strip() or "unnamed"
        return os.path.join(RECORDINGS_DIR, f"{safe_name}.json")

    @staticmethod
    def _context_signature(event):
        details = event.get("details", {}) or {}
        event_type = event.get("event_type", "")
        keys = (
            "tcode", "from_screen", "to_screen", "from_window", "to_window",
            "window_id", "title", "type", "text", "reason", "change_count",
        )
        return event_type, tuple(str(details.get(key, "")) for key in keys)

    @staticmethod
    def _compact_context_events(events: list) -> list:
        compacted = []
        for event in events or []:
            if not _is_context_event(event):
                continue
            if compacted and SAPRecorder._context_signature(compacted[-1]) == SAPRecorder._context_signature(event):
                continue
            compacted.append(json.loads(json.dumps(event, ensure_ascii=False)))
        return compacted

    @staticmethod
    def _extract_context_events(events: list) -> list:
        """Extract screen outcomes and recover screen changes from old transient T-Code events."""
        context_events = []
        last_tcode = ""
        last_screen = ""

        for event in events or []:
            event_type = event.get("event_type", "")
            details = event.get("details", {}) or {}

            if _is_context_event(event):
                context_events.append(event)
                if event_type == "SCREEN_CHANGE":
                    last_tcode = str(details.get("tcode", last_tcode) or last_tcode)
                    last_screen = str(details.get("to_screen", last_screen) or last_screen)
                continue

            if event_type != "TCODE_CHANGE":
                continue

            to_tcode = str(details.get("to_tcode", "") or "").strip()
            from_tcode = str(details.get("from_tcode", "") or "").strip()
            screen_number = str(details.get("screen_number", "") or "")
            if _is_user_operation_event(event):
                last_tcode = to_tcode
                last_screen = screen_number or last_screen
                continue

            if (
                str(event.get("source", "")).lower() == "mcp"
                and not from_tcode
                and to_tcode
                and to_tcode == last_tcode
            ):
                if last_screen and screen_number and screen_number != last_screen:
                    context_events.append({
                        "timestamp": event.get("timestamp", ""),
                        "event_type": "SCREEN_CHANGE",
                        "source": "mcp_recovered",
                        "details": {
                            "tcode": to_tcode,
                            "from_screen": last_screen,
                            "to_screen": screen_number,
                            "title": details.get("title", ""),
                            "user_action": False,
                        },
                    })
                last_screen = screen_number or last_screen

        return SAPRecorder._compact_context_events(context_events)

    @staticmethod
    def _compact_events(events: list) -> list:
        """Keep only verified user operations and collapse incremental field typing."""
        compacted = []
        pending_field = None

        def flush_pending_field():
            nonlocal pending_field
            if pending_field:
                compacted.append(pending_field)
                pending_field = None

        for event in events or []:
            if not _is_user_operation_event(event):
                continue

            event_type = event.get("event_type", "")
            if event_type == "FIELD_CHANGE":
                details = event.get("details", {}) or {}
                element_id = details.get("field_key") or details.get("element_id", "")
                pending_details_current = pending_field.get("details", {}) if pending_field else {}
                pending_element_id = pending_details_current.get("field_key") or pending_details_current.get("element_id", "")
                if pending_field and pending_element_id == element_id:
                    pending_details = pending_field["details"]
                    pending_details["to_value"] = details.get("to_value", "")
                    pending_details["tcode"] = details.get("tcode", pending_details.get("tcode", ""))
                    pending_details["screen_number"] = details.get(
                        "screen_number", pending_details.get("screen_number", "")
                    )
                    pending_field["timestamp"] = event.get("timestamp", pending_field.get("timestamp", ""))
                else:
                    flush_pending_field()
                    pending_field = json.loads(json.dumps(event, ensure_ascii=False))
                continue

            flush_pending_field()
            if event_type == "TCODE_CHANGE" and compacted:
                previous = compacted[-1]
                if (
                    previous.get("event_type") == "TCODE_CHANGE"
                    and previous.get("details", {}).get("to_tcode")
                    == event.get("details", {}).get("to_tcode")
                ):
                    continue
            compacted.append(json.loads(json.dumps(event, ensure_ascii=False)))

        flush_pending_field()
        return compacted

    @staticmethod
    def _generate_summary(events: list) -> str:
        """
        根據事件清單自動產生摘要文字。

        Args:
            events: 事件清單

        Returns:
            str: 摘要文字
        """
        if not events:
            return "（無操作紀錄）"

        parts = []
        tcodes = []
        field_changes = 0
        screen_events = 0
        save_actions = 0

        for event in events:
            event_type = event.get("event_type", "")
            details = event.get("details", {})

            if event_type == "TCODE_CHANGE":
                to_tcode = details.get("to_tcode", "?")
                if to_tcode not in tcodes:
                    tcodes.append(to_tcode)
            elif event_type == "FIELD_CHANGE":
                field_changes += 1
            elif event_type == "SAVE_ACTION":
                save_actions += 1
            elif event_type in ("SCREEN_CHANGE", "ACTIVE_WINDOW_CHANGE", "WINDOW_OPEN", "WINDOW_CLOSE"):
                screen_events += 1

        if tcodes:
            parts.append(f"涉及交易: {' → '.join(tcodes)}")
        if field_changes:
            parts.append(f"修改了 {field_changes} 個欄位")
        if save_actions:
            parts.append(f"執行了 {save_actions} 次儲存")
        if screen_events:
            parts.append(f"偵測到 {screen_events} 個畫面/視窗變化")
        parts.append(f"共 {len(events)} 個操作步驟")

        return "，".join(parts)

    def generate_sop_with_llm(self, name, events, auth=None, screen_state=None, provider=None, provider_name=None, context_events=None):
        """
        使用 LLM 將錄製的 raw events 轉換為自然語言 SOP 指南。

        Args:
            name: SOP 名稱
            events: compacted events 列表
            auth: GitHub Copilot provider 使用的認證物件
            screen_state: 停止錄製時的畫面掃描（可選）
            provider: 已建立的 LLM provider（可選）
            provider_name: provider 名稱（可選）

        Returns:
            str: 儲存的 .md 檔案路徑，失敗時回傳空字串
        """
        # Defense in depth: never send dirty legacy events directly to the LLM.
        verified_events = self._compact_events(events)
        derived_context = self._extract_context_events(events)
        verified_context = self._compact_context_events(
            list(context_events or []) + derived_context
        )

        # Use compact JSON to minimise token usage
        events = verified_events
        events_json = json.dumps(events, ensure_ascii=False)
        context_json = json.dumps(verified_context, ensure_ascii=False)
        if len(context_json) > 40_000:
            context_json = context_json[:40_000] + "... (truncated)"

        # Truncate events list if the JSON is still too large (~4 chars per token,
        # target ≤ 80 k tokens for events to leave headroom for prompt + screen context)
        MAX_EVENTS_CHARS = 320_000
        truncated = False
        if len(events_json) > MAX_EVENTS_CHARS:
            truncated_events: list = []
            total = 0
            for ev in events:
                s = json.dumps(ev, ensure_ascii=False)
                if total + len(s) > MAX_EVENTS_CHARS:
                    break
                truncated_events.append(ev)
                total += len(s)
            events_json = json.dumps(truncated_events, ensure_ascii=False)
            truncated = True

        truncation_note = (
            "\n\n> ⚠️ 注意：錄製事件數量過多，已自動截斷，僅包含前段事件。"
            if truncated else ""
        )

        screen_context = ""
        if screen_state:
            screen_json = json.dumps(screen_state, ensure_ascii=False, indent=2)
            # Cap screen state to ~5 k tokens worth of characters
            MAX_SCREEN_CHARS = 20_000
            if len(screen_json) > MAX_SCREEN_CHARS:
                screen_json = screen_json[:MAX_SCREEN_CHARS] + "\n... (截斷，僅顯示前部分)"
            screen_context = f"\n\n## 停止錄製時的畫面狀態\n```json\n{screen_json}\n```"

        prompt = f"""請將以下 SAP GUI 操作錄製事件整理成一份清晰的自然語言操作指南（SOP）。

## 錄製名稱
{name}

## 已驗證的使用者操作（JSON）
```json
{events_json}
```

## 畫面結果與系統脈絡（JSON，僅供驗證結果，不是使用者操作）
```json
{context_json}
```
{truncation_note}{screen_context}

## 要求
1. 用繁體中文撰寫
2. 每個步驟要清楚說明：操作什麼、在哪裡操作、填入什麼值
3. 如果事件中有 element_id，在步驟中附註元件 ID，方便系統定位
4. 步驟應該是使用者可以跟著做的指引，不是技術日誌
5. 在開頭簡述這個 SOP 的目的
6. 如果有 T-Code，明確說明要進入哪個交易
7. 「已驗證的使用者操作」是唯一可轉成操作步驟的資料；不要把畫面標籤、唯讀輸出值、Frame、Container 或系統載入值寫成使用者輸入
8. 「畫面結果與系統脈絡」只能用來確認前一步結果、彈窗或畫面跳轉；不得單獨編造成點擊、輸入或選取動作
9. 如果脈絡顯示畫面已跳轉但缺少明確按鍵事件，可以寫成「完成前述輸入後執行/按 Enter，確認進入下一畫面」，並標註此動作是由畫面結果推定
10. 如果欄位在畫面上已有可接受的目前值，Study Mode 應引導使用者確認沿用，而不是要求重新輸入錄製值
11. element_id 格式為 `{{table_id}}#r{{row}}#{{col}}` 的是 ALV Grid 或 GuiTableControl cell，row 從 0 起算；優先使用 details.column_title / column，描述為「表格第 N 列的 XXX 欄位」，N = row+1
12. 嚴格維持事件順序；KEY_PRESS、BUTTON_CLICK、TAB_SELECT、SAVE_ACTION 必須各自轉成明確步驟。SAVE_ACTION.inferred=true 代表已由成功狀態確認儲存，但不得虛構為點擊按鈕或 Ctrl+S；inferred=false 時才依 method 描述實際按鈕或快捷鍵
13. 對客戶編號、採購單號、物料號、數量、價格等每次會改變的業務值，使用「輸入適當的值（參考值：`錄製值`）」；T-Code、固定文件類型或固定選項可直接寫錄製值
14. 若同一欄位有逐字輸入，僅使用 compact event 的最終值；不得把中途字串（例如 `o`、`cu`）當作完成值
15. SYSTEM_RESET 是 SAP 在成功儲存後自動清空或重設畫面的系統結果，只能作為脈絡，不得產生刪除、清空或取消勾選步驟
16. 如果已驗證操作不足以形成完整流程，加入「待人工確認」段落，不得用 SAP 常識補造不存在的步驟
17. 直接輸出 Markdown 格式的 SOP，不要加額外的包裝或說明

## 輸出格式範例
# SOP: [名稱]

## 目的
[簡述]

## 前提條件
- 已登入 SAP GUI

## 操作步驟
1. 進入交易 XX01 — 在 T-Code 欄位（wnd[0]/tbar[0]/okcd）輸入 "XX01" 並按 Enter
2. 在「欄位名稱」欄位（wnd[0]/usr/ctxtXXX）填入 "值"
3. ...
"""

        try:
            llm = provider or create_llm_provider(normalize_provider_name(provider_name), auth=auth)
            result = llm.chat_completions(
                [
                    {"role": "system", "content": "你是一個 SAP GUI 操作文件撰寫專家。你的任務是將 JSON 格式的操作錄製事件轉換成清晰的自然語言操作指南。"},
                    {"role": "user", "content": prompt},
                ],
                tools=None,
            )
            choices = result.get("choices", [])
            if not choices:
                print("\033[31m[Recorder] SOP 生成失敗: API 回應無 choices\033[0m")
                return ""

            sop_text = choices[0].get("message", {}).get("content", "")
            if not sop_text.strip():
                print("\033[31m[Recorder] SOP 生成失敗: 回傳內容為空\033[0m")
                return ""

            os.makedirs(SKILLS_DIR, exist_ok=True)
            safe_name = "".join(c for c in name if c.isalnum() or c in (" ", "_", "-", ".", "（", "）"))
            safe_name = safe_name.strip() or "unnamed"
            filepath = os.path.join(SKILLS_DIR, f"{safe_name}.md")
            with open(filepath, "w", encoding="utf-8") as f:
                f.write(sop_text)

            print(f"\033[1;32m  📄 已生成自然語言 SOP: {filepath}\033[0m")
            return filepath
        except Exception as exc:
            print(f"\033[31m[Recorder] SOP 生成失敗: {exc}\033[0m")
            return ""
