"""
Hierarchical SAP knowledge and Study skill draft library.

This module keeps exploratory Study Mode grounded in evidence:
module -> business cycle -> source document. Distilled output is saved as
drafts first; only an explicit promote action copies a draft into ./skills.
"""

import hashlib
import html
import json
import os
import re
import shutil
import time
import urllib.parse
import urllib.request
import zipfile
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
KNOWLEDGE_DIRNAME = "knowledge"
KNOWLEDGE_INDEX_FILENAME = "_knowledge_index.json"
KNOWLEDGE_DOCS_DIRNAME = "documents"
DRAFTS_RELATIVE_DIR = os.path.join("skills", "_drafts")


class _HTMLTextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.title = ""
        self._in_title = False
        self._skip_depth = 0
        self.parts = []

    def handle_starttag(self, tag, _attrs):
        tag = tag.lower()
        if tag == "title":
            self._in_title = True
        if tag in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1
        if tag in {"p", "br", "div", "li", "tr", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "title":
            self._in_title = False
        if tag in {"script", "style", "noscript", "svg"} and self._skip_depth:
            self._skip_depth -= 1
        if tag in {"p", "div", "li", "tr", "h1", "h2", "h3"}:
            self.parts.append("\n")

    def handle_data(self, data):
        text = " ".join(str(data or "").split())
        if not text:
            return
        if self._in_title:
            self.title = (self.title + " " + text).strip()
            return
        if self._skip_depth:
            return
        self.parts.append(text)
        self.parts.append(" ")

    def text(self):
        raw = html.unescape("".join(self.parts))
        return re.sub(r"\n{3,}", "\n\n", raw).strip()


class SAPKnowledgeLibrary:
    """Import, index, distill, and promote SAP knowledge-backed Study drafts."""

    MODULE_RULES = {
        "MM": [
            "mm", "material", "material master", "物料", "物料主檔", "庫存", "採購",
            "mm01", "mm02", "mm03", "mmbe", "mara", "marc", "mard", "mseg", "mkpf",
            "me21n", "me22n", "me23n", "migo", "miro",
        ],
        "FI": [
            "fi", "finance", "financial", "accounting", "ap", "ar", "gl", "g/l",
            "應付", "應收", "總帳", "會計", "供應商", "客戶明細", "fbl1n", "fbl3n",
            "fbl5n", "fb50", "fb60", "miro",
        ],
        "SD": [
            "sd", "sales", "billing", "invoice", "customer", "銷售", "請款", "發票",
            "出貨", "付款人", "vf01", "vf03", "vf05", "va01", "va02", "va03",
            "vl01n", "vbrk", "vbrp", "likp", "lips",
        ],
        "ABAP": [
            "abap", "se38", "se80", "se11", "程式", "報表", "原始碼", "activate",
            "syntax", "debug", "program",
        ],
        "Basis": [
            "basis", "su01", "sm37", "st22", "scc4", "rfc", "spool", "系統管理",
            "使用者", "權限", "背景作業",
        ],
    }

    CYCLE_RULES = {
        "Procure-to-Pay": [
            "procure", "purchase", "purchasing", "supplier", "vendor", "ap",
            "採購", "供應商", "應付", "me21n", "me23n", "migo", "miro", "fbl1n",
        ],
        "Order-to-Cash": [
            "order-to-cash", "sales order", "delivery", "billing", "customer",
            "銷售", "出貨", "請款", "發票", "付款人", "va01", "va03", "vl01n",
            "vf01", "vf05",
        ],
        "Record-to-Report": [
            "record-to-report", "general ledger", "gl", "g/l", "financial close",
            "總帳", "結帳", "會計", "fbl3n", "fb50",
        ],
        "Material Master": [
            "material master", "物料主檔", "顯示物料", "查詢物料", "mm01", "mm02",
            "mm03", "mara", "marc",
        ],
        "Billing": [
            "billing", "invoice", "billing document", "請款文件", "開立發票",
            "發票清單", "vf01", "vf03", "vf05", "vbrk", "vbrp",
        ],
        "Inventory": [
            "inventory", "stock", "庫存", "存貨", "mmbe", "mard", "mseg",
        ],
        "ABAP Development": [
            "abap", "se38", "se80", "program", "report", "syntax", "activate",
            "程式", "報表", "原始碼",
        ],
        "Basis Administration": [
            "basis", "user", "authorization", "background job", "使用者", "權限",
            "背景作業", "su01", "sm37", "st22",
        ],
    }

    HIGH_TRUST_DOMAINS = (
        "help.sap.com",
        "learning.sap.com",
        "support.sap.com",
        "community.sap.com",
        "sap.com",
    )

    def __init__(self, root_dir=None):
        self.root_dir = os.path.abspath(root_dir or ROOT_DIR)
        self.knowledge_dir = os.path.join(self.root_dir, KNOWLEDGE_DIRNAME)
        self.documents_dir = os.path.join(self.knowledge_dir, KNOWLEDGE_DOCS_DIRNAME)
        self.index_path = os.path.join(self.knowledge_dir, KNOWLEDGE_INDEX_FILENAME)
        self.drafts_dir = os.path.join(self.root_dir, DRAFTS_RELATIVE_DIR)
        os.makedirs(self.documents_dir, exist_ok=True)
        os.makedirs(self.drafts_dir, exist_ok=True)
        self.index = self._load_index()
        self._web_cache = {}

    # Public API
    def import_source(self, source, source_type="", tags=None):
        """Import a file, folder, or URL into hierarchical knowledge index."""
        source = str(source or "").strip()
        if not source:
            raise ValueError("source is required")
        tags = self._split_tags(tags)

        imported = []
        if self._is_url(source):
            title, text = self._read_url(source)
            imported.extend(self._index_document(
                title=title or source,
                text=text,
                source_path="",
                source_url=source,
                source_type=source_type or self._source_type_for_url(source),
                extra_tags=tags,
            ))
        else:
            path = os.path.abspath(os.path.expanduser(source))
            if not os.path.exists(path):
                raise FileNotFoundError(source)
            paths = self._iter_document_paths(path)
            for item in paths:
                title, text = self._read_file(item)
                if not text.strip():
                    continue
                imported.extend(self._index_document(
                    title=title or Path(item).stem,
                    text=text,
                    source_path=item,
                    source_url="",
                    source_type=source_type or "local_document",
                    extra_tags=tags,
                ))

        if imported:
            self._save_index()
        return imported

    def rebuild(self):
        """Rebuild metadata from stored normalized documents."""
        entries = []
        for text_path in sorted(Path(self.documents_dir).glob("*.txt")):
            try:
                text = text_path.read_text(encoding="utf-8")
            except OSError:
                continue
            old_entries = [
                item for item in self.index.get("documents", [])
                if item.get("content_hash") == text_path.stem
            ]
            if old_entries:
                base = old_entries[0]
                source_path = base.get("source_path", "")
                source_url = base.get("source_url", "")
                source_type = base.get("source_type", "rebuilt")
                title = base.get("document_title") or Path(source_path or source_url or text_path.name).stem
            else:
                source_path = ""
                source_url = ""
                source_type = "rebuilt"
                title = text_path.stem
            entries.extend(self._build_entries(
                title=title,
                text=text,
                source_path=source_path,
                source_url=source_url,
                source_type=source_type,
                content_hash=text_path.stem,
                extra_tags=[],
            ))
        self.index["documents"] = entries
        self.index["updated_at"] = self._now()
        self._save_index()
        return self.index_path

    def search(self, query, limit=None, include_drafts=False):
        query = str(query or "").strip()
        if not query:
            return []
        limit = int(limit or os.getenv("STUDY_KNOWLEDGE_MAX_MATCHES", "5") or 5)
        query_terms = self._terms(query)
        scored = []
        for entry in self.index.get("documents", []):
            score = self._score_entry(query, query_terms, entry)
            if score > 0:
                item = dict(entry)
                item["score"] = score
                scored.append(item)
        if include_drafts:
            for draft in self.list_drafts():
                score = self._score_text(query_terms, draft.get("search_text", ""))
                if score > 0:
                    item = {
                        "id": draft.get("id"),
                        "module": draft.get("module", ""),
                        "business_cycle": draft.get("business_cycle", ""),
                        "document_title": draft.get("title", ""),
                        "source_path": draft.get("path", ""),
                        "source_type": "draft_skill",
                        "tags": draft.get("tags", []),
                        "tcode": [],
                        "confidence": "draft",
                        "content_hash": "",
                        "summary": draft.get("summary", ""),
                        "score": score,
                    }
                    scored.append(item)
        scored.sort(key=lambda item: (item.get("score", 0), item.get("confidence", "")), reverse=True)
        return scored[:limit]

    def distill(self, source_or_query, limit=None):
        """Create draft Study skills under skills/_drafts/module/cycle/document.md."""
        source_or_query = str(source_or_query or "").strip()
        if not source_or_query:
            raise ValueError("source_or_query is required")

        if self._is_url(source_or_query) or os.path.exists(os.path.expanduser(source_or_query)):
            entries = self.import_source(source_or_query)
        else:
            entries = self.search(source_or_query, limit=limit)

        if not entries:
            return []

        created = []
        seen = set()
        for entry in entries:
            key = (entry.get("content_hash"), entry.get("module"), entry.get("business_cycle"))
            if key in seen:
                continue
            seen.add(key)
            created.append(self._write_draft(entry))
        return created

    def list_drafts(self):
        drafts = []
        base = Path(self.drafts_dir)
        if not base.exists():
            return drafts
        for path in sorted(base.rglob("*.md")):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                continue
            rel = path.relative_to(base)
            parts = rel.parts
            module = parts[0] if len(parts) > 2 else ""
            cycle = parts[1] if len(parts) > 2 else ""
            title = self._first_markdown_title(text) or path.stem
            tags = self._extract_metadata_line(text, "Tags")
            drafts.append({
                "id": str(rel).replace("\\", "/"),
                "path": str(path),
                "relative_path": str(rel).replace("\\", "/"),
                "module": module,
                "business_cycle": cycle,
                "title": self._clean_title(title),
                "tags": self._split_tags(tags),
                "summary": self._section_preview(text, "目的") or self._section_preview(text, "Purpose"),
                "search_text": f"{title}\n{rel}\n{text[:4000]}",
            })
        return drafts

    def promote_draft(self, draft_query):
        draft = self._find_draft(draft_query)
        if not draft:
            raise FileNotFoundError(f"找不到 draft skill: {draft_query}")

        src = draft["path"]
        title = draft.get("title") or Path(src).stem
        dest_name = self._safe_filename(title)
        skills_dir = os.path.join(self.root_dir, "skills")
        os.makedirs(skills_dir, exist_ok=True)
        dest = os.path.join(skills_dir, f"{dest_name}.md")
        if os.path.exists(dest) and os.path.abspath(dest) != os.path.abspath(src):
            suffix = self._safe_filename(f"{draft.get('module')}_{draft.get('business_cycle')}")
            dest = os.path.join(skills_dir, f"{dest_name}_{suffix}.md")

        text = Path(src).read_text(encoding="utf-8")
        metadata = (
            "\n\n---\n"
            f"Promoted: {self._now()}\n"
            f"來源階層: {draft.get('module')} > {draft.get('business_cycle')} > {draft.get('relative_path')}\n"
            "來源: /skills promote\n"
        )
        with open(dest, "w", encoding="utf-8") as f:
            f.write(text.rstrip() + metadata)

        try:
            from sap_skill_library import SAPSkillLibrary
            SAPSkillLibrary(skill_dirs=[("skill", skills_dir)]).rebuild_index()
        except Exception:
            pass
        return dest

    def build_evidence_pack(self, goal, screen_state=None, skill_library=None):
        """Return Markdown evidence pack for ad-hoc Study Mode."""
        goal = str(goal or "").strip()
        formal_matches = self._match_formal_skills(goal, skill_library)
        draft_matches = self._match_drafts(goal)
        knowledge_matches = self.search(goal, limit=int(os.getenv("STUDY_KNOWLEDGE_MAX_MATCHES", "5") or 5))
        web_matches = self.web_search(goal) if self._env_enabled("STUDY_WEB_SEARCH_ENABLED", "false") else []
        evidence_summary = self._evidence_summary(
            formal_matches,
            draft_matches,
            knowledge_matches,
            web_matches,
        )

        lines = [
            "# Study Evidence Pack",
            "",
            "## 查詢目標",
            goal or "(未提供)",
            "",
            "## Evidence Summary",
            f"- evidence_level: {evidence_summary['evidence_level']}",
            f"- best_confidence: {evidence_summary['best_confidence']}",
            f"- should_confirm_flow: {str(evidence_summary['should_confirm_flow']).lower()}",
            f"- recommended_source: {evidence_summary['recommended_source']}",
            "",
            "## 目前 SAP 畫面",
            self._screen_brief(screen_state),
            "",
            "## 證據優先級",
            "1. 正式 skill / recording",
            "2. 已 promote 的公司 skill",
            "3. 同 module / business cycle draft",
            "4. 匯入 knowledge 文件",
            "5. 即時官方搜尋",
            "6. 社群或一般搜尋",
            "7. LLM prior",
            "",
            "## 正式 Skill 命中",
            self._format_formal_matches(formal_matches),
            "",
            "## 階層式 Draft 命中",
            self._format_evidence_entries(draft_matches),
            "",
            "## Knowledge 命中",
            self._format_evidence_entries(knowledge_matches),
        ]
        if self._env_enabled("STUDY_WEB_SEARCH_ENABLED", "false"):
            lines.extend([
                "",
                "## 即時網搜 Evidence",
                self._format_web_matches(web_matches),
            ])
        else:
            lines.extend([
                "",
                "## 即時網搜 Evidence",
                "- 未啟用。若需要，設定 `STUDY_WEB_SEARCH_ENABLED=true`。",
            ])

        lines.extend([
            "",
            "## Structured Rationale 要求",
            "- 不保存或輸出 raw chain-of-thought。",
            "- 每個候選流程都要標示 module、business_cycle、來源與信心。",
            "- 若沒有正式 skill，先引用 draft / knowledge / web evidence；若只剩 LLM prior，必須先請使用者確認第一個關鍵 T-Code 或流程方向。",
            "- 下一步只提出一個安全引導動作，並使用 `guide_user_action` 或 `visualize_element`。",
        ])
        return "\n".join(lines)

    def _evidence_summary(self, formal_matches, draft_matches, knowledge_matches, web_matches):
        if formal_matches:
            return {
                "evidence_level": "formal_skill",
                "best_confidence": "high",
                "should_confirm_flow": False,
                "recommended_source": "formal skill / recording",
            }
        if draft_matches:
            return {
                "evidence_level": "draft",
                "best_confidence": "draft",
                "should_confirm_flow": False,
                "recommended_source": "draft skill",
            }
        if knowledge_matches:
            confidence = str(knowledge_matches[0].get("confidence") or "medium")
            return {
                "evidence_level": "knowledge",
                "best_confidence": confidence,
                "should_confirm_flow": confidence not in {"high", "medium"},
                "recommended_source": "knowledge",
            }
        if web_matches:
            confidence = str(web_matches[0].get("confidence") or "low")
            return {
                "evidence_level": "web",
                "best_confidence": confidence,
                "should_confirm_flow": True,
                "recommended_source": "web search",
            }
        return {
            "evidence_level": "llm_prior_only",
            "best_confidence": "low",
            "should_confirm_flow": True,
            "recommended_source": "LLM prior",
        }

    def web_search(self, query, max_results=None):
        max_results = int(max_results or os.getenv("STUDY_WEB_SEARCH_MAX_RESULTS", "5") or 5)
        timeout = float(os.getenv("STUDY_WEB_SEARCH_TIMEOUT_SECONDS", "8") or 8)
        ttl = int(os.getenv("STUDY_WEB_SEARCH_CACHE_TTL_SECONDS", "86400") or 86400)
        cache_key = f"{query}::{max_results}"
        cache = getattr(self, "_web_cache", {})
        now = time.time()
        if ttl > 0 and cache_key in cache:
            cached_at, cached_results = cache[cache_key]
            if now - cached_at <= ttl:
                return list(cached_results)

        queries = [
            f"site:help.sap.com SAP {query}",
            f"site:learning.sap.com SAP {query}",
            f"SAP {query}",
        ]
        results = []
        seen = set()
        for item_query in queries:
            try:
                for result in self._duckduckgo_html_search(item_query, timeout=timeout):
                    url = result.get("url", "")
                    key = self._normalize_url(url)
                    if not key or key in seen:
                        continue
                    seen.add(key)
                    result["source_type"] = self._source_type_for_url(url)
                    result["confidence"] = "medium" if result["source_type"] in {"official", "community"} else "low"
                    results.append(result)
                    if len(results) >= max_results:
                        if ttl > 0:
                            cache[cache_key] = (now, list(results))
                            self._web_cache = cache
                        return results
            except Exception as exc:
                results.append({
                    "title": "Web search failed",
                    "url": "",
                    "snippet": str(exc),
                    "source_type": "error",
                    "confidence": "none",
                })
                if ttl > 0:
                    cache[cache_key] = (now, list(results))
                    self._web_cache = cache
                return results
        if ttl > 0:
            cache[cache_key] = (now, list(results))
            self._web_cache = cache
        return results

    # Import and indexing internals
    def _index_document(self, title, text, source_path, source_url, source_type, extra_tags):
        normalized_text = self._normalize_document_text(text)
        content_hash = hashlib.sha256(normalized_text.encode("utf-8")).hexdigest()
        text_path = os.path.join(self.documents_dir, f"{content_hash}.txt")
        with open(text_path, "w", encoding="utf-8") as f:
            f.write(normalized_text)

        entries = self._build_entries(
            title=title,
            text=normalized_text,
            source_path=source_path,
            source_url=source_url,
            source_type=source_type,
            content_hash=content_hash,
            extra_tags=extra_tags,
        )
        self._upsert_entries(entries)
        return entries

    def _build_entries(self, title, text, source_path, source_url, source_type, content_hash, extra_tags):
        modules = self._rank_candidates(text, self.MODULE_RULES, default="General")
        cycles = self._rank_candidates(text, self.CYCLE_RULES, default="General")
        tcodes = self._extract_tcodes(text)
        summary = self._summarize(text)
        tags = self._dedupe([
            *(extra_tags or []),
            *modules[:3],
            *cycles[:3],
            *tcodes[:8],
            *self._keyword_tags(text),
        ])

        nodes = self._candidate_nodes(modules, cycles)
        if not nodes:
            nodes = [("General", "General")]

        entries = []
        for module, cycle in nodes:
            confidence = self._confidence(source_type, module, cycle, tcodes, text)
            entry_id = f"{content_hash[:12]}-{self._slug(module)}-{self._slug(cycle)}"
            entries.append({
                "id": entry_id,
                "module": module,
                "business_cycle": cycle,
                "document_title": self._clean_title(title) or content_hash[:12],
                "source_path": source_path,
                "source_url": source_url,
                "source_type": source_type,
                "tags": tags,
                "tcode": tcodes,
                "confidence": confidence,
                "content_hash": content_hash,
                "text_path": os.path.relpath(
                    os.path.join(self.documents_dir, f"{content_hash}.txt"),
                    self.root_dir,
                ),
                "summary": summary,
                "imported_at": self._now(),
                "updated_at": self._now(),
            })
        return entries

    def _upsert_entries(self, entries):
        existing = self.index.get("documents", [])
        incoming_ids = {entry["id"] for entry in entries}
        incoming_keys = {
            (entry.get("content_hash"), entry.get("module"), entry.get("business_cycle"))
            for entry in entries
        }
        kept = [
            entry for entry in existing
            if entry.get("id") not in incoming_ids
            and (entry.get("content_hash"), entry.get("module"), entry.get("business_cycle")) not in incoming_keys
        ]
        self.index["documents"] = kept + entries
        self.index["updated_at"] = self._now()

    def _write_draft(self, entry):
        content_hash = entry.get("content_hash", "")
        text_path = os.path.join(self.root_dir, entry.get("text_path", ""))
        try:
            source_text = Path(text_path).read_text(encoding="utf-8")
        except OSError:
            source_text = ""

        module = entry.get("module") or "General"
        cycle = entry.get("business_cycle") or "General"
        title = entry.get("document_title") or content_hash[:12]
        draft_dir = os.path.join(self.drafts_dir, self._safe_filename(module), self._safe_filename(cycle))
        os.makedirs(draft_dir, exist_ok=True)
        draft_path = os.path.join(draft_dir, f"{self._safe_filename(title)}.md")

        steps, has_verified_steps = self._extract_process_steps(source_text, entry)
        user_data = self._extract_user_data(source_text)
        risks = self._extract_risks(source_text)
        source_ref = entry.get("source_url") or entry.get("source_path") or entry.get("content_hash")

        lines = [
            f"# SOP: {title}",
            "",
            "## Metadata",
            f"- module: {module}",
            f"- business_cycle: {cycle}",
            f"- document_title: {title}",
            f"- source_type: {entry.get('source_type', '')}",
            f"- source: {source_ref}",
            f"- confidence: {entry.get('confidence', '')}",
            f"- content_hash: {content_hash}",
            f"- tcode: {', '.join(entry.get('tcode', []) or []) or '待確認'}",
            f"- Tags: {', '.join(entry.get('tags', []) or [])}",
            "",
            "## 目的",
            self._purpose_from_entry(entry),
            "",
            "## 適用情境",
            f"- 使用者目標與 `{module} > {cycle}` 相關，且來源文件標題為「{title}」。",
            "- 這是蒸餾草稿；正式教學前仍需要依實際 SAP 畫面驗證。",
            "",
            "## 前提條件",
            "- 使用者已登入正確 SAP 系統與 client。",
            "- 使用者具備此流程所需權限。",
            "- 若文件未提供必要欄位，Study Mode 必須先請使用者確認，不可編造。",
            "",
            "## T-Code",
            ", ".join(entry.get("tcode", []) or ["待確認"]),
            "",
            "## 操作步驟",
            *steps,
            "",
            "## 使用者需確認資料",
            *(user_data or ["- 待確認：文件沒有明確列出本次需輸入的業務資料。"]),
            "",
            "## 風險",
            *(risks or ["- 未讀到明確寫入、過帳、刪除或啟用風險；仍需依實際畫面確認。"]),
            "",
            "## 來源摘要",
            entry.get("summary", "") or "- 無摘要。",
            "",
            "## 信心與未驗證項目",
            f"- 信心: {entry.get('confidence', '')}",
        ]
        if not has_verified_steps:
            lines.append("- 待確認：來源沒有清楚的逐步操作，以下步驟是保守骨架，不是正式 SOP。")
        if not entry.get("tcode"):
            lines.append("- 待確認：來源沒有明確 T-Code。")
        lines.extend([
            "- 待確認：實際欄位 ID、彈窗、公司客製欄位需由 SAP 畫面驗證。",
            "",
            "## Structured Rationale",
            f"- 依據來源: {source_ref}",
            f"- 階層: {module} > {cycle} > {title}",
            f"- 下一個安全引導步驟: 先請使用者確認候選 T-Code / 流程方向，再高亮 SAP 欄位或按鈕。",
            "",
            f"建立時間: {self._now()}",
        ])

        with open(draft_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines).rstrip() + "\n")

        return {
            "path": draft_path,
            "relative_path": os.path.relpath(draft_path, self.root_dir),
            "module": module,
            "business_cycle": cycle,
            "document_title": title,
            "confidence": entry.get("confidence", ""),
        }

    # Formatting helpers
    def _format_formal_matches(self, matches):
        if not matches:
            return "- 無正式 skill / recording 命中。"
        lines = []
        for match in matches:
            lines.append(
                f"- {match.get('name')} | source={match.get('source')} | score={match.get('score')} | {match.get('summary', '')[:120]}"
            )
        return "\n".join(lines)

    def _format_evidence_entries(self, entries):
        if not entries:
            return "- 無命中。"
        lines = []
        for entry in entries[:8]:
            source = entry.get("source_url") or entry.get("source_path") or entry.get("relative_path") or ""
            lines.append(
                "- "
                f"{entry.get('document_title') or entry.get('title')} | "
                f"{entry.get('module')} > {entry.get('business_cycle')} | "
                f"source={entry.get('source_type', '')} | "
                f"confidence={entry.get('confidence', '')} | "
                f"score={entry.get('score', '-')}"
            )
            if source:
                lines.append(f"  - source: {source}")
            summary = entry.get("summary", "")
            if summary:
                lines.append(f"  - summary: {summary[:220]}")
        return "\n".join(lines)

    def _format_web_matches(self, matches):
        if not matches:
            return "- 無網搜結果。"
        lines = []
        for match in matches:
            lines.append(
                f"- {match.get('title')} | {match.get('source_type')} | confidence={match.get('confidence')}"
            )
            if match.get("url"):
                lines.append(f"  - url: {match.get('url')}")
            if match.get("snippet"):
                lines.append(f"  - snippet: {match.get('snippet')[:220]}")
        return "\n".join(lines)

    def _screen_brief(self, screen_state):
        if not screen_state:
            return "- 無法取得目前 SAP 畫面。"
        lines = [
            f"- T-Code: {screen_state.get('tcode') or 'N/A'}",
            f"- Title: {screen_state.get('title') or 'N/A'}",
            f"- Screen: {screen_state.get('screen_number') or 'N/A'}",
        ]
        status = screen_state.get("status_bar") or {}
        if status.get("text"):
            lines.append(f"- Status: [{status.get('type', '')}] {status.get('text', '')}")
        popup = screen_state.get("active_popup") or {}
        if popup:
            lines.append(f"- Popup: {popup.get('title', '')} ({popup.get('id', '')})")
        fields = screen_state.get("fields", [])[:10]
        if fields:
            lines.append("- Fields:")
            for field in fields:
                label = field.get("label") or field.get("name") or field.get("tooltip") or field.get("id", "")
                value = field.get("value", "")
                suffix = f" = {value}" if value else ""
                lines.append(f"  - {label}{suffix} ({field.get('id', '')})")
        return "\n".join(lines)

    # Matching helpers
    def _match_formal_skills(self, goal, skill_library):
        if not skill_library:
            return []
        try:
            skills = skill_library.list_skills()
        except Exception:
            return []
        terms = self._terms(goal)
        scored = []
        for skill in skills:
            text = f"{skill.get('name', '')}\n{skill.get('summary', '')}\n{skill.get('source', '')}"
            score = self._score_text(terms, text)
            if score <= 0:
                continue
            item = dict(skill)
            item["score"] = score
            scored.append(item)
        scored.sort(key=lambda item: item.get("score", 0), reverse=True)
        return scored[:5]

    def _match_drafts(self, goal):
        terms = self._terms(goal)
        scored = []
        for draft in self.list_drafts():
            score = self._score_text(terms, draft.get("search_text", ""))
            if score <= 0:
                continue
            item = {
                "document_title": draft.get("title", ""),
                "module": draft.get("module", ""),
                "business_cycle": draft.get("business_cycle", ""),
                "source_path": draft.get("path", ""),
                "source_type": "draft_skill",
                "confidence": "draft",
                "summary": draft.get("summary", ""),
                "score": score,
            }
            scored.append(item)
        scored.sort(key=lambda item: item.get("score", 0), reverse=True)
        return scored[:5]

    def _find_draft(self, draft_query):
        query = str(draft_query or "").strip().strip('"')
        if not query:
            return None
        path = Path(query)
        if not path.is_absolute():
            path = Path(self.root_dir) / query
        if path.exists() and path.suffix.lower() == ".md":
            for draft in self.list_drafts():
                if os.path.abspath(draft["path"]) == os.path.abspath(str(path)):
                    return draft

        normalized = self._lookup_key(query)
        candidates = []
        for draft in self.list_drafts():
            haystack = " ".join([
                draft.get("relative_path", ""),
                draft.get("title", ""),
                draft.get("module", ""),
                draft.get("business_cycle", ""),
            ])
            key = self._lookup_key(haystack)
            if normalized and normalized in key:
                candidates.append((100 + len(normalized), draft))
                continue
            score = self._score_text(self._terms(query), haystack)
            if score:
                candidates.append((score, draft))
        if not candidates:
            return None
        candidates.sort(key=lambda item: item[0], reverse=True)
        return candidates[0][1]

    # Document readers
    def _iter_document_paths(self, path):
        supported = {".md", ".txt", ".html", ".htm", ".json", ".pdf", ".docx"}
        if os.path.isfile(path):
            return [path]
        result = []
        skip_dir_names = {".git", ".venv", "__pycache__", ".uv-cache", "external"}
        for root, dirs, files in os.walk(path):
            dirs[:] = [item for item in dirs if item not in skip_dir_names]
            rel_parts = Path(os.path.relpath(root, path)).parts
            if "knowledge" in rel_parts and "documents" in rel_parts:
                dirs[:] = []
                continue
            if len(rel_parts) >= 2 and rel_parts[-2:] == ("skills", "_drafts"):
                dirs[:] = []
                continue
            for filename in files:
                if Path(filename).suffix.lower() in supported:
                    result.append(os.path.join(root, filename))
        return sorted(result)

    def _read_file(self, path):
        suffix = Path(path).suffix.lower()
        title = Path(path).stem
        if suffix in {".md", ".txt"}:
            return title, Path(path).read_text(encoding="utf-8", errors="replace")
        if suffix in {".html", ".htm"}:
            raw = Path(path).read_text(encoding="utf-8", errors="replace")
            parser = _HTMLTextParser()
            parser.feed(raw)
            return parser.title or title, parser.text()
        if suffix == ".json":
            data = json.loads(Path(path).read_text(encoding="utf-8", errors="replace"))
            return title, json.dumps(data, ensure_ascii=False, indent=2)
        if suffix == ".docx":
            return title, self._read_docx_text(path)
        if suffix == ".pdf":
            return title, self._read_pdf_text(path)
        return title, ""

    def _read_url(self, url):
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": "SAP_Copilot/0.10 StudyKnowledge (+local)",
                "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.8",
            },
        )
        timeout = float(os.getenv("STUDY_WEB_SEARCH_TIMEOUT_SECONDS", "8") or 8)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(2_000_000)
            content_type = resp.headers.get("Content-Type", "")
        encoding = "utf-8"
        match = re.search(r"charset=([^;\s]+)", content_type, re.I)
        if match:
            encoding = match.group(1)
        decoded = raw.decode(encoding, errors="replace")
        if "html" in content_type.lower() or "<html" in decoded[:500].lower():
            parser = _HTMLTextParser()
            parser.feed(decoded)
            return parser.title or url, parser.text()
        return url, decoded

    def _read_docx_text(self, path):
        parts = []
        with zipfile.ZipFile(path) as zf:
            for name in ("word/document.xml", "word/footnotes.xml", "word/endnotes.xml"):
                if name not in zf.namelist():
                    continue
                xml = zf.read(name).decode("utf-8", errors="replace")
                xml = re.sub(r"</w:p>", "\n", xml)
                text = re.sub(r"<[^>]+>", "", xml)
                parts.append(html.unescape(text))
        return "\n".join(parts)

    def _read_pdf_text(self, path):
        try:
            from pypdf import PdfReader
        except Exception:
            return "PDF text extraction unavailable: install pypdf to import this PDF."
        reader = PdfReader(path)
        parts = []
        for page in reader.pages[:200]:
            try:
                parts.append(page.extract_text() or "")
            except Exception:
                continue
        return "\n".join(parts)

    # Extraction helpers
    def _rank_candidates(self, text, rules, default):
        lower = str(text or "").lower()
        scored = []
        for name, terms in rules.items():
            score = 0
            for term in terms:
                term_lower = term.lower()
                if not term_lower:
                    continue
                score += lower.count(term_lower)
            if score:
                scored.append((score, name))
        if not scored:
            return [default]
        scored.sort(reverse=True)
        return [name for _score, name in scored[:3]]

    def _candidate_nodes(self, modules, cycles):
        nodes = []
        for module in modules[:3]:
            for cycle in cycles[:3]:
                if cycle == "ABAP Development" and module not in {"ABAP", "General"}:
                    continue
                if cycle == "Basis Administration" and module not in {"Basis", "General"}:
                    continue
                if cycle in {"Material Master", "Inventory", "Procure-to-Pay"} and module not in {"MM", "FI", "General"}:
                    continue
                if cycle in {"Billing", "Order-to-Cash"} and module not in {"SD", "FI", "General"}:
                    continue
                nodes.append((module, cycle))
        return nodes[:4]

    def _extract_tcodes(self, text):
        raw = re.findall(r"(?<![A-Z0-9])(?:/N)?[A-Z]{2,5}\d{1,3}[A-Z]?(?![A-Z0-9])", str(text or "").upper())
        result = []
        for item in raw:
            item = item[2:] if item.startswith("/N") else item
            if item not in result:
                result.append(item)
        return result[:20]

    def _keyword_tags(self, text):
        tags = []
        haystack = str(text or "").lower()
        for tag, aliases in {
            "查詢": ["查詢", "查看", "顯示", "display", "show"],
            "建立": ["建立", "新增", "create"],
            "修改": ["修改", "變更", "change"],
            "主檔": ["主檔", "master data"],
            "報表": ["報表", "report"],
        }.items():
            if any(alias.lower() in haystack for alias in aliases):
                tags.append(tag)
        return tags

    def _confidence(self, source_type, module, cycle, tcodes, text):
        score = 0
        source_type = str(source_type or "").lower()
        if source_type in {"official", "sap_official"}:
            score += 35
        elif source_type in {"company", "company_sop", "local_document"}:
            score += 25
        elif source_type in {"community"}:
            score += 15
        if module and module != "General":
            score += 20
        if cycle and cycle != "General":
            score += 20
        if tcodes:
            score += 15
        if len(str(text or "")) > 800:
            score += 10
        if score >= 75:
            return "high"
        if score >= 45:
            return "medium"
        return "low"

    def _extract_process_steps(self, text, entry):
        lines = []
        for raw in str(text or "").splitlines():
            line = raw.strip()
            if not line:
                continue
            if re.match(r"^(\d+[\.)、]|[-*]\s+)", line):
                cleaned = re.sub(r"^(\d+[\.)、]|[-*]\s+)", "", line).strip()
                if 6 <= len(cleaned) <= 220:
                    lines.append(cleaned)
            if len(lines) >= 15:
                break
        if lines:
            return [f"{idx}. {line}" for idx, line in enumerate(lines, 1)], True

        tcode = ", ".join(entry.get("tcode", []) or ["待確認"])
        return [
            "1. 先確認目前 SAP 系統、client 與權限是否符合此流程。",
            f"2. 請使用者確認候選 T-Code / 流程方向：{tcode}。",
            "3. 依目前畫面欄位與來源摘要，引導使用者輸入本次必要條件；欄位值若已有預設值先確認是否沿用。",
            "4. 進入結果或明細畫面後，請使用者確認畫面標題、狀態列與關鍵資料是否符合目的。",
        ], False

    def _extract_user_data(self, text):
        targets = []
        for label in [
            "公司代碼", "日期", "物料", "料號", "供應商", "客戶", "付款人",
            "銷售組織", "採購組織", "工廠", "文件號碼", "會計年度",
        ]:
            if label in str(text or ""):
                targets.append(f"- {label}: 依本次查詢或操作需求由使用者確認。")
        return targets[:12]

    def _extract_risks(self, text):
        risks = []
        lower = str(text or "").lower()
        if any(term in lower for term in ["post", "posting", "過帳"]):
            risks.append("- 可能涉及過帳，需確認測試/正式環境與資料正確性。")
        if any(term in lower for term in ["delete", "刪除"]):
            risks.append("- 可能涉及刪除，Study Mode 不應自動執行。")
        if any(term in lower for term in ["save", "儲存", "release", "釋放", "activate", "啟用"]):
            risks.append("- 可能涉及保存、釋放或啟用，需使用者明確確認。")
        if any(term in lower for term in ["change", "modify", "修改", "變更"]):
            risks.append("- 可能修改主檔或交易資料，需確認授權與影響範圍。")
        return risks

    def _purpose_from_entry(self, entry):
        tcode = ", ".join(entry.get("tcode", []) or [])
        suffix = f" 相關 T-Code: {tcode}。" if tcode else ""
        return (
            f"依來源文件蒸餾出 `{entry.get('module')} > {entry.get('business_cycle')}` "
            f"下的 Study Mode 教學草稿。{suffix}"
        )

    # Web search internals
    def _duckduckgo_html_search(self, query, timeout):
        params = urllib.parse.urlencode({"q": query})
        url = f"https://duckduckgo.com/html/?{params}"
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 SAP_Copilot"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html_text = resp.read(1_000_000).decode("utf-8", errors="replace")

        results = []
        blocks = re.findall(r'<a[^>]+class="result__a"[^>]+href="([^"]+)"[^>]*>(.*?)</a>', html_text, re.I | re.S)
        snippets = re.findall(r'<a[^>]+class="result__snippet"[^>]*>(.*?)</a>|<div[^>]+class="result__snippet"[^>]*>(.*?)</div>', html_text, re.I | re.S)
        flat_snippets = [self._strip_html(a or b) for a, b in snippets]
        for idx, (href, title_html) in enumerate(blocks):
            real_url = self._decode_duckduckgo_url(html.unescape(href))
            results.append({
                "title": self._strip_html(title_html),
                "url": real_url,
                "snippet": flat_snippets[idx] if idx < len(flat_snippets) else "",
            })
        return results

    # Scoring and text utilities
    def _score_entry(self, query, query_terms, entry):
        text = "\n".join([
            entry.get("document_title", ""),
            entry.get("summary", ""),
            entry.get("module", ""),
            entry.get("business_cycle", ""),
            " ".join(entry.get("tags", []) or []),
            " ".join(entry.get("tcode", []) or []),
            entry.get("source_path", ""),
            entry.get("source_url", ""),
        ])
        score = self._score_text(query_terms, text)
        if self._lookup_key(query) == self._lookup_key(entry.get("document_title", "")):
            score += 80
        if entry.get("confidence") == "high":
            score += 8
        elif entry.get("confidence") == "medium":
            score += 4
        return score

    def _score_text(self, query_terms, text):
        normalized = self._lookup_key(text)
        score = 0
        for term in query_terms:
            if not term:
                continue
            if term in normalized:
                score += max(4, min(30, len(term)))
        return score

    def _terms(self, text):
        raw = str(text or "")
        terms = set()
        terms.add(self._lookup_key(raw))
        for token in re.findall(r"[A-Za-z0-9_/-]+|[\u4e00-\u9fff]{1,8}", raw):
            key = self._lookup_key(token)
            if key:
                terms.add(key)
        for tcode in self._extract_tcodes(raw):
            terms.add(self._lookup_key(tcode))
        return {term for term in terms if term}

    @staticmethod
    def _lookup_key(text):
        return "".join(ch.lower() for ch in str(text or "") if ch.isalnum())

    @staticmethod
    def _normalize_document_text(text):
        text = str(text or "").replace("\r\n", "\n").replace("\r", "\n")
        lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.splitlines()]
        text = "\n".join(line for line in lines if line)
        return re.sub(r"\n{3,}", "\n\n", text).strip()

    @staticmethod
    def _summarize(text, max_chars=520):
        text = re.sub(r"\s+", " ", str(text or "")).strip()
        return text[:max_chars].strip()

    @staticmethod
    def _split_tags(tags):
        if not tags:
            return []
        if isinstance(tags, (list, tuple, set)):
            raw = tags
        else:
            raw = re.split(r"[,，;；]", str(tags))
        result = []
        for item in raw:
            item = str(item or "").strip()
            if item:
                result.append(item)
        return SAPKnowledgeLibrary._dedupe(result)

    @staticmethod
    def _dedupe(items):
        result = []
        seen = set()
        for item in items:
            item = str(item or "").strip()
            key = item.lower()
            if not item or key in seen:
                continue
            seen.add(key)
            result.append(item)
        return result

    @staticmethod
    def _safe_filename(text):
        text = str(text or "").strip()
        safe = "".join(ch if (ch.isalnum() or ch in " _-.（）()") else "_" for ch in text)
        safe = re.sub(r"\s+", "_", safe).strip("._ ")
        return safe[:120] or "unnamed"

    @staticmethod
    def _slug(text):
        text = SAPKnowledgeLibrary._safe_filename(text).lower()
        return re.sub(r"_+", "_", text).strip("_") or "general"

    @staticmethod
    def _clean_title(title):
        title = str(title or "").strip()
        title = re.sub(r"^#+\s*", "", title)
        for prefix in ("SOP:", "SOP：", "Skill:", "Skill："):
            if title.startswith(prefix):
                title = title[len(prefix):].strip()
        return title or ""

    @staticmethod
    def _first_markdown_title(text):
        for line in str(text or "").splitlines():
            if line.startswith("#"):
                return line.lstrip("#").strip()
        return ""

    @staticmethod
    def _extract_metadata_line(text, key):
        prefixes = (f"{key}:", f"{key}：", f"- {key}:", f"- {key}：")
        for line in str(text or "").splitlines():
            stripped = line.strip()
            if stripped.startswith(prefixes):
                return stripped.split(":", 1)[-1].split("：", 1)[-1].strip()
        return ""

    @staticmethod
    def _section_preview(text, heading):
        capture = False
        parts = []
        for line in str(text or "").splitlines():
            if line.strip().startswith("## "):
                if capture:
                    break
                capture = line.strip().lstrip("#").strip() == heading
                continue
            if capture and line.strip():
                parts.append(line.strip())
                if len(" ".join(parts)) > 220:
                    break
        return " ".join(parts)[:260]

    @staticmethod
    def _strip_html(value):
        value = re.sub(r"<[^>]+>", "", str(value or ""))
        return html.unescape(re.sub(r"\s+", " ", value)).strip()

    @staticmethod
    def _decode_duckduckgo_url(url):
        parsed = urllib.parse.urlparse(url)
        if "duckduckgo.com" in parsed.netloc and parsed.query:
            params = urllib.parse.parse_qs(parsed.query)
            if "uddg" in params:
                return params["uddg"][0]
        return url

    @staticmethod
    def _normalize_url(url):
        parsed = urllib.parse.urlparse(str(url or ""))
        if not parsed.scheme or not parsed.netloc:
            return ""
        return parsed._replace(fragment="").geturl()

    @classmethod
    def _source_type_for_url(cls, url):
        host = urllib.parse.urlparse(url).netloc.lower()
        if any(domain in host for domain in ("help.sap.com", "learning.sap.com", "support.sap.com", "sap.com")):
            return "official"
        if "community.sap.com" in host:
            return "community"
        return "web"

    @staticmethod
    def _is_url(value):
        return str(value or "").lower().startswith(("http://", "https://"))

    @staticmethod
    def _env_enabled(name, default="false"):
        return str(os.getenv(name, default)).strip().lower() in {"1", "true", "yes", "y", "on"}

    @staticmethod
    def _now():
        return datetime.now().isoformat(timespec="seconds")

    def _load_index(self):
        if not os.path.exists(self.index_path):
            return {
                "version": 1,
                "created_at": self._now(),
                "updated_at": self._now(),
                "documents": [],
            }
        try:
            with open(self.index_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("knowledge index must be an object")
            data.setdefault("version", 1)
            data.setdefault("documents", [])
            return data
        except Exception:
            backup = f"{self.index_path}.broken-{int(time.time())}"
            shutil.copyfile(self.index_path, backup)
            return {
                "version": 1,
                "created_at": self._now(),
                "updated_at": self._now(),
                "documents": [],
                "recovered_from": backup,
            }

    def _save_index(self):
        os.makedirs(self.knowledge_dir, exist_ok=True)
        with open(self.index_path, "w", encoding="utf-8") as f:
            json.dump(self.index, f, ensure_ascii=False, indent=2)
