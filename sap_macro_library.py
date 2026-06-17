"""
Structured Markdown macro support for SAP Auto / Study workflows.

Macros are intentionally deterministic.  They are not a replacement for
recordings or skills; they are a compact way to replay known GUI element IDs
with a small set of runtime inputs.
"""

from __future__ import annotations

import os
import re
import shlex
import time
import json
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable, Iterable


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))


def _resolve_macro_dir(value: str = "") -> str:
    configured = str(value or "").strip()
    if not configured:
        return os.path.join(ROOT_DIR, "macros")
    if os.path.isabs(configured):
        return configured
    return os.path.join(ROOT_DIR, configured)


MACROS_DIR = _resolve_macro_dir(os.getenv("SAP_MACRO_DIR"))
MACRO_STEP_SCAN_RETRIES = int(os.getenv("SAP_MACRO_STEP_SCAN_RETRIES", "5"))
MACRO_STEP_SCAN_RETRY_SECONDS = float(os.getenv("SAP_MACRO_STEP_SCAN_RETRY_SECONDS", "0.4"))
SAP_MACRO_RESOLVE_MIN_CONFIDENCE = float(os.getenv("SAP_MACRO_RESOLVE_MIN_CONFIDENCE", "0.85"))
SAP_MACRO_PROMOTE_PRIMARY_AFTER = int(os.getenv("SAP_MACRO_PROMOTE_PRIMARY_AFTER", "3"))


def _resolve_macro_path(value: str, default_relative: str) -> str:
    configured = str(value or "").strip() or default_relative
    if os.path.isabs(configured):
        return configured
    return os.path.join(ROOT_DIR, configured)


def _env_bool_early(name: str, default: str = "false") -> bool:
    return str(os.getenv(name, default)).strip().lower() in {"1", "true", "yes", "y", "on"}


SAP_MACRO_LEARNING_ENABLED = _env_bool_early("SAP_MACRO_LEARNING_ENABLED", "true")
SAP_MACRO_AUTO_WRITEBACK = _env_bool_early("SAP_MACRO_AUTO_WRITEBACK", "true")
MACRO_LEARNED_INDEX = _resolve_macro_path(os.getenv("SAP_MACRO_LEARNED_INDEX"), "macros/_learned_index.json")
MACRO_BACKUP_DIR = _resolve_macro_path(os.getenv("SAP_MACRO_BACKUP_DIR"), "macros/_backups")


PLACEHOLDER_RE = re.compile(r"\{\{\s*([A-Za-z_][\w.-]*)(?:\|([^}]+))?\s*\}\}")


class SAPMacroError(ValueError):
    """Raised when a macro file or invocation is invalid."""


def macro_result_has_unsafe_actions(result: dict) -> bool:
    """Return True when fallback could overwrite user/SAP state after a macro failure."""
    unsafe_actions = {
        "sap_set_field",
        "sap_set_batch_fields",
        "sap_set_fields_and_enter",
        "sap_select_checkbox",
        "sap_select_radio_button",
        "sap_select_combobox_entry",
        "sap_press_button",
        "sap_select_tab",
        "sap_select_table_row",
        "sap_select_popup_table_row_and_confirm",
        "sap_handle_popup",
        "sap_set_textedit",
    }

    def walk(items):
        for item in items or []:
            if not isinstance(item, dict):
                continue
            yield item
            nested = item.get("results")
            if isinstance(nested, list):
                yield from walk(nested)

    for item in walk((result or {}).get("results", [])):
        backend = str(item.get("backend") or "")
        action = str(item.get("action") or "")
        if backend == "macro_validation":
            continue
        if backend == "macro" and action == "skip_if_empty":
            continue
        if action in {"start_checks", "step_targets", "end_checks"}:
            continue
        if action in unsafe_actions:
            return True
    return False


def macro_result_has_effectful_actions(result: dict) -> bool:
    """Backward compatible alias for callers that need unsafe-action detection."""
    return macro_result_has_unsafe_actions(result)


def macro_result_can_fallback_to_auto(result: dict) -> bool:
    """Autoroute can fall back when macro failed before any field/button mutation."""
    return not (result or {}).get("success", True) and not macro_result_has_unsafe_actions(result or {})


@dataclass
class MacroInput:
    name: str
    label: str = ""
    default: str = ""
    required: bool = True
    description: str = ""


@dataclass
class MacroStep:
    index: int
    action: str
    element_id: str = ""
    value: str = ""
    label: str = ""
    description: str = ""
    required: bool = False
    mode: str = "both"
    options: dict = field(default_factory=dict)


@dataclass
class MacroCheck:
    section: str
    kind: str
    target: str = ""
    value: str = ""
    required: bool = True
    description: str = ""
    options: dict = field(default_factory=dict)


@dataclass
class SAPMacro:
    name: str
    path: str
    description: str = ""
    mode: str = "both"
    tags: list[str] = field(default_factory=list)
    inputs: list[MacroInput] = field(default_factory=list)
    start_checks: list[MacroCheck] = field(default_factory=list)
    steps: list[MacroStep] = field(default_factory=list)
    end_checks: list[MacroCheck] = field(default_factory=list)
    metadata: dict = field(default_factory=dict)


def _safe_name(name: str) -> str:
    return "".join(ch for ch in str(name or "") if ch.isalnum() or ch in (" ", "_", "-", ".", "（", "）")).strip()


def _lookup_key(value: str) -> str:
    return "".join(ch.lower() for ch in str(value or "") if ch.isalnum())


def _split_csv(value: str) -> list[str]:
    return [item.strip() for item in str(value or "").split(",") if item.strip()]


def _split_aliases(value: str) -> list[str]:
    return [
        item.strip()
        for item in re.split(r"[,，;；\n]+", str(value or ""))
        if item.strip()
    ]


def _parse_bool(value, default=False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"1", "true", "yes", "y", "on", "x", "勾選", "是", "必填"}:
        return True
    if text in {"0", "false", "no", "n", "off", "", "取消", "否", "選填"}:
        return False
    return default


def _strip_quotes(value: str) -> str:
    text = str(value or "").strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in {"'", '"'}:
        return text[1:-1]
    return text


def _parse_front_matter(markdown_text: str) -> tuple[dict, str]:
    lines = markdown_text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, markdown_text

    end_index = None
    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            end_index = index
            break
    if end_index is None:
        return {}, markdown_text

    metadata = {}
    for line in lines[1:end_index]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or ":" not in stripped:
            continue
        key, value = stripped.split(":", 1)
        metadata[key.strip().lower()] = _strip_quotes(value.strip())

    return metadata, "\n".join(lines[end_index + 1 :])


def _normalize_header(value: str, table_heading: str = "") -> str:
    text = str(value or "").strip().lower().replace(" ", "_").replace("-", "_")
    heading = str(table_heading or "").lower()
    is_input_table = any(token in heading for token in ("input", "輸入", "參數", "變數"))
    is_boundary_table = any(token in heading for token in ("start", "end", "起始", "開始", "結束", "完成"))
    aliases = {
        "no": "index",
        "#": "index",
        "step": "index",
        "序號": "index",
        "步驟": "index",
        "action_type": "action",
        "type": "action",
        "動作": "action",
        "動作類型": "action",
        "condition": "condition",
        "check": "condition",
        "檢查": "condition",
        "條件": "condition",
        "target": "target",
        "目標": "target",
        "element": "element_id",
        "id": "element_id",
        "gui_id": "element_id",
        "field_id": "element_id",
        "元件": "element_id",
        "元件id": "element_id",
        "元件_id": "element_id",
        "元件號碼": "element_id",
        "gui元件號碼": "element_id",
        "element_type": "element_type",
        "expected_type": "element_type",
        "match_type": "element_type",
        "元件類型": "element_type",
        "expected_label": "expected_label",
        "match_label": "expected_label",
        "label_contains": "expected_label",
        "預期標籤": "expected_label",
        "expected_text": "expected_text",
        "match_text": "expected_text",
        "text_contains": "expected_text",
        "預期文字": "expected_text",
        "expected_value": "expected_value",
        "match_value": "expected_value",
        "預期值": "expected_value",
        "input": "value",
        "input_value": "value",
        "輸入": "value",
        "輸入值": "value",
        "值": "value",
        "title": "label",
        "欄位": "label",
        "欄位名稱": "label",
        "instruction": "description",
        "note": "description",
        "說明": "description",
        "描述": "description",
        "required": "required",
        "必填": "required",
        "模式": "mode",
        "變數": "name",
        "變數名": "name",
        "key": "name",
        "default": "default",
        "預設": "default",
        "預設值": "default",
    }
    if is_boundary_table:
        if text in {"name", "名稱", "key", "type", "類型"}:
            return "condition"
        if text in {"value", "值", "expected", "預期"}:
            return "value"
    if text in {"name", "名稱"}:
        return "name" if is_input_table else "label"
    return aliases.get(text, text)


def _split_markdown_row(line: str) -> list[str]:
    text = line.strip()
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|"):
        text = text[:-1]
    cells = []
    current = []
    in_placeholder = False
    index = 0
    while index < len(text):
        char = text[index]
        next_two = text[index : index + 2]
        if next_two == "{{":
            in_placeholder = True
            current.append(next_two)
            index += 2
            continue
        if next_two == "}}" and in_placeholder:
            in_placeholder = False
            current.append(next_two)
            index += 2
            continue
        if char == "\\" and index + 1 < len(text) and text[index + 1] == "|":
            current.append("|")
            index += 2
            continue
        if char == "|" and not in_placeholder:
            cells.append("".join(current).replace("<br>", "\n").strip())
            current = []
        else:
            current.append(char)
        index += 1
    cells.append("".join(current).replace("<br>", "\n").strip())
    return cells


def _format_markdown_row(cells: list[str]) -> str:
    return "| " + " | ".join(str(cell or "").strip() for cell in cells) + " |"


def _is_separator_row(line: str) -> bool:
    cells = _split_markdown_row(line)
    return bool(cells) and all(re.fullmatch(r":?-{3,}:?", cell.strip()) for cell in cells if cell.strip())


def _iter_markdown_tables(markdown_text: str) -> Iterable[tuple[str, list[dict]]]:
    lines = markdown_text.splitlines()
    current_heading = ""
    index = 0
    while index < len(lines):
        stripped = lines[index].strip()
        if stripped.startswith("#"):
            current_heading = stripped.lstrip("#").strip().lower()
            index += 1
            continue
        if "|" not in stripped or index + 1 >= len(lines) or not _is_separator_row(lines[index + 1]):
            index += 1
            continue

        headers = [_normalize_header(cell, current_heading) for cell in _split_markdown_row(lines[index])]
        rows = []
        index += 2
        while index < len(lines) and "|" in lines[index].strip():
            values = _split_markdown_row(lines[index])
            row = {}
            for pos, header in enumerate(headers):
                row[header] = values[pos] if pos < len(values) else ""
            rows.append(row)
            index += 1
        yield current_heading, rows


def _placeholder_options(option_text: str) -> dict:
    options = {}
    for part in str(option_text or "").split("|"):
        if "=" in part:
            key, value = part.split("=", 1)
            options[key.strip().lower()] = _strip_quotes(value.strip())
    return options


def _placeholders(value: str) -> list[tuple[str, dict]]:
    found = []
    for match in PLACEHOLDER_RE.finditer(str(value or "")):
        found.append((match.group(1), _placeholder_options(match.group(2) or "")))
    return found


def _replace_placeholders(value: str, runtime_values: dict, input_map: dict) -> tuple[str, list[str]]:
    missing = []

    def replace(match):
        name = match.group(1)
        inline_options = _placeholder_options(match.group(2) or "")
        definition = input_map.get(name)
        default = inline_options.get("default")
        if definition and definition.default and default is None:
            default = definition.default

        if name in runtime_values and str(runtime_values.get(name, "")) != "":
            return str(runtime_values.get(name))
        if default is not None:
            return str(default)
        missing.append(name)
        return ""

    return PLACEHOLDER_RE.sub(replace, str(value or "")), missing


def _short_sap_id(value: str) -> str:
    text = str(value or "")
    start = text.find("wnd[")
    return text[start:] if start >= 0 else text


def _norm_match_text(value: str) -> str:
    return str(value or "").strip().lower()


class SAPMacroLibrary:
    """Load and execute structured SAP macros from Markdown files."""

    FIELD_ACTIONS = {"input", "set_text", "set_field", "field", "value", "text"}

    ACTION_ALIASES = {
        "填值": "input",
        "輸入值": "input",
        "輸入": "input",
        "field": "input",
        "text": "input",
        "t_code": "tcode",
        "t-code": "tcode",
        "transaction": "tcode",
        "交易": "tcode",
        "checkbox": "checkbox",
        "check": "checkbox",
        "勾選": "checkbox",
        "取消勾選": "checkbox",
        "radio": "radio",
        "選radio": "radio",
        "combobox": "combo",
        "dropdown": "combo",
        "select": "combo",
        "下拉": "combo",
        "button": "click",
        "press": "click",
        "click": "click",
        "點擊": "click",
        "按鈕": "click",
        "enter": "key",
        "send_key": "key",
        "key": "key",
        "vkey": "key",
        "按鍵": "key",
        "tab": "tab",
        "頁籤": "tab",
        "popup": "popup",
        "彈窗": "popup",
        "table_row": "table_row",
        "選列": "table_row",
        "popup_table_confirm": "popup_table_confirm",
        "textedit": "textedit",
        "editor": "textedit",
        "focus": "focus",
        "定位": "focus",
        "wait": "wait",
        "等待": "wait",
        "skip": "skip",
        "comment": "skip",
    }

    KEY_ALIASES = {
        "0": "Enter",
        "3": "Back",
        "8": "Execute",
        "11": "Save",
        "12": "Cancel",
        "f3": "Back",
        "f8": "Execute",
        "f11": "Save",
        "f12": "Cancel",
        "enter": "Enter",
        "execute": "Execute",
        "save": "Save",
        "back": "Back",
        "cancel": "Cancel",
        "執行": "Execute",
        "儲存": "Save",
        "返回": "Back",
        "取消": "Cancel",
    }

    def __init__(self, macro_dirs=None):
        self.macro_dirs = macro_dirs or [MACROS_DIR]
        self.strict_match = _parse_bool(os.getenv("SAP_MACRO_STRICT_MATCH", "true"), True)
        self.require_boundaries = _parse_bool(os.getenv("SAP_MACRO_REQUIRE_BOUNDARIES", "true"), True)
        self.autoroute_enabled = _parse_bool(os.getenv("SAP_MACRO_AUTOROUTE_ENABLED", "true"), True)
        self.autoroute_min_score = int(os.getenv("SAP_MACRO_AUTOROUTE_MIN_SCORE", "70"))
        self.learning_enabled = _parse_bool(os.getenv("SAP_MACRO_LEARNING_ENABLED", str(SAP_MACRO_LEARNING_ENABLED)), SAP_MACRO_LEARNING_ENABLED)
        self.auto_writeback = _parse_bool(os.getenv("SAP_MACRO_AUTO_WRITEBACK", str(SAP_MACRO_AUTO_WRITEBACK)), SAP_MACRO_AUTO_WRITEBACK)
        self.learned_index_path = _resolve_macro_path(os.getenv("SAP_MACRO_LEARNED_INDEX"), "macros/_learned_index.json")
        self.backup_dir = _resolve_macro_path(os.getenv("SAP_MACRO_BACKUP_DIR"), "macros/_backups")
        self.resolve_min_confidence = float(os.getenv("SAP_MACRO_RESOLVE_MIN_CONFIDENCE", str(SAP_MACRO_RESOLVE_MIN_CONFIDENCE)))
        self.promote_primary_after = int(os.getenv("SAP_MACRO_PROMOTE_PRIMARY_AFTER", str(SAP_MACRO_PROMOTE_PRIMARY_AFTER)))
        for directory in self.macro_dirs:
            os.makedirs(directory, exist_ok=True)

    def list_macros(self) -> list[dict]:
        items = []
        seen = set()
        for directory in self.macro_dirs:
            if not os.path.exists(directory):
                continue
            for filename in sorted(os.listdir(directory)):
                if not filename.lower().endswith(".md"):
                    continue
                path = os.path.join(directory, filename)
                try:
                    macro = self.load_path(path)
                except Exception:
                    continue
                key = _lookup_key(macro.name)
                if key in seen:
                    continue
                seen.add(key)
                items.append({
                    "name": macro.name,
                    "description": macro.description,
                    "mode": macro.mode,
                    "tags": macro.tags,
                    "input_count": len(macro.inputs),
                    "step_count": len(macro.steps),
                    "filepath": path,
                })
        return items

    def _load_learned_index(self) -> dict:
        if not os.path.exists(self.learned_index_path):
            return {"version": 1, "macros": {}, "recordings": []}
        try:
            with open(self.learned_index_path, "r", encoding="utf-8") as file:
                data = json.load(file)
            if isinstance(data, dict):
                data.setdefault("version", 1)
                data.setdefault("macros", {})
                data.setdefault("recordings", [])
                return data
        except Exception:
            pass
        return {"version": 1, "macros": {}, "recordings": []}

    def _save_learned_index(self, data: dict):
        os.makedirs(os.path.dirname(self.learned_index_path), exist_ok=True)
        with open(self.learned_index_path, "w", encoding="utf-8") as file:
            json.dump(data, file, ensure_ascii=False, indent=2)

    def _learned_ids_for_step(self, macro_name: str, step_index: int) -> list[str]:
        data = self._load_learned_index()
        step_data = (
            data.get("macros", {})
            .get(str(macro_name), {})
            .get("steps", {})
            .get(str(step_index), {})
        )
        ids = step_data.get("ids") or {}
        ranked = sorted(
            ids.items(),
            key=lambda item: (int(item[1].get("success_count", 0)), str(item[1].get("last_seen", ""))),
            reverse=True,
        )
        return [item[0] for item in ranked]

    def _record_learning_event(self, macro: SAPMacro, event: dict) -> dict:
        data = self._load_learned_index()
        macros = data.setdefault("macros", {})
        macro_data = macros.setdefault(macro.name, {"path": macro.path, "steps": {}})
        macro_data["path"] = macro.path
        steps = macro_data.setdefault("steps", {})
        step_data = steps.setdefault(str(event["step"]), {"ids": {}})
        ids = step_data.setdefault("ids", {})
        resolved_id = event["resolved_id"]
        id_data = ids.setdefault(resolved_id, {"success_count": 0})
        id_data["success_count"] = int(id_data.get("success_count", 0)) + 1
        id_data["last_seen"] = datetime.now().isoformat(timespec="seconds")
        id_data["source"] = event.get("source", "runtime")
        id_data["confidence"] = event.get("confidence", 0)
        id_data["original_id"] = event.get("original_id", "")
        id_data["tcode"] = event.get("tcode", "")
        id_data["screen_number"] = event.get("screen_number", "")
        id_data["screen_title"] = event.get("screen_title", "")
        step_data["last_resolved_id"] = resolved_id
        self._save_learned_index(data)
        return id_data

    def _backup_macro_file(self, macro: SAPMacro) -> str:
        os.makedirs(self.backup_dir, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        filename = f"{timestamp}-{os.path.basename(macro.path)}"
        backup_path = os.path.join(self.backup_dir, filename)
        shutil.copy2(macro.path, backup_path)
        return backup_path

    def _writeback_macro_learning(self, macro: SAPMacro, events: list[dict]) -> dict:
        if not self.learning_enabled or not self.auto_writeback or not events:
            return {"enabled": self.learning_enabled, "writeback": False, "events": len(events)}

        accepted = []
        promotions = []
        for event in events:
            confidence = float(event.get("confidence") or 0)
            resolved_id = str(event.get("resolved_id") or "")
            original_id = str(event.get("original_id") or "")
            if not resolved_id or resolved_id == original_id or confidence < self.resolve_min_confidence:
                continue
            learned = self._record_learning_event(macro, event)
            accepted.append(event)
            if int(learned.get("success_count", 0)) >= self.promote_primary_after:
                promotions.append(event)

        if not accepted:
            return {"enabled": True, "writeback": False, "events": len(events), "accepted": 0}

        backup_path = self._backup_macro_file(macro)
        self._update_macro_markdown_ids(macro.path, accepted, promotions)
        return {
            "enabled": True,
            "writeback": True,
            "events": len(events),
            "accepted": len(accepted),
            "promoted": len(promotions),
            "backup": backup_path,
            "index": self.learned_index_path,
        }

    def _update_macro_markdown_ids(self, path: str, events: list[dict], promotions: list[dict]):
        with open(path, "r", encoding="utf-8") as file:
            lines = file.read().splitlines()
        promote_by_step = {int(event["step"]): event for event in promotions}
        events_by_step: dict[int, list[dict]] = {}
        for event in events:
            events_by_step.setdefault(int(event["step"]), []).append(event)

        index = 0
        in_steps = False
        while index < len(lines):
            stripped = lines[index].strip()
            if stripped.startswith("#"):
                in_steps = any(token in stripped.lower() for token in ("steps", "步驟", "操作", "macro"))
                index += 1
                continue
            if not in_steps or "|" not in stripped or index + 1 >= len(lines) or not _is_separator_row(lines[index + 1]):
                index += 1
                continue

            headers = _split_markdown_row(lines[index])
            normalized = [_normalize_header(cell, "steps") for cell in headers]
            if "alternate_ids" not in normalized:
                insert_at = normalized.index("description") if "description" in normalized else len(headers)
                headers.insert(insert_at, "alternate_ids")
                normalized.insert(insert_at, "alternate_ids")
                lines[index] = _format_markdown_row(headers)
                separator = _split_markdown_row(lines[index + 1])
                separator.insert(insert_at, "---")
                lines[index + 1] = _format_markdown_row(separator)
            alt_pos = normalized.index("alternate_ids")
            element_pos = normalized.index("element_id") if "element_id" in normalized else -1
            step_pos = normalized.index("index") if "index" in normalized else 0

            row_index = index + 2
            while row_index < len(lines) and "|" in lines[row_index].strip():
                cells = _split_markdown_row(lines[row_index])
                while len(cells) < len(headers):
                    cells.append("")
                try:
                    step_number = int(str(cells[step_pos]).strip())
                except Exception:
                    row_index += 1
                    continue
                if step_number in events_by_step:
                    current_alt = [item.strip() for item in re.split(r"[,，;；\n]+", cells[alt_pos]) if item.strip()]
                    current_element = cells[element_pos].strip() if element_pos >= 0 else ""
                    for event in events_by_step[step_number]:
                        resolved_id = str(event.get("resolved_id") or "").strip()
                        original_id = str(event.get("original_id") or "").strip()
                        if resolved_id and resolved_id not in current_alt and resolved_id != current_element:
                            current_alt.append(resolved_id)
                        if original_id and step_number in promote_by_step and original_id not in current_alt and original_id != resolved_id:
                            current_alt.append(original_id)
                    if step_number in promote_by_step and element_pos >= 0:
                        cells[element_pos] = promote_by_step[step_number]["resolved_id"]
                    cells[alt_pos] = ";".join(current_alt)
                    lines[row_index] = _format_markdown_row(cells[:len(headers)])
                row_index += 1
            break
        with open(path, "w", encoding="utf-8", newline="\n") as file:
            file.write("\n".join(lines) + "\n")

    def _record_result_screen_observations(self, macro: SAPMacro, results: list[dict], values: dict) -> int:
        if not self.learning_enabled:
            return 0
        observations = []
        for result in results:
            observations.extend(self._result_screen_observations_from_tool(macro, result, values))
            for nested in result.get("results") or []:
                if isinstance(nested, dict):
                    observations.extend(self._result_screen_observations_from_tool(macro, nested, values))
        if not observations:
            return 0
        data = self._load_learned_index()
        macro_data = data.setdefault("macros", {}).setdefault(macro.name, {"path": macro.path, "steps": {}})
        result_fields = macro_data.setdefault("result_fields", {})
        for item in observations:
            key = item["name"] or item["id"]
            current = result_fields.setdefault(key, {"seen_count": 0})
            current["seen_count"] = int(current.get("seen_count", 0)) + 1
            current.update(item)
            current["last_seen"] = datetime.now().isoformat(timespec="seconds")
        self._save_learned_index(data)
        return len(observations)

    def _result_screen_observations_from_tool(self, macro: SAPMacro, result: dict, values: dict) -> list[dict]:
        screen_after = result.get("screen_after") or result.get("screen_summary") or {}
        screen = screen_after.get("screen") if isinstance(screen_after, dict) else {}
        if not isinstance(screen, dict):
            return []
        if macro.name != "mmbe_stock_overview":
            return []
        if str(screen.get("transaction") or "").upper() != "MMBE" or str(screen.get("screen_number") or "") != "300":
            return []
        screen_elements = screen_after.get("screen_elements") or {}
        raw = screen_elements.get("raw") if isinstance(screen_elements, dict) else ""
        elements = self._elements_from_raw_text(raw)
        desired = str(values.get("material") or "").strip().upper()
        observations = []
        for element in elements:
            name = str(element.get("name") or "")
            element_id = _short_sap_id(element.get("id") or element.get("element_id") or "")
            text = str(element.get("text") or element.get("value") or "")
            if name == "IO_MATERIAL" and (not desired or text.strip().upper() == desired):
                observations.append({
                    "id": element_id,
                    "name": name,
                    "role": "result_material",
                    "value": text,
                    "tcode": "MMBE",
                    "screen_number": "300",
                    "screen_title": screen.get("title") or "",
                    "source": "screen_after",
                })
        return observations

    def _elements_from_raw_text(self, raw_text: str) -> list[dict]:
        if not raw_text:
            return []
        try:
            parsed = json.loads(raw_text)
        except Exception:
            return []
        if isinstance(parsed, list):
            return [item for item in parsed if isinstance(item, dict)]
        if isinstance(parsed, dict):
            for key in ("elements", "data", "items", "result"):
                value = parsed.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
        return []

    def learn_from_recordings(self, recordings_dir: str = "recordings") -> dict:
        directory = recordings_dir if os.path.isabs(recordings_dir) else os.path.join(ROOT_DIR, recordings_dir)
        summary = {"recordings": 0, "observations": 0, "macro_updates": 0, "files": []}
        if not os.path.isdir(directory):
            return summary
        data = self._load_learned_index()
        final_by_recording = []
        for filename in sorted(os.listdir(directory)):
            if not filename.lower().endswith(".json"):
                continue
            path = os.path.join(directory, filename)
            try:
                with open(path, "r", encoding="utf-8") as file:
                    recording = json.load(file)
            except Exception:
                continue
            observations = self._recording_observations(recording, path)
            if not observations:
                continue
            summary["recordings"] += 1
            summary["observations"] += len(observations)
            summary["files"].append({"path": path, "observations": len(observations)})
            final_by_recording.extend(observations)
        data["recordings"] = final_by_recording
        self._save_learned_index(data)
        summary["macro_updates"] = self._apply_recording_observations_to_macros(final_by_recording)
        return summary

    def _recording_observations(self, recording: dict, path: str) -> list[dict]:
        raw_events = recording.get("raw_events") or recording.get("events") or []
        latest: dict[tuple[str, str, str], dict] = {}
        for event in raw_events:
            details = event.get("details") if isinstance(event, dict) else {}
            if not isinstance(details, dict):
                continue
            element_id = str(details.get("element_id") or details.get("id") or "").strip()
            if not element_id:
                continue
            value = str(details.get("to_value") if details.get("to_value") is not None else details.get("value") or "")
            tcode = str(details.get("tcode") or "").strip().upper()
            screen_number = str(details.get("screen_number") or "").strip()
            if element_id.endswith("/okcd"):
                if len(value.strip()) < 2:
                    continue
                value = value.strip().upper()
                element_id = "wnd[0]/tbar[0]/okcd"
            key = (element_id, tcode, screen_number)
            latest[key] = {
                "source_path": path,
                "element_id": _short_sap_id(element_id),
                "value": value,
                "tcode": tcode,
                "screen_number": screen_number,
                "event_type": event.get("event_type", ""),
                "timestamp": event.get("timestamp", ""),
            }
        return list(latest.values())

    def _apply_recording_observations_to_macros(self, observations: list[dict]) -> int:
        if not observations:
            return 0
        updates = 0
        for item in self.list_macros():
            try:
                macro = self.load_macro(item["name"])
            except Exception:
                continue
            tcode = self._macro_tcode(macro).upper()
            if not tcode:
                continue
            matching = [obs for obs in observations if obs.get("tcode") == tcode]
            if not matching:
                continue
            events = []
            for step in macro.steps:
                if not self._step_requires_element_match(step):
                    continue
                suffix = self._technical_suffix(step.element_id)
                for obs in matching:
                    if suffix and suffix == self._technical_suffix(obs.get("element_id", "")):
                        events.append({
                            "step": step.index,
                            "label": step.label,
                            "original_id": step.element_id,
                            "resolved_id": obs["element_id"],
                            "source": "recording",
                            "confidence": 0.9,
                            "tcode": tcode,
                            "screen_number": obs.get("screen_number", ""),
                            "screen_title": "",
                        })
            if events:
                self._writeback_macro_learning(macro, events)
                updates += len(events)
        return updates

    @staticmethod
    def _technical_suffix(element_id: str) -> str:
        text = _short_sap_id(element_id)
        if "/" in text:
            text = text.rsplit("/", 1)[-1]
        for prefix in ("ctxt", "txt", "rad", "chk", "cmb", "btn"):
            if text.startswith(prefix):
                text = text[len(prefix):]
        return text.upper()

    def audit_macro(self, name: str) -> str:
        macro = self.load_macro(name)
        data = self._load_learned_index()
        macro_data = data.get("macros", {}).get(macro.name, {})
        lines = [f"## Macro Audit: {macro.name}", f"- path: {macro.path}", f"- tcode: {self._macro_tcode(macro) or '-'}", ""]
        lines.append("| step | label | primary | alternate_ids | learned |")
        lines.append("|---|---|---|---|---|")
        step_data = macro_data.get("steps", {})
        for step in macro.steps:
            learned = []
            ids = step_data.get(str(step.index), {}).get("ids", {})
            for resolved_id, info in sorted(ids.items(), key=lambda item: int(item[1].get("success_count", 0)), reverse=True):
                learned.append(f"{resolved_id}({info.get('success_count', 0)}x/{info.get('source', '')})")
            lines.append(
                "| {step} | {label} | `{primary}` | `{alts}` | {learned} |".format(
                    step=step.index,
                    label=step.label or "-",
                    primary=step.element_id or "-",
                    alts="; ".join(self._step_alternate_ids(step)) or "-",
                    learned=", ".join(learned) or "-",
                )
            )
        result_fields = macro_data.get("result_fields") or {}
        if result_fields:
            lines.extend(["", "### Result Screen Observations"])
            for key, info in result_fields.items():
                lines.append(
                    f"- {key}: `{info.get('id')}` value={info.get('value', '')} "
                    f"screen={info.get('screen_number', '')} seen={info.get('seen_count', 0)}"
                )
        return "\n".join(lines)

    def doctor_macro(self, session, agent, name: str) -> str:
        macro = self.load_macro(name)
        self._prepare_runtime_step_options(macro)
        snapshot = self._scan_macro_screen(session, agent)
        lines = [f"## Macro Doctor: {macro.name}"]
        screen = snapshot.get("screen") or {}
        lines.append(f"- screen: {screen.get('transaction') or screen.get('tcode') or '-'} / {screen.get('screen_number') or '-'} / {screen.get('title') or '-'}")
        start = self._validate_checks("start", macro.start_checks, snapshot)
        lines.append(f"- start: {'OK' if start.get('success') else 'FAILED'} {start.get('error', '')}")
        lines.append("")
        lines.append("| step | action | label | status | detail |")
        lines.append("|---|---|---|---|---|")
        for step in macro.steps:
            if not self._step_requires_element_match(step):
                lines.append(f"| {step.index} | {step.action} | {step.label or '-'} | SKIP | no element match required |")
                continue
            result = self._validate_step_targets(snapshot, [step])
            status = "OK" if result.get("success") else "FAILED"
            detail = result.get("error") or ", ".join(item.get("resolved_id", "") for item in result.get("resolutions") or [])
            lines.append(f"| {step.index} | {step.action} | {step.label or '-'} | {status} | {detail or '-'} |")
        return "\n".join(lines)

    def load_macro(self, name: str) -> SAPMacro:
        path = self._find_macro_path(name)
        if not path:
            raise FileNotFoundError(f"找不到 macro: '{name}'")
        return self.load_path(path)

    def load_path(self, path: str) -> SAPMacro:
        with open(path, "r", encoding="utf-8") as file:
            raw_text = file.read()
        metadata, body = _parse_front_matter(raw_text)
        stem = os.path.splitext(os.path.basename(path))[0]
        title = self._markdown_title(body) or metadata.get("name") or stem
        macro = SAPMacro(
            name=metadata.get("name") or title,
            path=path,
            description=metadata.get("description", ""),
            mode=(metadata.get("mode") or "both").lower(),
            tags=_split_csv(metadata.get("tags", "")),
            metadata=metadata,
        )

        input_rows = []
        start_rows = []
        step_rows = []
        end_rows = []
        for heading, rows in _iter_markdown_tables(body):
            if any(token in heading for token in ("input", "輸入", "參數", "變數")):
                input_rows.extend(rows)
            elif self._is_start_heading(heading):
                start_rows.extend(rows)
            elif self._is_end_heading(heading):
                end_rows.extend(rows)
            elif any(token in heading for token in ("step", "操作", "步驟", "macro")):
                step_rows.extend(rows)

        macro.inputs = self._parse_inputs(input_rows)
        macro.start_checks = self._parse_checks(start_rows, "start")
        macro.steps = self._parse_steps(step_rows)
        macro.end_checks = self._parse_checks(end_rows, "end")
        if not macro.steps:
            raise SAPMacroError(f"Macro 缺少 steps table: {path}")
        macro.inputs = self._merge_placeholder_inputs(macro.inputs, macro.steps)
        return macro

    def parse_values(self, text: str) -> tuple[str, dict]:
        tokens = shlex.split(str(text or ""), posix=False)
        name_parts = []
        values = {}
        for token in tokens:
            token = token.strip()
            if not token:
                continue
            if "=" in token:
                key, value = token.split("=", 1)
                values[key.strip()] = _strip_quotes(value.strip())
            else:
                name_parts.append(_strip_quotes(token))
        return " ".join(name_parts).strip(), values

    def match_request(self, text: str, min_score: int | None = None) -> dict | None:
        """Find a likely macro before falling back to Auto ReAct."""
        if not self.autoroute_enabled:
            return None
        if min_score is None:
            min_score = self.autoroute_min_score
        request = str(text or "").strip()
        if not request:
            return None
        request_key = _lookup_key(request)
        request_lower = request.lower()
        _name, explicit_values = self.parse_values(request)
        best = None
        for item in self.list_macros():
            try:
                macro = self.load_macro(item["name"])
            except Exception:
                continue
            score, reasons = self._score_macro_request(macro, request, request_key, request_lower)
            if score <= 0:
                continue
            runtime_values = self._infer_runtime_values(macro, request, explicit_values)
            candidate = {
                "macro": macro,
                "name": macro.name,
                "score": score,
                "reasons": reasons,
                "values": runtime_values,
            }
            if not best or score > best["score"]:
                best = candidate
        if best and best["score"] >= min_score:
            return best
        return None

    def _score_macro_request(self, macro: SAPMacro, request: str, request_key: str, request_lower: str) -> tuple[int, list[str]]:
        score = 0
        reasons = []
        name_key = _lookup_key(macro.name)
        if name_key and (name_key in request_key or request_key in name_key):
            score += 100
            reasons.append("macro_name")

        aliases = _split_aliases(macro.metadata.get("aliases", ""))
        for alias in aliases:
            alias_key = _lookup_key(alias)
            if alias and alias in request:
                score += 95
                reasons.append(f"alias:{alias}")
                break
            if alias_key and alias_key in request_key:
                score += 90
                reasons.append(f"alias_key:{alias}")
                break

        haystack_items = [macro.description, *macro.tags]
        for item in haystack_items:
            text = str(item or "").strip()
            key = _lookup_key(text)
            if not key or len(key) < 2:
                continue
            if key in request_key:
                score += 18
                reasons.append(f"tag:{text}")

        tcode = self._macro_tcode(macro)
        if tcode and tcode.lower() in request_lower:
            score += 85
            reasons.append(f"tcode:{tcode}")

        domain_keywords = self._macro_domain_keywords(macro)
        for keyword, weight in domain_keywords.items():
            if keyword in request:
                score += weight
                reasons.append(f"keyword:{keyword}")

        intent_score, intent_reasons = self._score_special_intents(macro, request)
        score += intent_score
        reasons.extend(intent_reasons)

        return min(score, 100), reasons

    @staticmethod
    def _macro_tcode(macro: SAPMacro) -> str:
        for step in macro.steps:
            if str(step.action).lower() == "tcode" and step.value:
                return str(step.value).strip()
        return ""

    @staticmethod
    def _macro_domain_keywords(macro: SAPMacro) -> dict[str, int]:
        name = macro.name
        if name == "mb52_stock_list":
            return {"所有庫存": 90, "顯示所有庫存": 95, "庫存清單": 85, "庫存列表": 85, "列出庫存": 80, "庫存": 45}
        if name == "mmbe_stock_overview":
            return {"庫存總覽": 80, "顯示物料庫存": 80, "查物料庫存": 80, "物料庫存": 75, "庫存概況": 65, "庫存": 35}
        if name == "va05_list_orders":
            return {"銷售訂單清單": 80, "訂單清單": 60, "銷售訂單": 45, "sales order": 45}
        if name == "va03_display_order":
            return {"顯示銷售訂單": 85, "查看銷售訂單": 80, "銷售訂單": 45}
        if name == "vf05_list_billing":
            return {"請款": 55, "請款單": 70, "請款文件": 70, "發票清單": 65, "billing": 45}
        if name == "mb51_material_docs":
            return {"物料憑證": 80, "物料文件": 70, "移動類型": 45, "過帳": 35}
        if name == "me2m_po_by_material":
            return {"採購單": 55, "採購單清單": 80, "依物料查採購單": 85, "po": 35}
        return {}

    def _score_special_intents(self, macro: SAPMacro, request: str) -> tuple[int, list[str]]:
        reasons = []
        text = str(request or "")
        material = self._extract_material_candidate(text)
        partner = self._extract_business_partner_candidate(text)
        order_number = self._extract_order_number_candidate(text)
        display_intent = any(keyword in text for keyword in ("查看", "顯示", "查詢", "開啟", "看", "狀態", "情況", "內容"))
        list_intent = any(keyword in text for keyword in ("清單", "列表", "列出", "一覽"))
        sales_order_context = any(keyword in text for keyword in ("銷售訂單", "銷售單", "訂單號", "訂單", "sales order", "Sales Order", "vbeln", "VBELN"))
        non_sales_document_context = any(keyword in text for keyword in ("請購", "採購", "請款", "發票", "物料憑證", "物料文件", "出貨", "交貨", "purchase", "Purchase", "billing", "Billing"))
        generic_order_number_context = "單號" in text and not non_sales_document_context
        if (
            macro.name == "va03_display_order"
            and order_number
            and display_intent
            and not list_intent
            and (sales_order_context or generic_order_number_context)
        ):
            return 95, [f"intent:display_order:{order_number}"]

        billing_intent = any(keyword in text for keyword in ("請款", "請款文件", "請款單", "發票", "billing", "Billing"))
        if macro.name == "vf05_list_billing" and billing_intent:
            if partner:
                return 88, [f"intent:billing_by_partner:{partner}"]
            return 72, ["intent:billing_list"]

        order_list_intent = any(keyword in text for keyword in ("銷售訂單", "訂單")) and list_intent
        if macro.name == "va05_list_orders" and order_list_intent:
            if partner:
                return 88, [f"intent:sales_order_list_by_partner:{partner}"]
            return 75, ["intent:sales_order_list"]

        po_intent = (
            any(keyword in text for keyword in ("採購單", "採購文件", "purchase order", "Purchase Order"))
            or bool(re.search(r"\bpo\b", text, flags=re.I))
        )
        if macro.name == "me2m_po_by_material" and po_intent and material:
            return 92, [f"intent:po_by_material:{material}"]

        material_doc_intent = any(keyword in text for keyword in ("物料憑證", "物料文件", "移動紀錄", "物料異動", "material document"))
        if macro.name == "mb51_material_docs" and material_doc_intent:
            if material:
                return 92, [f"intent:material_docs:{material}"]
            return 78, ["intent:material_docs"]

        if "庫存" not in text:
            return 0, reasons

        stock_list_intent = any(keyword in text for keyword in ("所有", "清單", "列表", "列出", "一覽", "總表"))
        material_stock_intent = bool(material) or "物料" in text

        if macro.name == "mmbe_stock_overview" and material_stock_intent:
            if material:
                return 95, [f"intent:material_stock:{material}"]
            return 55, ["intent:material_stock"]
        if macro.name == "mb52_stock_list" and stock_list_intent:
            return 90, ["intent:stock_list"]
        if macro.name == "mb52_stock_list" and not material:
            return 40, ["intent:generic_stock"]
        return 0, reasons

    def _infer_runtime_values(self, macro: SAPMacro, request: str, explicit_values: dict) -> dict:
        values = dict(explicit_values or {})
        if "material" in {item.name for item in macro.inputs} and "material" not in values:
            material = self._extract_material_candidate(request)
            if material:
                values["material"] = material
        if macro.name == "va03_display_order" and "sales_order" not in values:
            order_number = self._extract_order_number_candidate(request)
            if order_number:
                values["sales_order"] = order_number
        if macro.name == "vf05_list_billing" and "payer" not in values:
            partner = self._extract_business_partner_candidate(request)
            if partner:
                values["payer"] = partner
        if macro.name == "va05_list_orders" and "sold_to" not in values:
            partner = self._extract_business_partner_candidate(request)
            if partner:
                values["sold_to"] = partner
        return values

    @staticmethod
    def _extract_order_number_candidate(request: str) -> str:
        text = str(request or "")
        explicit = re.search(r"(?:sales[_ -]?order|vbeln|銷售訂單|銷售單|訂單號|訂單|單號)\s*[:=：]?\s*(\d{6,12})", text, flags=re.I)
        if explicit:
            return explicit.group(1)
        match = re.search(r"(?<!\d)(\d{8,10})(?!\d)", text)
        return match.group(1) if match else ""

    @staticmethod
    def _extract_business_partner_candidate(request: str) -> str:
        text = str(request or "")
        explicit = re.search(
            r"(?:payer|customer|sold[_ -]?to|付款人|客戶|買方|售達方)\s*[:=：]?\s*([A-Z]{1,4}\d{3,10})",
            text,
            flags=re.I,
        )
        if explicit:
            return explicit.group(1).upper()
        if any(keyword in text for keyword in ("付款人", "客戶", "買方", "售達方")):
            match = re.search(r"(?<![A-Z0-9_/-])([A-Z]{1,4}\d{3,10})(?![A-Z0-9_/-])", text, flags=re.I)
            if match:
                return match.group(1).upper()
        return ""

    @staticmethod
    def _extract_material_candidate(request: str) -> str:
        text = str(request or "")
        explicit = re.search(r"(?:material|matnr|物料|料號)\s*[:=：]?\s*([A-Z]{2,}[A-Z0-9_/-]*\d[A-Z0-9_/-]*)", text, flags=re.I)
        if explicit:
            return explicit.group(1).upper()
        candidates = re.findall(
            r"(?<![A-Z0-9_/-])([A-Z]{2,}[A-Z0-9_/-]*\d[A-Z0-9_/-]*)(?![A-Z0-9_/-])",
            text,
            flags=re.I,
        )
        ignored = {"MB51", "MB52", "MMBE", "ME2M", "VA03", "VA05", "VF05"}
        for candidate in candidates:
            upper = candidate.upper()
            if upper not in ignored:
                return upper
        return ""

    def prompt_missing_inputs(
        self,
        macro: SAPMacro,
        runtime_values: dict | None = None,
        prompt_callback: Callable[[MacroInput], str] | None = None,
    ) -> dict:
        values = dict(runtime_values or {})
        for item in macro.inputs:
            if item.name in values and str(values[item.name]) != "":
                continue
            if item.default:
                values[item.name] = item.default
                continue
            if not item.required:
                values[item.name] = ""
                continue
            if not prompt_callback:
                raise SAPMacroError(f"Macro 缺少必要輸入值: {item.name}")
            value = prompt_callback(item)
            if value is None or str(value).strip() == "":
                raise SAPMacroError(f"Macro 必要輸入值不可為空: {item.name}")
            values[item.name] = str(value).strip()
        return values

    def format_summary(self, macro: SAPMacro) -> str:
        lines = [
            f"## Macro: {macro.name}",
            f"檔案: {macro.path}",
            f"模式: {macro.mode}",
        ]
        if macro.description:
            lines.append(f"說明: {macro.description}")
        if macro.tags:
            lines.append(f"Tags: {', '.join(macro.tags)}")
        lines.append("")
        if macro.inputs:
            lines.append("### Inputs")
            for item in macro.inputs:
                default = f" default={item.default}" if item.default else ""
                required = "required" if item.required else "optional"
                label = f" ({item.label})" if item.label else ""
                lines.append(f"- {item.name}{label}: {required}{default}")
            lines.append("")
        lines.append("### Start")
        if macro.start_checks:
            for check in macro.start_checks:
                target = f" target={check.target}" if check.target else ""
                value = f" value={check.value}" if check.value else ""
                lines.append(f"- {check.kind}{target}{value}")
        else:
            lines.append("- (missing)")
        lines.append("")
        lines.append("### Steps")
        for step in macro.steps:
            value = f" = {step.value}" if step.value else ""
            element = f" [{step.element_id}]" if step.element_id else ""
            label = f" {step.label}" if step.label else ""
            lines.append(f"{step.index}. {step.action}{label}{value}{element}")
        lines.append("")
        lines.append("### End")
        if macro.end_checks:
            for check in macro.end_checks:
                target = f" target={check.target}" if check.target else ""
                value = f" value={check.value}" if check.value else ""
                lines.append(f"- {check.kind}{target}{value}")
        else:
            lines.append("- (missing)")
        return "\n".join(lines)

    def format_study_context(self, macro: SAPMacro, runtime_values: dict | None = None) -> str:
        self._validate_macro_shape(macro)
        values = dict(runtime_values or {})
        input_map = {item.name: item for item in macro.inputs}
        lines = [
            f"# Structured Macro Study Guide: {macro.name}",
            "",
            "這是一份結構化 Macro，可作為 Study Mode 的高信心參考。Study Mode 仍必須依目前 SAP 畫面狀態引導使用者；若元件不存在或畫面不同，請先說明差異並要求使用者確認。",
            "",
            "## Metadata",
            f"- mode: {macro.mode}",
            f"- source_path: {macro.path}",
            f"- generated_at: {datetime.now().isoformat(timespec='seconds')}",
        ]
        if macro.description:
            lines.append(f"- description: {macro.description}")
        if macro.tags:
            lines.append(f"- tags: {', '.join(macro.tags)}")
        lines.extend(["", "## Start Conditions"])
        if macro.start_checks:
            for check in macro.start_checks:
                lines.append(f"- {check.kind}: target={check.target or '-'} value={check.value or '-'}")
        else:
            lines.append("- 缺少 Start 條件；此 Macro 不應直接執行。")
        lines.extend(["", "## End Conditions"])
        if macro.end_checks:
            for check in macro.end_checks:
                lines.append(f"- {check.kind}: target={check.target or '-'} value={check.value or '-'}")
        else:
            lines.append("- 缺少 End 條件；此 Macro 不應直接執行。")
        lines.extend(["", "## Runtime Inputs"])
        if macro.inputs:
            for item in macro.inputs:
                current = values.get(item.name, item.default)
                required = "required" if item.required else "optional"
                lines.append(f"- {item.name}: {current or '(待使用者提供)'} [{required}] {item.label}")
        else:
            lines.append("- 無")
        lines.extend([
            "",
            "## Macro Steps",
            "| # | action | element_id | value | label | instruction |",
            "|---|---|---|---|---|---|",
        ])
        for step in macro.steps:
            value, missing = _replace_placeholders(step.value, values, input_map)
            display_value = value if not missing else f"{step.value} (missing: {', '.join(missing)})"
            instruction = self._study_instruction_for_step(step, display_value)
            lines.append(
                "| {idx} | {action} | `{element}` | `{value}` | {label} | {instruction} |".format(
                    idx=step.index,
                    action=step.action,
                    element=step.element_id,
                    value=display_value.replace("|", "\\|"),
                    label=(step.label or "").replace("|", "\\|"),
                    instruction=instruction.replace("|", "\\|"),
                )
            )
        lines.extend([
            "",
            "## Study Rules",
            "- 只能使用 guide_user_action / visualize_element，不可替使用者寫入 SAP。",
            "- 對 action=input 且 value 使用動態變數的步驟，請讓使用者輸入本次值；若 runtime input 已提供，提示該值即可。",
            "- 對 tcode、固定 checkbox/radio、固定按鈕可明確引導，但仍需確認目前畫面是否吻合。",
            "- Macro 必須先確認 Start 條件，並在每個步驟前確認 element_id、元件類型與標籤/文字符合，最後確認 End 條件。",
            "- 若畫面已有可接受預設值，先讓使用者確認沿用，不要要求重填。",
        ])
        return "\n".join(lines)

    def execute_macro(
        self,
        session,
        agent,
        macro: SAPMacro,
        runtime_values: dict | None = None,
        log_callback: Callable[[str], None] | None = None,
        stop_on_error: bool = True,
    ) -> dict:
        values = dict(runtime_values or {})
        input_map = {item.name: item for item in macro.inputs}
        results = []
        learning_events = []

        self._validate_macro_shape(macro)
        self._prepare_runtime_step_options(macro)
        start_snapshot = self._scan_macro_screen(session, agent)
        satisfied = self._macro_already_satisfied(macro, values, start_snapshot)
        if satisfied:
            results.append(satisfied)
            payload = self._macro_result(macro, results)
            payload["already_satisfied"] = True
            return payload
        start_result = self._validate_checks("start", macro.start_checks, start_snapshot)
        results.append(start_result)
        self._log_validation(log_callback, start_result)
        if stop_on_error and not start_result.get("success", True):
            return self._macro_result(macro, results)

        step_index = 0
        while step_index < len(macro.steps):
            step = macro.steps[step_index]
            action = self._normalize_action(step.action)
            if action == "skip":
                step_index += 1
                continue
            if self._step_should_skip(step, values, input_map):
                results.append({
                    "success": True,
                    "backend": "macro",
                    "action": "skip_if_empty",
                    "step": step.index,
                })
                self._log_result(log_callback, step, results[-1])
                step_index += 1
                continue

            if action in self.FIELD_ACTIONS:
                grouped, next_index, skip_enter = self._collect_field_group(macro.steps, step_index, values, input_map)
                if not grouped and next_index > step_index:
                    step_index = next_index
                    continue
                if len(grouped) > 1 or skip_enter:
                    match_result = self._scan_and_validate_step_targets(session, agent, [item[0] for item in grouped])
                    results.append(match_result)
                    self._log_validation(log_callback, match_result)
                    if stop_on_error and not match_result.get("success", True):
                        return self._macro_result(macro, results, failed_step=grouped[0][0])
                    result = self._execute_field_group(session, agent, grouped, validate=skip_enter)
                    results.append(result)
                    self._log_result(log_callback, grouped[0][0], result, grouped=grouped)
                    if stop_on_error and not result.get("success", True):
                        return self._macro_result(macro, results, failed_step=grouped[0][0])
                    learning_events.extend(self._resolution_events_from_result(macro, match_result, result))
                    step_index = next_index + (1 if skip_enter else 0)
                    continue

            resolved_value, missing = _replace_placeholders(step.value, values, input_map)
            if missing:
                raise SAPMacroError(f"Step {step.index} 缺少必要輸入值: {', '.join(missing)}")
            if action == "wait":
                seconds = float(resolved_value or step.options.get("seconds") or 1)
                time.sleep(seconds)
                result = {"success": True, "backend": "local", "action": "wait", "seconds": seconds}
            elif action == "popup_table_confirm" and not agent.mcp_tool_available("sap_select_popup_table_row_and_confirm"):
                match_result = self._scan_and_validate_step_targets(session, agent, [step])
                results.append(match_result)
                self._log_validation(log_callback, match_result)
                if stop_on_error and not match_result.get("success", True):
                    return self._macro_result(macro, results, failed_step=step)
                result = self._execute_popup_table_confirm_fallback(session, agent, step, resolved_value)
            else:
                if self._step_requires_element_match(step):
                    match_result = self._scan_and_validate_step_targets(session, agent, [step])
                    results.append(match_result)
                    self._log_validation(log_callback, match_result)
                    if stop_on_error and not match_result.get("success", True):
                        return self._macro_result(macro, results, failed_step=step)
                tool_name, tool_args = self._step_to_tool(step, resolved_value)
                result = agent.execute_macro_tool_call(
                    session,
                    tool_name,
                    tool_args,
                    attach_screen=action in {"tcode", "key"},
                )
            results.append(result)
            self._log_result(log_callback, step, result)
            if stop_on_error and not result.get("success", True):
                return self._macro_result(macro, results, failed_step=step)
            if "match_result" in locals():
                learning_events.extend(self._resolution_events_from_result(macro, match_result, result))
                del match_result
            step_index += 1

        end_snapshot = self._scan_macro_screen(session, agent)
        end_result = self._validate_checks("end", macro.end_checks, end_snapshot)
        results.append(end_result)
        self._log_validation(log_callback, end_result)
        payload = self._macro_result(macro, results)
        if payload.get("success"):
            payload["learning"] = self._writeback_macro_learning(macro, learning_events)
            payload["learning"]["result_observations"] = self._record_result_screen_observations(macro, results, values)
        return payload

    @staticmethod
    def _macro_result(macro: SAPMacro, results: list[dict], failed_step: MacroStep | None = None) -> dict:
        success = all(item.get("success", True) for item in results)
        payload = {
            "success": success,
            "macro": macro.name,
            "step_count": len(macro.steps),
            "results": results,
        }
        if failed_step:
            payload["failed_step"] = failed_step.index
            payload["error"] = f"Macro stopped at step {failed_step.index}"
        return payload

    def _prepare_runtime_step_options(self, macro: SAPMacro):
        for step in macro.steps:
            step.options["_macro_name"] = macro.name
            step.options["_original_element_id"] = step.element_id
            step.options["_learned_ids"] = self._learned_ids_for_step(macro.name, step.index)

    def _macro_already_satisfied(self, macro: SAPMacro, values: dict, snapshot: dict) -> dict | None:
        if macro.name != "mmbe_stock_overview" or not snapshot.get("success", True):
            return None
        screen = snapshot.get("screen") or {}
        transaction = str(screen.get("transaction") or screen.get("tcode") or "").upper()
        screen_number = str(screen.get("screen_number") or "")
        title = str(screen.get("title") or "")
        if transaction != "MMBE" or screen_number != "300":
            return None
        desired = str(values.get("material") or "").strip().upper()
        if not desired:
            return None
        current = self._snapshot_value_by_names(snapshot, {"IO_MATERIAL", "MS_MATNR", "MATNR"})
        if current and current.strip().upper() == desired:
            return {
                "success": True,
                "backend": "macro_validation",
                "action": "already_satisfied",
                "screen_number": screen_number,
                "title": title,
                "field": "IO_MATERIAL",
                "value": current,
            }
        return None

    def _snapshot_value_by_names(self, snapshot: dict, names: set[str]) -> str:
        wanted = {str(name).upper() for name in names}
        for element in self._snapshot_elements(snapshot):
            name = str(element.get("name") or "").upper()
            element_id = str(element.get("id") or element.get("element_id") or "").upper()
            if name in wanted or any(f"/{item}" in element_id or element_id.endswith(item) for item in wanted):
                value = element.get("value")
                if value is None:
                    value = element.get("text")
                if value is not None:
                    return str(value)
        return ""

    def _resolution_events_from_result(self, macro: SAPMacro, match_result: dict, tool_result: dict) -> list[dict]:
        if not tool_result.get("success", True):
            return []
        events = []
        for event in match_result.get("resolutions") or []:
            if event.get("source") == "primary":
                continue
            event = dict(event)
            event["macro"] = macro.name
            events.append(event)
        return events

    def _find_macro_path(self, name: str) -> str:
        target = _lookup_key(name)
        candidate_names = [name, _safe_name(name)]
        for directory in self.macro_dirs:
            for candidate in candidate_names:
                for path in (
                    os.path.join(directory, f"{candidate}.md"),
                    os.path.join(directory, f"{_safe_name(candidate)}.md"),
                ):
                    if os.path.exists(path):
                        return path
            if not os.path.exists(directory):
                continue
            scored = []
            for filename in os.listdir(directory):
                if not filename.lower().endswith(".md"):
                    continue
                path = os.path.join(directory, filename)
                try:
                    macro = self.load_path(path)
                except Exception:
                    continue
                keys = [_lookup_key(macro.name), _lookup_key(os.path.splitext(filename)[0])]
                keys.extend(_lookup_key(tag) for tag in macro.tags)
                if target in keys:
                    return path
                for key in keys:
                    if target and key and (target in key or key in target):
                        scored.append((len(key), path))
            if scored:
                scored.sort(reverse=True)
                return scored[0][1]
        return ""

    @staticmethod
    def _markdown_title(body: str) -> str:
        for line in body.splitlines():
            stripped = line.strip()
            if stripped.startswith("#"):
                title = stripped.lstrip("#").strip()
                for prefix in ("Macro:", "Macro：", "巨集:", "巨集："):
                    if title.startswith(prefix):
                        return title[len(prefix) :].strip()
                return title
        return ""

    def _parse_inputs(self, rows: list[dict]) -> list[MacroInput]:
        inputs = []
        seen = set()
        for row in rows:
            name = (row.get("name") or "").strip()
            if not name or name in seen:
                continue
            seen.add(name)
            inputs.append(MacroInput(
                name=name,
                label=row.get("label", "").strip(),
                default=row.get("default", "").strip(),
                required=_parse_bool(row.get("required"), True),
                description=row.get("description", "").strip(),
            ))
        return inputs

    def _parse_steps(self, rows: list[dict]) -> list[MacroStep]:
        steps = []
        for position, row in enumerate(rows, 1):
            action = (row.get("action") or "").strip()
            if not action:
                continue
            index_text = str(row.get("index") or position).strip()
            try:
                index = int(index_text)
            except ValueError:
                index = position
            known = {"index", "action", "element_id", "value", "label", "description", "required", "mode"}
            options = {key: value for key, value in row.items() if key not in known and value != ""}
            steps.append(MacroStep(
                index=index,
                action=self._normalize_action(action),
                element_id=(row.get("element_id") or "").strip().strip("`"),
                value=(row.get("value") or "").strip(),
                label=(row.get("label") or "").strip(),
                description=(row.get("description") or "").strip(),
                required=_parse_bool(row.get("required"), False),
                mode=(row.get("mode") or "both").strip().lower(),
                options=options,
            ))
        return steps

    def _parse_checks(self, rows: list[dict], section: str) -> list[MacroCheck]:
        checks = []
        for row in rows:
            expanded = self._expand_check_row(row, section)
            checks.extend(expanded)
        return checks

    def _expand_check_row(self, row: dict, section: str) -> list[MacroCheck]:
        condition = (row.get("condition") or "").strip()
        target = (row.get("target") or row.get("element_id") or "").strip().strip("`")
        value = (row.get("value") or "").strip()
        required = _parse_bool(row.get("required"), True)
        description = (row.get("description") or "").strip()
        known = {"condition", "target", "element_id", "value", "required", "description", "mode"}
        options = {key: val for key, val in row.items() if key not in known and val != ""}

        if condition:
            return [MacroCheck(
                section=section,
                kind=self._normalize_check_kind(condition),
                target=target,
                value=value,
                required=required,
                description=description,
                options=options,
            )]

        checks = []
        for key, raw_value in row.items():
            if key in {"required", "description", "mode"} or raw_value in (None, ""):
                continue
            kind = self._normalize_check_kind(key)
            if kind == "element_id":
                checks.append(MacroCheck(section=section, kind="element", target=str(raw_value).strip(), required=required, description=description))
            else:
                checks.append(MacroCheck(section=section, kind=kind, value=str(raw_value).strip(), required=required, description=description))
        return checks

    @staticmethod
    def _is_start_heading(heading: str) -> bool:
        text = str(heading or "").lower()
        return any(token in text for token in ("start", "起始", "開始", "前置", "入口"))

    @staticmethod
    def _is_end_heading(heading: str) -> bool:
        text = str(heading or "").lower()
        return any(token in text for token in ("end", "結束", "完成", "出口", "終點"))

    @staticmethod
    def _normalize_check_kind(kind: str) -> str:
        text = str(kind or "").strip().lower().replace(" ", "_").replace("-", "_")
        aliases = {
            "transaction": "tcode",
            "transaction_code": "tcode",
            "t_code": "tcode",
            "t-code": "tcode",
            "交易": "tcode",
            "交易碼": "tcode",
            "screen": "screen_number",
            "screen_no": "screen_number",
            "screen_number": "screen_number",
            "畫面": "screen_number",
            "畫面號": "screen_number",
            "title": "title",
            "title_contains": "title_contains",
            "標題": "title",
            "標題包含": "title_contains",
            "window": "active_window",
            "active_window": "active_window",
            "視窗": "active_window",
            "element": "element",
            "element_id": "element",
            "元件": "element",
            "元件id": "element",
            "status": "status_text",
            "status_text": "status_text",
            "狀態列": "status_text",
            "status_type": "status_type",
            "訊息類型": "status_type",
        }
        return aliases.get(text, text)

    def _validate_macro_shape(self, macro: SAPMacro):
        if not self.require_boundaries:
            return
        missing = []
        if not macro.start_checks:
            missing.append("## Start")
        if not macro.end_checks:
            missing.append("## End")
        if missing:
            raise SAPMacroError(
                f"Macro 缺少明確的 {' / '.join(missing)} 區段；請先定義 start/end 條件，避免在錯誤畫面直接操作。"
            )

    def _merge_placeholder_inputs(self, inputs: list[MacroInput], steps: list[MacroStep]) -> list[MacroInput]:
        by_name = {item.name: item for item in inputs}
        for step in steps:
            for name, options in _placeholders(step.value):
                if name in by_name:
                    continue
                by_name[name] = MacroInput(
                    name=name,
                    label=options.get("label", name),
                    default=options.get("default", ""),
                    required=not _parse_bool(options.get("optional"), False),
                    description=f"自動從 step {step.index} 的 value 佔位符推斷",
                )
        return list(by_name.values())

    def _normalize_action(self, action: str) -> str:
        text = str(action or "").strip().lower().replace(" ", "_")
        return self.ACTION_ALIASES.get(text, text)

    def _normalize_key(self, value: str) -> str:
        text = str(value or "Enter").strip()
        return self.KEY_ALIASES.get(text.lower(), text)

    def _step_to_tool(self, step: MacroStep, value: str) -> tuple[str, dict]:
        action = self._normalize_action(step.action)
        element_id = step.element_id
        if action == "tcode":
            return "sap_execute_transaction", {"tcode": value}
        if action in self.FIELD_ACTIONS:
            return "sap_set_field", {"field_id": element_id, "value": value}
        if action == "checkbox":
            selected = _parse_bool(value, True)
            return "sap_select_checkbox", {"checkbox_id": element_id, "selected": selected}
        if action == "radio":
            return "sap_select_radio_button", {"radio_id": element_id}
        if action == "combo":
            return "sap_select_combobox_entry", {"combobox_id": element_id, "key_or_value": value}
        if action == "click":
            return "sap_press_button", {"button_id": element_id}
        if action == "tab":
            return "sap_select_tab", {"tab_id": element_id}
        if action == "key":
            return "sap_send_key", {"key": self._normalize_key(value)}
        if action == "popup":
            return "sap_handle_popup", {
                "action": value or step.options.get("popup_action") or "confirm",
                "button_text": step.options.get("button_text", ""),
            }
        if action == "table_row":
            args = {
                "table_id": element_id,
                "row_text": value,
                "selected": _parse_bool(step.options.get("selected"), True),
            }
            row_index = step.options.get("row") or step.options.get("row_index")
            if row_index not in (None, ""):
                args["row_index"] = int(row_index)
            return "sap_select_table_row", args
        if action == "popup_table_confirm":
            row_value = step.options.get("row") or value
            return "sap_select_popup_table_row_and_confirm", {
                "table_id": element_id,
                "row": int(row_value),
                "confirm_action": step.options.get("confirm_action", "confirm") or "confirm",
            }
        if action == "textedit":
            return "sap_set_textedit", {"textedit_id": element_id, "text": value}
        if action == "focus":
            return "sap_set_focus", {"element_id": element_id}
        raise SAPMacroError(f"不支援的 macro action: {step.action}")

    def _collect_field_group(self, steps: list[MacroStep], start: int, values: dict, input_map: dict):
        grouped = []
        index = start
        while index < len(steps):
            step = steps[index]
            if self._normalize_action(step.action) not in self.FIELD_ACTIONS:
                break
            value, missing = _replace_placeholders(step.value, values, input_map)
            if self._step_should_clear_empty_resolved(step, value, missing):
                value = ""
                missing = []
            if self._step_should_skip_resolved(step, value, missing):
                index += 1
                continue
            if missing:
                raise SAPMacroError(f"Step {step.index} 缺少必要輸入值: {', '.join(missing)}")
            if not step.element_id:
                break
            grouped.append((step, value))
            index += 1
        skip_enter = False
        if index < len(steps):
            next_step = steps[index]
            next_value, next_missing = _replace_placeholders(next_step.value, values, input_map)
            skip_enter = (
                bool(grouped)
                and self._normalize_action(next_step.action) == "key"
                and not self._step_should_skip_resolved(next_step, next_value, next_missing)
                and self._normalize_key(next_value) == "Enter"
            )
        return grouped, index, skip_enter

    def _step_should_skip(self, step: MacroStep, values: dict, input_map: dict) -> bool:
        value, missing = _replace_placeholders(step.value, values, input_map)
        return self._step_should_skip_resolved(step, value, missing)

    @staticmethod
    def _step_should_skip_resolved(step: MacroStep, value: str, missing: list[str]) -> bool:
        if SAPMacroLibrary._step_should_clear_empty_resolved(step, value, missing):
            return False
        if not _parse_bool(step.options.get("skip_if_empty"), False):
            return False
        if missing:
            return True
        return str(value or "").strip() == ""

    @staticmethod
    def _step_should_clear_empty_resolved(step: MacroStep, value: str, missing: list[str]) -> bool:
        if not _parse_bool(step.options.get("clear_if_empty") or step.options.get("clear_on_empty"), False):
            return False
        return bool(missing) or str(value or "").strip() == ""

    def _scan_macro_screen(self, session, agent) -> dict:
        if hasattr(agent, "scan_macro_screen"):
            try:
                snapshot = agent.scan_macro_screen(session)
                if isinstance(snapshot, dict):
                    return snapshot
            except Exception as exc:
                return {"success": False, "backend": "macro_scan", "error": str(exc), "screen": {}, "elements": []}
        return {"success": False, "backend": "macro_scan", "error": "agent does not support macro screen scan", "screen": {}, "elements": []}

    def _scan_and_validate_step_targets(self, session, agent, steps: list[MacroStep]) -> dict:
        attempts = max(1, MACRO_STEP_SCAN_RETRIES)
        last_result = None
        for attempt in range(1, attempts + 1):
            snapshot = self._scan_macro_screen(session, agent)
            result = self._validate_step_targets(snapshot, steps)
            result["attempt"] = attempt
            if result.get("success", True):
                return result
            last_result = result
            if attempt < attempts:
                time.sleep(MACRO_STEP_SCAN_RETRY_SECONDS)
        return last_result or {
            "success": False,
            "backend": "macro_validation",
            "action": "step_targets",
            "error": "step target validation failed",
        }

    def _validate_checks(self, section: str, checks: list[MacroCheck], snapshot: dict) -> dict:
        if not self.strict_match:
            return {"success": True, "backend": "macro_validation", "action": f"{section}_checks", "skipped": True}
        if not snapshot.get("success", True):
            return {
                "success": False,
                "backend": "macro_validation",
                "action": f"{section}_checks",
                "error": f"scan failed: {snapshot.get('error', '')}",
            }
        failures = []
        for check in checks:
            ok, message = self._check_matches_snapshot(check, snapshot)
            if not ok and check.required:
                failures.append(message)
        return {
            "success": not failures,
            "backend": "macro_validation",
            "action": f"{section}_checks",
            "checked": len(checks),
            "failures": failures,
            "error": "; ".join(failures),
        }

    def _validate_step_targets(self, snapshot: dict, steps: list[MacroStep]) -> dict:
        if not self.strict_match:
            return {"success": True, "backend": "macro_validation", "action": "step_targets", "skipped": True}
        if not snapshot.get("success", True):
            return {
                "success": False,
                "backend": "macro_validation",
                "action": "step_targets",
                "error": f"scan failed: {snapshot.get('error', '')}",
            }
        failures = []
        resolutions = []
        checked = 0
        for step in steps:
            if not self._step_requires_element_match(step):
                continue
            checked += 1
            ok, message = self._step_matches_snapshot(step, snapshot)
            if not ok:
                failures.append(message)
            else:
                resolution = self._step_resolution_event(step, snapshot)
                if resolution:
                    resolutions.append(resolution)
        return {
            "success": not failures,
            "backend": "macro_validation",
            "action": "step_targets",
            "checked": checked,
            "failures": failures,
            "error": "; ".join(failures),
            "resolutions": resolutions,
        }

    def _step_requires_element_match(self, step: MacroStep) -> bool:
        action = self._normalize_action(step.action)
        if action in {"wait", "skip", "key", "popup", "tcode"}:
            return False
        return bool(step.element_id)

    def _step_matches_snapshot(self, step: MacroStep, snapshot: dict) -> tuple[bool, str]:
        step.options.pop("_last_resolution", None)
        element = self._find_snapshot_element(snapshot, step.element_id)
        if element:
            step.options["_last_resolution"] = self._make_step_resolution(step, element, snapshot, "primary", 1.0)
        if not element:
            element = self._find_step_alternative_element(step, snapshot)
            if element:
                resolved_id = element.get("id") or element.get("element_id") or ""
                if resolved_id:
                    step.element_id = _short_sap_id(resolved_id)
            else:
                candidates = self._candidate_hint_for_step(step, snapshot)
                suffix = f"；候選: {candidates}" if candidates else ""
                return False, f"step {step.index} 找不到元件 {step.element_id}{suffix}"
        if not element:
            return False, f"step {step.index} 找不到元件 {step.element_id}"
        type_ok, type_message = self._match_step_type(step, element)
        if not type_ok:
            return False, f"step {step.index} {type_message}"
        for key in ("expected_label", "expected_text", "expected_value"):
            expected = str(step.options.get(key) or "").strip()
            if not expected:
                continue
            actual = self._element_text_for_key(element, key)
            if _norm_match_text(expected) not in _norm_match_text(actual):
                return False, f"step {step.index} 元件 {step.element_id} {key} 不符，預期包含 {expected}，實際 {actual}"
        return True, ""

    def _find_step_alternative_element(self, step: MacroStep, snapshot: dict) -> dict | None:
        for candidate_id, source, confidence in self._step_alternate_id_candidates(step, snapshot):
            element = self._find_snapshot_element(snapshot, candidate_id)
            if element:
                step.options["_last_resolution"] = self._make_step_resolution(step, element, snapshot, source, confidence)
                return element
        label = str(step.label or "").strip()
        if not label:
            return None
        allowed = self._expected_types_for_action(step.action)
        matches = []
        for element in self._snapshot_elements(snapshot):
            actual_type = str(element.get("type") or "")
            if allowed and actual_type not in allowed and not any(item and item in actual_type for item in allowed):
                continue
            text = self._element_text_for_key(element, "expected_label")
            element_id = str(element.get("id") or element.get("element_id") or "")
            name = str(element.get("name") or "")
            if _norm_match_text(label) in _norm_match_text(text) or _norm_match_text(label) in _norm_match_text(name):
                matches.append(element)
        if len(matches) == 1:
            step.options["_last_resolution"] = self._make_step_resolution(step, matches[0], snapshot, "label", 0.87)
            return matches[0]
        return None

    def _make_step_resolution(self, step: MacroStep, element: dict, snapshot: dict, source: str, confidence: float) -> dict:
        screen = snapshot.get("screen") or {}
        resolved_id = _short_sap_id(element.get("id") or element.get("element_id") or "")
        return {
            "step": step.index,
            "label": step.label,
            "original_id": step.options.get("_original_element_id") or step.element_id,
            "resolved_id": resolved_id,
            "source": source,
            "confidence": confidence,
            "tcode": screen.get("transaction") or screen.get("tcode") or "",
            "screen_number": str(screen.get("screen_number") or ""),
            "screen_title": screen.get("title") or "",
        }

    @staticmethod
    def _step_resolution_event(step: MacroStep, snapshot: dict) -> dict | None:
        resolution = step.options.get("_last_resolution")
        if not isinstance(resolution, dict):
            return None
        if not resolution.get("resolved_id"):
            return None
        return dict(resolution)

    def _step_alternate_id_candidates(self, step: MacroStep, snapshot: dict) -> list[tuple[str, str, float]]:
        candidates = []
        for learned_id in step.options.get("_learned_ids") or []:
            candidates.append((learned_id, "learned_index", 0.97))
        for candidate_id in self._step_alternate_ids(step):
            candidates.append((candidate_id, "alternate_ids", 0.95))
        for result_id in self._result_screen_ids_for_step(step, snapshot):
            candidates.append((result_id, "result_screen", 0.9))
        seen = set()
        unique = []
        for candidate_id, source, confidence in candidates:
            short_id = _short_sap_id(candidate_id)
            if short_id and short_id not in seen:
                seen.add(short_id)
                unique.append((short_id, source, confidence))
        return unique

    def _result_screen_ids_for_step(self, step: MacroStep, snapshot: dict) -> list[str]:
        macro_name = str(step.options.get("_macro_name") or "")
        if macro_name != "mmbe_stock_overview" or str(step.label or "") != "物料":
            return []
        screen = snapshot.get("screen") or {}
        if str(screen.get("transaction") or "").upper() == "MMBE" and str(screen.get("screen_number") or "") == "300":
            return ["wnd[0]/usr/ctxtIO_MATERIAL"]
        return []

    @staticmethod
    def _step_alternate_ids(step: MacroStep) -> list[str]:
        raw_values = []
        for key in ("alternate_ids", "fallback_ids", "候選元件", "替代元件"):
            value = step.options.get(key)
            if value:
                raw_values.append(value)
        ids = []
        for raw in raw_values:
            for item in re.split(r"[,，;；\n]+", str(raw or "")):
                cleaned = item.strip().strip("`")
                if cleaned:
                    ids.append(cleaned)
        return ids

    def _candidate_hint_for_step(self, step: MacroStep, snapshot: dict) -> str:
        allowed = self._expected_types_for_action(step.action)
        hints = []
        for element in self._snapshot_elements(snapshot):
            actual_type = str(element.get("type") or "")
            if allowed and actual_type not in allowed and not any(item and item in actual_type for item in allowed):
                continue
            element_id = _short_sap_id(element.get("id") or element.get("element_id") or "")
            label = self._element_text_for_key(element, "expected_label")
            if element_id:
                hints.append(f"{element_id}({label})" if label else element_id)
            if len(hints) >= 5:
                break
        return ", ".join(hints)

    def _match_step_type(self, step: MacroStep, element: dict) -> tuple[bool, str]:
        actual = str(element.get("type") or "")
        expected = str(step.options.get("element_type") or "").strip()
        if expected:
            allowed = {item.strip() for item in expected.split(",") if item.strip()}
        else:
            allowed = self._expected_types_for_action(step.action)
        if allowed and actual not in allowed and not any(item and item in actual for item in allowed):
            return False, f"元件 {step.element_id} 類型不符，預期 {sorted(allowed)}，實際 {actual}"
        return True, ""

    def _expected_types_for_action(self, action: str) -> set[str]:
        normalized = self._normalize_action(action)
        if normalized in self.FIELD_ACTIONS:
            return {"GuiTextField", "GuiCTextField", "GuiPasswordField", "GuiOkCodeField"}
        if normalized == "checkbox":
            return {"GuiCheckBox"}
        if normalized == "radio":
            return {"GuiRadioButton"}
        if normalized == "combo":
            return {"GuiComboBox"}
        if normalized == "click":
            return {"GuiButton"}
        if normalized == "tab":
            return {"GuiTab"}
        if normalized in {"table_row", "popup_table_confirm"}:
            return {"GuiTableControl", "GuiGridView", "GuiShell", "GuiCtrlGridView"}
        if normalized == "textedit":
            return {"GuiTextedit", "GuiTextEdit", "GuiAbapEditor", "GuiShell"}
        if normalized == "focus":
            return set()
        return set()

    def _check_matches_snapshot(self, check: MacroCheck, snapshot: dict) -> tuple[bool, str]:
        screen = snapshot.get("screen") or {}
        kind = self._normalize_check_kind(check.kind)
        value = str(check.value or "").strip()
        if kind == "tcode":
            actual = str(screen.get("transaction") or screen.get("tcode") or "").strip()
            return actual.upper() == value.upper(), f"{check.section} tcode 不符，預期 {value}，實際 {actual}"
        if kind == "screen_number":
            actual = str(screen.get("screen_number") or "").strip()
            return actual == value, f"{check.section} screen 不符，預期 {value}，實際 {actual}"
        if kind == "title":
            actual = str(screen.get("title") or "").strip()
            return actual == value, f"{check.section} title 不符，預期 {value}，實際 {actual}"
        if kind == "title_contains":
            actual = str(screen.get("title") or "").strip()
            return _norm_match_text(value) in _norm_match_text(actual), f"{check.section} title 不包含 {value}，實際 {actual}"
        if kind == "active_window":
            actual = str(screen.get("active_window") or snapshot.get("active_window") or "").strip()
            return actual == value, f"{check.section} active_window 不符，預期 {value}，實際 {actual}"
        if kind == "status_text":
            status = screen.get("status_bar") or snapshot.get("status_bar") or {}
            actual = str(status.get("text") or "").strip()
            return _norm_match_text(value) in _norm_match_text(actual), f"{check.section} status text 不包含 {value}，實際 {actual}"
        if kind == "status_type":
            status = screen.get("status_bar") or snapshot.get("status_bar") or {}
            actual = str(status.get("type") or "").strip()
            return actual == value, f"{check.section} status type 不符，預期 {value}，實際 {actual}"
        if kind == "element":
            target = check.target or value
            element = self._find_snapshot_element(snapshot, target)
            if not element:
                if self._is_tolerable_okcode_start_check(check, snapshot):
                    return True, ""
                return False, f"{check.section} 找不到元件 {target}"
            expected_type = str(check.options.get("element_type") or "").strip()
            if expected_type:
                actual_type = str(element.get("type") or "")
                allowed = {item.strip() for item in expected_type.split(",") if item.strip()}
                if actual_type not in allowed and not any(item and item in actual_type for item in allowed):
                    return False, f"{check.section} 元件 {target} 類型不符，預期 {sorted(allowed)}，實際 {actual_type}"
            return True, ""
        return True, ""

    @staticmethod
    def _is_tolerable_okcode_start_check(check: MacroCheck, snapshot: dict) -> bool:
        if str(check.section or "").lower() != "start":
            return False
        target = _short_sap_id(check.target or check.value or "")
        if target != "wnd[0]/tbar[0]/okcd":
            return False
        screen = snapshot.get("screen") or {}
        active_window = str(snapshot.get("active_window") or screen.get("active_window") or "wnd[0]")
        return active_window == "wnd[0]"

    def _find_snapshot_element(self, snapshot: dict, element_id: str) -> dict | None:
        wanted = _short_sap_id(element_id)
        for element in self._snapshot_elements(snapshot):
            current = _short_sap_id(element.get("id") or element.get("element_id") or "")
            if current == wanted:
                return element
        return None

    def _snapshot_elements(self, snapshot: dict) -> list[dict]:
        elements = []
        for key in ("elements", "fields"):
            value = snapshot.get(key)
            if isinstance(value, list):
                elements.extend(item for item in value if isinstance(item, dict))
        screen = snapshot.get("screen") or {}
        for key in ("elements", "fields"):
            value = screen.get(key)
            if isinstance(value, list):
                elements.extend(item for item in value if isinstance(item, dict))
        popup = snapshot.get("active_popup") or screen.get("active_popup") or {}
        if isinstance(popup, dict):
            for key in ("elements", "fields"):
                value = popup.get(key)
                if isinstance(value, list):
                    elements.extend(item for item in value if isinstance(item, dict))
        return elements

    @staticmethod
    def _element_text_for_key(element: dict, key: str) -> str:
        if key == "expected_label":
            return str(element.get("label") or element.get("name") or element.get("tooltip") or element.get("text") or "")
        if key == "expected_value":
            return str(element.get("value") or element.get("text") or "")
        return str(element.get("text") or element.get("label") or element.get("tooltip") or element.get("name") or "")

    def _execute_field_group(self, session, agent, grouped: list[tuple[MacroStep, str]], validate: bool = False) -> dict:
        fields = {step.element_id: value for step, value in grouped}
        if validate and agent.mcp_tool_available("sap_set_fields_and_enter"):
            return agent.execute_macro_tool_call(
                session,
                "sap_set_fields_and_enter",
                {"fields": fields, "skip_readonly": True},
                attach_screen=False,
            )
        if len(fields) > 1 and agent.mcp_tool_available("sap_set_batch_fields"):
            return agent.execute_macro_tool_call(
                session,
                "sap_set_batch_fields",
                {"fields": fields, "validate": False, "skip_readonly": True},
                attach_screen=False,
            )

        results = []
        for step, value in grouped:
            tool_name, tool_args = self._step_to_tool(step, value)
            results.append(agent.execute_macro_tool_call(session, tool_name, tool_args, attach_screen=False))
        if validate:
            results.append(agent.execute_macro_tool_call(session, "sap_send_key", {"key": "Enter"}, attach_screen=False))
        return {
            "success": all(item.get("success", True) for item in results),
            "backend": "macro_sequence",
            "action": "field_group",
            "field_count": len(grouped),
            "results": results,
        }

    def _execute_popup_table_confirm_fallback(self, session, agent, step: MacroStep, value: str) -> dict:
        row_value = step.options.get("row") or value
        select_result = agent.execute_macro_tool_call(
            session,
            "sap_select_table_row",
            {
                "table_id": step.element_id,
                "row_index": int(row_value),
                "selected": True,
            },
            attach_screen=False,
        )
        if not select_result.get("success", True):
            return {
                "success": False,
                "backend": "macro_sequence",
                "action": "popup_table_confirm",
                "results": [select_result],
                "error": "popup table row selection failed",
            }
        popup_result = agent.execute_macro_tool_call(
            session,
            "sap_handle_popup",
            {"action": step.options.get("confirm_action", "confirm") or "confirm"},
            attach_screen=False,
        )
        return {
            "success": popup_result.get("success", True),
            "backend": "macro_sequence",
            "action": "popup_table_confirm",
            "results": [select_result, popup_result],
        }

    def _study_instruction_for_step(self, step: MacroStep, value: str) -> str:
        action = self._normalize_action(step.action)
        target = step.label or step.element_id or "目前欄位"
        if step.description:
            return step.description
        if action == "tcode":
            return f"在 T-Code 欄位輸入 {value} 並按 Enter。"
        if action in self.FIELD_ACTIONS:
            return f"在「{target}」輸入本次值 {value}。"
        if action == "checkbox":
            return f"將「{target}」設定為 {'勾選' if _parse_bool(value, True) else '取消勾選'}。"
        if action == "radio":
            return f"選取 radio 選項「{target}」。"
        if action == "combo":
            return f"在「{target}」下拉選單選擇 {value}。"
        if action == "click":
            return f"按下「{target}」。"
        if action == "key":
            return f"按下 {self._normalize_key(value)}。"
        return f"依 macro action={step.action} 完成此步驟。"

    @staticmethod
    def _log_result(log_callback, step, result, grouped=None):
        if not log_callback:
            return
        if grouped:
            label = f"steps {grouped[0][0].index}-{grouped[-1][0].index}"
        else:
            label = f"step {step.index}"
        status = "OK" if result.get("success", True) else "FAILED"
        backend = result.get("backend", "")
        error = result.get("error", "")
        suffix = f" ({backend})" if backend else ""
        if error:
            suffix += f" {error}"
        log_callback(f"{label}: {status}{suffix}")

    @staticmethod
    def _log_validation(log_callback, result):
        if not log_callback:
            return
        status = "OK" if result.get("success", True) else "FAILED"
        action = result.get("action", "validation")
        checked = result.get("checked")
        detail = f" checked={checked}" if checked is not None else ""
        error = result.get("error", "")
        suffix = f" {error}" if error else ""
        log_callback(f"{action}: {status}{detail}{suffix}")
