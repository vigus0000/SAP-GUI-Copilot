"""Parse Markdown SOP files into deterministic Study Mode steps."""

import re
from dataclasses import dataclass, field
from typing import Optional


_ELEMENT_RE = re.compile(
    r"\b(wnd\[\d+\]/[\w\[\]/.%@+:,$-]+(?:/[\w\[\]/.%@+:,$-]+)*)"
)
_NON_ACTIONABLE_SUFFIXES = ("/sbar", "/titl", "-TEXT", "-TO_TEXT")
_VKEY_PATTERNS = (
    (re.compile(r"btn\[3\]|F3\b|按.*?F3|返回.*?F3", re.I), 3),
    (re.compile(r"btn\[8\]|F8\b|按.*?F8|執行.*?按|Execute", re.I), 8),
    (re.compile(r"btn\[11\]|Ctrl\+S|Ctrl-S|儲存.*?按", re.I), 11),
    (re.compile(r"btn\[12\]|F12\b|按.*?F12|取消.*?按", re.I), 12),
    (re.compile(r"\bEnter\b|按.*?Enter|按下.*?Enter", re.I), 0),
)
_VKEY_LABELS = {
    0: "Enter",
    3: "F3 (返回)",
    8: "F8 (執行)",
    11: "Ctrl+S (儲存)",
    12: "F12 (取消)",
}


@dataclass
class StepItem:
    n: int
    header: str
    detail: str
    element_id: str = ""
    all_element_ids: list[str] = field(default_factory=list)
    vkey: Optional[int] = None


def parse_sop_steps(sop_text: str) -> list[StepItem]:
    """Extract numbered steps, SAP element IDs and common VKeys."""
    section_match = re.search(
        r"##\s*(?:[\w一二三四五六七八九十]+[、\s])?操作步驟.*?\n(.*?)(?=\n##\s|\Z)",
        str(sop_text or ""),
        re.DOTALL,
    )
    content = section_match.group(1) if section_match else str(sop_text or "")
    matches = list(re.finditer(r"^(\d+)\.\s+(.+)", content, re.MULTILINE))
    if not matches:
        return []

    steps = []
    for index, match in enumerate(matches):
        body_start = match.end()
        body_end = matches[index + 1].start() if index + 1 < len(matches) else len(content)
        header = match.group(2).strip()
        body = content[body_start:body_end].strip()
        detail = f"{header}\n{body}" if body else header

        element_ids = []
        seen = set()
        for raw_id in _ELEMENT_RE.findall(detail):
            element_id = raw_id.rstrip(".,;）)、`")
            if "..." in element_id or any(element_id.endswith(s) for s in _NON_ACTIONABLE_SUFFIXES):
                continue
            if element_id not in seen:
                seen.add(element_id)
                element_ids.append(element_id)

        vkey = None
        for pattern, value in _VKEY_PATTERNS:
            if pattern.search(detail):
                vkey = value
                break

        steps.append(StepItem(
            n=int(match.group(1)),
            header=header,
            detail=detail,
            element_id=element_ids[0] if element_ids else "",
            all_element_ids=element_ids,
            vkey=vkey,
        ))
    return steps


def steps_confidence(steps: list[StepItem]) -> float:
    if not steps:
        return 0.0
    return sum(bool(step.element_id) for step in steps) / len(steps)


def vkey_label(vkey: int) -> str:
    return _VKEY_LABELS.get(vkey, f"VKey {vkey}")


def clean_step_instruction(step: StepItem) -> str:
    """Remove technical element paths while preserving readable instructions."""
    result = []
    for original_line in step.detail.splitlines():
        line = original_line
        stripped = line.strip()
        if re.match(r"[-•]\s*元件\s*I[Dd][：:]\s*wnd\[", stripped):
            continue

        separator = re.search(r"\s*[—–-]+\s*元件\s*I[Dd][：:]", line)
        if separator:
            line = line[:separator.start()].rstrip()

        line = re.sub(
            r"`?wnd\[\d+\]/[\w\[\]/.%@+:,$-]+(?:/[\w\[\]/.%@+:,$-]+)*`?",
            "",
            line,
        )
        line = re.sub(r"\(\s*\)|（\s*）", "", line)
        line = re.sub(r" {2,}", " ", line).rstrip()
        if line.strip() not in {"", "-", "•", "—"}:
            result.append(line)
    return "\n".join(result).strip()


DisplayLine = tuple[str, str]


def compress_for_display(text: str) -> list[DisplayLine]:
    """Compact SOP text and classify required/optional table rows for UI display."""
    lines = str(text or "").splitlines()
    if not lines:
        return [("heading", str(text or ""))]

    result: list[DisplayLine] = [("heading", lines[0])]
    in_table = False
    table_rows: list[str] = []

    def flush_table():
        required = []
        optional = []
        for row_line in table_rows:
            cells = [cell.strip().strip("`") for cell in row_line.split("|") if cell.strip()]
            if not cells:
                continue
            entry = f"  • {cells[0]}"
            if len(cells) > 1 and cells[-1]:
                entry += f"（例：{cells[-1]}）"
            is_optional = any(
                keyword in cell
                for cell in cells
                for keyword in ("選填", "自動帶入", "可選", "非必填")
            )
            (optional if is_optional else required).append(entry)

        if required:
            result.append(("req_section", "  ─ 必填 ─"))
            result.extend(("req", entry) for entry in required)
        if optional:
            result.append(("opt_section", "  ─ 選填 ─"))
            result.extend(("opt", entry) for entry in optional)
        table_rows.clear()

    for line in lines[1:]:
        stripped = line.strip()
        if not stripped:
            if in_table:
                flush_table()
                in_table = False
            continue

        if stripped.startswith("|"):
            if re.match(r"^\|[\s\-:]+\|", stripped):
                in_table = True
            elif in_table:
                table_rows.append(stripped)
            continue

        if in_table:
            flush_table()
            in_table = False

        indent_level = (len(line) - len(line.lstrip(" \t"))) // 2
        if re.match(r"^[-•]?\s*(?:若|如果|如需|否則|注意|備注|說明)", stripped) and indent_level <= 1:
            continue
        if indent_level < 2:
            result.append(("body", line))

    if in_table:
        flush_table()
    return result


def display_lines_to_text(lines: list[DisplayLine]) -> str:
    parts = []
    for kind, text in lines:
        if kind == "req_section":
            parts.append("\n[必填]")
        elif kind == "opt_section":
            parts.append("[選填]")
        else:
            parts.append(text)
    return "\n".join(parts)
