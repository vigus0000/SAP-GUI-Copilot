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
from datetime import datetime


# 錄製檔案儲存目錄
RECORDINGS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "recordings")


# 事件類型的中文描述
EVENT_TYPE_LABELS = {
    "TCODE_CHANGE": "🔄 交易切換",
    "SCREEN_CHANGE": "📄 畫面跳轉",
    "FIELD_CHANGE": "✏️ 欄位修改",
    "STATUS_MESSAGE": "💬 狀態訊息",
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
        self._current_recording = {
            "name": name,
            "created_at": self._recording_start_time.isoformat(),
            "duration_seconds": 0,
            "event_count": 0,
            "events": [],
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
        self._current_recording["duration_seconds"] = round(duration, 1)
        self._current_recording["event_count"] = len(self._current_recording["events"])

        # 自動產生摘要
        self._current_recording["summary"] = self._generate_summary(
            self._current_recording["events"]
        )

        # 儲存到檔案
        filepath = self._get_filepath(self._recording_name)
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(self._current_recording, f, ensure_ascii=False, indent=2)

        event_count = self._current_recording["event_count"]
        print(f"\n\033[1;32m  ⏹️ 錄製完成: {self._recording_name}\033[0m")
        print(f"\033[90m     共記錄 {event_count} 個操作，持續 {duration:.1f} 秒\033[0m")
        print(f"\033[90m     已儲存至: {filepath}\033[0m")

        # 重置狀態
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
            return

        self._current_recording["events"].append(event)

        # 即時在 CLI 顯示偵測到的事件
        event_type = event.get("event_type", "UNKNOWN")
        label = EVENT_TYPE_LABELS.get(event_type, f"❓ {event_type}")
        details = event.get("details", {})

        if event_type == "TCODE_CHANGE":
            detail_str = f"{details.get('from_tcode', '?')} → {details.get('to_tcode', '?')}"
        elif event_type == "SCREEN_CHANGE":
            detail_str = f"畫面 {details.get('from_screen', '?')} → {details.get('to_screen', '?')}"
        elif event_type == "FIELD_CHANGE":
            elem_id = details.get("element_id", "?")
            # 只顯示最後一段 ID（更簡潔）
            short_id = elem_id.split("/")[-1] if "/" in elem_id else elem_id
            detail_str = f"{short_id} = \"{details.get('to_value', '')}\""
        elif event_type == "STATUS_MESSAGE":
            detail_str = f"[{details.get('type', '?')}] {details.get('text', '')}"
        else:
            detail_str = json.dumps(details, ensure_ascii=False)[:60]

        count = len(self._current_recording["events"])
        print(f"\033[31m  📝 [{count:3d}] {label}  {detail_str}\033[0m")

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
                with open(filepath, "r", encoding="utf-8") as f:
                    data = json.load(f)
                recordings.append({
                    "name": data.get("name", filename),
                    "created_at": data.get("created_at", ""),
                    "duration_seconds": data.get("duration_seconds", 0),
                    "event_count": data.get("event_count", 0),
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
            return json.load(f)

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
                elem_id = details.get("element_id", "?")
                short_id = elem_id.split("/")[-1] if "/" in elem_id else elem_id
                step = f"填入欄位: {short_id} = \"{details.get('to_value', '')}\""
            elif event_type == "STATUS_MESSAGE":
                step = f"狀態訊息: [{details.get('type', '?')}] {details.get('text', '')}"
            else:
                step = f"{event_type}: {json.dumps(details, ensure_ascii=False)[:50]}"

            lines.append(f"{i}. {step}")

        return "\n".join(lines)

    def _get_filepath(self, name: str) -> str:
        """產生錄製檔案的完整路徑"""
        # 清理檔案名稱（移除不合法字元）
        safe_name = "".join(c for c in name if c.isalnum() or c in (" ", "_", "-", ".", "（", "）"))
        safe_name = safe_name.strip() or "unnamed"
        return os.path.join(RECORDINGS_DIR, f"{safe_name}.json")

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

        for event in events:
            event_type = event.get("event_type", "")
            details = event.get("details", {})

            if event_type == "TCODE_CHANGE":
                to_tcode = details.get("to_tcode", "?")
                if to_tcode not in tcodes:
                    tcodes.append(to_tcode)
            elif event_type == "FIELD_CHANGE":
                field_changes += 1

        if tcodes:
            parts.append(f"涉及交易: {' → '.join(tcodes)}")
        if field_changes:
            parts.append(f"修改了 {field_changes} 個欄位")
        parts.append(f"共 {len(events)} 個操作步驟")

        return "，".join(parts)
