"""
SAP SOP / Skill Library.

Phase 3 introduces a shared library layer for guided SOP execution.
It can read curated skills from ./skills and existing recordings from ./recordings.
"""

import json
import os
import re
from datetime import datetime

from sap_recorder import RECORDINGS_DIR, SAPRecorder


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
SKILLS_DIR = os.path.join(ROOT_DIR, "skills")


class SAPSkillLibrary:
    """Read SOP skills from curated skill files and recorded SOP files."""

    QUERY_PREFIXES = (
        "/study",
        "請教我如何",
        "請教我怎麼",
        "請教我",
        "教我如何",
        "教我怎麼",
        "教我",
        "幫我如何",
        "幫我怎麼",
        "幫我",
        "我要學",
        "我想學",
        "如何",
        "怎麼",
        "怎樣",
        "請問",
        "請",
    )

    QUERY_SUFFIXES = (
        "的教學",
        "教學",
        "流程",
        "操作",
        "怎麼做",
        "怎麼用",
        "怎麼查",
    )

    def __init__(self, skill_dirs=None):
        self.skill_dirs = skill_dirs or [
            ("skill", SKILLS_DIR),
            ("recording", RECORDINGS_DIR),
        ]
        for _, directory in self.skill_dirs:
            os.makedirs(directory, exist_ok=True)

    def list_skills(self) -> list:
        skills = []
        seen = set()
        for source_type, directory in self.skill_dirs:
            if not os.path.exists(directory):
                continue
            for filename in self._sorted_skill_files(directory):
                is_json = filename.endswith(".json")
                is_md = filename.endswith(".md")
                if not is_json and not is_md:
                    continue
                path = os.path.join(directory, filename)
                try:
                    data = self._load_path(path, source_type)
                except (json.JSONDecodeError, OSError, ValueError):
                    continue
                name = data.get("name") or os.path.splitext(filename)[0]
                key = self._lookup_key(name)
                if key in seen:
                    continue
                seen.add(key)
                skills.append({
                    "name": name,
                    "created_at": data.get("created_at", ""),
                    "duration_seconds": data.get("duration_seconds", 0),
                    "event_count": data.get("event_count", 0),
                    "raw_event_count": data.get("raw_event_count", data.get("event_count", 0)),
                    "summary": data.get("summary", ""),
                    "source": data.get("source", source_type),
                    "filepath": path,
                    "format": data.get("format", "json"),
                })
        return skills

    def load_skill(self, name: str) -> dict:
        for source_type, directory in self.skill_dirs:
            path = self._find_skill_path(directory, name)
            if path:
                return self._load_path(path, source_type)
        raise FileNotFoundError(f"找不到 SOP / skill: '{name}'")

    def get_skill_summary(self, name: str) -> str:
        data = self.load_skill(name)
        return self._format_summary(data)

    def save_markdown_skill(self, name: str, markdown_text: str) -> str:
        """Save a Markdown skill into the primary skills directory."""
        canonical_name = self.canonical_skill_name(name)
        safe_name = self._safe_name(canonical_name)
        skills_dir = self.skill_dirs[0][1]
        os.makedirs(skills_dir, exist_ok=True)

        path = os.path.join(skills_dir, f"{safe_name}.md")
        content = markdown_text.strip()
        if not content.startswith("#"):
            content = f"# SOP: {canonical_name}\n\n{content}"
        else:
            content = self._replace_markdown_title(content, canonical_name)

        metadata = (
            f"\n\n---\n"
            f"自動建立時間: {datetime.now().isoformat(timespec='seconds')}\n"
            f"來源: /study 即席教學\n"
        )
        if canonical_name != name:
            metadata += f"原始查詢: {name}\n"
            metadata += f"查詢別名: {name}\n"
        with open(path, "w", encoding="utf-8") as f:
            f.write(content + metadata)
        return path

    # Compatibility with existing CLI naming.
    def list_recordings(self) -> list:
        return self.list_skills()

    def load_recording(self, name: str) -> dict:
        return self.load_skill(name)

    def get_recording_summary(self, name: str) -> str:
        return self.get_skill_summary(name)

    def _find_skill_path(self, directory, name):
        candidate_names = self._lookup_variants(name)
        candidates = []
        for candidate_name in candidate_names:
            safe_name = self._safe_name(candidate_name)
            candidates.extend([
                os.path.join(directory, f"{candidate_name}.md"),
                os.path.join(directory, f"{safe_name}.md"),
                os.path.join(directory, f"{candidate_name}.json"),
                os.path.join(directory, f"{safe_name}.json"),
            ])
        for path in candidates:
            if os.path.exists(path):
                return path

        target_keys = {self._lookup_key(item) for item in candidate_names if item}
        if not os.path.exists(directory):
            return ""
        fuzzy_candidates = []
        for filename in self._sorted_skill_files(directory):
            is_json = filename.endswith(".json")
            is_md = filename.endswith(".md")
            if not is_json and not is_md:
                continue
            path = os.path.join(directory, filename)
            names = self._candidate_names_for_path(path, is_json)
            keys = {self._lookup_key(item) for item in names if item}
            if keys & target_keys:
                return path
            for target_key in target_keys:
                for key in keys:
                    if target_key and key and (target_key in key or key in target_key):
                        fuzzy_candidates.append((len(key), path))
                        break
        if fuzzy_candidates:
            fuzzy_candidates.sort(reverse=True)
            return fuzzy_candidates[0][1]
        return ""

    def _load_path(self, path, source_type):
        # Markdown SOP 檔案
        if path.endswith(".md"):
            with open(path, "r", encoding="utf-8") as f:
                sop_text = f.read()
            name = os.path.splitext(os.path.basename(path))[0]
            return {
                "name": name,
                "source": "skill",
                "filepath": path,
                "format": "markdown",
                "sop_text": sop_text,
                "events": [],
                "raw_events": [],
                "event_count": 0,
                "raw_event_count": 0,
                "duration_seconds": 0,
                "summary": sop_text[:200].replace("\n", " ").strip(),
                "created_at": "",
            }

        # JSON SOP 檔案
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        if not isinstance(data, dict):
            raise ValueError("Skill file must contain a JSON object")

        name = data.get("name") or os.path.splitext(os.path.basename(path))[0]
        events = data.get("events", [])
        raw_events = data.get("raw_events")

        normalized = dict(data)
        normalized["name"] = name
        normalized["source"] = source_type
        normalized["filepath"] = path

        if raw_events is None:
            raw_events = events
            events = SAPRecorder._compact_events(raw_events)
            normalized["raw_events"] = raw_events
            normalized["events"] = events

        normalized["raw_event_count"] = len(normalized.get("raw_events", []))
        normalized["event_count"] = len(normalized.get("events", []))
        if "duration_seconds" not in normalized:
            normalized["duration_seconds"] = 0
        if not normalized.get("summary"):
            normalized["summary"] = SAPRecorder._generate_summary(normalized.get("events", []))

        return normalized

    def _format_summary(self, data):
        # Markdown SOP 直接回傳內容
        if data.get("format") == "markdown" and data.get("sop_text"):
            return data["sop_text"]

        lines = [f"## SOP / Skill: {data.get('name', '')}"]
        lines.append(f"來源: {data.get('source', 'unknown')}")
        lines.append(f"檔案: {data.get('filepath', '')}")
        lines.append(f"建立時間: {data.get('created_at', 'N/A')}")
        lines.append(f"操作數量: {data.get('event_count', 0)}")
        if data.get("raw_event_count") and data.get("raw_event_count") != data.get("event_count"):
            lines.append(f"原始事件數量: {data.get('raw_event_count')}")
        lines.append(f"持續時間: {data.get('duration_seconds', 0)} 秒")
        lines.append("")

        if data.get("summary"):
            lines.append("### 摘要")
            lines.append(data["summary"])
            lines.append("")

        lines.append("### 操作步驟")
        for i, event in enumerate(data.get("events", []), 1):
            lines.append(f"{i}. {self._event_step_text(event)}")

        return "\n".join(lines)

    @staticmethod
    def _event_step_text(event):
        event_type = event.get("event_type", "")
        details = event.get("details", {})
        if event_type == "TCODE_CHANGE":
            return f"切換交易: {details.get('from_tcode', '?')} -> {details.get('to_tcode', '?')}"
        if event_type == "SCREEN_CHANGE":
            return f"畫面跳轉: {details.get('from_screen', '?')} -> {details.get('to_screen', '?')} ({details.get('title', '')})"
        if event_type == "FIELD_CHANGE":
            elem_id = details.get("element_id", "?")
            short_id = elem_id.split("/")[-1] if "/" in elem_id else elem_id
            return f"填入欄位: {short_id} = \"{details.get('to_value', '')}\""
        if event_type == "ACTIVE_WINDOW_CHANGE":
            return f"活動視窗變更: {details.get('from_window', '?')} -> {details.get('to_window', '?')} ({details.get('title', '')})"
        if event_type == "WINDOW_OPEN":
            return f"彈窗開啟: {details.get('window_id', '?')} ({details.get('title', '')})"
        if event_type == "WINDOW_CLOSE":
            return f"彈窗關閉: {details.get('window_id', '?')} ({details.get('title', '')})"
        if event_type == "FOCUS_CHANGE":
            elem_id = details.get("to_element", "?")
            short_id = elem_id.split("/")[-1] if "/" in elem_id else elem_id
            return f"焦點移動: {short_id}"
        if event_type == "STATUS_MESSAGE":
            return f"狀態訊息: [{details.get('type', '?')}] {details.get('text', '')}"
        return f"{event_type}: {json.dumps(details, ensure_ascii=False)[:50]}"

    @staticmethod
    def _safe_name(name: str) -> str:
        safe_name = "".join(c for c in name if c.isalnum() or c in (" ", "_", "-", ".", "（", "）"))
        return safe_name.strip() or "unnamed"

    @classmethod
    def canonical_skill_name(cls, name: str) -> str:
        """Turn a natural-language /study query into a stable skill name."""
        original = str(name or "").strip()
        text = re.sub(r"\s+", "", original)
        text = text.strip(" ：:，,。.!！?？、")

        changed = True
        while changed:
            changed = False
            for prefix in cls.QUERY_PREFIXES:
                if text.lower().startswith(prefix.lower()) and len(text) > len(prefix):
                    text = text[len(prefix):].strip(" ：:，,。.!！?？、")
                    changed = True
                    break

        changed = True
        while changed:
            changed = False
            for suffix in cls.QUERY_SUFFIXES:
                if text.endswith(suffix) and len(text) > len(suffix):
                    text = text[:-len(suffix)].strip(" ：:，,。.!！?？、")
                    changed = True
                    break

        return text or original or "unnamed"

    @classmethod
    def _lookup_key(cls, name: str) -> str:
        canonical = cls.canonical_skill_name(name)
        return "".join(ch.lower() for ch in canonical if ch.isalnum())

    @classmethod
    def _lookup_variants(cls, name: str) -> list:
        variants = []
        canonical = cls.canonical_skill_name(name)
        for value in (canonical, name, cls._safe_name(canonical), cls._safe_name(name)):
            value = str(value or "").strip()
            if value and value not in variants:
                variants.append(value)
        return variants

    @classmethod
    def _sorted_skill_files(cls, directory):
        filenames = [
            filename for filename in os.listdir(directory)
            if filename.endswith(".json") or filename.endswith(".md")
        ]

        def sort_key(filename):
            stem = os.path.splitext(filename)[0]
            canonical = cls.canonical_skill_name(stem)
            is_canonical = stem == canonical
            return (0 if is_canonical else 1, canonical, stem)

        return sorted(filenames, key=sort_key)

    @classmethod
    def _candidate_names_for_path(cls, path, is_json):
        base_name = os.path.splitext(os.path.basename(path))[0]
        names = [base_name, cls.canonical_skill_name(base_name)]
        try:
            if is_json:
                with open(path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if isinstance(data, dict):
                    names.append(str(data.get("name") or ""))
                    aliases = data.get("aliases") or data.get("alias") or []
                    if isinstance(aliases, str):
                        aliases = [aliases]
                    names.extend(str(item) for item in aliases)
            else:
                with open(path, "r", encoding="utf-8") as f:
                    for _ in range(80):
                        line = f.readline()
                        if not line:
                            break
                        stripped = line.strip()
                        if stripped.startswith("#"):
                            names.append(cls._clean_markdown_title(stripped))
                        elif stripped.startswith(("原始查詢:", "查詢別名:", "Aliases:", "Alias:")):
                            _, value = stripped.split(":", 1)
                            names.extend(item.strip() for item in value.split(","))
        except (json.JSONDecodeError, OSError, ValueError):
            pass
        return [item for item in names if item]

    @staticmethod
    def _clean_markdown_title(line: str) -> str:
        title = line.lstrip("#").strip()
        for prefix in ("SOP:", "SOP：", "Skill:", "Skill："):
            if title.startswith(prefix):
                return title[len(prefix):].strip()
        return title

    @classmethod
    def _replace_markdown_title(cls, content: str, canonical_name: str) -> str:
        lines = content.splitlines()
        if not lines:
            return f"# SOP: {canonical_name}"
        for idx, line in enumerate(lines):
            if line.startswith("#"):
                lines[idx] = f"# SOP: {canonical_name}"
                return "\n".join(lines)
        return f"# SOP: {canonical_name}\n\n{content}"
