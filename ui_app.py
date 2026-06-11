"""
SAP GUI Copilot floating UI.

Phase 4 adds a lightweight Tkinter shell around the existing CLI capabilities.
The UI thread only renders widgets; SAP COM and LLM calls run in a worker thread.
"""

import json
import os
import queue
import threading
import time
import tkinter as tk
from tkinter import messagebox, simpledialog, ttk
from tkinter.scrolledtext import ScrolledText

import pythoncom

import llm_brain
from copilot_auth import CopilotAuth
from llm_brain import SAPAgent
from sap_agent_tools import (
    confirmed_click,
    confirmed_handle_popup,
    confirmed_send_vkey,
    scan_sap_screen,
    visualize_element,
)
from sap_core import SAPConnection
from sap_monitor import SAPMonitor
from sap_recorder import SAPRecorder
from sap_skill_library import SAPSkillLibrary
from mcp_client import get_default_sync_client


APP_VERSION = "0.10.0"

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
    allow_draft = env_enabled("STUDY_ALLOW_DRAFT", "false")
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


def _build_ad_hoc_study_context(goal, screen_state):
    return f"""# Ad-hoc Study Goal: {goal}

This is an explicit exploratory draft. No existing SOP or verified skill was found.

## Current SAP Screen
{_screen_brief(screen_state)}

## Coach Rules
- First tell the user this is a draft guide without a recorded SOP.
- Use the current SAP screen as the source of truth.
- Do not present SAP common knowledge guesses as verified facts.
- If the T-Code, field, business meaning, or next action is uncertain, ask the user or recommend recording a real SOP with `/record`.
- You may suggest candidate T-Codes only when clearly labeled as candidates that require user confirmation.
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
        self.emit("log", text)

    def emit_state(self):
        state = {
            "ready": self.ready,
            "mode": self.agent.mode if self.agent else "auto",
            "model": self.agent.model if self.agent else "",
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

    def _connect(self):
        self.log("Initializing Copilot and SAP connection...")
        self.ready = False
        try:
            self.auth = CopilotAuth()
            if not self.auth.is_logged_in():
                self.log("GitHub Copilot is not logged in; starting device login in console...")
                if not self.auth.login():
                    raise RuntimeError("GitHub Copilot login failed")
            self.auth.get_token()

            self.sap = SAPConnection()
            session = self.sap.get_session()
            info = self.sap.get_session_info(session)

            self.agent = SAPAgent(self.auth)
            self.agent._handle_confirmation = self._handle_confirmation_ui
            llm_brain.TOOL_FUNCTIONS["guide_user_action"] = self._guide_user_action_ui
            self.recorder = SAPRecorder()
            self.skill_library = SAPSkillLibrary()
            self.ready = True
            self.log(
                "Connected: "
                f"{info.get('system_name', 'SAP')} client={info.get('client', 'N/A')} "
                f"user={info.get('user', 'N/A')} tcode={info.get('transaction', 'N/A')}"
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
                self._connect()
            elif action == "set_mode":
                self._set_mode(job.get("mode", "auto"))
            elif action == "send":
                self._handle_text(job.get("text", ""))
            elif action == "scan":
                self._scan()
            elif action == "mcp_probe":
                self._mcp_probe()
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

    def _guide_user_action_ui(self, session, element_id: str, instruction: str):
        viz_result = {}
        current_value = ""
        if element_id:
            try:
                viz_result = visualize_element(
                    session,
                    element_id=element_id,
                    duration_seconds=1.2,
                    set_focus=True,
                )
            except Exception as exc:
                viz_result = {"success": False, "error": str(exc)}

            try:
                element = session.FindById(element_id)
                for attr in ("Text", "Key", "Value"):
                    value = getattr(element, attr, None)
                    if value not in (None, ""):
                        current_value = str(value)
                        break
            except Exception:
                pass

        prompt_lines = [instruction]
        if element_id:
            prompt_lines.append(f"\nElement: {element_id}")
        if current_value:
            prompt_lines.append(f"Current value: {current_value}")
        if viz_result and not viz_result.get("success"):
            prompt_lines.append(f"Highlight failed: {viz_result.get('error', '')}")
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
            "element_id": element_id,
            "instruction": instruction,
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
        elif cmd == "/connect":
            self._connect()
        else:
            self.log("Available commands: /scan, /mcp, /record, /stop, /recordings, /play, /study, /ask, /solve, /auto, /reset")

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

    def _run_study(self, goal):
        self._ensure_ready()
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
                    "No SOP / skill found. Study Mode was not started to avoid unverified guidance.\n"
                    "Use /record [name] to capture a real flow, /solve for troubleshooting, "
                    "or /study --draft [goal] for an explicit exploratory draft."
                )
                return
            sop_text = _build_ad_hoc_study_context(goal, initial_screen)
            request = (
                f"No existing SOP / skill was found for '{goal}'. "
                "This is an explicit exploratory draft. Guide from the current SAP screen, "
                "state uncertainty, and finish with a draft summary."
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
        finally:
            self.study_context = ""
            self.agent.set_mode("auto")


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
        outer = ttk.Frame(self.root, padding=10)
        outer.pack(fill=tk.BOTH, expand=True)

        header = ttk.Frame(outer)
        header.pack(fill=tk.X)
        ttk.Label(header, text="SAP GUI Copilot", font=("Segoe UI", 14, "bold")).pack(side=tk.LEFT)
        ttk.Checkbutton(
            header,
            text="Top",
            variable=self.always_on_top_var,
            command=self._toggle_topmost,
        ).pack(side=tk.RIGHT)

        status = ttk.Frame(outer)
        status.pack(fill=tk.X, pady=(8, 8))
        ttk.Label(status, textvariable=self.mode_var, width=14).pack(side=tk.LEFT)
        ttk.Label(status, textvariable=self.sap_var).pack(side=tk.LEFT, padx=(8, 0))
        ttk.Label(status, textvariable=self.model_var).pack(side=tk.RIGHT)

        modes = ttk.Frame(outer)
        modes.pack(fill=tk.X, pady=(0, 8))
        for mode in ("auto", "ask", "solve"):
            ttk.Button(
                modes,
                text=MODE_LABELS[mode],
                command=lambda item=mode: self.worker.submit("set_mode", mode=item),
            ).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(modes, text="Study", command=self._study_dialog).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(modes, text="Connect", command=lambda: self.worker.submit("connect")).pack(side=tk.RIGHT)

        actions = ttk.Frame(outer)
        actions.pack(fill=tk.X, pady=(0, 8))
        ttk.Button(actions, text="Scan", command=lambda: self.worker.submit("scan")).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(actions, text="MCP", command=lambda: self.worker.submit("mcp_probe")).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(actions, text="Reset", command=lambda: self.worker.submit("reset")).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(actions, text="Record", command=self._record_dialog).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(actions, text="Stop", command=lambda: self.worker.submit("stop_record")).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Button(actions, text="SOP", command=lambda: self.worker.submit("recordings")).pack(side=tk.LEFT, padx=(0, 6))
        ttk.Label(actions, textvariable=self.recording_var).pack(side=tk.RIGHT)

        self.output = ScrolledText(outer, wrap=tk.WORD, height=20, font=("Consolas", 10))
        self.output.pack(fill=tk.BOTH, expand=True)
        self.output.configure(state=tk.DISABLED)

        input_row = ttk.Frame(outer)
        input_row.pack(fill=tk.X, pady=(8, 0))
        self.input_text = tk.Text(input_row, height=3, wrap=tk.WORD, font=("Segoe UI", 10))
        self.input_text.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self.input_text.bind("<Control-Return>", lambda _event: self.send())
        self.input_text.bind("<Return>", self._return_key)
        ttk.Button(input_row, text="Send", command=self.send).pack(side=tk.LEFT, padx=(8, 0), fill=tk.Y)

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
        goal = simpledialog.askstring("Study", "SOP name, or --draft goal:", parent=self.root)
        if goal:
            self.worker.submit("study", goal=goal.strip())

    def send(self):
        text = self.input_text.get("1.0", tk.END).strip()
        if not text:
            return
        self.input_text.delete("1.0", tk.END)
        self.worker.submit("send", text=text)

    def _append(self, text):
        timestamp = time.strftime("%H:%M:%S")
        self.output.configure(state=tk.NORMAL)
        self.output.insert(tk.END, f"[{timestamp}] {text.rstrip()}\n\n")
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
        self.mode_var.set(f"Mode: {MODE_LABELS.get(mode, mode)}")
        tcode = state.get("tcode") or "-"
        user = state.get("user") or "-"
        client = state.get("client") or "-"
        self.sap_var.set(f"SAP: {ready} | {tcode} | {client}/{user}")
        self.model_var.set(f"Model: {state.get('model') or '-'}")
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
