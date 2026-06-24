"""Import SAP GUI Scripting recorder VBScript files as SAP_Copilot recordings."""

from __future__ import annotations

from datetime import datetime, timedelta
import re


_FIND_BY_ID = r'(?:session\.)?findById\(\s*"(?P<id>[^"]+)"\s*\)'
_ASSIGNMENT_RE = re.compile(
    _FIND_BY_ID
    + r"\.(?P<property>text|key|selected)\s*=\s*(?P<value>.+?)\s*$",
    re.IGNORECASE,
)
_METHOD_RE = re.compile(
    _FIND_BY_ID
    + r"\.(?P<method>press|select|doubleClick|setFocus)\s*$",
    re.IGNORECASE,
)
_VKEY_RE = re.compile(
    _FIND_BY_ID + r"\.sendVKey\s+(?P<vkey>\d+)",
    re.IGNORECASE,
)
_MODIFY_CELL_RE = re.compile(
    _FIND_BY_ID
    + r"\.modifyCell\s+(?P<row>\d+)\s*,\s*"
    r'"(?P<column>[^"]+)"\s*,\s*(?P<value>.+?)\s*$',
    re.IGNORECASE,
)


def _short_id(element_id: str) -> str:
    value = str(element_id or "")
    start = value.find("wnd[")
    return value[start:] if start >= 0 else value


def _parse_vbs_value(raw_value: str):
    value = str(raw_value or "").strip()
    if value.startswith('"') and value.endswith('"'):
        return value[1:-1].replace('""', '"')
    lowered = value.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if re.fullmatch(r"-?\d+(?:\.\d+)?", value):
        return value
    return value


def _event(event_type: str, details: dict, sequence: int) -> dict:
    timestamp = datetime.now() + timedelta(milliseconds=sequence)
    return {
        "timestamp": timestamp.isoformat(timespec="milliseconds"),
        "event_type": event_type,
        "source": "vbs_import",
        "details": details,
    }


def _compact_field_events(events: list[dict]) -> list[dict]:
    """Keep only the last consecutive write to the same field/cell."""
    compacted = []
    pending = None

    def flush():
        nonlocal pending
        if pending is not None:
            compacted.append(pending)
            pending = None

    for event in events:
        if event.get("event_type") != "FIELD_CHANGE":
            flush()
            compacted.append(event)
            continue

        element_id = event.get("details", {}).get("element_id", "")
        if pending and pending.get("details", {}).get("element_id") == element_id:
            pending = event
        else:
            flush()
            pending = event
    flush()
    return compacted


def parse_vbs(vbs_text: str, name: str = "import") -> dict:
    """Parse common SAP GUI recorder statements into a recording dictionary."""
    raw_events = []
    pending_tcode = ""
    current_tcode = ""
    sequence = 0

    for raw_line in str(vbs_text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("'"):
            continue

        match = _MODIFY_CELL_RE.search(line)
        if match:
            element_id = _short_id(match.group("id"))
            row = int(match.group("row"))
            column = match.group("column")
            value = _parse_vbs_value(match.group("value"))
            raw_events.append(_event("FIELD_CHANGE", {
                "element_id": f"{element_id}#r{row}#{column}",
                "from_value": "",
                "to_value": str(value),
                "control_type": "GuiGridView",
                "grid_id": element_id,
                "row": row,
                "column": column,
                "tcode": current_tcode,
            }, sequence))
            sequence += 1
            continue

        match = _ASSIGNMENT_RE.search(line)
        if match:
            element_id = _short_id(match.group("id"))
            property_name = match.group("property").lower()
            value = _parse_vbs_value(match.group("value"))

            if element_id.endswith("/tbar[0]/okcd") and property_name == "text":
                pending_tcode = str(value).lstrip("/n").strip()
                continue

            details = {
                "element_id": element_id,
                "from_value": "",
                "to_value": str(value),
                "property": property_name,
                "tcode": current_tcode,
            }
            if property_name == "selected":
                details["control_type"] = "GuiCheckBox"
                details["selected"] = bool(value)
            elif property_name == "key":
                details["control_type"] = "GuiComboBox"
                details["key"] = str(value)
            raw_events.append(_event("FIELD_CHANGE", details, sequence))
            sequence += 1
            continue

        match = _VKEY_RE.search(line)
        if match:
            element_id = _short_id(match.group("id"))
            vkey = int(match.group("vkey"))
            if pending_tcode and vkey == 0:
                raw_events.append(_event("TCODE_CHANGE", {
                    "from_tcode": current_tcode,
                    "to_tcode": pending_tcode,
                    "element_id": "wnd[0]/tbar[0]/okcd",
                }, sequence))
                current_tcode = pending_tcode
                pending_tcode = ""
            else:
                raw_events.append(_event("BUTTON_CLICK", {
                    "element_id": element_id,
                    "action": "send_vkey",
                    "vkey": vkey,
                    "tcode": current_tcode,
                }, sequence))
            sequence += 1
            continue

        match = _METHOD_RE.search(line)
        if match:
            element_id = _short_id(match.group("id"))
            method = match.group("method").lower()
            if method == "setfocus":
                continue
            event_type = "TAB_SELECT" if method == "select" and "/tabp" in element_id else "BUTTON_CLICK"
            raw_events.append(_event(event_type, {
                "element_id": element_id,
                "action": method,
                "tcode": current_tcode,
            }, sequence))
            sequence += 1

    events = _compact_field_events(raw_events)
    tcodes = []
    for event in events:
        if event.get("event_type") == "TCODE_CHANGE":
            tcode = event.get("details", {}).get("to_tcode", "")
            if tcode and tcode not in tcodes:
                tcodes.append(tcode)

    field_count = sum(event.get("event_type") == "FIELD_CHANGE" for event in events)
    action_count = sum(event.get("event_type") in {"BUTTON_CLICK", "TAB_SELECT"} for event in events)
    summary_parts = []
    if tcodes:
        summary_parts.append(f"涉及交易: {' -> '.join(tcodes)}")
    if field_count:
        summary_parts.append(f"修改了 {field_count} 個欄位")
    if action_count:
        summary_parts.append(f"記錄了 {action_count} 個按鈕/頁籤動作")
    summary_parts.append(f"共 {len(events)} 個操作步驟")

    return {
        "name": str(name or "import").strip() or "import",
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "duration_seconds": 0,
        "event_count": len(events),
        "raw_event_count": len(raw_events),
        "raw_events": raw_events,
        "events": events,
        "summary": "，".join(summary_parts),
        "source": "vbs_import",
        "format": "json",
    }
