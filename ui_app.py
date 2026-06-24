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
from sap_table_inspector import format_table_inspection, inspect_current_tables
from mcp_client import get_default_sync_client
from sop_step_parser import compress_for_display, display_lines_to_text


APP_VERSION = "0.13.0"

MODE_LABELS = {
    "auto": "Auto",
    "ask": "Ask",
    "solve": "Solve",
    "study": "Study",
}

THEME = {
    "bg": "#F8F5F0",
    "panel": "#EEE8DD",
    "panel_alt": "#E6DED2",
    "text": "#332D28",
    "muted": "#7A7168",
    "accent": "#C43E08",
    "accent_dark": "#A33205",
    "accent_soft": "#EFD7C5",
    "green": "#21854B",
    "green_dark": "#176638",
    "purple": "#6129B7",
    "blue": "#213A8F",
    "danger": "#8E3326",
    "border": "#D6CFC4",
    "output_bg": "#FCFAF7",
    "input_bg": "#EDE7DD",
    "button_bg": "#E5DED4",
    "button_active": "#D8CEC1",
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


def _scan_with_timeout(session, timeout: float = 3.0):
    """在背景 daemon thread 執行 scan_sap_screen，超時直接回傳 None。
    daemon=True 確保即使 scan 卡住，app 仍可正常關閉。"""
    result: list = [None]

    def _do():
        try:
            result[0] = scan_sap_screen(session)
        except Exception:
            pass

    t = threading.Thread(target=_do, daemon=True)
    t.start()
    t.join(timeout=timeout)
    return result[0]


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
        self.ready = False
        self.last_connect_error = ""
        self.study_context = ""
        self.study_skill_name = ""
        self.prompt_counter = 0
        self.prompt_lock = threading.Lock()
        self.pending_prompts = {}

    def submit(self, action, **payload):
        self.inbound_queue.put({"action": action, **payload})

    def emit(self, kind, payload):
        self.outbound_queue.put({"kind": kind, "payload": payload})

    def log(self, text):
        self.emit("log", text)

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
            self.agent._request_study_prompt = self._request_study_prompt_ui
            llm_brain.TOOL_FUNCTIONS["guide_user_action"] = self._guide_user_action_ui
            self.recorder = SAPRecorder()
            self.skill_library = SAPSkillLibrary()
            self.knowledge_library = SAPKnowledgeLibrary()
            self.ready = True
            self.log(
                "Connected: "
                f"{info.get('system_name', 'SAP')} client={info.get('client', 'N/A')} "
                f"user={info.get('user', 'N/A')} tcode={info.get('transaction', 'N/A')} "
                f"provider={provider_display_name(self.agent.provider_name)} model={self.agent.model}"
            )
        except Exception as exc:
            self.last_connect_error = str(exc)
            self.log(f"Connection failed: {exc}")
        else:
            self.last_connect_error = ""
        self.emit_state()

    def _ensure_ready(self):
        if not self.ready:
            detail = f" Last connection error: {self.last_connect_error}" if self.last_connect_error else ""
            raise RuntimeError(f"UI is not connected. Press Connect first.{detail}")

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

    def _request_prompt(self, title, message, default="", prompt_type="text", extra=None):
        with self.prompt_lock:
            self.prompt_counter += 1
            prompt_id = self.prompt_counter
            response_queue = queue.Queue(maxsize=1)
            self.pending_prompts[prompt_id] = response_queue

        payload = {
            "id": prompt_id,
            "type": prompt_type,
            "title": title,
            "message": message,
            "default": default,
        }
        if extra:
            payload.update(extra)
        self.emit("prompt", payload)
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
            # 掃當前畫面讓 AI 知道使用者現在在哪，才能修正下一步
            current_screen = _scan_with_timeout(session, timeout=2.0) or {}
            current_screen_brief = {
                "tcode":         current_screen.get("tcode", ""),
                "screen_number": current_screen.get("screen_number", ""),
                "title":         current_screen.get("title", ""),
            }
            self.log(f"找不到欄位 {element_id}，AI 正在根據目前畫面重新判斷…")
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
                "current_screen": current_screen_brief,
                "hint": (
                    "The specified element_id was not found on the current screen. "
                    "Use current_screen to understand where the user is now, "
                    "then call guide_user_action again with the correct element_id "
                    "for this screen, or give a plain text instruction without element_id."
                ),
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

        display_lines = compress_for_display(_compact_study_text(instruction, max_lines=20, max_chars=2000))
        if display_choices:
            display_lines += [("body", f"  {i}. {c}") for i, c in enumerate(display_choices, 1)]

        step_title = reason or "Study Step"  # reason 通常是「步驟 N/M」

        user_response = str(self._request_prompt(
            "Study Step",
            display_lines_to_text(display_lines),
            default="",
            prompt_type="study_step",
            extra={
                "step_title": step_title,
                "body": display_lines,
                "current_value": current_value or "",
                "warn": "",
            },
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

    def _request_study_prompt_ui(self, title: str, message: str, warn: str = "") -> str:
        """
        Study Mode 無 element_id 步驟 / AI 回覆的 UI 版本 hook。
        取代 terminal print/input，改以彈出對話框呈現。
        """
        display_lines = compress_for_display(message)
        user_response = str(self._request_prompt(
            "Study Step",
            display_lines_to_text(display_lines),
            default="",
            prompt_type="study_step",
            extra={
                "step_title": title,
                "body": display_lines,
                "current_value": "",
                "warn": warn,
            },
        ) or "").strip()
        return user_response

    def _set_mode(self, mode):
        self._ensure_ready()
        self.agent.set_mode(mode)
        if mode != "study":
            self.study_context = ""
            self.study_skill_name = ""
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
                self.log(SAPSkillLibrary.format_module_list())
                self.log("/study [模組代碼]            查看課程（MM / SD / FI / CO / PP）\n"
                         "/study [模組代碼] [名稱]     啟動教練，例如 /study FI 應付帳款查詢\n"
                         "/study --draft [目標]        探索草稿（無需既有 SOP）")
            else:
                sop_name, _allow, _save = parse_study_request(arg)
                module_code, remainder = SAPSkillLibrary.parse_module_prefix(sop_name)
                if module_code and not remainder:
                    self.log(self.skill_library.format_module_curriculum(module_code))
                else:
                    self._run_study(arg)
        elif cmd == "/knowledge":
            self._handle_knowledge_command(arg)
        elif cmd == "/skills":
            self._handle_skills_command(arg)
        elif cmd == "/import":
            if not arg:
                self.log("Usage: /import <vbs檔路徑> [錄製名稱]\n"
                         "  將 SAP GUI 內建錄製器產生的 .vbs 腳本匯入為錄製檔。\n"
                         "  範例: /import C:\\Users\\user\\Desktop\\VA01.vbs VA01建立銷售訂單")
            else:
                parts2 = arg.split(maxsplit=1)
                vbs_path = parts2[0].strip('"').strip("'")
                rec_name = parts2[1].strip() if len(parts2) > 1 else _os.path.splitext(_os.path.basename(vbs_path))[0]
                self._import_vbs(vbs_path, rec_name)
        elif cmd == "/connect":
            self._connect(arg or None)
        else:
            self.log("Available commands: /scan, /inspect table, /mcp, /record, /stop, /recordings, /play, /study, /knowledge, /skills, /ask, /solve, /auto, /connect, /reset, /import")

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
            if self.study_skill_name:
                try:
                    data = self.skill_library.load_skill(self.study_skill_name)
                    return data.get("sop_text") or self.study_context
                except Exception:
                    pass
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
        session = self._session()
        extra_context = self._extra_context_for_current_mode()

        response = self.agent.process_message(session, text, extra_context=extra_context)
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
            recorded = self.recorder.add_event(event)
            if not recorded:
                return
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
                context_events=rec_data.get("context_events", []),
            )
            if sop_path:
                self.log(f"AI SOP saved: {sop_path}")
        except Exception as exc:
            self.log(f"AI SOP generation skipped: {exc}")

    def _import_vbs(self, vbs_path: str, rec_name: str):
        """匯入 SAP GUI 內建錄製器的 .vbs 腳本，存成錄製 JSON 並產生 SOP。"""
        import sap_vbs_parser as _vbs_parser
        try:
            with open(vbs_path, encoding="utf-8-sig", errors="replace") as f:
                vbs_text = f.read()
        except FileNotFoundError:
            self.log(f"找不到檔案: {vbs_path}")
            return
        except Exception as exc:
            self.log(f"讀取 VBS 失敗: {exc}")
            return

        self.log(f"解析 VBS 腳本: {vbs_path}")
        recording = _vbs_parser.parse_vbs(vbs_text, rec_name)

        raw_count = recording.get("raw_event_count", 0)
        evt_count = recording.get("event_count", 0)
        self.log(f"解析完成：原始動作 {raw_count} 個，壓縮後 {evt_count} 個")

        if not self.recorder:
            from sap_recorder import SAPRecorder
            self.recorder = SAPRecorder()

        filepath = self.recorder.save_recording(recording)
        self.log(f"錄製已儲存: {filepath}")

        try:
            sop_path = self.recorder.generate_sop_with_llm(
                rec_name,
                recording.get("events", []),
                self.auth,
                screen_state=None,
                provider=self.agent.provider if self.agent else None,
                context_events=recording.get("context_events", []),
            )
            if sop_path:
                self.log(f"AI SOP 已產生: {sop_path}")
        except Exception as exc:
            self.log(f"AI SOP 產生跳過: {exc}")

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

    def _run_study(self, goal):
        self._ensure_ready()
        goal, allow_draft_study, save_draft_skill = parse_study_request(goal)
        _, goal = SAPSkillLibrary.parse_module_prefix(goal)
        if not goal:
            self.log(SAPSkillLibrary.format_module_list())
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
            self.study_skill_name = goal
        else:
            sop_text = self.skill_library.get_skill_summary(goal)
            request = "Use the following SOP reference to guide the user interactively."
            self.study_skill_name = goal

        self.log(f"Study Mode: {goal}")
        if not skill_found:
            self.study_skill_name = ""
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


# ── Study Step 自訂 Dialog ────────────────────────────────────────────────────

class StudyStepDialog(tk.Toplevel):
    """
    Study Mode 步驟引導 dialog。
    - 藍色標題列顯示步驟編號
    - 主要說明文字（壓縮過的 instruction）
    - 若有目前欄位值則以灰底小框顯示
    - 回覆輸入框 + OK / 略過 按鈕
    """

    # SAP 藍色系
    _HDR_BG   = "#003399"
    _HDR_FG   = "#ffffff"
    _BODY_BG  = "#f8f9fb"
    _VAL_BG   = "#e8ecf2"
    _WARN_FG  = "#c75000"
    _BTN_OK   = "#003399"
    _BTN_FG   = "#ffffff"
    _BTN_SKIP = "#e0e4ef"
    _BTN_SKIP_FG = "#333333"

    def __init__(self, parent, step_title: str, body,
                 current_value: str = "", warn: str = ""):
        super().__init__(parent)
        self.result: str = ""
        self.title("Study Step")
        self.resizable(False, False)
        self.configure(bg=self._BODY_BG)

        # 置中於父視窗
        self.transient(parent)
        self.grab_set()

        self._build(step_title, body, current_value, warn)

        # 等視窗繪製完畢後置中
        self.update_idletasks()
        w, h = self.winfo_width(), self.winfo_height()
        pw, ph = parent.winfo_width(), parent.winfo_height()
        px, py = parent.winfo_rootx(), parent.winfo_rooty()
        self.geometry(f"+{px + (pw - w) // 2}+{py + (ph - h) // 2}")

        self._entry.focus_set()
        self.bind("<Return>", lambda _: self._on_ok())
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)
        self.wait_window(self)

    def _build(self, step_title, body, current_value, warn):
        # ── 標題列 ──────────────────────────────────────
        hdr = tk.Frame(self, bg=self._HDR_BG, pady=10, padx=16)
        hdr.pack(fill="x")
        tk.Label(
            hdr, text=step_title,
            bg=self._HDR_BG, fg=self._HDR_FG,
            font=("Segoe UI", 11, "bold"), anchor="w", wraplength=500,
        ).pack(fill="x")

        # ── 內容區 ──────────────────────────────────────
        body_frame = tk.Frame(self, bg=self._BODY_BG, padx=14, pady=10)
        body_frame.pack(fill="both", expand=True)

        # 說明文字（捲動式，依行數自動調整高度，最多 15 行）
        if body:
            # body 可能是 list[DisplayLine] 或舊式 str
            if isinstance(body, str):
                body = compress_for_display(body)
            line_count = len(body)
            txt_height = max(3, min(15, line_count + 1))
            txt = ScrolledText(
                body_frame,
                font=("Segoe UI", 10),
                bg=self._BODY_BG, fg="#1a1a1a",
                relief="flat", bd=0,
                wrap="word",
                height=txt_height,
                state="normal",
                padx=2, pady=2,
            )
            # 設定顏色 tag
            txt.tag_configure("heading",     font=("Segoe UI", 10, "bold"), foreground="#1a1a1a")
            txt.tag_configure("body",        foreground="#333333")
            txt.tag_configure("req_section", font=("Segoe UI", 9, "bold"),  foreground="#c0392b")
            txt.tag_configure("req",         foreground="#c0392b")
            txt.tag_configure("opt_section", font=("Segoe UI", 9, "bold"),  foreground="#888888")
            txt.tag_configure("opt",         foreground="#888888")
            # 逐行插入並套用 tag
            for kind, line_text in body:
                txt.insert("end", line_text + "\n", kind)
            txt.config(state="disabled")
            txt.pack(fill="both", expand=True, pady=(0, 6))

        # 目前欄位值
        if current_value:
            val_frame = tk.Frame(body_frame, bg="#fef3c7", padx=10, pady=6)
            val_frame.pack(fill="x", pady=(0, 6))
            tk.Label(
                val_frame, text=f"目前值：{current_value}",
                bg="#fef3c7", fg="#b45309",
                font=("Segoe UI", 10, "bold"), anchor="w",
            ).pack(fill="x")

        # 無法定位元件警告
        if warn:
            tk.Label(
                body_frame, text=f"⚠  {warn}",
                bg=self._BODY_BG, fg=self._WARN_FG,
                font=("Segoe UI", 9), anchor="w",
            ).pack(fill="x", pady=(0, 4))

        # 提示文字
        tk.Label(
            body_frame,
            text="完成後按 OK；有問題可直接輸入；/skip 略過；/done 結束",
            bg=self._BODY_BG, fg="#888",
            font=("Segoe UI", 8), anchor="w",
        ).pack(fill="x", pady=(2, 4))

        # 輸入框
        self._entry = tk.Entry(body_frame, font=("Segoe UI", 10), relief="solid", bd=1)
        self._entry.pack(fill="x", ipady=5)

        # 分隔線 + 按鈕列
        tk.Frame(self, height=1, bg="#cdd3e0").pack(fill="x")

        btn_row = tk.Frame(self, bg=self._BODY_BG, padx=16, pady=10)
        btn_row.pack(fill="x")

        tk.Button(
            btn_row, text="略過", width=7,
            bg=self._BTN_SKIP, fg=self._BTN_SKIP_FG,
            font=("Segoe UI", 9), relief="flat", bd=0,
            activebackground="#ccd5ea",
            command=self._on_skip,
        ).pack(side="right", padx=(6, 0))

        tk.Button(
            btn_row, text="OK", width=9,
            bg=self._BTN_OK, fg=self._BTN_FG,
            font=("Segoe UI", 9, "bold"), relief="flat", bd=0,
            activebackground="#0044bb",
            command=self._on_ok,
        ).pack(side="right")

    def _on_ok(self):
        self.result = self._entry.get().strip()
        self.destroy()

    def _on_skip(self):
        self.result = "/skip"
        self.destroy()

    def _on_cancel(self):
        self.result = ""
        self.destroy()


# 五大模組顏色（對應 UI 色條）
MODULE_COLORS = {
    "MM": "#26C6DA",   # 青藍
    "SD": "#66BB6A",   # 綠
    "FI": "#FFA726",   # 橙黃
    "CO": "#EF5350",   # 紅
    "PP": "#7E57C2",   # 紫
}


def _center_on_parent(dialog, parent):
    dialog.update_idletasks()
    w, h = dialog.winfo_width(), dialog.winfo_height()
    pw, ph = parent.winfo_width(), parent.winfo_height()
    px, py = parent.winfo_rootx(), parent.winfo_rooty()
    dialog.geometry(f"+{px + (pw - w) // 2}+{py + (ph - h) // 2}")


# ── 模組選擇 Dialog ───────────────────────────────────────────────────────────

class ModuleSelectDialog(tk.Toplevel):
    """
    五大 SAP 模組選擇，每列左側有模組專屬色條。
    result: 模組代碼字串（"MM" / "SD" / ...），或 "" 表示取消。
    """
    _HDR_BG  = "#003399"
    _HDR_FG  = "#ffffff"
    _BG      = "#f8f9fb"
    _ROW_BG  = "#ffffff"
    _ROW_HOV = "#eef2fa"

    def __init__(self, parent):
        super().__init__(parent)
        self.result: str = ""
        self.title("開始教學")
        self.resizable(False, False)
        self.configure(bg=self._BG)
        self.transient(parent)
        self.grab_set()
        self._build()
        _center_on_parent(self, parent)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self.wait_window(self)

    def _build(self):
        tk.Frame(self, bg=self._HDR_BG, padx=16, pady=10).pack(fill="x")
        hdr = self.winfo_children()[-1]
        tk.Label(hdr, text="選擇模組", bg=self._HDR_BG, fg=self._HDR_FG,
                 font=("Segoe UI", 12, "bold"), anchor="w").pack(fill="x")

        content = tk.Frame(self, bg=self._BG, padx=14, pady=12)
        content.pack(fill="both", expand=True)

        from sap_skill_library import SAP_MODULES
        for code, info in SAP_MODULES.items():
            color = MODULE_COLORS.get(code, "#888")
            self._row(content, code, info["name"], info["description"], color)

        tk.Frame(self, height=1, bg="#cdd3e0").pack(fill="x")
        btn_bar = tk.Frame(self, bg=self._BG, padx=14, pady=8)
        btn_bar.pack(fill="x")
        tk.Button(btn_bar, text="取消", font=("Segoe UI", 9),
                  bg="#e0e4ef", fg="#333", relief="flat", bd=0,
                  activebackground="#ccd5ea", command=self.destroy,
                  width=8, cursor="hand2").pack(side="right")

    def _row(self, parent, code, name, desc, color):
        outer = tk.Frame(parent, bg=self._ROW_BG, cursor="hand2",
                         relief="solid", bd=1)
        outer.pack(fill="x", pady=(0, 7))

        # 色條 — 保存引用，hover 時絕不碰它
        stripe = tk.Frame(outer, bg=color, width=5)
        stripe.pack(side="left", fill="y")

        inner = tk.Frame(outer, bg=self._ROW_BG, padx=10, pady=9)
        inner.pack(side="left", fill="both", expand=True)

        top = tk.Frame(inner, bg=self._ROW_BG)
        top.pack(fill="x")
        tk.Label(top, text=code, font=("Segoe UI", 11, "bold"),
                 fg=color, bg=self._ROW_BG, width=4, anchor="w").pack(side="left")
        tk.Label(top, text=name, font=("Segoe UI", 10, "bold"),
                 fg="#1a1a1a", bg=self._ROW_BG, anchor="w").pack(side="left")
        tk.Label(inner, text=desc, font=("Segoe UI", 9),
                 fg="#777", bg=self._ROW_BG, anchor="w").pack(fill="x")

        def _select(c=code):
            self.result = c
            self.destroy()

        def _enter(e, i=inner):
            # 只改 inner，色條 stripe 完全不動
            i.configure(bg=self._ROW_HOV)
            for w in _all_children(i):
                _try_bg(w, self._ROW_HOV)

        def _leave(e, i=inner):
            i.configure(bg=self._ROW_BG)
            for w in _all_children(i):
                _try_bg(w, self._ROW_BG)

        # 點擊與 hover 綁定到 outer + inner（跳過 stripe）
        for w in [outer, inner] + _all_children(inner):
            w.bind("<Button-1>", lambda e, fn=_select: fn())
            w.bind("<Enter>", _enter)
            w.bind("<Leave>", _leave)


# ── TCode 查詢 + 輸入 Dialog ──────────────────────────────────────────────────

class SkillSelectDialog(tk.Toplevel):
    """
    列出模組的 TCode 清單（純文字參考），使用者自行輸入想學的 TCode。
    result: goal 字串，或 "" 表示取消。
    """
    _HDR_BG  = "#003399"
    _HDR_FG  = "#ffffff"
    _BG      = "#f8f9fb"

    def __init__(self, parent, module_code: str, module_name: str,
                 tcode_rows: list):
        """
        tcode_rows: list of (tcode, zh_name, has_skill)
        """
        super().__init__(parent)
        self.result: str = ""
        self._entry_var = tk.StringVar()
        self.title("選擇 TCode")
        self.resizable(False, False)
        self.configure(bg=self._BG)
        self.transient(parent)
        self.grab_set()
        self._build(module_code, module_name, tcode_rows)
        _center_on_parent(self, parent)
        self.protocol("WM_DELETE_WINDOW", self.destroy)
        self._entry.focus_set()
        self.bind("<Return>", lambda _: self._confirm())
        self.wait_window(self)

    def _build(self, module_code, module_name, tcode_rows):
        color = MODULE_COLORS.get(module_code, "#003399")

        # 標題
        hdr = tk.Frame(self, bg=self._HDR_BG, padx=16, pady=10)
        hdr.pack(fill="x")
        # 色條放在標題右端
        tk.Label(hdr, text=f"{module_code}", font=("Segoe UI", 13, "bold"),
                 fg=color, bg=self._HDR_BG, anchor="w").pack(side="left")
        tk.Label(hdr, text=f"  {module_name}",
                 font=("Segoe UI", 11, "bold"),
                 fg=self._HDR_FG, bg=self._HDR_BG, anchor="w").pack(side="left")

        content = tk.Frame(self, bg=self._BG, padx=16, pady=10)
        content.pack(fill="both", expand=True)

        # TCode 列表（捲動文字框）
        tk.Label(content, text="可教學的 TCode",
                 font=("Segoe UI", 9, "bold"), fg="#003399",
                 bg=self._BG, anchor="w").pack(fill="x", pady=(0, 4))

        list_frame = tk.Frame(content, bg=self._BG)
        list_frame.pack(fill="x")

        txt = ScrolledText(list_frame, font=("Consolas", 10),
                           bg="#ffffff", fg="#1a1a1a",
                           relief="solid", bd=1,
                           wrap="none", height=min(len(tcode_rows) + 1, 12),
                           state="normal", padx=8, pady=6,
                           cursor="arrow")
        txt.tag_configure("has",  foreground="#1a1a1a")
        txt.tag_configure("miss", foreground="#b0b0b0")
        txt.tag_configure("tick", foreground=color, font=("Consolas", 10, "bold"))
        txt.tag_configure("code", foreground="#003399", font=("Consolas", 10, "bold"))

        for tcode, zh_name, has_skill in tcode_rows:
            if has_skill:
                txt.insert("end", "✓ ", "tick")
                txt.insert("end", f"{tcode:<10}", "code")
                txt.insert("end", f"{zh_name}\n", "has")
            else:
                txt.insert("end", "  ")
                txt.insert("end", f"{tcode:<10}", "miss")
                txt.insert("end", f"{zh_name}\n", "miss")

        # 點擊行 → 填入 input
        def _on_click(event):
            idx = txt.index(f"@{event.x},{event.y}")
            line_start = f"{idx.split('.')[0]}.0"
            line_end   = f"{idx.split('.')[0]}.end"
            line_text  = txt.get(line_start, line_end).strip()
            # 取第一個 token（TCode），去掉 ✓ 前綴
            tokens = line_text.lstrip("✓ ").split()
            if tokens:
                self._entry_var.set(tokens[0])
                self._entry.focus_set()
                self._entry.select_range(0, "end")

        txt.bind("<Button-1>", _on_click)
        txt.config(state="disabled")
        txt.pack(fill="x")

        # 輸入區
        tk.Label(content, text="輸入 TCode 開始教學：",
                 font=("Segoe UI", 9, "bold"), fg="#333",
                 bg=self._BG, anchor="w").pack(fill="x", pady=(12, 4))

        self._entry = tk.Entry(content, textvariable=self._entry_var,
                               font=("Segoe UI", 11), relief="solid", bd=1)
        self._entry.pack(fill="x", ipady=5)

        # 按鈕
        tk.Frame(self, height=1, bg="#cdd3e0").pack(fill="x")
        btn_bar = tk.Frame(self, bg=self._BG, padx=16, pady=10)
        btn_bar.pack(fill="x")

        tk.Button(btn_bar, text="取消", font=("Segoe UI", 9),
                  bg="#e0e4ef", fg="#333", relief="flat", bd=0,
                  activebackground="#ccd5ea", command=self.destroy,
                  width=8, cursor="hand2").pack(side="right", padx=(6, 0))
        tk.Button(btn_bar, text="開始教學", font=("Segoe UI", 9, "bold"),
                  bg="#003399", fg="#fff", relief="flat", bd=0,
                  activebackground="#0044bb", command=self._confirm,
                  width=10, cursor="hand2").pack(side="right")

    def _confirm(self):
        val = self._entry_var.get().strip()
        if val:
            self.result = val
            self.destroy()


# ── 輔助函式 ──────────────────────────────────────────────────────────────────

def _all_children(widget):
    kids = list(widget.winfo_children())
    for k in list(kids):
        kids.extend(_all_children(k))
    return kids


def _try_bg(widget, color):
    try:
        widget.configure(bg=color)
    except tk.TclError:
        pass


class SAPCopilotUI:
    def __init__(self, root):
        self.root = root
        self.root.title(f"SAP GUI Copilot {APP_VERSION}")
        self.root.geometry("640x520")
        self.root.minsize(520, 400)
        self.root.configure(bg=THEME["bg"])
        self._configure_theme()

        self.outbound_queue = queue.Queue()
        self.worker = SAPCopilotWorker(self.outbound_queue)

        self.mode_var = tk.StringVar(value="● Auto Mode")
        self.sap_var = tk.StringVar(value="○ not connected")
        self.model_var = tk.StringVar(value="-")
        self.recording_var = tk.StringVar(value="")
        self.always_on_top_var = tk.BooleanVar(value=True)

        self._build_widgets()
        self.root.attributes("-topmost", True)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.worker.start()
        self.root.after(100, self._poll_worker)

    def _configure_theme(self):
        self.style = ttk.Style(self.root)
        try:
            self.style.theme_use("clam")
        except tk.TclError:
            pass

        
        output_font = ("Consolas", 9)
        default_font = ("Segoe UI", 9, "bold")

        self.root.option_add("*Font", default_font)
        self.root.option_add("*TCombobox*Listbox.font", default_font)

        self.style.configure(".", font=default_font)
        self.style.configure("TFrame", background=THEME["bg"])
        self.style.configure("Panel.TFrame", background=THEME["panel"], relief="flat")
        self.style.configure("Header.TFrame", background=THEME["panel"])
        self.style.configure("Toolbar.TFrame", background=THEME["bg"])
        self.style.configure("Input.TFrame", background=THEME["input_bg"])

        self.style.configure("TLabel", background=THEME["bg"], foreground=THEME["text"])
        self.style.configure("Version.TLabel", background=THEME["panel"], foreground=THEME["muted"], font=("Segoe UI", 7, "bold"))
        self.style.configure("ModeStatus.TLabel", background=THEME["panel"], foreground=THEME["purple"], font=("Segoe UI", 8, "bold"))
        self.style.configure("SapStatus.TLabel", background=THEME["panel"], foreground=THEME["green"], font=("Segoe UI", 8, "bold"))
        self.style.configure("ModelStatus.TLabel", background=THEME["panel"], foreground=THEME["muted"], font=("Segoe UI", 8, "bold"))
        self.style.configure("Recording.TLabel", background=THEME["bg"], foreground=THEME["danger"], font=("Segoe UI", 8, "bold"))

        self.style.configure("TCheckbutton", background=THEME["bg"], foreground=THEME["muted"])
        self.style.configure("TRadiobutton", background=THEME["panel"], foreground=THEME["text"])
        self.style.configure("TLabelFrame", background=THEME["panel"], bordercolor=THEME["border"])
        
        self.style.configure(
            "TEntry",
            fieldbackground=THEME["output_bg"],
            foreground=THEME["text"],
            bordercolor=THEME["border"],
            font=default_font,
        )
        self.output_font = output_font

    # 功能
    def _flat_button(self, parent, text, command, *, fg=None, bg=None, width=None):
        return tk.Button(
            parent,
            text=text,
            command=command,
            font=("Segoe UI", 9, "bold"),
            fg=fg or THEME["text"],
            bg=bg or THEME["button_bg"],
            activeforeground=fg or THEME["text"],
            activebackground=THEME["button_active"],
            relief=tk.FLAT,
            borderwidth=0,
            padx=8,
            pady=5,
            cursor="hand2",
            width=width or 8,
        )

    def _build_widgets(self):
        outer = ttk.Frame(self.root, padding=14, style="TFrame")
        outer.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(outer, padding=(14, 12), style="Header.TFrame")
        header.pack(fill=tk.X, pady=(0, 8))
        title_block = ttk.Frame(header, style="Header.TFrame")
        title_block.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # 標題
        title_row = ttk.Frame(title_block, style="Header.TFrame")
        title_row.pack(anchor=tk.W)
        tk.Label(
            title_row,
            text="SAP GUI Copilot",
            font=("Segoe UI", 20, "bold"),
            fg=THEME["accent"],
            bg=THEME["panel"],
        ).pack(side=tk.LEFT)
        ttk.Label(title_row, text=f" v{APP_VERSION}", style="Version.TLabel").pack(side=tk.LEFT, padx=(10, 0), pady=(20, 0))

        status = ttk.Frame(title_block, style="Header.TFrame")
        status.pack(fill=tk.X, pady=(24, 0))
        ttk.Label(status, textvariable=self.mode_var, style="ModeStatus.TLabel").pack(side=tk.LEFT)
        ttk.Label(status, textvariable=self.sap_var, style="SapStatus.TLabel").pack(side=tk.LEFT, padx=(20, 0))

        # TOP(功能)
        header_actions = ttk.Frame(header, style="Header.TFrame")
        header_actions.pack(side=tk.RIGHT, anchor=tk.N)
        top_button = tk.Checkbutton(
            header_actions,
            text="📌 Top",
            variable=self.always_on_top_var,
            command=self._toggle_topmost,
            indicatoron=False,
            font=("Segoe UI", 10, "bold"),
            fg=THEME["accent_dark"],
            bg=THEME["button_bg"],
            activeforeground=THEME["accent_dark"],
            activebackground=THEME["button_active"],
            selectcolor=THEME["button_bg"],
            relief=tk.FLAT,
            borderwidth=0,
            padx=12,
            pady=9,
            cursor="hand2",
        )
        top_button.pack(side=tk.LEFT, padx=(0, 6))
        self._flat_button(
            header_actions,
            "⟳ Connect",
            self._connect_dialog,
            fg=THEME["blue"],
            width=10,
        ).pack(side=tk.LEFT)
        ttk.Label(header_actions, textvariable=self.model_var, style="ModelStatus.TLabel").pack(anchor=tk.E, pady=(22, 0))

        modes = ttk.Frame(outer, style="Toolbar.TFrame")
        modes.pack(fill=tk.X, pady=(0, 6))
        mode_buttons = [
            ("● Auto", "auto", THEME["purple"]),
            ("● Ask", "ask", THEME["green_dark"]),
        ]
        for label, mode, color in mode_buttons:
            self._flat_button(
                modes,
                label,
                command=lambda item=mode: self.worker.submit("set_mode", mode=item),
                fg=color,
            ).pack(side=tk.LEFT, padx=(0, 4))
        self._flat_button(modes, "▣ Study", self._study_dialog, fg=THEME["blue"]).pack(side=tk.LEFT, padx=(0, 4))
        self._flat_button(modes, "⏺ Record", self._record_dialog, fg=THEME["danger"]).pack(side=tk.LEFT, padx=(0, 4))

        actions = ttk.Frame(outer, style="Toolbar.TFrame")
        actions.pack(fill=tk.X, pady=(0, 8))
        self._flat_button(actions, "🔍 Scan", lambda: self.worker.submit("scan")).pack(side=tk.LEFT, padx=(0, 4))
        self._flat_button(actions, "↺ Reset", lambda: self.worker.submit("reset")).pack(side=tk.LEFT, padx=(0, 4))
        self._flat_button(actions, "■ Stop", lambda: self.worker.submit("stop_record"), fg=THEME["danger"]).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Label(actions, textvariable=self.recording_var, style="Recording.TLabel").pack(side=tk.RIGHT)

        self.output = ScrolledText(
            outer,
            wrap=tk.WORD,
            height=6,
            font=self.output_font,
            bg=THEME["output_bg"],
            fg=THEME["text"],
            insertbackground=THEME["accent"],
            selectbackground=THEME["accent_soft"],
            relief=tk.SOLID,
            borderwidth=1,
            padx=12,
            pady=12,
        )
        self.output.pack(fill=tk.BOTH, expand=True)
        self.output.tag_configure("timestamp", foreground="#B4AAA1", font=self.output_font)
        self.output.tag_configure("you", foreground=THEME["blue"], font=self.output_font)
        self.output.tag_configure("ai", foreground=THEME["green"], font=self.output_font)
        self.output.configure(state=tk.DISABLED)

        input_row = ttk.Frame(outer, padding=(0, 0), style="Input.TFrame")
        input_row.pack(fill=tk.X, pady=(10, 0))
        self.input_text = tk.Text(
            input_row,
            height=2,
            wrap=tk.WORD,
            font=("Segoe UI", 9, "bold"),
            bg=THEME["input_bg"],
            fg=THEME["text"],
            insertbackground=THEME["accent"],
            selectbackground=THEME["accent_soft"],
            relief=tk.FLAT,
            borderwidth=0,
            padx=10,
            pady=10,
        )
        self.input_text.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.input_text.bind("<Control-Return>", lambda _event: self.send())
        self.input_text.bind("<Return>", self._return_key)
        send_button = tk.Button(
            input_row,
            text="發送\n↵",
            command=self.send,
            font=("Segoe UI", 9, "bold"),
            fg="#FFFFFF",
            bg=THEME["accent"],
            activeforeground="#FFFFFF",
            activebackground=THEME["accent_dark"],
            relief=tk.FLAT,
            borderwidth=0,
            padx=14,
            pady=4,
            cursor="hand2",
        )
        send_button.pack(side=tk.RIGHT, fill=tk.Y)

        self._append("UI ready. Use buttons or commands like /scan, /mcp, /ask, /solve, /recordings.\n")

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
        import re as _re
        from sap_skill_library import SAP_MODULES

        # Step 1: 選模組
        mod_dlg = ModuleSelectDialog(self.root)
        module_code = mod_dlg.result
        if not module_code:
            return

        # Step 2: 組裝 TCode 清單
        info = SAP_MODULES.get(module_code, {})
        module_name = info.get("name", module_code)
        lib = self.worker.skill_library

        # suggested_skills 為唯一來源，以 skill 檔是否存在判斷 ✓
        import os as _os
        _skills_dir = (lib.skill_dirs[0][1] if lib else "") or ""

        def _has_skill(tcode: str) -> bool:
            if not _skills_dir:
                return False
            return _os.path.exists(_os.path.join(_skills_dir, f"{tcode}.md"))

        tcode_rows = [
            (tcode, name, _has_skill(tcode))
            for name, tcode, _desc in info.get("suggested_skills", [])
        ]

        skill_dlg = SkillSelectDialog(
            self.root,
            module_code=module_code,
            module_name=module_name,
            tcode_rows=tcode_rows,
        )
        if skill_dlg.result:
            self.worker.submit("study", goal=skill_dlg.result)

    def _connect_dialog(self):
        current = normalize_provider_name(os.getenv("LLM_PROVIDER", "github_copilot"))
        dialog = tk.Toplevel(self.root)
        dialog.title("Connect")
        dialog.configure(bg=THEME["bg"])
        dialog.transient(self.root)
        dialog.grab_set()
        dialog.resizable(False, False)

        provider_var = tk.StringVar(value=current)
        model_var = tk.StringVar()
        endpoint_var = tk.StringVar()
        key_var = tk.StringVar()
        status_var = tk.StringVar()

        outer = ttk.Frame(dialog, padding=14, style="TFrame")
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
        method_text = tk.Text(
            method_box,
            width=72,
            height=8,
            wrap=tk.WORD,
            bg=THEME["output_bg"],
            fg=THEME["text"],
            insertbackground=THEME["accent"],
            relief=tk.FLAT,
            padx=8,
            pady=8,
        )
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

        ttk.Label(outer, textvariable=status_var, style="Subtle.TLabel").pack(fill=tk.X, pady=(8, 0))

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

        ttk.Button(buttons, text="Connect", command=_connect_selected, style="Accent.TButton").pack(side=tk.RIGHT)
        ttk.Button(buttons, text="Cancel", command=dialog.destroy, style="Warm.TButton").pack(side=tk.RIGHT, padx=(0, 8))

        dialog.bind("<Return>", lambda _event: _connect_selected())
        dialog.bind("<Escape>", lambda _event: dialog.destroy())
        dialog.wait_window()

    def send(self):
        text = self.input_text.get("1.0", tk.END).strip()
        if not text:
            return
        self.input_text.delete("1.0", tk.END)
        self.worker.submit("send", text=text)

    def _append(self, text):
        timestamp = time.strftime("%H:%M:%S")
        self.output.configure(state=tk.NORMAL)
        self.output.insert(tk.END, f"[{timestamp}] ", "timestamp")
        line = text.rstrip()
        if line.startswith("You:"):
            self.output.insert(tk.END, "You:", "you")
            self.output.insert(tk.END, line[4:])
        elif line.startswith("AI:"):
            self.output.insert(tk.END, "AI:", "ai")
            self.output.insert(tk.END, line[3:])
        else:
            self.output.insert(tk.END, line)
        self.output.insert(tk.END, "\n\n")
        self.output.see(tk.END)
        self.output.configure(state=tk.DISABLED)

    def _poll_worker(self):
        while True:
            try:
                item = self.outbound_queue.get_nowait()
            except queue.Empty:
                break
            kind = item.get("kind")
            payload = item.get("payload")
            if kind == "log":
                self._append(str(payload))
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
        elif prompt_type == "study_step":
            dlg = StudyStepDialog(
                self.root,
                step_title=payload.get("step_title") or title,
                body=payload.get("body") or message,
                current_value=payload.get("current_value") or "",
                warn=payload.get("warn") or "",
            )
            response = dlg.result
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
        status_icon = "✓" if state.get("ready") else "○"
        self.sap_var.set(f"{status_icon} {tcode}   {client} / {user}" if state.get("ready") else f"{status_icon} {ready}")
        provider = provider_display_name(state.get("provider") or "github_copilot")
        self.model_var.set(state.get("model") or provider or "-")
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
