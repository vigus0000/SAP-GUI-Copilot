"""
SAP GUI Copilot floating UI.

Phase 4 adds a lightweight Tkinter shell around the existing CLI capabilities.
The UI thread only renders widgets; SAP COM and LLM calls run in a worker thread.
"""

import json
import os
import queue
import re
import threading
import time
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText

import pythoncom

import llm_brain
from copilot_auth import CopilotAuth
from llm_provider import create_llm_provider, normalize_provider_name, provider_display_name
from llm_brain import SAPAgent
from sap_agent_tools import (
    confirmed_click,
    confirmed_handle_popup,
    confirmed_send_vkey,
    inspect_study_target,
    scan_sap_screen,
    visualize_element,
)
from sap_core import SAPConnection
from sap_knowledge_library import SAPKnowledgeLibrary
from sap_monitor import SAPMonitor
from sap_recorder import SAPRecorder
from sap_skill_library import SAPSkillLibrary
from sap_macro_library import SAPMacroError, SAPMacroLibrary, macro_result_can_fallback_to_auto
from sap_table_inspector import format_table_inspection, inspect_current_tables
from mcp_client import get_default_sync_client


APP_VERSION = "0.13.1"

UI_COLORS = {
    "bg": "#f4efe7",
    "panel": "#f9f6f0",
    "surface": "#ebe4d8",
    "border": "#d2c7b7",
    "text": "#2c231a",
    "muted": "#8a7a6b",
    "accent": "#c73900",
    "user": "#0d47a1",
    "ai": "#087a39",
    "system": "#6b5947",
    "warning": "#a15c00",
    "error": "#b00020",
    "macro": "#5b2bbf",
}

MODE_LABELS = {
    "auto": "Auto",
    "ask": "Ask",
    "solve": "Solve",
    "study": "Study",
}


def env_enabled(name, default="false"):
    return str(os.getenv(name, default)).strip().lower() in {"1", "true", "yes", "y", "on"}


def parse_study_request(raw_text):
    text = str(raw_text or "").strip()
    require_draft_flag = env_enabled("STUDY_REQUIRE_DRAFT_FLAG", "false")
    allow_draft = (not require_draft_flag) or env_enabled("STUDY_ALLOW_DRAFT", "true")
    save_draft = env_enabled("STUDY_SAVE_DRAFT_SKILL", "false")

    tokens = text.split()
    cleaned = []
    for token in tokens:
        lowered = token.lower()
        if lowered in {"--draft", "/draft", "--explore", "/explore"}:
            allow_draft = True
            continue
        if lowered in {"--save-draft", "/save-draft"}:
            allow_draft = True
            save_draft = True
            continue
        cleaned.append(token)

    return " ".join(cleaned).strip(), allow_draft, save_draft


def _screen_brief(screen_state):
    if not screen_state:
        return "- 無法取得目前 SAP 畫面"

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


def _compact_study_text(value, max_lines=5, max_chars=520):
    lines = [line.strip() for line in str(value or "").splitlines() if line.strip()]
    if not lines:
        return ""
    text = "\n".join(lines[:max_lines])
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "..."
    if len(lines) > max_lines:
        text += "\n..."
    return text


def _normalize_study_choices(choices):
    if not choices:
        return []
    if isinstance(choices, str):
        raw_items = re.split(r"[,，;；\n]", choices)
    elif isinstance(choices, (list, tuple)):
        raw_items = choices
    else:
        raw_items = []
    result = []
    for item in raw_items:
        text = str(item or "").strip()
        if text:
            result.append(text)
    return result[:6]


def _build_ad_hoc_study_context(goal, screen_state, evidence_pack=""):
    return f"""# Ad-hoc Study Goal: {goal}

This is an explicit exploratory draft. No existing SOP or verified skill was found.

## Current SAP Screen
{_screen_brief(screen_state)}

## Evidence Pack
{evidence_pack or "- No evidence pack was built."}

## Coach Rules
- First tell the user this is a draft guide without a recorded SOP.
- Use evidence priority: formal skill/recording > promoted skill > draft > imported knowledge > official search > community/general search > LLM prior.
- Every candidate flow must show module, business_cycle, source, and confidence.
- Do not present SAP common knowledge guesses as verified facts.
- If the T-Code, field, business meaning, or next action is uncertain, ask the user to confirm the first key T-Code / flow direction or recommend recording a real SOP with `/record`.
- Guide the user with `guide_user_action` and `visualize_element`; do not operate SAP directly.
- If a needed value is user-specific, ask the user to enter it in SAP and confirm.
- Finish with a draft summary and list which steps still require recording or human verification.
"""


def _extract_study_guidance_steps(agent):
    steps = []
    for message in getattr(agent, "conversation_history", []):
        if message.get("role") != "assistant":
            continue
        for tool_call in message.get("tool_calls", []) or []:
            func = tool_call.get("function", {})
            if func.get("name") != "guide_user_action":
                continue
            try:
                args = json.loads(func.get("arguments", "{}"))
            except Exception:
                args = {}
            instruction = str(args.get("instruction", "")).strip()
            element_id = str(args.get("element_id", "")).strip()
            if instruction:
                suffix = f" (element: `{element_id}`)" if element_id else ""
                steps.append(f"{len(steps) + 1}. {instruction}{suffix}")
    return steps


def _build_learned_skill_markdown(name, response, guidance_steps, initial_screen):
    lines = [
        f"# SOP: {name}",
        "",
        "## Purpose",
        f"Guide the user through: {name}",
        "",
        "## Initial Screen",
        _screen_brief(initial_screen),
        "",
        "## Steps",
    ]
    if guidance_steps:
        lines.extend(guidance_steps)
    else:
        lines.append("1. Review the current SAP screen and follow the Study Mode guidance.")
    lines.extend([
        "",
        "## Coach Summary",
        response.strip() or "(No summary returned.)",
        "",
        "## Notes",
        "- This skill is a reference, not an absolute replay script.",
        "- Study Mode should adapt to the live SAP screen before guiding the user.",
    ])
    return "\n".join(lines)


class SAPCopilotWorker(threading.Thread):
    def __init__(self, outbound_queue):
        super().__init__(daemon=True)
        self.inbound_queue = queue.Queue()
        self.outbound_queue = outbound_queue
        self.stop_requested = False

        self.auth = None
        self.sap = None
        self.agent = None
        self.recorder = None
        self.monitor = None
        self.skill_library = None
        self.knowledge_library = None
        self.macro_library = None
        self.ready = False
        self.study_context = ""
        self.prompt_counter = 0
        self.prompt_lock = threading.Lock()
        self.pending_prompts = {}

    def submit(self, action, **payload):
        self.inbound_queue.put({"action": action, **payload})

    def emit(self, kind, payload):
        self.outbound_queue.put({"kind": kind, "payload": payload})

    def log(self, text):
        if self._is_dialog_log(text) or self._is_ui_status_log(text):
            self.emit("dialog_log", text)
        else:
            self.emit("terminal_log", text)

    @staticmethod
    def _is_dialog_log(text):
        stripped = str(text or "").strip()
        return stripped.startswith("You:") or stripped.startswith("AI:") or stripped.startswith("AI:\n")

    @staticmethod
    def _is_ui_status_log(text):
        stripped = str(text or "").strip()
        return (
            stripped.startswith("Mode switched")
            or (stripped.startswith("Initializing ") and "SAP connection" in stripped)
            or stripped.startswith("Connected:")
            or stripped.startswith("Connection failed:")
        )

    def emit_state(self):
        state = {
            "ready": self.ready,
            "mode": self.agent.mode if self.agent else "auto",
            "model": self.agent.model if self.agent else "",
            "provider": self.agent.provider_name if self.agent else normalize_provider_name(os.getenv("LLM_PROVIDER", "github_copilot")),
            "recording": self.recorder.recording_name if self.recorder and self.recorder.is_recording else "",
        }
        try:
            if self.sap:
                session = self.sap.get_session()
                info = self.sap.get_session_info(session)
                state.update({
                    "tcode": info.get("transaction", ""),
                    "client": info.get("client", ""),
                    "user": info.get("user", ""),
                })
        except Exception:
            pass
        self.emit("state", state)

    def run(self):
        pythoncom.CoInitialize()
        try:
            self._connect()
            while not self.stop_requested:
                try:
                    job = self.inbound_queue.get(timeout=0.2)
                except queue.Empty:
                    continue
                self._handle_job(job)
        finally:
            self._stop_monitor()
            pythoncom.CoUninitialize()

    def _connect(self, provider_name=None):
        requested_provider = normalize_provider_name(provider_name or os.getenv("LLM_PROVIDER", "github_copilot"))
        self.log(f"Initializing {provider_display_name(requested_provider)} and SAP connection...")
        self.ready = False
        try:
            self.auth = None
            if requested_provider == "github_copilot":
                self.auth = CopilotAuth()
                if not self.auth.is_logged_in():
                    self.log("GitHub Copilot is not logged in; starting device login in console...")
                    if not self.auth.login():
                        raise RuntimeError("GitHub Copilot login failed")
                self.auth.get_token()
            else:
                provider = create_llm_provider(requested_provider)
                provider.ensure_login()
            os.environ["LLM_PROVIDER"] = requested_provider

            self.sap = SAPConnection()
            session = self.sap.get_session()
            info = self.sap.get_session_info(session)

            self.agent = SAPAgent(auth=self.auth, provider_name=requested_provider)
            self.agent._handle_confirmation = self._handle_confirmation_ui
            llm_brain.TOOL_FUNCTIONS["guide_user_action"] = self._guide_user_action_ui
            self.recorder = SAPRecorder()
            self.skill_library = SAPSkillLibrary()
            self.knowledge_library = SAPKnowledgeLibrary()
            self.macro_library = SAPMacroLibrary()
            self.ready = True
            self.log(
                "Connected: "
                f"{info.get('system_name', 'SAP')} client={info.get('client', 'N/A')} "
                f"user={info.get('user', 'N/A')} tcode={info.get('transaction', 'N/A')} "
                f"provider={provider_display_name(self.agent.provider_name)} model={self.agent.model}"
            )
        except Exception as exc:
            self.log(f"Connection failed: {exc}")
        self.emit_state()

    def _ensure_ready(self):
        if not self.ready:
            raise RuntimeError("UI is not connected. Press Connect first.")

    def _session(self):
        self._ensure_ready()
        return self.sap.get_session()

    def _handle_job(self, job):
        action = job.get("action", "")
        try:
            if action == "shutdown":
                self.stop_requested = True
            elif action == "connect":
                self._connect(job.get("provider"))
            elif action == "set_mode":
                self._set_mode(job.get("mode", "auto"))
            elif action == "send":
                self._handle_text(job.get("text", ""))
            elif action == "scan":
                self._scan()
            elif action == "mcp_probe":
                self._mcp_probe()
            elif action == "inspect_table":
                self._inspect_table()
            elif action == "reset":
                self.agent.reset_conversation()
                self.log("Conversation reset.")
            elif action == "record":
                self._start_recording(job.get("name", ""))
            elif action == "stop_record":
                self._stop_recording()
            elif action == "recordings":
                self._show_recordings()
            elif action == "study":
                self._run_study(job.get("goal", ""))
            elif action == "macro":
                self._handle_macro_command(job.get("arg", ""))
            elif action == "play":
                self._play(job.get("name", ""))
            else:
                self.log(f"Unknown UI action: {action}")
        except Exception as exc:
            self.log(f"Error: {exc}")
        finally:
            self.emit_state()

    def resolve_prompt(self, prompt_id, response):
        with self.prompt_lock:
            response_queue = self.pending_prompts.pop(prompt_id, None)
        if response_queue:
            response_queue.put(response)

    def _request_prompt(self, title, message, default="", prompt_type="text"):
        with self.prompt_lock:
            self.prompt_counter += 1
            prompt_id = self.prompt_counter
            response_queue = queue.Queue(maxsize=1)
            self.pending_prompts[prompt_id] = response_queue

        self.emit("prompt", {
            "id": prompt_id,
            "type": prompt_type,
            "title": title,
            "message": message,
            "default": default,
        })
        return response_queue.get()

    def _handle_confirmation_ui(self, session, tool_result, tool_name, tool_args):
        message = (
            "SAP Copilot wants to execute a sensitive action.\n\n"
            f"Tool: {tool_name}\n"
            f"Arguments: {json.dumps(tool_args, ensure_ascii=False)}\n\n"
            "Allow this action?"
        )
        approved = bool(self._request_prompt("Confirm SAP Action", message, prompt_type="confirm"))
        if not approved:
            return {
                "success": False,
                "action": tool_name,
                "error": "User rejected sensitive action in UI.",
            }

        if tool_name == "click" and "element_id" in tool_args:
            return confirmed_click(session, tool_args["element_id"])
        if tool_name == "send_vkey":
            return confirmed_send_vkey(
                session,
                tool_args.get("vkey"),
                window_id=tool_args.get("window_id", ""),
            )
        if tool_name == "handle_popup":
            return confirmed_handle_popup(session, **tool_args)

        return {
            "success": False,
            "action": tool_name,
            "error": f"No UI confirmation handler for {tool_name}.",
        }

    def _guide_user_action_ui(
        self,
        session,
        element_id: str,
        instruction: str,
        reason: str = "",
        confidence: str = "",
        source: str = "",
        choices=None,
        expected_response_type: str = "confirm",
    ):
        target = inspect_study_target(
            session,
            element_id=element_id,
            expected_response_type=expected_response_type,
            instruction=instruction,
        )
        display_choices = _normalize_study_choices(choices)
        if not target.get("actionable"):
            self.log(f"Study target invalid: {target.get('error')}")
            return {
                "success": False,
                "action": "guide_user_action",
                "element_id": target.get("element_id", element_id),
                "element_type": target.get("element_type", ""),
                "instruction": instruction,
                "reason": reason,
                "confidence": confidence,
                "source": source,
                "choices": display_choices,
                "expected_response_type": expected_response_type,
                "target_actionable": False,
                "error": target.get("error", "Study target is not actionable."),
                "retry_advice": target.get("retry_advice", ""),
            }

        viz_result = {}
        target_id = target.get("element_id", "") or ""
        current_value = target.get("current_value", "")
        if target_id:
            try:
                viz_result = visualize_element(
                    session,
                    element_id=target_id,
                    duration_seconds=1.2,
                    set_focus=True,
                )
            except Exception as exc:
                viz_result = {"success": False, "error": str(exc)}

        display_instruction = _compact_study_text(instruction, max_lines=5, max_chars=520)
        display_reason = _compact_study_text(reason, max_lines=1, max_chars=180)

        prompt_lines = [display_instruction or instruction]
        if display_reason:
            prompt_lines.append(f"\n理由: {display_reason}")
        if source or confidence:
            prompt_lines.append(f"來源/信心: {source or '未標示來源'} / {confidence or '未標示信心'}")
        if display_choices:
            prompt_lines.append("選項:")
            for index, choice in enumerate(display_choices, 1):
                prompt_lines.append(f"{index}. {choice}")
        if target_id:
            prompt_lines.append(f"\nElement: {target_id}")
        if target.get("element_type"):
            prompt_lines.append(f"Element type: {target.get('element_type')}")
        if current_value:
            prompt_lines.append(f"Current value: {current_value}")
        if viz_result and not viz_result.get("success"):
            prompt_lines.append(f"Highlight failed: {viz_result.get('error', '')}")
        if expected_response_type == "choice":
            prompt_lines.append("\n請輸入選項編號或文字；直接 OK 代表接受建議選項。")
        elif expected_response_type == "value":
            prompt_lines.append("\n請輸入本次要使用的值；或輸入 /skip、/done。")
        else:
            prompt_lines.append("\n完成後直接按 OK；可輸入回覆內容，或輸入 /skip、/done。")

        user_response = str(self._request_prompt(
            "Study Step",
            "\n".join(prompt_lines),
            default="",
            prompt_type="text",
        ) or "").strip()
        skipped = user_response.lower() in ("/skip", "skip", "s")
        finish_requested = user_response.lower() in (
            "/done",
            "/finish",
            "/end",
            "done",
            "finish",
            "end",
            "完成",
            "結束",
        )

        return {
            "success": True,
            "action": "guide_user_action",
            "element_id": target_id,
            "element_type": target.get("element_type", ""),
            "instruction": instruction,
            "reason": reason,
            "confidence": confidence,
            "source": source,
            "choices": display_choices,
            "expected_response_type": expected_response_type,
            "target_actionable": True,
            "user_response": user_response,
            "user_confirmed": not skipped,
            "skipped": skipped,
            "finish_requested": finish_requested,
            "visualize": viz_result,
            "current_value": current_value,
            "message": "User skipped this step." if skipped else "User confirmed this study step in UI.",
        }

    def _set_mode(self, mode):
        self._ensure_ready()
        self.agent.set_mode(mode)
        if mode != "study":
            self.study_context = ""
        self.log(f"Mode switched to {MODE_LABELS.get(mode, mode)}.")

    def _handle_text(self, text):
        text = text.strip()
        if not text:
            return
        if text.startswith("/"):
            self._handle_command(text)
            return
        self._process_message(text)

    def _handle_command(self, text):
        parts = text.split(maxsplit=1)
        cmd = parts[0].lower()
        arg = parts[1].strip() if len(parts) > 1 else ""

        if cmd in ("/auto", "/ask", "/solve"):
            self._set_mode(cmd[1:])
        elif cmd == "/scan":
            self._scan()
        elif cmd == "/mcp":
            self._mcp_probe()
        elif cmd == "/inspect":
            if not arg or arg.lower() in {"table", "tables"}:
                self._inspect_table()
            else:
                self.log("Usage: /inspect table")
        elif cmd == "/reset":
            self.agent.reset_conversation()
            self.log("Conversation reset.")
        elif cmd == "/record":
            if not arg:
                self.log("Usage: /record [SOP name]")
            else:
                self._start_recording(arg)
        elif cmd == "/stop":
            self._stop_recording()
        elif cmd == "/recordings":
            self._show_recordings()
        elif cmd == "/play":
            if not arg:
                self.log("Usage: /play [SOP name]")
            else:
                self._play(arg)
        elif cmd == "/study":
            if not arg:
                self.log("Usage: /study [SOP name] | /study --draft [goal] | /study --save-draft [goal]")
            else:
                self._run_study(arg)
        elif cmd == "/macros":
            self._show_macros()
        elif cmd == "/macro":
            self._handle_macro_command(arg)
        elif cmd == "/knowledge":
            self._handle_knowledge_command(arg)
        elif cmd == "/skills":
            self._handle_skills_command(arg)
        elif cmd == "/connect":
            self._connect(arg or None)
        else:
            self.log("Available commands: /scan, /inspect table, /mcp, /record, /stop, /recordings, /play, /study, /macro, /knowledge, /skills, /ask, /solve, /auto, /connect, /reset")

    def _parse_inline_options(self, text):
        options = {}
        remaining = str(text or "").strip()
        for name in ("--type", "--tags"):
            pattern = re.compile(rf"\s{name}\s+([^\s]+)", re.I)
            match = pattern.search(f" {remaining}")
            if match:
                options[name.lstrip("-").replace("-", "_")] = match.group(1).strip().strip('"')
                remaining = pattern.sub(" ", f" {remaining}", count=1).strip()
        return remaining.strip().strip('"'), options

    def _knowledge_usage(self):
        return "\n".join([
            "Usage:",
            "/knowledge import <path-or-url> [--type official|company|community] [--tags tag1,tag2]",
            "/knowledge search <query>",
            "/knowledge distill <path-or-query>",
            "/knowledge rebuild",
            "/skills drafts",
            "/skills promote <draft-relative-path-or-title>",
        ])

    def _format_knowledge_entries(self, entries):
        if not entries:
            return "No results."
        lines = []
        for index, item in enumerate(entries, 1):
            source = item.get("source_url") or item.get("source_path") or item.get("relative_path") or item.get("path") or ""
            lines.append(
                f"{index}. {item.get('document_title') or item.get('title')} "
                f"[{item.get('module', '')} > {item.get('business_cycle', '')}] "
                f"confidence={item.get('confidence', '')} score={item.get('score', '-')}"
            )
            if item.get("tcode"):
                lines.append(f"   T-Code: {', '.join(item.get('tcode', []))}")
            if source:
                lines.append(f"   {source}")
            if item.get("summary"):
                lines.append(f"   {item.get('summary', '')[:180]}")
        return "\n".join(lines)

    def _handle_knowledge_command(self, arg):
        self._ensure_ready()
        parts = str(arg or "").strip().split(maxsplit=1)
        if not parts:
            self.log(self._knowledge_usage())
            return
        subcommand = parts[0].lower()
        rest = parts[1].strip() if len(parts) > 1 else ""
        try:
            if subcommand == "import":
                source, options = self._parse_inline_options(rest)
                if not source:
                    self.log(self._knowledge_usage())
                    return
                entries = self.knowledge_library.import_source(
                    source,
                    source_type=options.get("type", ""),
                    tags=options.get("tags", ""),
                )
                self.log(f"Imported {len(entries)} hierarchy nodes:\n{self._format_knowledge_entries(entries[:8])}")
            elif subcommand == "search":
                if not rest:
                    self.log(self._knowledge_usage())
                    return
                self.log("Knowledge search:\n" + self._format_knowledge_entries(
                    self.knowledge_library.search(rest, include_drafts=True)
                ))
            elif subcommand == "distill":
                if not rest:
                    self.log(self._knowledge_usage())
                    return
                drafts = self.knowledge_library.distill(rest)
                lines = [f"Created {len(drafts)} draft skill(s):"]
                for item in drafts:
                    lines.append(
                        f"- {item.get('document_title')} "
                        f"[{item.get('module')} > {item.get('business_cycle')}] "
                        f"{item.get('relative_path')}"
                    )
                self.log("\n".join(lines))
            elif subcommand == "rebuild":
                self.log(f"Knowledge index rebuilt: {self.knowledge_library.rebuild()}")
            else:
                self.log(self._knowledge_usage())
        except Exception as exc:
            self.log(f"Knowledge command failed: {exc}")

    def _handle_skills_command(self, arg):
        self._ensure_ready()
        parts = str(arg or "").strip().split(maxsplit=1)
        if not parts:
            self.log(self._knowledge_usage())
            return
        subcommand = parts[0].lower()
        rest = parts[1].strip() if len(parts) > 1 else ""
        try:
            if subcommand == "drafts":
                drafts = self.knowledge_library.list_drafts()
                if not drafts:
                    self.log("No draft skill found.")
                    return
                lines = ["Draft skills:"]
                for index, draft in enumerate(drafts, 1):
                    lines.append(
                        f"{index}. {draft.get('title')} "
                        f"[{draft.get('module')} > {draft.get('business_cycle')}] "
                        f"{draft.get('relative_path')}"
                    )
                self.log("\n".join(lines))
            elif subcommand == "promote":
                if not rest:
                    self.log(self._knowledge_usage())
                    return
                path = self.knowledge_library.promote_draft(rest)
                self.skill_library.rebuild_index()
                self.log(f"Draft promoted to formal skill: {path}")
            else:
                self.log(self._knowledge_usage())
        except Exception as exc:
            self.log(f"Skills command failed: {exc}")

    def _extra_context_for_current_mode(self):
        if self.agent.mode == "study":
            return self.study_context
        if self.agent.mode not in ("ask", "solve"):
            return ""
        try:
            recordings = self.skill_library.list_recordings()
            if not recordings:
                return ""
            return self.skill_library.get_recording_summary(recordings[-1]["name"])
        except Exception:
            return ""

    def _process_message(self, text):
        self._ensure_ready()
        self.log(f"You: {text}")
        if self._run_matched_macro(text):
            return
        session = self._session()
        response = self.agent.process_message(
            session,
            text,
            extra_context=self._extra_context_for_current_mode(),
        )
        self.log(f"AI:\n{response}")

    def _scan(self):
        screen = scan_sap_screen(self._session())
        self.log("Screen scan:\n" + self._format_screen(screen))

    def _inspect_table(self):
        session = self._session()
        report = inspect_current_tables(
            session=session,
            max_depth=10,
            max_rows=10,
            include_rows=False,
            use_focus=True,
        )
        self.log(format_table_inspection(report))

    def _mcp_probe(self):
        client = get_default_sync_client()
        try:
            report = client.probe(attach=True)
        except Exception as exc:
            report = client.launch_summary()
            report["error"] = str(exc)

        command = report.get("command", "")
        args = report.get("args") or []
        command_line = " ".join([command] + [str(arg) for arg in args]).strip()
        lines = [
            "MCP SAP GUI diagnostics:",
            f"Enabled: {report.get('enabled')}",
            f"Mode: {report.get('mode') or '-'}",
            f"Command: {command_line or '-'}",
            f"CWD: {report.get('cwd') or '-'}",
            f"UV_CACHE_DIR: {report.get('uv_cache_dir') or '-'}",
            f"Prerequisites: {'OK' if report.get('available') else 'FAILED'}",
            f"Initialize: {'OK' if report.get('initialized') else 'FAILED'}",
        ]
        attach = report.get("attach") or {}
        if attach:
            attach_text = "OK" if attach.get("attached") else attach.get("reason", "not attached")
            if attach.get("tool"):
                attach_text = f"{attach_text} via {attach.get('tool')}"
            lines.append(f"SAP Attach: {attach_text}")
        tools = report.get("tools") or []
        lines.append(f"Tool Count: {report.get('tool_count', len(tools))}")
        if tools:
            suffix = " ..." if len(tools) > 12 else ""
            lines.append("Tools: " + ", ".join(tools[:12]) + suffix)
        error = report.get("error") or report.get("last_error") or ""
        if error:
            lines.append("Error:")
            lines.append(error)
        stderr_tail = report.get("stderr_tail") or ""
        if stderr_tail:
            lines.append("Server stderr tail:")
            lines.append(stderr_tail[-3000:])
        self.log("\n".join(lines))

    def _format_screen(self, screen):
        lines = [
            f"T-Code: {screen.get('tcode', 'N/A')}",
            f"Title: {screen.get('title', 'N/A')}",
            f"Screen: {screen.get('screen_number', 'N/A')}",
        ]
        status = screen.get("status_bar") or {}
        if status.get("text"):
            lines.append(f"Status: [{status.get('type', '')}] {status.get('text', '')}")
        popup = screen.get("active_popup") or {}
        if popup:
            lines.append(f"Popup: {popup.get('title', '')} ({popup.get('id', '')})")
            for msg in popup.get("messages", [])[:6]:
                lines.append(f"  - {msg.get('text', '')}")
        fields = screen.get("fields", [])[:12]
        if fields:
            lines.append("Fields:")
            for field in fields:
                label = field.get("label") or field.get("name") or field.get("tooltip") or field.get("id", "")
                value = field.get("value", "")
                lines.append(f"  - {label}: {value} ({field.get('id', '')})")
        return "\n".join(lines)

    def _start_recording(self, name):
        self._ensure_ready()
        if self.recorder.is_recording:
            self.log(f"Already recording: {self.recorder.recording_name}")
            return
        session = self._session()
        self.recorder.start_recording(name)
        self.monitor = SAPMonitor(session, poll_interval=0.3)

        def on_event(event):
            self.recorder.add_event(event)
            event_type = event.get("event_type", "UNKNOWN")
            details = event.get("details", {})
            self.log(f"REC {event_type}: {json.dumps(details, ensure_ascii=False)[:180]}")

        self.monitor.on_event = on_event
        self.monitor.start()
        self.log(f"Recording started: {name}")

    def _stop_monitor(self):
        if self.monitor and self.monitor.is_running:
            self.monitor.stop()
        self.monitor = None

    def _stop_recording(self):
        self._ensure_ready()
        if not self.recorder.is_recording:
            self.log("No active recording.")
            return

        name = self.recorder.recording_name
        self._stop_monitor()
        filepath = self.recorder.stop_recording()
        self.log(f"Recording saved: {filepath}")

        try:
            rec_data = self.recorder.load_recording(name)
            screen_state = None
            try:
                screen_state = scan_sap_screen(self._session())
            except Exception:
                pass
            sop_path = self.recorder.generate_sop_with_llm(
                name,
                rec_data.get("events", []),
                self.auth,
                screen_state=screen_state,
                provider=self.agent.provider,
            )
            if sop_path:
                self.log(f"AI SOP saved: {sop_path}")
        except Exception as exc:
            self.log(f"AI SOP generation skipped: {exc}")

    def _show_recordings(self):
        self._ensure_ready()
        recordings = self.skill_library.list_recordings()
        if not recordings:
            self.log("No SOP / skill found.")
            return
        lines = ["Available SOP / Skill:"]
        for index, item in enumerate(recordings, 1):
            summary = item.get("summary", "")
            suffix = f" - {summary}" if summary else ""
            lines.append(f"{index}. {item.get('name', '')} [{item.get('source', '')}]{suffix}")
        self.log("\n".join(lines))

    def _play(self, name):
        self._ensure_ready()
        self.log(self.skill_library.get_recording_summary(name))

    def _show_macros(self):
        self._ensure_ready()
        macros = self.macro_library.list_macros()
        if not macros:
            self.log("No macro found. Put structured Markdown macro files under macros/.")
            return
        lines = ["Macros:"]
        for index, item in enumerate(macros, 1):
            tags = ", ".join(item.get("tags") or [])
            tag_text = f" | {tags}" if tags else ""
            lines.append(
                f"{index}. {item.get('name')} [{item.get('mode')}] "
                f"inputs={item.get('input_count')} steps={item.get('step_count')}{tag_text}"
            )
            if item.get("description"):
                lines.append(f"   {item.get('description')}")
            lines.append(f"   {item.get('filepath')}")
        self.log("\n".join(lines))

    def _prompt_macro_input(self, item):
        message = f"Macro input: {item.name}"
        if item.label:
            message += f"\n{item.label}"
        if item.description:
            message += f"\n{item.description}"
        value = self._request_prompt(
            "Macro Input",
            message,
            default=item.default or "",
            prompt_type="text",
        )
        return str(value or item.default or "").strip()

    def _macro_values(self, macro, values):
        return self.macro_library.prompt_missing_inputs(
            macro,
            values,
            prompt_callback=self._prompt_macro_input,
        )

    def _run_macro(self, arg):
        self._ensure_ready()
        name, values = self.macro_library.parse_values(arg)
        if not name:
            self.log("Usage: /macro run <macro-name> key=value ...")
            return
        macro = self.macro_library.load_macro(name)
        if macro.mode == "study":
            raise SAPMacroError("This macro is mode=study. Use /macro study or /study --macro instead of /macro run.")
        runtime_values = self._macro_values(macro, values)
        self.log(f"Macro Run: {macro.name}")
        result = self.macro_library.execute_macro(
            self._session(),
            self.agent,
            macro,
            runtime_values,
            log_callback=lambda text: self.log(f"  {text}"),
        )
        result["runtime_values"] = runtime_values
        if result.get("success"):
            self.log(f"Macro finished: {macro.name}")
        else:
            self.log(f"Macro finished with failed steps: {macro.name}")
        return result

    @staticmethod
    def _macro_post_verify_prompt(user_goal, macro, runtime_values, result):
        summary = {
            "macro": macro.name,
            "runtime_values": runtime_values,
            "macro_success": bool(result.get("success")),
            "failed_step": result.get("failed_step"),
            "step_count": result.get("step_count"),
        }
        return (
            "Macro strict flow has just finished. Before declaring the task complete, "
            "perform one Auto ReAct verification against the current live SAP screen.\n\n"
            "Rules:\n"
            "1. Treat the current SAP screen as the source of truth.\n"
            "2. Original user goal must be satisfied, not only the macro End condition.\n"
            "3. If the screen is still a selection/input screen and the user asked to query/list/display results, "
            "execute the safe next action such as Enter/F8/Execute when appropriate.\n"
            "4. Do not restart the same T-Code or re-fill fields that already match unless the screen clearly shows incorrect values.\n"
            "5. If the goal is already satisfied, answer briefly with the visible result/state.\n"
            "6. If more information is needed, ask one concise follow-up question.\n\n"
            f"Original user goal:\n{user_goal}\n\n"
            f"Macro summary:\n{json.dumps(summary, ensure_ascii=False)}"
        )

    def _run_macro_post_react_check(self, text, macro, runtime_values, result):
        if not env_enabled("SAP_MACRO_POST_REACT_VERIFY", "true"):
            return ""
        self.log("Macro post-check: running Auto ReAct verification before final completion.")
        response = self.agent.process_message(
            self._session(),
            self._macro_post_verify_prompt(text, macro, runtime_values, result),
        )
        self.log(f"AI:\n{response}")
        return response

    def _run_matched_macro(self, text):
        if self.agent.mode != "auto":
            return False
        match = self.macro_library.match_request(text)
        if not match:
            return False
        macro = match["macro"]
        reasons = ", ".join(match.get("reasons") or [])
        self.log(f"Macro matched: {macro.name} (score={match.get('score')}, reasons={reasons})")
        self.log("Using Macro strict flow directly; skipping Auto ReAct iterations.")
        if macro.mode == "study":
            raise SAPMacroError("Matched macro is mode=study. Use /macro study or /study --macro.")
        runtime_values = self._macro_values(macro, match.get("values") or {})
        result = self.macro_library.execute_macro(
            self._session(),
            self.agent,
            macro,
            runtime_values,
            log_callback=lambda line: self.log(f"  {line}"),
        )
        result["runtime_values"] = runtime_values
        if result.get("success"):
            self.log(f"Macro strict flow finished: {macro.name}")
            self._run_macro_post_react_check(text, macro, runtime_values, result)
        else:
            self.log(f"Macro finished with failed steps: {macro.name}")
            if macro_result_can_fallback_to_auto(result):
                self.log("Macro failed before field/button mutation; falling back to Auto ReAct.")
                return False
        return True

    def _run_macro_study(self, arg):
        self._ensure_ready()
        name, values = self.macro_library.parse_values(arg)
        if not name:
            self.log("Usage: /macro study <macro-name> key=value ...")
            return
        macro = self.macro_library.load_macro(name)
        runtime_values = self._macro_values(macro, values)
        context = self.macro_library.format_study_context(macro, runtime_values)
        self.log(f"Study Macro: {macro.name}")
        if macro.mode == "auto":
            self.log("Note: this macro is mode=auto; Study will use it only as guidance.")
        self.agent.set_mode("study")
        self.study_context = context
        response = self.agent.process_message(
            self._session(),
            "Use the following Structured Macro to guide the user interactively from the first step.",
            extra_context=context,
        )
        self.log(f"AI:\n{response}")
        self.log("Study Mode remains active. Use Auto/Ask/Solve buttons or /auto to leave Study Mode.")

    def _handle_macro_command(self, arg):
        self._ensure_ready()
        parts = str(arg or "").strip().split(maxsplit=1)
        if not parts:
            self.log("Usage: /macros | /macro show <name> | /macro run <name> key=value ... | /macro study <name> key=value ... | /macro learn recordings | /macro audit <name> | /macro doctor <name>")
            return
        subcommand = parts[0].lower()
        rest = parts[1].strip() if len(parts) > 1 else ""
        try:
            if subcommand in {"list", "ls"}:
                self._show_macros()
            elif subcommand == "show":
                name, _values = self.macro_library.parse_values(rest)
                if not name:
                    self.log("Usage: /macro show <name>")
                    return
                self.log(self.macro_library.format_summary(self.macro_library.load_macro(name)))
            elif subcommand == "run":
                self._run_macro(rest)
            elif subcommand == "study":
                self._run_macro_study(rest)
            elif subcommand == "learn":
                target = rest or "recordings"
                if target.lower() != "recordings":
                    raise SAPMacroError("Only /macro learn recordings is supported.")
                summary = self.macro_library.learn_from_recordings("recordings")
                self.log("Macro Learn Recordings:\n" + json.dumps(summary, ensure_ascii=False, indent=2))
            elif subcommand == "audit":
                name, _values = self.macro_library.parse_values(rest)
                if not name:
                    self.log("Usage: /macro audit <name>")
                    return
                self.log(self.macro_library.audit_macro(name))
            elif subcommand == "doctor":
                name, _values = self.macro_library.parse_values(rest)
                if not name:
                    self.log("Usage: /macro doctor <name>")
                    return
                self.log(self.macro_library.doctor_macro(self._session(), self.agent, name))
            else:
                self._run_macro(arg)
        except (FileNotFoundError, SAPMacroError) as exc:
            self.log(f"Macro failed: {exc}")

    def _run_study(self, goal):
        self._ensure_ready()
        raw_goal = str(goal or "").strip()
        if raw_goal.lower().startswith(("--macro ", "/macro ")):
            macro_arg = raw_goal.split(maxsplit=1)[1].strip() if len(raw_goal.split(maxsplit=1)) > 1 else ""
            self._run_macro_study(macro_arg)
            return
        goal, allow_draft_study, save_draft_skill = parse_study_request(goal)
        if not goal:
            self.log("Usage: /study [SOP name] | /study --draft [goal] | /study --save-draft [goal]")
            return
        initial_screen = None
        try:
            initial_screen = scan_sap_screen(self._session())
        except Exception:
            pass

        skill_found = True
        try:
            skill_data = self.skill_library.load_skill(goal)
        except FileNotFoundError:
            skill_found = False
            skill_data = {}

        if not skill_found:
            if not allow_draft_study:
                self.log(
                    "No SOP / skill found. STUDY_REQUIRE_DRAFT_FLAG=true, so Study Mode requires an explicit --draft flag.\n"
                    "Use /record [name] to capture a real flow, /solve for troubleshooting, "
                    "or /study --draft [goal] for an explicit exploratory draft. "
                    "Set STUDY_REQUIRE_DRAFT_FLAG=false to let /study [goal] start evidence-driven draft guidance."
                )
                return
            evidence_pack = self.knowledge_library.build_evidence_pack(
                goal,
                initial_screen,
                skill_library=self.skill_library,
            )
            sop_text = _build_ad_hoc_study_context(goal, initial_screen, evidence_pack)
            request = (
                f"No existing SOP / skill was found for '{goal}'. "
                "This is an explicit exploratory draft. Guide from the evidence pack and current SAP screen, "
                "state source/confidence, ask for confirmation when evidence is weak, and finish with a draft summary."
            )
        elif skill_data.get("format") == "markdown" and skill_data.get("sop_text"):
            sop_text = skill_data["sop_text"]
            request = "Use the following SOP reference to guide the user interactively."
        else:
            sop_text = self.skill_library.get_skill_summary(goal)
            request = "Use the following SOP reference to guide the user interactively."

        self.log(f"Study Mode: {goal}")
        if not skill_found:
            if save_draft_skill:
                self.log("Exploratory draft mode is enabled. This session will be saved as a draft skill.")
            else:
                self.log("Exploratory draft mode is enabled. This session will not be saved automatically.")
        self.agent.set_mode("study")
        self.study_context = sop_text
        try:
            response = self.agent.process_message(
                self._session(),
                request,
                extra_context=sop_text,
            )
            self.log(f"AI:\n{response}")
            if not skill_found and save_draft_skill:
                canonical_name = self.skill_library.canonical_skill_name(goal)
                markdown = _build_learned_skill_markdown(
                    canonical_name,
                    response,
                    _extract_study_guidance_steps(self.agent),
                    initial_screen,
                )
                path = self.skill_library.save_markdown_skill(goal, markdown)
                self.log(f"Draft skill saved: {path}")
            elif not skill_found:
                self.log("Draft study was not saved. Use /record for a verified SOP or /study --save-draft to save an exploratory draft.")
            self.log("Study Mode remains active. Use Auto/Ask/Solve buttons or /auto to leave Study Mode.")
        except Exception as exc:
            self.log(f"Study Mode failed: {exc}")


class SAPCopilotUI:
    def __init__(self, root):
        self.root = root
        self.root.title(f"SAP GUI Copilot {APP_VERSION}")
        self.root.geometry("760x620")
        self.root.minsize(560, 420)

        self.outbound_queue = queue.Queue()
        self.worker = SAPCopilotWorker(self.outbound_queue)

        self.mode_var = tk.StringVar(value="Auto")
        self.sap_var = tk.StringVar(value="SAP: connecting...")
        self.model_var = tk.StringVar(value="Model: -")
        self.recording_var = tk.StringVar(value="")
        self.always_on_top_var = tk.BooleanVar(value=True)

        self._build_widgets()
        self.root.attributes("-topmost", True)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.worker.start()
        self.root.after(100, self._poll_worker)

    def _build_widgets(self):
        self.root.configure(bg=UI_COLORS["bg"])
        style = ttk.Style(self.root)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("App.TFrame", background=UI_COLORS["bg"])
        style.configure("Panel.TFrame", background=UI_COLORS["panel"])
        style.configure("App.TLabel", background=UI_COLORS["bg"], foreground=UI_COLORS["text"])
        style.configure("Panel.TLabel", background=UI_COLORS["panel"], foreground=UI_COLORS["text"])
        style.configure("Title.TLabel", background=UI_COLORS["bg"], foreground=UI_COLORS["accent"], font=("Segoe UI", 14, "bold"))
        style.configure("Version.TLabel", background=UI_COLORS["bg"], foreground=UI_COLORS["muted"], font=("Segoe UI", 8))
        style.configure("Status.TLabel", background=UI_COLORS["panel"], foreground=UI_COLORS["system"], font=("Segoe UI", 9))
        style.configure("Mode.TLabel", background=UI_COLORS["panel"], foreground=UI_COLORS["macro"], font=("Segoe UI", 9, "bold"))
        style.configure("App.TButton", background=UI_COLORS["surface"], foreground=UI_COLORS["text"], padding=(10, 5))
        style.configure("Primary.TButton", background="#cf3d00", foreground="#ffffff", padding=(14, 8))
        style.map("Primary.TButton", background=[("active", "#a83200")], foreground=[("active", "#ffffff")])

        outer = ttk.Frame(self.root, padding=10, style="App.TFrame")
        outer.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(outer, style="App.TFrame")
        header.pack(fill=tk.X)
        ttk.Label(header, text="SAP GUI Copilot", style="Title.TLabel").pack(side=tk.LEFT)
        ttk.Label(header, text=f"v{APP_VERSION}", style="Version.TLabel").pack(side=tk.LEFT, padx=(8, 0), pady=(4, 0))
        ttk.Checkbutton(
            header,
            text="Top",
            variable=self.always_on_top_var,
            command=self._toggle_topmost,
        ).pack(side=tk.RIGHT, padx=(8, 0))
        ttk.Button(header, text="Connect", style="App.TButton", command=self._connect_dialog).pack(side=tk.RIGHT)

        status = ttk.Frame(outer, padding=(10, 8), style="Panel.TFrame")
        status.pack(fill=tk.X, pady=(8, 8))
        ttk.Label(status, textvariable=self.mode_var, width=14, style="Mode.TLabel").pack(side=tk.LEFT)
        ttk.Label(status, textvariable=self.sap_var, style="Status.TLabel").pack(side=tk.LEFT, padx=(8, 0))
        ttk.Label(status, textvariable=self.model_var, style="Status.TLabel").pack(side=tk.RIGHT)

        modes = ttk.Frame(outer, style="App.TFrame")
        modes.pack(fill=tk.X, pady=(0, 8))
        for mode in ("auto", "ask", "solve"):
            ttk.Button(
                modes,
                text=MODE_LABELS[mode],
                style="App.TButton",
                command=lambda item=mode: self.worker.submit("set_mode", mode=item),
            ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(modes, text="Study", style="App.TButton", command=self._study_dialog).pack(side=tk.LEFT, padx=(0, 6))

        actions = ttk.Frame(outer, style="App.TFrame")
        actions.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(actions, text="Scan", style="App.TButton", command=lambda: self.worker.submit("scan")).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(actions, text="Inspect", style="App.TButton", command=lambda: self.worker.submit("inspect_table")).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(actions, text="MCP", style="App.TButton", command=lambda: self.worker.submit("mcp_probe")).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(actions, text="Reset", style="App.TButton", command=lambda: self.worker.submit("reset")).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(actions, text="Record", style="App.TButton", command=self._record_dialog).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(actions, text="Stop", style="App.TButton", command=lambda: self.worker.submit("stop_record")).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(actions, text="SOP", style="App.TButton", command=lambda: self.worker.submit("recordings")).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(actions, text="Macro", style="App.TButton", command=self._macro_dialog).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Label(actions, textvariable=self.recording_var, style="App.TLabel").pack(side=tk.RIGHT)

        self.output = ScrolledText(
            outer,
            wrap=tk.WORD,
            height=20,
            font=("Segoe UI", 10),
            bg=UI_COLORS["panel"],
            fg=UI_COLORS["text"],
            insertbackground=UI_COLORS["text"],
            relief=tk.SOLID,
            bd=1,
            padx=10,
            pady=8,
        )
        self.output.pack(fill=tk.BOTH, expand=True)
        self.output.configure(state=tk.DISABLED)
        self._configure_output_tags()

        input_row = ttk.Frame(outer, style="App.TFrame")
        input_row.pack(fill=tk.X, pady=(8, 0))
        self.input_text = tk.Text(
            input_row,
            height=3,
            wrap=tk.WORD,
            font=("Segoe UI", 10),
            bg="#fffdf8",
            fg=UI_COLORS["text"],
            insertbackground=UI_COLORS["text"],
            relief=tk.SOLID,
            bd=1,
            padx=8,
            pady=6,
        )
        self.input_text.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.input_text.bind("<Control-Return>", lambda _event: self.send())
        self.input_text.bind("<Return>", self._return_key)
        ttk.Button(input_row, text="發送 ↵", style="Primary.TButton", command=self.send).pack(side=tk.LEFT, padx=(8, 0), fill=tk.Y)

        self._print_terminal_log("UI ready. Use buttons or commands like /scan, /mcp, /ask, /solve, /recordings, /macro.")

    def _toggle_topmost(self):
        self.root.attributes("-topmost", bool(self.always_on_top_var.get()))

    def _return_key(self, event):
        if event.state & 0x0001:
            return None
        self.send()
        return "break"

    def _record_dialog(self):
        name = simpledialog.askstring("Record", "SOP name:", parent=self.root)
        if name:
            self.worker.submit("record", name=name.strip())

    def _study_dialog(self):
        goal = simpledialog.askstring("Study", "SOP name, or --draft goal:", parent=self.root)
        if goal:
            self.worker.submit("study", goal=goal.strip())

    def _macro_dialog(self):
        arg = simpledialog.askstring(
            "Macro",
            "list | show <name> | run <name> key=value ... | study <name> key=value ...:",
            parent=self.root,
        )
        if arg:
            self.worker.submit("macro", arg=arg.strip())

    def _connect_dialog(self):
        current = normalize_provider_name(os.getenv("LLM_PROVIDER", "github_copilot"))
        dialog = tk.Toplevel(self.root)
        dialog.title("Connect")
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)

        provider_var = tk.StringVar(value=current)
        model_var = tk.StringVar()
        endpoint_var = tk.StringVar()
        key_var = tk.StringVar()
        status_var = tk.StringVar()

        outer = ttk.Frame(dialog, padding=12)
        outer.pack(fill=tk.BOTH, expand=True)

        provider_box = ttk.LabelFrame(outer, text="LLM Provider")
        provider_box.pack(fill=tk.X)
        ttk.Radiobutton(
            provider_box,
            text="GitHub Copilot",
            variable=provider_var,
            value="github_copilot",
        ).pack(anchor=tk.W, padx=8, pady=(6, 2))
        ttk.Radiobutton(
            provider_box,
            text="Codex OAuth",
            variable=provider_var,
            value="codex_oauth",
        ).pack(anchor=tk.W, padx=8, pady=(2, 6))

        method_box = ttk.LabelFrame(outer, text="連線方法")
        method_box.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        method_text = tk.Text(method_box, width=72, height=8, wrap=tk.WORD)
        method_text.pack(fill=tk.BOTH, expand=True, padx=8, pady=8)
        method_text.configure(state=tk.DISABLED)

        form = ttk.Frame(outer)
        form.pack(fill=tk.X, pady=(10, 0))
        ttk.Label(form, text="Model").grid(row=0, column=0, sticky=tk.W, pady=2)
        model_entry = ttk.Entry(form, textvariable=model_var, width=46)
        model_entry.grid(row=0, column=1, sticky=tk.W, padx=(8, 0), pady=2)
        endpoint_label = ttk.Label(form, text="Endpoint")
        endpoint_label.grid(row=1, column=0, sticky=tk.W, pady=2)
        endpoint_entry = ttk.Entry(form, textvariable=endpoint_var, width=46)
        endpoint_entry.grid(row=1, column=1, sticky=tk.W, padx=(8, 0), pady=2)
        auth_label = ttk.Label(form, text="Auth")
        auth_label.grid(row=2, column=0, sticky=tk.W, pady=2)
        key_entry = ttk.Entry(form, textvariable=key_var, width=46, show="*")
        key_entry.grid(row=2, column=1, sticky=tk.W, padx=(8, 0), pady=2)

        ttk.Label(outer, textvariable=status_var, foreground="#666").pack(fill=tk.X, pady=(8, 0))

        def _write_method(text):
            method_text.configure(state=tk.NORMAL)
            method_text.delete("1.0", tk.END)
            method_text.insert("1.0", text)
            method_text.configure(state=tk.DISABLED)

        def _refresh_fields(*_args):
            provider = normalize_provider_name(provider_var.get())
            if provider == "github_copilot":
                model_var.set(os.getenv("COPILOT_MODEL", "gpt-5-mini"))
                endpoint_var.set("https://api.githubcopilot.com/chat/completions")
                key_var.set("")
                endpoint_label.configure(text="Endpoint")
                auth_label.configure(text="API Key")
                endpoint_entry.configure(state=tk.DISABLED)
                key_entry.configure(state=tk.DISABLED, show="")
                _write_method(
                    "GitHub Copilot 連線方式\n"
                    "1. 使用 GitHub OAuth Device Flow。\n"
                    "2. 不需要在此輸入 API Key。\n"
                    "3. 若尚未登入，Connect 後會在 console 顯示 GitHub 授權碼。\n"
                    "4. 適合沿用既有 GitHub Copilot 訂閱。"
                )
                status_var.set("目前將使用 GitHub Copilot OAuth。")
            else:
                model_var.set(os.getenv("CODEX_OAUTH_MODEL") or "gpt-5.5")
                endpoint_var.set(os.getenv("CODEX_OAUTH_CHAT_URL", "https://chatgpt.com/backend-api/codex/responses"))
                key_var.set(os.path.join(os.path.expanduser("~"), ".codex", "auth.json"))
                endpoint_label.configure(text="Endpoint")
                auth_label.configure(text="Token Cache")
                endpoint_entry.configure(state=tk.DISABLED)
                key_entry.configure(state=tk.DISABLED, show="")
                _write_method(
                    "Codex OAuth 連線方式\n"
                    "1. 使用 OpenAI/Codex OAuth 帳號登入，不需要 API Key。\n"
                    "2. 首次使用請按 /login，系統只會開啟 Codex 瀏覽器登入頁。\n"
                    "3. 登入後讀取本機 ~/.codex/auth.json 的 OAuth token。\n"
                    "4. 模型呼叫由 SAP_Copilot 直接走 HTTP endpoint，不使用命令列推理。"
                )
                status_var.set("Connect 只會使用瀏覽器 OAuth 登入；模型推理不走命令列。")

        provider_var.trace_add("write", _refresh_fields)
        _refresh_fields()

        buttons = ttk.Frame(outer)
        buttons.pack(fill=tk.X, pady=(12, 0))

        def _connect_selected():
            provider = normalize_provider_name(provider_var.get())
            if provider == "github_copilot":
                os.environ["COPILOT_MODEL"] = model_var.get().strip() or os.getenv("COPILOT_MODEL", "gpt-5-mini")
            else:
                os.environ["CODEX_OAUTH_MODEL"] = model_var.get().strip() or os.getenv("CODEX_OAUTH_MODEL", "gpt-5.5")
            os.environ["LLM_PROVIDER"] = provider
            self.worker.submit("connect", provider=provider)
            dialog.destroy()

        ttk.Button(buttons, text="Connect", command=_connect_selected).pack(side=tk.RIGHT)
        ttk.Button(buttons, text="Cancel", command=dialog.destroy).pack(side=tk.RIGHT, padx=(0, 8))

        dialog.bind("<Return>", lambda _event: _connect_selected())
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.wait_window()

    def send(self):
        text = self.input_text.get("1.0", tk.END).strip()
        if not text:
            return
        self.input_text.delete("1.0", tk.END)
        self.worker.submit("send", text=text)

    def _configure_output_tags(self):
        self.output.tag_configure("time", foreground="#b7a891", font=("Segoe UI", 8))
        self.output.tag_configure("user_label", foreground=UI_COLORS["user"], font=("Segoe UI", 10, "bold"))
        self.output.tag_configure("ai_label", foreground=UI_COLORS["ai"], font=("Segoe UI", 10, "bold"))
        self.output.tag_configure("system_label", foreground=UI_COLORS["system"], font=("Segoe UI", 9, "bold"))
        self.output.tag_configure("body", foreground=UI_COLORS["text"], font=("Segoe UI", 10), lmargin1=12, lmargin2=12, rmargin=14)
        self.output.tag_configure("user_body", foreground="#0d47a1", font=("Segoe UI", 10, "bold"), lmargin1=12, lmargin2=12, rmargin=14)
        self.output.tag_configure(
            "system_body",
            foreground=UI_COLORS["system"],
            font=("Segoe UI", 9),
            lmargin1=12,
            lmargin2=12,
            rmargin=14,
            spacing3=5,
        )
        self.output.tag_configure(
            "ai_body",
            foreground=UI_COLORS["text"],
            font=("Segoe UI", 10),
            lmargin1=12,
            lmargin2=12,
            rmargin=14,
            spacing3=6,
        )

    @staticmethod
    def _classify_log_entry(text):
        stripped = str(text or "").strip()
        if stripped.startswith("You:"):
            return "user", stripped[4:].strip()
        if stripped.startswith("AI:\n"):
            return "ai", stripped[3:].lstrip()
        if stripped.startswith("AI:"):
            return "ai", stripped[3:].strip()
        if SAPCopilotWorker._is_ui_status_log(stripped):
            return "system", stripped
        return "terminal", stripped

    def _append(self, text):
        timestamp = time.strftime("%H:%M:%S")
        role, body = self._classify_log_entry(text)
        if role not in {"user", "ai", "system"}:
            self._print_terminal_log(text)
            return
        self.output.configure(state=tk.NORMAL)
        self.output.insert(tk.END, f"[{timestamp}] ", ("time",))
        if role == "user":
            self.output.insert(tk.END, "You: ", ("user_label",))
            self.output.insert(tk.END, (body or "").rstrip() + "\n\n", ("user_body",))
        elif role == "system":
            self.output.insert(tk.END, "System: ", ("system_label",))
            self.output.insert(tk.END, (body or "").rstrip() + "\n\n", ("system_body",))
        else:
            self.output.insert(tk.END, "AI:\n", ("ai_label",))
            self.output.insert(tk.END, (body or "(AI 未回傳任何訊息)").rstrip() + "\n\n", ("ai_body",))
        self.output.see(tk.END)
        self.output.configure(state=tk.DISABLED)

    @staticmethod
    def _print_terminal_log(text):
        timestamp = time.strftime("%H:%M:%S")
        print(f"[{timestamp}] {str(text or '').rstrip()}", flush=True)

    def _poll_worker(self):
        while True:
            try:
                item = self.outbound_queue.get_nowait()
            except queue.Empty:
                break
            kind = item.get("kind")
            payload = item.get("payload")
            if kind == "dialog_log":
                self._append(str(payload))
            elif kind == "terminal_log":
                self._print_terminal_log(payload)
            elif kind == "log":
                self._print_terminal_log(payload)
            elif kind == "state":
                self._apply_state(payload or {})
            elif kind == "prompt":
                self._handle_prompt(payload or {})
        self.root.after(100, self._poll_worker)

    def _handle_prompt(self, payload):
        prompt_id = payload.get("id")
        prompt_type = payload.get("type", "text")
        title = payload.get("title") or "SAP GUI Copilot"
        message = payload.get("message") or ""
        if prompt_type == "confirm":
            response = messagebox.askyesno(title, message, parent=self.root)
        else:
            response = simpledialog.askstring(
                title,
                message,
                initialvalue=payload.get("default", ""),
                parent=self.root,
            )
            if response is None:
                response = ""
        self.worker.resolve_prompt(prompt_id, response)

    def _apply_state(self, state):
        mode = state.get("mode", "auto")
        ready = "ready" if state.get("ready") else "not connected"
        self.mode_var.set(f"● {MODE_LABELS.get(mode, mode)} Mode")
        tcode = state.get("tcode") or "-"
        user = state.get("user") or "-"
        client = state.get("client") or "-"
        status_mark = "✓" if state.get("ready") else "!"
        self.sap_var.set(f"{status_mark} {ready}  {tcode}  {client}/{user}")
        provider = provider_display_name(state.get("provider") or "github_copilot")
        self.model_var.set(f"{provider} / {state.get('model') or '-'}")
        recording = state.get("recording") or ""
        self.recording_var.set(f"REC: {recording}" if recording else "")

    def close(self):
        if self.worker.recorder and self.worker.recorder.is_recording:
            if not messagebox.askyesno("Recording", "Recording is active. Stop and close?"):
                return
            self.worker.submit("stop_record")
        self.worker.submit("shutdown")
        self.root.after(250, self.root.destroy)


def main():
    root = tk.Tk()
    SAPCopilotUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
