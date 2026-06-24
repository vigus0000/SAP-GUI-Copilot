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
SKILL_INDEX_FILENAME = "_skill_index.json"

SAP_MODULES = {
    "MM": {
        "name": "物料管理",
        "description": "採購、庫存、物料主檔管理",
        "tcodes": {"ME51N", "ME5A", "ME21N", "ME2L", "ME2M", "ME2N", "MIGO", "MMBE", "MB51", "MM01"},
        "keywords": {"物料", "採購", "庫存", "料號", "material", "purchase", "stock", "inventory"},
        "suggested_skills": [
            ("請購單建立",          "ME51N", "建立採購申請請購單"),
            ("請購單列表",          "ME5A",  "查詢請購單清單報表"),
            ("採購單建立",          "ME21N", "建立標準採購訂單"),
            ("採購單查詢（依供應商）", "ME2L",  "依供應商查詢採購訂單清單"),
            ("採購單查詢（依物料）",  "ME2M",  "依物料查詢採購訂單清單"),
            ("採購單查詢（依採購文件）","ME2N", "查詢採購訂單清單"),
            ("採購單收貨",          "MIGO",  "執行貨物收貨過帳"),
            ("庫存數量查詢",        "MMBE",  "查詢物料各工廠庫存概況"),
            ("物料異動文件查詢",    "MB51",  "查詢物料異動記錄"),
            ("物料主檔",           "MM01",  "建立／查詢物料主檔"),
        ],
    },
    "SD": {
        "name": "銷售配銷",
        "description": "銷售訂單、交貨、請款與客戶管理",
        "tcodes": {"VA01", "VA02", "VA05", "VL01N", "VL02N", "VL06F", "VF01", "VF05", "VF03"},
        "keywords": {"銷售", "訂單", "交貨", "發票", "客戶", "sales", "delivery", "billing", "invoice"},
        "suggested_skills": [
            ("銷售訂單建立",              "VA01",  "建立標準銷售訂單"),
            ("銷售訂單更改",              "VA02",  "修改已建立的銷售訂單"),
            ("銷售文件清單",              "VA05",  "依條件查詢銷售訂單清單"),
            ("出貨單建立",               "VL01N", "依銷售訂單建立出貨單"),
            ("出貨單變更／發貨過帳",       "VL02N", "修改出貨單並執行發貨過帳"),
            ("出貨單清單",               "VL06F", "查詢出貨單清單"),
            ("請款文件建立",              "VF01",  "依交貨單建立請款發票"),
            ("請款文件清單",              "VF05",  "查詢請款文件清單"),
            ("請款文件會計文件查詢",       "VF03",  "查詢請款文件及對應 FI 憑證"),
        ],
    },
    "FI": {
        "name": "財務會計",
        "description": "總帳、應收應付帳款、憑證過帳",
        "tcodes": {"FBL5N", "F-28", "MIRO", "MIR6", "FBL1N", "F-53"},
        "keywords": {"財務", "會計", "憑證", "應付", "應收", "過帳", "帳款", "finance", "accounting"},
        "suggested_skills": [
            ("客戶明細查詢",       "FBL5N", "查詢客戶應收帳款明細"),
            ("應收帳款結清",       "F-28",  "執行客戶應收帳款沖銷結清"),
            ("發票驗證",          "MIRO",  "輸入供應商發票產生應付帳款"),
            ("發票文件查詢",       "MIR6",  "查詢採購發票驗證文件清單"),
            ("應付帳款查詢",       "FBL1N", "查詢供應商應付帳款明細"),
            ("應付帳款結清",       "F-53",  "執行供應商應付帳款付款結清"),
        ],
    },
    "CO": {
        "name": "管理會計",
        "description": "成本中心、利潤中心、內部訂單管理",
        "tcodes": {"KS01", "KS03", "KSB1", "KE5Z", "KO01", "KO03"},
        "keywords": {"成本", "利潤", "管理會計", "cost", "profit", "controlling"},
        "suggested_skills": [
            ("成本中心查詢", "KS03", "查詢成本中心主檔資料"),
            ("成本中心實際值報表", "KSB1", "查詢成本中心實際成本明細"),
            ("利潤中心報表", "KE5Z", "查詢利潤中心損益"),
            ("內部訂單查詢", "KO03", "查詢內部訂單主檔"),
        ],
    },
    "PP": {
        "name": "生產規劃",
        "description": "生產訂單、物料需求規劃、BOM 管理",
        "tcodes": {"CO01", "CO02", "CO03", "CS01", "CS03", "MD01", "MD04", "CA03"},
        "keywords": {"生產", "製造", "工單", "bom", "mrp", "production", "manufacturing", "planning"},
        "suggested_skills": [
            ("生產訂單建立", "CO01", "建立生產製造訂單"),
            ("生產訂單查詢", "CO03", "查詢生產訂單明細"),
            ("BOM查詢", "CS03", "查詢物料清單結構"),
            ("MRP執行", "MD01", "執行物料需求規劃"),
            ("物料需求查詢", "MD04", "查詢物料需求與供給"),
            ("工藝路線查詢", "CA03", "查詢生產工藝路線"),
        ],
    },
}


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

    TAG_GROUPS = {
        "查詢": {"查詢", "查看", "顯示", "查", "display", "show", "displaying"},
        "建立": {"建立", "新增", "創建", "create", "new"},
        "修改": {"修改", "變更", "更改", "change", "edit"},
        "物料": {"物料", "料號", "material", "matnr", "mara", "mm03", "mmbe"},
        "庫存": {"庫存", "存貨", "inventory", "stock", "mmbe"},
        "請款": {"請款", "發票", "billing", "invoice", "vf05", "vbrk", "vbrp"},
        "銷售": {"銷售", "sales", "sd", "va01", "va02", "va03"},
        "採購": {"採購", "purchase", "purchasing", "me21n", "me22n", "me23n"},
        "ABAP": {"abap", "se38", "程式", "program", "report"},
    }

    GENERIC_TAGS = {"查詢", "建立", "修改"}

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
                if filename == SKILL_INDEX_FILENAME:
                    continue
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

        tags = self.extract_skill_tags(canonical_name, f"{name}\n{content}")
        metadata = (
            f"\n\n---\n"
            f"自動建立時間: {datetime.now().isoformat(timespec='seconds')}\n"
            f"來源: /study 即席教學\n"
            f"Tags: {', '.join(tags)}\n"
        )
        if canonical_name != name:
            metadata += f"原始查詢: {name}\n"
            metadata += f"查詢別名: {name}\n"
        with open(path, "w", encoding="utf-8") as f:
            f.write(content + metadata)
        self.rebuild_index()
        return path

    def rebuild_index(self) -> str:
        """Rebuild a lightweight tag index for semantic-ish skill lookup."""
        entries = []
        for source_type, directory in self.skill_dirs:
            if not os.path.exists(directory):
                continue
            for filename in self._sorted_skill_files(directory):
                if filename == SKILL_INDEX_FILENAME:
                    continue
                is_json = filename.endswith(".json")
                is_md = filename.endswith(".md")
                if not is_json and not is_md:
                    continue
                path = os.path.join(directory, filename)
                try:
                    data = self._load_path(path, source_type)
                except (json.JSONDecodeError, OSError, ValueError):
                    continue
                text = data.get("sop_text") or data.get("summary") or ""
                tags = data.get("tags") or self.extract_skill_tags(data.get("name", ""), text)
                entries.append({
                    "name": data.get("name", ""),
                    "canonical_name": self.canonical_skill_name(data.get("name", "")),
                    "tags": tags,
                    "source": source_type,
                    "format": data.get("format", "json"),
                    "filepath": os.path.relpath(path, ROOT_DIR),
                    "updated_at": datetime.now().isoformat(timespec="seconds"),
                })

        index_path = os.path.join(self.skill_dirs[0][1], SKILL_INDEX_FILENAME)
        with open(index_path, "w", encoding="utf-8") as f:
            json.dump({"version": 1, "skills": entries}, f, ensure_ascii=False, indent=2)
        return index_path

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
            if filename == SKILL_INDEX_FILENAME:
                continue
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
                        fuzzy_candidates.append((50 + len(key), path))
                        break
            semantic_score = self._semantic_score(name, names)
            if semantic_score > 0:
                fuzzy_candidates.append((semantic_score, path))
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
            tags = self._markdown_tags(sop_text) or self.extract_skill_tags(name, sop_text)
            return {
                "name": name,
                "source": "skill",
                "filepath": path,
                "format": "markdown",
                "sop_text": sop_text,
                "tags": tags,
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

        if text.startswith("查") and not text.startswith("查詢") and len(text) > 1:
            text = f"查詢{text[1:]}"

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
            if filename != SKILL_INDEX_FILENAME and (filename.endswith(".json") or filename.endswith(".md"))
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
                    tags = data.get("tags") or []
                    if isinstance(tags, str):
                        tags = [tags]
                    names.extend(str(item) for item in tags)
            else:
                with open(path, "r", encoding="utf-8") as f:
                    for _ in range(80):
                        line = f.readline()
                        if not line:
                            break
                        stripped = line.strip()
                        if stripped.startswith("#"):
                            names.append(cls._clean_markdown_title(stripped))
                        elif stripped.startswith(("原始查詢:", "查詢別名:", "Aliases:", "Alias:", "Tags:", "Tag:", "標籤:")):
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
    def _markdown_tags(cls, sop_text: str) -> list:
        tags = []
        for line in str(sop_text or "").splitlines():
            stripped = line.strip()
            if stripped.startswith(("Tags:", "Tag:", "標籤:")):
                _, value = stripped.split(":", 1)
                tags.extend(item.strip() for item in value.split(",") if item.strip())
        return cls._dedupe_tags(tags)

    @classmethod
    def extract_skill_tags(cls, name: str, text: str = "") -> list:
        haystack = f"{name}\n{text}"
        normalized = cls._normalize_semantic_text(haystack)
        normalized_name = cls._normalize_semantic_text(name)
        tags = []

        for tag, aliases in cls.TAG_GROUPS.items():
            search_area = normalized_name if tag in cls.GENERIC_TAGS else normalized
            if any(cls._normalize_semantic_text(alias) in search_area for alias in aliases):
                tags.append(tag)

        for tcode in re.findall(r"\b[A-Z]{2,4}\d{1,3}[A-Z]?\b", str(haystack).upper()):
            tags.append(tcode)

        canonical = cls.canonical_skill_name(name)
        if canonical:
            tags.insert(0, canonical)
        return cls._dedupe_tags(tags)

    @classmethod
    def _semantic_terms(cls, *texts) -> set:
        terms = set()
        for text in texts:
            if not text:
                continue
            normalized = cls._normalize_semantic_text(text)
            if normalized:
                terms.add(normalized)
            terms.add(cls._lookup_key(text))
            terms.update(cls.extract_skill_tags(str(text), str(text)))
        return {term for term in terms if term}

    @classmethod
    def _semantic_score(cls, query: str, candidate_names: list) -> int:
        query_terms = cls._semantic_terms(query)
        candidate_terms = cls._semantic_terms(*candidate_names)
        overlap = query_terms & candidate_terms
        if not overlap:
            return 0

        non_generic_overlap = {term for term in overlap if term not in cls.GENERIC_TAGS}
        if not non_generic_overlap:
            return 0

        score = len(non_generic_overlap) * 12 + len(overlap) * 3
        query_key = cls._lookup_key(query)
        candidate_keys = {cls._lookup_key(item) for item in candidate_names if item}
        if query_key in candidate_keys:
            score += 80
        return score

    @staticmethod
    def _normalize_semantic_text(text: str) -> str:
        return "".join(ch.lower() for ch in str(text or "") if ch.isalnum())

    @staticmethod
    def _dedupe_tags(tags: list) -> list:
        result = []
        seen = set()
        for tag in tags:
            tag = str(tag or "").strip()
            key = tag.lower()
            if not tag or key in seen:
                continue
            seen.add(key)
            result.append(tag)
        return result

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

    # ===== SAP 模組課程 =====

    @classmethod
    def get_module(cls, code: str) -> dict:
        """Return module info dict for a given module code (e.g. 'MM'), or None."""
        return SAP_MODULES.get(str(code or "").upper())

    @classmethod
    def is_module_code(cls, text: str) -> bool:
        return str(text or "").upper() in SAP_MODULES

    @classmethod
    def parse_module_prefix(cls, text: str):
        """Extract optional SAP module code prefix from a study argument.

        Returns (module_code, remainder).  module_code is None if not detected.
        Examples:
          "MM"            → ("MM", "")
          "MM 物料查詢"   → ("MM", "物料查詢")
          "依公司代碼查詢" → (None, "依公司代碼查詢")
        """
        tokens = str(text or "").strip().split(maxsplit=1)
        if not tokens:
            return None, text
        if cls.is_module_code(tokens[0]):
            return tokens[0].upper(), tokens[1].strip() if len(tokens) > 1 else ""
        return None, text

    def list_skills_by_module(self, module_code: str) -> list:
        """Return existing skills that belong to the given SAP module."""
        module = self.get_module(module_code)
        if not module:
            return []
        tcodes = {t.lower() for t in module["tcodes"]}
        keywords = {k.lower() for k in module["keywords"]}
        matched = []
        for skill in self.list_skills():
            haystack = self._normalize_semantic_text(
                f"{skill['name']} {skill.get('summary', '')}"
            )
            if any(t in haystack for t in tcodes) or any(k in haystack for k in keywords):
                matched.append(skill)
        return matched

    @classmethod
    def format_module_list(cls) -> str:
        """Return a plain-text overview of all SAP modules."""
        lines = ["📘 SAP 五大模組課程", ""]
        for code, info in SAP_MODULES.items():
            lines.append(f"  {code}  {info['name']:<8}  {info['description']}")
        lines += ["", "輸入 /study [模組代碼] 查看課程，例如 /study MM"]
        return "\n".join(lines)

    def format_module_curriculum(self, module_code: str) -> str:
        """Return a plain-text curriculum for one SAP module."""
        module = SAP_MODULES.get(module_code)
        if not module:
            return f"未知模組代碼：{module_code}"

        lines = [
            f"📘 {module_code} — {module['name']}",
            f"  {module['description']}",
            "",
        ]

        existing = self.list_skills_by_module(module_code)
        existing_keys = {self._lookup_key(s["name"]) for s in existing}

        if existing:
            lines.append("[已有 Skill - 可直接啟動教練]")
            for skill in existing:
                lines.append(f"  - {skill['name']}")
                lines.append(f"    /study {module_code} {skill['name']}")
            lines.append("")

        missing = [
            (name, tcode, desc)
            for name, tcode, desc in module["suggested_skills"]
            if self._lookup_key(name) not in existing_keys
        ]
        if missing:
            lines.append("[建議課程 - 尚未錄製]")
            for name, tcode, desc in missing:
                lines.append(f"  - {name} ({tcode})  {desc}")
                lines.append(f"    /record {name}   或   /study --draft {name}")
            lines.append("")

        lines.append(f"輸入 /study {module_code} [課程名稱] 啟動教練引導")
        return "\n".join(lines)
