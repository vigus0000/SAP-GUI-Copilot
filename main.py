"""
SAP GUI Copilot — CLI 入口 (Phase 4 — Agentic Coach)

互動式 REPL 介面，支援：
- 自然語言指令 → AI 操作 SAP (Auto Mode)
- /scan        → 顯示當前畫面掃描結果
- /connect [provider] → 切換 LLM provider（github_copilot / codex）
- /login       → 重新執行 GitHub Copilot 授權
- /reset       → 重置對話歷史
- /record [名稱] → 開始錄製操作（Record Mode）
- /stop        → 停止錄製，AI 生成自然語言 SOP
- /recordings  → 列出所有已錄製的 SOP
- /play [名稱]  → 顯示指定 SOP 的操作步驟
- /study [名稱]  → AI 教練引導執行既有 SOP / Skill
- /macro       → 執行或引導結構化 Markdown Macro
- /ask         → 切換到 Ask Mode（問答模式）
- /solve       → 切換到 Solve Mode（問題排解模式）
- /auto        → 切換回 Auto Mode（自動代操）
- /mcp         → 檢查 MCP SAP GUI server 狀態
- /quit        → 結束程式
"""

import os
import json
import re
import sys
import time
from datetime import datetime

from copilot_auth import CopilotAuth
from llm_provider import (
    create_llm_provider,
    normalize_provider_name,
    provider_display_name,
)
from sap_core import SAPConnection
from sap_agent_tools import scan_sap_screen
from llm_brain import SAPAgent
from sap_monitor import SAPMonitor
from sap_recorder import SAPRecorder
from sap_knowledge_library import SAPKnowledgeLibrary
from sap_skill_library import SAPSkillLibrary
from sap_macro_library import SAPMacroError, SAPMacroLibrary, macro_result_can_fallback_to_auto
from mcp_client import get_default_sync_client
from sap_table_inspector import format_table_inspection, inspect_current_tables


def env_enabled(name, default="false"):
    return str(os.getenv(name, default)).strip().lower() in {"1", "true", "yes", "y", "on"}


def ensure_llm_provider(provider_name=None):
    """Create and validate the configured LLM provider."""
    normalized = normalize_provider_name(provider_name or os.getenv("LLM_PROVIDER", "github_copilot"))
    auth = None
    print(f"{Colors.CYAN}[1/2] 檢查 LLM Provider: {provider_display_name(normalized)}...{Colors.RESET}")

    if normalized == "github_copilot":
        auth = CopilotAuth()
        if not auth.is_logged_in():
            print(f"{Colors.YELLOW}  尚未登入 GitHub Copilot，開始授權流程{Colors.RESET}")
            success = auth.login()
            if not success:
                raise RuntimeError("GitHub Copilot 授權失敗")
        else:
            print(f"{Colors.GREEN}  ✅ 已登入 GitHub Copilot{Colors.RESET}")
        auth.get_token()
        print(f"{Colors.GREEN}  ✅ Copilot API Token 有效{Colors.RESET}")
    elif normalized == "codex_oauth":
        provider = create_llm_provider(normalized)
        provider.ensure_login()
        print(f"{Colors.GREEN}  ✅ {provider_display_name(normalized)} 授權設定有效{Colors.RESET}")
    else:
        raise RuntimeError(f"不支援的 LLM Provider: {provider_name}")

    os.environ["LLM_PROVIDER"] = normalized
    return auth, normalized


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





# ===== 顏色常數 =====
class Colors:
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[90m"
    RED = "\033[1;31m"
    GREEN = "\033[1;32m"
    YELLOW = "\033[1;33m"
    BLUE = "\033[1;34m"
    MAGENTA = "\033[1;35m"
    CYAN = "\033[1;36m"
    WHITE = "\033[1;37m"
    BG_GREEN = "\033[42m"
    BG_BLUE = "\033[44m"
    BG_RED = "\033[41m"


def print_banner():
    """顯示啟動 Banner"""
    print(f"""
{Colors.CYAN}╔══════════════════════════════════════════════════╗
║                                                  ║
║   🤖 SAP GUI Copilot  V0.13.1 (Stage 2)         ║
║   ─────────────────────────────────────────────   ║
║   用自然語言操作 SAP，告別繁瑣的 T-Code！        ║
║                                                  ║
╚══════════════════════════════════════════════════╝{Colors.RESET}

{Colors.DIM}  指令說明：
    /scan          顯示當前 SAP 畫面掃描結果
    /record [名稱]  開始錄製操作 (Record Mode)
    /stop          停止錄製
    /recordings    列出所有已錄製的 SOP
    /play [名稱]    顯示指定 SOP 的操作步驟
    /study [名稱]   AI 教練引導既有 SOP；--draft 才啟動探索草稿
    /macro         執行或引導結構化 Markdown Macro
    /knowledge      匯入/搜尋/蒸餾 Study Mode knowledge
    /skills         管理蒸餾草稿與正式 skill
    /ask           切換到 Ask Mode（問答模式）
    /solve         切換到 Solve Mode（問題排解模式）
    /auto          切換回 Auto Mode（自動代操）
    /mcp           檢查 MCP SAP GUI server 狀態
    /connect       切換 LLM provider（github_copilot / codex）
    /login         重新執行目前 LLM provider 授權
    /reset         重置對話歷史
    /quit          結束程式{Colors.RESET}
""")


def print_screen_scan(session):
    """掃描並美觀地顯示當前 SAP 畫面"""
    try:
        result = scan_sap_screen(session)
        print(f"\n{Colors.CYAN}── SAP 畫面掃描結果 ──{Colors.RESET}")
        print(f"  {Colors.WHITE}T-Code:{Colors.RESET} {result.get('tcode', 'N/A')}")
        print(f"  {Colors.WHITE}標題:{Colors.RESET}   {result.get('title', 'N/A')}")
        print(f"  {Colors.WHITE}畫面:{Colors.RESET}   {result.get('screen_number', 'N/A')}")

        status = result.get("status_bar", {})
        if status.get("text"):
            color = Colors.RED if status.get("type") == "E" else Colors.GREEN
            print(f"  {Colors.WHITE}狀態列:{Colors.RESET} {color}[{status.get('type', '')}] {status.get('text', '')}{Colors.RESET}")

        elements = result.get("elements", [])
        print(f"\n  {Colors.WHITE}元件數量:{Colors.RESET} {len(elements)}")
        editors = result.get("editors", [])
        if editors:
            print(f"  {Colors.WHITE}Editor:{Colors.RESET}")
            for editor in editors:
                caps = editor.get("editor_capabilities") or {}
                cap_text = ", ".join(k for k, enabled in caps.items() if enabled)
                cap_suffix = f" │ {cap_text}" if cap_text else ""
                print(f"    {Colors.CYAN}{editor.get('id', '')}{Colors.RESET} │ {editor.get('type', '')}{cap_suffix}")
        print(f"  {Colors.DIM}{'─' * 80}{Colors.RESET}")

        for elem in elements:
            etype = elem.get("type", "")
            eid = elem.get("id", "")
            text = elem.get("text", "")
            tooltip = elem.get("tooltip", "")
            changeable = "✏️" if elem.get("changeable") else "  "

            # 根據類型使用不同顏色
            if "Button" in etype:
                color = Colors.YELLOW
            elif "TextField" in etype or "CTextField" in etype:
                color = Colors.GREEN
            elif "Label" in etype:
                color = Colors.DIM
            elif "Tab" in etype:
                color = Colors.MAGENTA
            else:
                color = Colors.RESET

            # 簡短顯示
            display_text = text[:30] + "..." if len(text) > 30 else text
            display_tooltip = f" ({tooltip})" if tooltip else ""
            print(f"  {changeable} {color}{etype:20s}{Colors.RESET} │ {display_text:35s}{Colors.DIM}{display_tooltip}{Colors.RESET}")
            print(f"     {Colors.DIM}{eid}{Colors.RESET}")

        # 顯示彈出視窗
        for key in result:
            if key.startswith("popup_wnd"):
                popup = result[key]
                active = " active" if popup.get("active") else ""
                print(f"\n  {Colors.YELLOW}⚠ 彈出視窗{active}: {popup.get('id', key)} │ {popup.get('title', '')}{Colors.RESET}")
                focused = popup.get("focused_element")
                if focused:
                    print(f"    {Colors.DIM}focused: {focused.get('id')} │ {focused.get('text', '') or focused.get('tooltip', '')}{Colors.RESET}")
                for message in popup.get("messages", []):
                    print(f"    {Colors.RED}message: {message.get('text', '')}{Colors.RESET}")
                for editor in popup.get("editors", []):
                    caps = editor.get("editor_capabilities") or {}
                    cap_text = ", ".join(k for k, enabled in caps.items() if enabled)
                    cap_suffix = f" │ {cap_text}" if cap_text else ""
                    print(f"    {Colors.CYAN}editor: {editor.get('id', '')} │ {editor.get('type', '')}{cap_suffix}{Colors.RESET}")
                for field in popup.get("fields", []):
                    mark = "*" if field.get("focused") else " "
                    label = field.get("label") or field.get("name") or field.get("tooltip") or "(未命名欄位)"
                    value = field.get("value", "")
                    print(f"    {mark} field: {label} │ {field.get('type', '')} │ {value} │ {field.get('id', '')}")
                    options = field.get("options", [])
                    if options:
                        preview = ", ".join(
                            f"{item.get('key') or '?'}={item.get('text')}"
                            for item in options[:8]
                        )
                        more = " ..." if len(options) > 8 else ""
                        print(f"      {Colors.DIM}options: {preview}{more}{Colors.RESET}")
                for table in popup.get("tables", []):
                    print(f"    {Colors.CYAN}table: {table.get('id', '')}{Colors.RESET}")
                    for row in table.get("rows", [])[:12]:
                        row_text = row.get("text", "")
                        if row_text:
                            print(f"      row {row.get('row')}: {row_text}")
                actions = popup.get("actions", [])
                if actions:
                    action_text = ", ".join(f"{item.get('action')}={item.get('id')}" for item in actions)
                    print(f"    {Colors.DIM}actions: {action_text}{Colors.RESET}")
                for elem in popup.get("elements", []):
                    label = elem.get("text") or elem.get("tooltip") or elem.get("name") or ""
                    print(f"    {elem.get('type', ''):20s} │ {label}")

        print(f"  {Colors.DIM}{'─' * 80}{Colors.RESET}")

        # 同時輸出完整 JSON（方便 debug）
        print(f"\n{Colors.DIM}  完整 JSON 已輸出，可用 /scan > file.json 儲存{Colors.RESET}")

    except Exception as e:
        print(f"{Colors.RED}  掃描失敗: {e}{Colors.RESET}")


def print_mcp_diagnostics():
    """顯示 MCP SAP GUI server 啟動與工具診斷。"""
    def console_safe(value):
        text = str(value or "")
        encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
        return text.encode(encoding, errors="replace").decode(encoding, errors="replace")

    print(f"\n{Colors.CYAN}── MCP SAP GUI 診斷 ──{Colors.RESET}")
    client = get_default_sync_client()
    try:
        report = client.probe(attach=True)
    except Exception as exc:
        report = client.launch_summary()
        report["error"] = str(exc)

    command = report.get("command", "")
    args = report.get("args") or []
    command_line = " ".join([command] + [str(arg) for arg in args]).strip()
    print(f"  {Colors.WHITE}Enabled:{Colors.RESET} {report.get('enabled')}")
    print(f"  {Colors.WHITE}Mode:{Colors.RESET} {report.get('mode') or '-'}")
    print(f"  {Colors.WHITE}Command:{Colors.RESET} {command_line or '-'}")
    print(f"  {Colors.WHITE}CWD:{Colors.RESET} {report.get('cwd') or '-'}")
    print(f"  {Colors.WHITE}UV_CACHE_DIR:{Colors.RESET} {report.get('uv_cache_dir') or '-'}")
    print(f"  {Colors.WHITE}Package:{Colors.RESET} {report.get('package') or '-'}")

    available = bool(report.get("available"))
    initialized = bool(report.get("initialized"))
    print(f"  {Colors.WHITE}Prerequisites:{Colors.RESET} {'OK' if available else 'FAILED'}")
    print(f"  {Colors.WHITE}Initialize:{Colors.RESET} {'OK' if initialized else 'FAILED'}")

    attach = report.get("attach") or {}
    if attach:
        attach_text = "OK" if attach.get("attached") else attach.get("reason", "not attached")
        if attach.get("tool"):
            attach_text = f"{attach_text} via {attach.get('tool')}"
        print(f"  {Colors.WHITE}SAP Attach:{Colors.RESET} {attach_text}")

    tools = report.get("tools") or []
    print(f"  {Colors.WHITE}Tool Count:{Colors.RESET} {report.get('tool_count', len(tools))}")
    if tools:
        sample = ", ".join(tools[:12])
        suffix = " ..." if len(tools) > 12 else ""
        print(f"  {Colors.WHITE}Tools:{Colors.RESET} {sample}{suffix}")

    error = report.get("error") or report.get("last_error") or ""
    if error:
        print(f"\n{Colors.YELLOW}  Error:{Colors.RESET}")
        print(f"{Colors.DIM}{console_safe(error)}{Colors.RESET}")

    stderr_tail = report.get("stderr_tail") or ""
    if stderr_tail:
        tail = stderr_tail[-3000:]
        print(f"\n{Colors.YELLOW}  Server stderr tail:{Colors.RESET}")
        print(f"{Colors.DIM}{console_safe(tail)}{Colors.RESET}")
    print()


def print_recordings_list(skill_library):
    """列出所有可用的 SOP / skill。"""
    recordings = skill_library.list_recordings()

    if not recordings:
        print(f"\n{Colors.YELLOW}  📂 尚無 SOP / skill{Colors.RESET}")
        print(f"{Colors.DIM}     使用 /record [名稱] 開始錄製，或將整理好的 skill JSON 放入 skills/{Colors.RESET}\n")
        return

    print(f"\n{Colors.CYAN}── 可用 SOP / Skill ──{Colors.RESET}")
    print(f"  {Colors.DIM}{'─' * 70}{Colors.RESET}")

    for i, rec in enumerate(recordings, 1):
        name = rec.get("name", "未命名")
        created = rec.get("created_at", "N/A")[:19]  # 截短到秒
        events = rec.get("event_count", 0)
        duration = rec.get("duration_seconds", 0)
        summary = rec.get("summary", "")
        source = rec.get("source", "recording")

        print(f"  {Colors.WHITE}{i}. {name}{Colors.RESET}")
        print(f"     {Colors.DIM}來源: {source} │ 時間: {created} │ 操作: {events} 個 │ 持續: {duration:.0f} 秒{Colors.RESET}")
        if summary:
            print(f"     {Colors.DIM}摘要: {summary}{Colors.RESET}")
        print()

    print(f"  {Colors.DIM}{'─' * 70}{Colors.RESET}")
    print(f"  {Colors.DIM}使用 /play [名稱] 檢視步驟，或 /study [名稱] 啟動教練引導{Colors.RESET}")
    print(f"  {Colors.DIM}若無錄製依據但需探索，請明確使用 /study --draft [目標]{Colors.RESET}\n")


def print_recording_steps(skill_library, name):
    """顯示指定 SOP / skill 的操作步驟。"""
    try:
        summary_text = skill_library.get_recording_summary(name)
        print(f"\n{Colors.CYAN}{summary_text}{Colors.RESET}\n")
    except FileNotFoundError:
        print(f"\n{Colors.RED}  找不到 SOP / skill: '{name}'{Colors.RESET}")
        print(f"{Colors.DIM}  使用 /recordings 查看所有可用項目{Colors.RESET}\n")


def _parse_inline_options(text):
    """Parse simple trailing options without forcing shell-like quoting rules."""
    options = {}
    remaining = str(text or "").strip()
    for name in ("--type", "--tags"):
        pattern = re.compile(rf"\s{name}\s+([^\s]+)", re.I)
        match = pattern.search(f" {remaining}")
        if match:
            options[name.lstrip("-").replace("-", "_")] = match.group(1).strip().strip('"')
            remaining = pattern.sub(" ", f" {remaining}", count=1).strip()
    return remaining.strip().strip('"'), options


def print_knowledge_usage():
    print(f"{Colors.YELLOW}  用法:{Colors.RESET}")
    print(f"{Colors.DIM}    /knowledge import <path-or-url> [--type official|company|community] [--tags tag1,tag2]{Colors.RESET}")
    print(f"{Colors.DIM}    /knowledge search <query>{Colors.RESET}")
    print(f"{Colors.DIM}    /knowledge distill <path-or-query>{Colors.RESET}")
    print(f"{Colors.DIM}    /knowledge rebuild{Colors.RESET}")
    print(f"{Colors.DIM}    /skills drafts{Colors.RESET}")
    print(f"{Colors.DIM}    /skills promote <draft-relative-path-or-title>{Colors.RESET}\n")


def print_knowledge_entries(entries, title="Knowledge Results"):
    if not entries:
        print(f"{Colors.YELLOW}  無結果{Colors.RESET}")
        return
    print(f"\n{Colors.CYAN}── {title} ──{Colors.RESET}")
    for index, item in enumerate(entries, 1):
        source = item.get("source_url") or item.get("source_path") or item.get("relative_path") or item.get("path") or ""
        print(
            f"  {Colors.WHITE}{index}. {item.get('document_title') or item.get('title')}{Colors.RESET} "
            f"{Colors.DIM}[{item.get('module', '')} > {item.get('business_cycle', '')}] "
            f"confidence={item.get('confidence', '')} score={item.get('score', '-')}{Colors.RESET}"
        )
        if item.get("tcode"):
            print(f"     {Colors.DIM}T-Code: {', '.join(item.get('tcode', []))}{Colors.RESET}")
        if source:
            print(f"     {Colors.DIM}{source}{Colors.RESET}")
        summary = item.get("summary", "")
        if summary:
            print(f"     {Colors.DIM}{summary[:180]}{Colors.RESET}")
    print()


def handle_knowledge_command(knowledge_library, arg):
    parts = str(arg or "").strip().split(maxsplit=1)
    if not parts:
        print_knowledge_usage()
        return
    subcommand = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""

    try:
        if subcommand == "import":
            source, options = _parse_inline_options(rest)
            if not source:
                print_knowledge_usage()
                return
            entries = knowledge_library.import_source(
                source,
                source_type=options.get("type", ""),
                tags=options.get("tags", ""),
            )
            print(f"{Colors.GREEN}  ✅ 已匯入 {len(entries)} 個階層節點{Colors.RESET}")
            print_knowledge_entries(entries[:8], "Imported Knowledge")
        elif subcommand == "search":
            if not rest:
                print_knowledge_usage()
                return
            print_knowledge_entries(
                knowledge_library.search(rest, include_drafts=True),
                "Knowledge Search",
            )
        elif subcommand == "distill":
            if not rest:
                print_knowledge_usage()
                return
            drafts = knowledge_library.distill(rest)
            if not drafts:
                print(f"{Colors.YELLOW}  沒有可蒸餾的 evidence{Colors.RESET}")
                return
            print(f"{Colors.GREEN}  ✅ 已產生 {len(drafts)} 份 draft skill{Colors.RESET}")
            for item in drafts:
                print(
                    f"  {Colors.WHITE}{item.get('document_title')}{Colors.RESET} "
                    f"{Colors.DIM}[{item.get('module')} > {item.get('business_cycle')}] "
                    f"{item.get('relative_path')}{Colors.RESET}"
                )
            print()
        elif subcommand == "rebuild":
            index_path = knowledge_library.rebuild()
            print(f"{Colors.GREEN}  ✅ Knowledge index 已重建: {index_path}{Colors.RESET}\n")
        else:
            print_knowledge_usage()
    except Exception as exc:
        print(f"{Colors.RED}  Knowledge 指令失敗: {exc}{Colors.RESET}\n")


def handle_skills_command(skill_library, knowledge_library, arg):
    parts = str(arg or "").strip().split(maxsplit=1)
    if not parts:
        print_knowledge_usage()
        return
    subcommand = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""

    try:
        if subcommand == "drafts":
            drafts = knowledge_library.list_drafts()
            if not drafts:
                print(f"{Colors.YELLOW}  目前沒有 draft skill{Colors.RESET}\n")
                return
            print(f"\n{Colors.CYAN}── Draft Skills ──{Colors.RESET}")
            for index, draft in enumerate(drafts, 1):
                print(
                    f"  {Colors.WHITE}{index}. {draft.get('title')}{Colors.RESET} "
                    f"{Colors.DIM}[{draft.get('module')} > {draft.get('business_cycle')}]{Colors.RESET}"
                )
                print(f"     {Colors.DIM}{draft.get('relative_path')}{Colors.RESET}")
            print()
        elif subcommand == "promote":
            if not rest:
                print_knowledge_usage()
                return
            promoted_path = knowledge_library.promote_draft(rest)
            skill_library.rebuild_index()
            print(f"{Colors.GREEN}  ✅ Draft 已提升為正式 skill: {promoted_path}{Colors.RESET}\n")
        else:
            print_knowledge_usage()
    except Exception as exc:
        print(f"{Colors.RED}  Skills 指令失敗: {exc}{Colors.RESET}\n")


def print_macro_usage():
    print(f"{Colors.YELLOW}  用法:{Colors.RESET}")
    print(f"{Colors.DIM}    /macros{Colors.RESET}")
    print(f"{Colors.DIM}    /macro list{Colors.RESET}")
    print(f"{Colors.DIM}    /macro show <macro-name>{Colors.RESET}")
    print(f"{Colors.DIM}    /macro run <macro-name> key=value ...{Colors.RESET}")
    print(f"{Colors.DIM}    /macro study <macro-name> key=value ...{Colors.RESET}")
    print(f"{Colors.DIM}    /macro learn recordings{Colors.RESET}")
    print(f"{Colors.DIM}    /macro audit <macro-name>{Colors.RESET}")
    print(f"{Colors.DIM}    /macro doctor <macro-name>{Colors.RESET}")
    print(f"{Colors.DIM}    /study --macro <macro-name> key=value ...{Colors.RESET}\n")


def print_macros_list(macro_library):
    macros = macro_library.list_macros()
    if not macros:
        print(f"\n{Colors.YELLOW}  尚無 Macro{Colors.RESET}")
        print(f"{Colors.DIM}     請將指定格式的 Markdown 放入 macros/，再使用 /macro run 或 /macro study{Colors.RESET}\n")
        return

    print(f"\n{Colors.CYAN}── 可用 Macro ──{Colors.RESET}")
    for index, item in enumerate(macros, 1):
        tags = ", ".join(item.get("tags") or [])
        tag_text = f" │ {tags}" if tags else ""
        print(
            f"  {Colors.WHITE}{index}. {item.get('name')}{Colors.RESET} "
            f"{Colors.DIM}[{item.get('mode')}] inputs={item.get('input_count')} "
            f"steps={item.get('step_count')}{tag_text}{Colors.RESET}"
        )
        if item.get("description"):
            print(f"     {Colors.DIM}{item.get('description')}{Colors.RESET}")
        print(f"     {Colors.DIM}{item.get('filepath')}{Colors.RESET}")
    print()


def _prompt_macro_input(item):
    label = f" ({item.label})" if item.label else ""
    default_text = f" [{item.default}]" if item.default else ""
    value = input(f"{Colors.CYAN}  Macro input {item.name}{label}{default_text} > {Colors.RESET}").strip()
    return value or item.default


def _prepare_macro_inputs(macro_library, macro, values):
    return macro_library.prompt_missing_inputs(
        macro,
        values,
        prompt_callback=_prompt_macro_input,
    )


def start_macro_study(agent, sap, macro_library, macro_name, values):
    macro = macro_library.load_macro(macro_name)
    runtime_values = _prepare_macro_inputs(macro_library, macro, values)
    sop_text = macro_library.format_study_context(macro, runtime_values)

    print(f"\n{Colors.CYAN}📘 Study Macro: {macro.name}{Colors.RESET}")
    if macro.mode == "auto":
        print(f"{Colors.YELLOW}  注意: 此 Macro 標記為 auto；Study 會只用它作為參考引導。{Colors.RESET}")
    print(f"{Colors.DIM}  Macro 只作為結構化參考；Study Mode 仍會逐步引導使用者操作。{Colors.RESET}\n")

    agent.set_mode("study")
    session = sap.get_session()
    response = agent.process_message(
        session,
        "請根據以下 Structured Macro，從第一步開始用互動式教練方式引導我完成操作。",
        extra_context=sop_text,
    )
    print(f"\n{Colors.MAGENTA}  AI > {Colors.RESET}{response}\n")
    print(f"{Colors.CYAN}  📘 Study Mode 仍保持啟用；輸入 /auto、/ask 或 /solve 可切換模式。{Colors.RESET}\n")


def run_macro(agent, sap, macro_library, macro_name, values, announce_completion=True):
    macro = macro_library.load_macro(macro_name)
    if macro.mode == "study":
        raise SAPMacroError("此 Macro 標記為 mode=study，不能用 /macro run 直接操作；請改用 /macro study 或 /study --macro")
    runtime_values = _prepare_macro_inputs(macro_library, macro, values)
    print(f"\n{Colors.CYAN}▶ Macro Run: {macro.name}{Colors.RESET}")
    print(f"{Colors.DIM}  共 {len(macro.steps)} 個步驟；動態輸入: {json.dumps(runtime_values, ensure_ascii=False)}{Colors.RESET}")
    session = sap.get_session()
    result = macro_library.execute_macro(
        session,
        agent,
        macro,
        runtime_values,
        log_callback=lambda text: print(f"{Colors.DIM}  {text}{Colors.RESET}"),
    )
    result["runtime_values"] = runtime_values
    if announce_completion:
        if result.get("success"):
            print(f"{Colors.GREEN}  ✅ Macro 執行完成: {macro.name}{Colors.RESET}\n")
        else:
            print(f"{Colors.RED}  ❌ Macro 執行完成但有失敗步驟: {macro.name}{Colors.RESET}\n")
    else:
        if result.get("success"):
            print(f"{Colors.GREEN}  Macro strict flow 已結束: {macro.name}{Colors.RESET}")
        else:
            print(f"{Colors.RED}  Macro strict flow 有失敗步驟: {macro.name}{Colors.RESET}")
    return result


def macro_post_react_verify(agent, sap, macro, runtime_values, user_input, result):
    if not env_enabled("SAP_MACRO_POST_REACT_VERIFY", "true"):
        return ""
    summary = {
        "macro": macro.name,
        "runtime_values": runtime_values,
        "macro_success": bool(result.get("success")),
        "failed_step": result.get("failed_step"),
        "step_count": result.get("step_count"),
    }
    prompt = (
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
        f"Original user goal:\n{user_input}\n\n"
        f"Macro summary:\n{json.dumps(summary, ensure_ascii=False)}"
    )
    print(f"{Colors.DIM}  Macro post-check: running Auto ReAct verification before final completion.{Colors.RESET}")
    response = agent.process_message(sap.get_session(), prompt)
    print(f"\n{Colors.MAGENTA}  AI > {Colors.RESET}{response}\n")
    return response


def try_run_matched_macro(agent, sap, macro_library, user_input):
    if agent.mode != "auto":
        return False
    match = macro_library.match_request(user_input)
    if not match:
        return False
    macro = match["macro"]
    reasons = ", ".join(match.get("reasons") or [])
    print(
        f"{Colors.CYAN}  ⚡ Macro matched: {macro.name} "
        f"(score={match.get('score')}, reasons={reasons}){Colors.RESET}"
    )
    print(f"{Colors.DIM}     將直接走 Macro 嚴格流程；不進入 ReAct 迭代。{Colors.RESET}")
    result = run_macro(agent, sap, macro_library, macro.name, match.get("values") or {}, announce_completion=False)
    if macro_result_can_fallback_to_auto(result):
        print(f"{Colors.YELLOW}  Macro 在尚未寫入欄位/按鈕前失敗，改回 Auto ReAct fallback。{Colors.RESET}\n")
        return False
    if result.get("success"):
        runtime_values = result.get("runtime_values") or match.get("values") or {}
        macro_post_react_verify(agent, sap, macro, runtime_values, user_input, result)
    return True


def handle_macro_command(macro_library, agent, sap, arg):
    parts = str(arg or "").strip().split(maxsplit=1)
    if not parts:
        print_macro_usage()
        return
    subcommand = parts[0].lower()
    rest = parts[1].strip() if len(parts) > 1 else ""

    try:
        if subcommand in {"list", "ls"}:
            print_macros_list(macro_library)
        elif subcommand == "show":
            macro_name, _values = macro_library.parse_values(rest)
            if not macro_name:
                print_macro_usage()
                return
            macro = macro_library.load_macro(macro_name)
            print(f"\n{Colors.CYAN}── Macro Detail ──{Colors.RESET}")
            print(macro_library.format_summary(macro))
            print()
        elif subcommand == "run":
            macro_name, values = macro_library.parse_values(rest)
            if not macro_name:
                print_macro_usage()
                return
            run_macro(agent, sap, macro_library, macro_name, values)
        elif subcommand == "study":
            macro_name, values = macro_library.parse_values(rest)
            if not macro_name:
                print_macro_usage()
                return
            start_macro_study(agent, sap, macro_library, macro_name, values)
        elif subcommand == "learn":
            target = rest or "recordings"
            if target.lower() != "recordings":
                raise SAPMacroError("目前 /macro learn 僅支援 recordings")
            summary = macro_library.learn_from_recordings("recordings")
            print(f"\n{Colors.CYAN}── Macro Learn Recordings ──{Colors.RESET}")
            print(json.dumps(summary, ensure_ascii=False, indent=2))
            print()
        elif subcommand == "audit":
            macro_name, _values = macro_library.parse_values(rest)
            if not macro_name:
                print_macro_usage()
                return
            print()
            print(macro_library.audit_macro(macro_name))
            print()
        elif subcommand == "doctor":
            macro_name, _values = macro_library.parse_values(rest)
            if not macro_name:
                print_macro_usage()
                return
            print()
            print(macro_library.doctor_macro(sap.get_session(), agent, macro_name))
            print()
        else:
            macro_name, values = macro_library.parse_values(str(arg or ""))
            if macro_name:
                run_macro(agent, sap, macro_library, macro_name, values)
            else:
                print_macro_usage()
    except (FileNotFoundError, SAPMacroError) as exc:
        print(f"{Colors.RED}  Macro 指令失敗: {exc}{Colors.RESET}\n")
    except Exception as exc:
        import traceback
        print(f"{Colors.RED}  Macro 執行例外: {exc}{Colors.RESET}")
        print(f"{Colors.DIM}{traceback.format_exc()}{Colors.RESET}\n")


def _screen_brief(screen_state):
    """Build a compact screen summary for learned Study Mode skills."""
    if not screen_state:
        return "- 無法取得啟動時畫面狀態"

    lines = [
        f"- T-Code: {screen_state.get('tcode') or 'N/A'}",
        f"- 畫面標題: {screen_state.get('title') or 'N/A'}",
        f"- Screen: {screen_state.get('screen_number') or 'N/A'}",
    ]
    fields = screen_state.get("fields", [])[:12]
    if fields:
        lines.append("- 可見欄位:")
        for field in fields:
            label = field.get("label") or field.get("name") or field.get("tooltip") or field.get("id", "")
            value = field.get("value", "")
            value_text = f" = {value}" if value else ""
            lines.append(f"  - {label}{value_text} ({field.get('id', '')})")
    popup = screen_state.get("active_popup")
    if popup:
        lines.append(f"- 活動彈窗: {popup.get('title', '')} ({popup.get('id', '')})")
    return "\n".join(lines)


def build_ad_hoc_study_context(goal, screen_state, evidence_pack=""):
    """Create a Study Mode context when no recorded skill exists yet."""
    return f"""# 即席 Study 任務: {goal}

## 狀態
目前沒有同名 SOP / skill。這是使用者明確要求的探索草稿，不是正式 SOP，也不是已驗證流程。

## 教學目標
{goal}

## 目前 SAP 畫面摘要
{_screen_brief(screen_state)}

## Evidence Pack
{evidence_pack or "- 尚未建立 evidence pack。"}

## 教練要求
- 先明確告知使用者：目前沒有錄製依據，以下內容只能作為探索草稿。
- 依證據優先級判斷：正式 skill/recording > promoted skill > draft > knowledge 文件 > 官方搜尋 > 社群/一般搜尋 > LLM prior。
- 候選流程必須標示 module、business_cycle、來源與信心；不要把未驗證推測說成已確認事實。
- 如果不確定 T-Code、欄位位置、資料意義或下一步，請先問使用者確認第一個關鍵 T-Code / 流程方向，或建議改用 `/record` 錄製一次正式流程。
- 每次只引導一個步驟，優先使用 `guide_user_action` 高亮欄位或按鈕。
- 不要替使用者寫入資料；由使用者在 SAP GUI 操作後確認。
- 若資訊不足，先提出最小必要問題或引導使用者確認目前畫面，不要硬猜。
- 完成本次教學後，請輸出一段「草稿」摘要，並列出哪些步驟仍需要錄製或人工驗證。
"""


def extract_study_guidance_steps(agent):
    """Extract guide_user_action tool calls from the latest Study Mode session."""
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
                suffix = f"（元件 ID: `{element_id}`）" if element_id else ""
                steps.append(f"{len(steps) + 1}. {instruction}{suffix}")
    return steps


def build_learned_skill_markdown(name, response, guidance_steps, initial_screen):
    """Persist an ad-hoc Study Mode session as a reusable Markdown skill."""
    lines = [
        f"# SOP: {name}",
        "",
        "## 目的",
        f"引導使用者完成「{name}」。此 skill 由 `/study --save-draft` 探索草稿保存而來，仍需依實際 SAP 操作驗證。",
        "",
        "## 初始畫面參考",
        _screen_brief(initial_screen),
        "",
        "## 操作步驟",
    ]

    if guidance_steps:
        lines.extend(guidance_steps)
    else:
        lines.append("1. 依目前 SAP 畫面狀態，由 Study Mode 教練判斷下一步並高亮提示使用者操作。")

    lines.extend([
        "",
        "## 教練輸出摘要",
        response.strip() if response else "本次教學未產生最終文字摘要。",
        "",
        "## 使用原則",
        "- 本 skill 是參考資料，不是絕對腳本；Study Mode 應優先依照目前 SAP 畫面狀態引導。",
        "- 若欄位已有目前值，先請使用者確認是否沿用，不要要求重打舊值。",
        "- 若畫面、T-Code 或欄位與本草稿不同，請依目前畫面調整教學步驟。",
        "",
        f"建立時間: {datetime.now().isoformat(timespec='seconds')}",
    ])
    return "\n".join(lines)





def get_mode_indicator(agent_mode, recorder):
    """取得模式指示器"""
    if recorder.is_recording:
        return f"{Colors.RED}🔴 REC{Colors.RESET}"
    elif agent_mode == "study":
        return f"{Colors.CYAN}📘 STUDY{Colors.RESET}"
    elif agent_mode == "ask":
        return f"{Colors.GREEN}🟢 ASK{Colors.RESET}"
    elif agent_mode == "solve":
        return f"{Colors.YELLOW}🟡 SOLVE{Colors.RESET}"
    else:
        return f"{Colors.MAGENTA}🟣 AUTO{Colors.RESET}"


def main():
    """主程式入口"""
    if any(arg.lower() in {"--ui", "ui", "/ui"} for arg in sys.argv[1:]):
        from ui_app import main as ui_main
        return ui_main()

    print_banner()

    # ===== Step 1: LLM Provider 認證 =====
    try:
        auth, llm_provider_name = ensure_llm_provider()
    except RuntimeError as exc:
        print(f"{Colors.RED}  ❌ LLM Provider 初始化失敗: {exc}{Colors.RESET}")
        sys.exit(1)

    # ===== Step 2: 連接 SAP GUI =====
    print(f"\n{Colors.CYAN}[2/2] 連接 SAP GUI...{Colors.RESET}")
    sap = SAPConnection()

    try:
        session = sap.get_session()
        info = sap.get_session_info(session)
        print(f"{Colors.GREEN}  ✅ SAP 連線成功{Colors.RESET}")
        print(f"  {Colors.DIM}系統: {info.get('system_name', 'N/A')} | "
              f"Client: {info.get('client', 'N/A')} | "
              f"使用者: {info.get('user', 'N/A')} | "
              f"T-Code: {info.get('transaction', 'N/A')}{Colors.RESET}")
    except ConnectionError as e:
        print(f"{Colors.RED}  ❌ SAP 連線失敗: {e}{Colors.RESET}")
        print(f"{Colors.YELLOW}  請先執行 sap_login.py 登入 SAP GUI{Colors.RESET}")
        sys.exit(1)

    # ===== 初始化 Agent、Monitor、Recorder =====
    agent = SAPAgent(auth=auth, provider_name=llm_provider_name)
    recorder = SAPRecorder()
    skill_library = SAPSkillLibrary()
    knowledge_library = SAPKnowledgeLibrary()
    macro_library = SAPMacroLibrary()

    # 初始化 Monitor（但不啟動，等使用者開始錄製時才啟動）
    monitor = None  # 延遲建立，因為 session 可能隨時需要刷新

    print(f"\n{Colors.GREEN}{'═' * 50}{Colors.RESET}")
    print(f"{Colors.GREEN}  🚀 SAP GUI Copilot 已就緒！{Colors.RESET}")
    print(f"{Colors.GREEN}  📍 當前模式: {Colors.MAGENTA}🟣 Auto Mode（自動代操）{Colors.RESET}")
    print(f"{Colors.GREEN}  🧠 LLM Provider: {Colors.WHITE}{provider_display_name(agent.provider_name)} / {agent.model}{Colors.RESET}")
    print(f"{Colors.GREEN}{'═' * 50}{Colors.RESET}\n")

    # ===== REPL 迴圈 =====
    while True:
        try:
            mode_indicator = get_mode_indicator(agent.mode, recorder)
            user_input = input(f"  {mode_indicator} {Colors.BLUE}You > {Colors.RESET}").strip()

            if not user_input:
                continue

            # ===== 指令處理 =====
            cmd = user_input.lower()

            # --- /quit ---
            if cmd == "/quit":
                # 確保停止錄製與監控
                if recorder.is_recording:
                    print(f"{Colors.YELLOW}  正在停止錄製...{Colors.RESET}")
                    recorder.stop_recording()
                if monitor and monitor.is_running:
                    monitor.stop()
                print(f"\n{Colors.DIM}  👋 再見！{Colors.RESET}\n")
                break

            # --- /scan ---
            elif cmd == "/scan":
                try:
                    session = sap.get_session()
                    print_screen_scan(session)
                except ConnectionError as e:
                    print(f"{Colors.RED}  SAP 連線已斷開: {e}{Colors.RESET}")

            elif cmd == "/inspect" or cmd.startswith("/inspect "):
                parts = user_input.split(maxsplit=1)
                target = parts[1].strip().lower() if len(parts) > 1 else "table"
                if target not in {"table", "tables"}:
                    print(f"{Colors.YELLOW}  Usage: /inspect table{Colors.RESET}")
                    continue
                try:
                    session = sap.get_session()
                    report = inspect_current_tables(
                        session=session,
                        max_depth=10,
                        max_rows=10,
                        include_rows=False,
                        use_focus=True,
                    )
                    print(f"\n{Colors.CYAN}{format_table_inspection(report)}{Colors.RESET}\n")
                except ConnectionError as e:
                    print(f"{Colors.RED}  SAP connection failed: {e}{Colors.RESET}")

            # --- /connect [github_copilot|codex] ---
            elif cmd == "/connect" or cmd.startswith("/connect "):
                parts = user_input.split(maxsplit=1)
                requested = parts[1].strip() if len(parts) > 1 else agent.provider_name
                previous_mode = agent.mode
                try:
                    auth, llm_provider_name = ensure_llm_provider(requested)
                    agent = SAPAgent(auth=auth, provider_name=llm_provider_name)
                    agent.set_mode(previous_mode)
                    print(
                        f"{Colors.GREEN}  ✅ 已切換 LLM Provider: "
                        f"{provider_display_name(agent.provider_name)} / {agent.model}{Colors.RESET}"
                    )
                except Exception as exc:
                    print(f"{Colors.RED}  LLM Provider 切換失敗: {exc}{Colors.RESET}")

            # --- /login ---
            elif cmd == "/login":
                if agent.provider_name == "github_copilot":
                    if auth is None:
                        auth = CopilotAuth()
                    auth.login()
                    agent = SAPAgent(auth=auth, provider_name="github_copilot")
                    print(f"{Colors.GREEN}  ✅ GitHub Copilot 已重新授權{Colors.RESET}")
                elif agent.provider_name == "codex_oauth":
                    provider = create_llm_provider(agent.provider_name)
                    login = getattr(provider, "login", None)
                    if not callable(login):
                        raise RuntimeError("目前 Codex provider 不支援 OAuth login")
                    login()
                    agent = SAPAgent(provider_name="codex_oauth")
                    print(f"{Colors.GREEN}  ✅ Codex OAuth 已重新授權{Colors.RESET}")
                else:
                    print(f"{Colors.YELLOW}  目前 provider 不支援互動式登入；請使用 /connect github_copilot 或 /connect codex。{Colors.RESET}")

            # --- /reset ---
            elif cmd == "/reset":
                agent.reset_conversation()
                print(f"{Colors.GREEN}  ✅ 對話歷史已重置{Colors.RESET}")

            # --- /record [名稱] ---
            elif cmd == "/record" or cmd.startswith("/record "):
                parts = user_input.split(maxsplit=1)
                if len(parts) < 2 or not parts[1].strip():
                    print(f"{Colors.YELLOW}  用法: /record [SOP名稱]{Colors.RESET}")
                    print(f"{Colors.DIM}  例如: /record 建立銷售訂單{Colors.RESET}")
                    continue

                sop_name = parts[1].strip()

                try:
                    # 確保 SAP Session 可用
                    session = sap.get_session()

                    # 開始錄製
                    recorder.start_recording(sop_name)

                    # 建立並啟動 Monitor
                    monitor = SAPMonitor(session, poll_interval=0.3)
                    monitor.on_event = recorder.add_event
                    monitor.start()

                except ConnectionError as e:
                    print(f"{Colors.RED}  SAP 連線已斷開: {e}{Colors.RESET}")
                except RuntimeError as e:
                    print(f"{Colors.RED}  {e}{Colors.RESET}")

            # --- /stop ---
            elif cmd == "/stop":
                if not recorder.is_recording:
                    print(f"{Colors.YELLOW}  目前沒有正在進行的錄製{Colors.RESET}")
                    continue

                # 停止 Monitor
                if monitor and monitor.is_running:
                    monitor.stop()
                    monitor = None

                # 取得錄製名稱（stop 後會被清除）
                rec_name = recorder.recording_name

                # 停止 Recorder 並儲存 JSON
                try:
                    filepath = recorder.stop_recording()
                except RuntimeError as e:
                    print(f"{Colors.RED}  {e}{Colors.RESET}")
                    continue

                # 使用 LLM 生成自然語言 SOP 並存入 ./skills/
                print(f"{Colors.CYAN}  🧠 正在使用 AI 生成自然語言 SOP...{Colors.RESET}")
                try:
                    rec_data = recorder.load_recording(rec_name) if rec_name else None
                    if rec_data:
                        screen_state = None
                        try:
                            session = sap.get_session()
                            screen_state = scan_sap_screen(session)
                        except Exception:
                            pass
                        sop_path = recorder.generate_sop_with_llm(
                            rec_name,
                            rec_data.get("events", []),
                            auth,
                            screen_state=screen_state,
                            provider=agent.provider,
                        )
                        if sop_path:
                            print(f"{Colors.GREEN}  ✅ 自然語言 SOP 已儲存，可用 /study {rec_name} 啟動教練引導{Colors.RESET}")
                        else:
                            print(f"{Colors.YELLOW}  ⚠ AI SOP 生成失敗，但 JSON 錄製已保存: {filepath}{Colors.RESET}")
                except Exception as e:
                    print(f"{Colors.YELLOW}  ⚠ AI SOP 生成過程出錯: {e}{Colors.RESET}")
                    print(f"{Colors.DIM}     JSON 錄製仍可使用: {filepath}{Colors.RESET}")

            # --- /recordings ---
            elif cmd == "/recordings":
                print_recordings_list(skill_library)

            # --- /play [名稱] ---
            elif cmd == "/play" or cmd.startswith("/play "):
                parts = user_input.split(maxsplit=1)
                if len(parts) < 2 or not parts[1].strip():
                    print(f"{Colors.YELLOW}  用法: /play [SOP名稱]{Colors.RESET}")
                    print(f"{Colors.DIM}  使用 /recordings 查看所有錄製{Colors.RESET}")
                    continue

                sop_name = parts[1].strip()
                print_recording_steps(skill_library, sop_name)

            # --- /knowledge ... ---
            elif cmd == "/knowledge" or cmd.startswith("/knowledge "):
                parts = user_input.split(maxsplit=1)
                handle_knowledge_command(
                    knowledge_library,
                    parts[1].strip() if len(parts) > 1 else "",
                )

            # --- /skills ... ---
            elif cmd == "/skills" or cmd.startswith("/skills "):
                parts = user_input.split(maxsplit=1)
                handle_skills_command(
                    skill_library,
                    knowledge_library,
                    parts[1].strip() if len(parts) > 1 else "",
                )

            # --- /macro ... ---
            elif cmd == "/macros":
                print_macros_list(macro_library)

            elif cmd == "/macro" or cmd.startswith("/macro "):
                parts = user_input.split(maxsplit=1)
                handle_macro_command(
                    macro_library,
                    agent,
                    sap,
                    parts[1].strip() if len(parts) > 1 else "",
                )

            # --- /study [名稱] ---
            elif cmd == "/study" or cmd.startswith("/study "):
                if recorder.is_recording:
                    print(f"{Colors.YELLOW}  請先 /stop 結束錄製，再執行 /study{Colors.RESET}")
                    continue

                parts = user_input.split(maxsplit=1)
                if len(parts) < 2 or not parts[1].strip():
                    print(f"{Colors.YELLOW}  用法: /study [SOP名稱]{Colors.RESET}")
                    print(f"{Colors.DIM}       /study --draft [教學目標]      明確啟動探索草稿，不保存 skill{Colors.RESET}")
                    print(f"{Colors.DIM}       /study --save-draft [教學目標] 明確啟動探索草稿並保存為 skill{Colors.RESET}")
                    print(f"{Colors.DIM}       /study --macro [Macro名稱] key=value ...  使用結構化 Macro 引導{Colors.RESET}")
                    print(f"{Colors.DIM}  使用 /recordings 查看所有錄製{Colors.RESET}")
                    continue

                raw_study_arg = parts[1].strip()
                if raw_study_arg.lower().startswith(("--macro ", "/macro ")):
                    macro_arg = raw_study_arg.split(maxsplit=1)[1].strip() if len(raw_study_arg.split(maxsplit=1)) > 1 else ""
                    macro_name, macro_values = macro_library.parse_values(macro_arg)
                    if not macro_name:
                        print_macro_usage()
                        continue
                    try:
                        start_macro_study(agent, sap, macro_library, macro_name, macro_values)
                    except Exception as exc:
                        import traceback
                        print(f"{Colors.RED}  Macro Study 錯誤: {exc}{Colors.RESET}")
                        print(f"{Colors.DIM}{traceback.format_exc()}{Colors.RESET}\n")
                    continue

                sop_name, allow_draft_study, save_draft_skill = parse_study_request(parts[1].strip())
                if not sop_name:
                    print(f"{Colors.YELLOW}  用法: /study [SOP名稱]{Colors.RESET}")
                    print(f"{Colors.DIM}       /study --draft [教學目標]      明確啟動探索草稿，不保存 skill{Colors.RESET}")
                    print(f"{Colors.DIM}       /study --save-draft [教學目標] 明確啟動探索草稿並保存為 skill{Colors.RESET}")
                    print(f"{Colors.DIM}       /study --macro [Macro名稱] key=value ...  使用結構化 Macro 引導{Colors.RESET}")
                    continue
                canonical_sop_name = skill_library.canonical_skill_name(sop_name)

                # 讀取 SOP（優先 .md，退回 .json）
                skill_found = True
                initial_screen = None
                try:
                    skill_data = skill_library.load_skill(sop_name)
                except FileNotFoundError:
                    skill_found = False
                    skill_data = {}
                    print(f"\n{Colors.YELLOW}  找不到 SOP / skill: '{sop_name}'{Colors.RESET}")
                    if not allow_draft_study:
                        print(f"{Colors.YELLOW}  目前設定 STUDY_REQUIRE_DRAFT_FLAG=true，未加 --draft 時不啟動探索教學。{Colors.RESET}")
                        print(f"{Colors.DIM}  建議做法:{Colors.RESET}")
                        print(f"{Colors.DIM}    1. 使用 /record [名稱] 錄製一次真實流程{Colors.RESET}")
                        print(f"{Colors.DIM}    2. 使用 /solve 描述目前卡關畫面，取得排錯建議{Colors.RESET}")
                        print(f"{Colors.DIM}    3. 若只是探索草稿，請明確使用 /study --draft [目標]{Colors.RESET}")
                        print(f"{Colors.DIM}    4. 若要讓 /study [目標] 自動進入 evidence draft，設定 STUDY_REQUIRE_DRAFT_FLAG=false{Colors.RESET}")
                        print(f"{Colors.DIM}       若要保存探索草稿，使用 /study --save-draft [目標]{Colors.RESET}\n")
                        continue
                    print(f"{Colors.DIM}  已啟動探索草稿模式；AI 只能依目前畫面與使用者確認引導，不會視為正式 SOP。{Colors.RESET}")
                    if save_draft_skill:
                        print(f"{Colors.DIM}  本次完成後會保存為 skill 草稿。{Colors.RESET}\n")
                    else:
                        print(f"{Colors.DIM}  本次不會自動保存；確認流程後建議用 /record 建立正式 SOP。{Colors.RESET}\n")
                    if canonical_sop_name != sop_name:
                        print(f"{Colors.DIM}  正規化名稱: {canonical_sop_name}{Colors.RESET}\n")
                    try:
                        session = sap.get_session()
                        initial_screen = scan_sap_screen(session)
                    except Exception:
                        initial_screen = None

                # 取得 SOP 文字內容
                if not skill_found:
                    evidence_pack = knowledge_library.build_evidence_pack(
                        sop_name,
                        initial_screen,
                        skill_library=skill_library,
                    )
                    sop_text = build_ad_hoc_study_context(sop_name, initial_screen, evidence_pack)
                    study_request = (
                        f"目前沒有既有 SOP。請以「{sop_name}」為學習目標，"
                        "根據 evidence pack、目前 SAP 畫面與使用者確認，用互動式教練方式引導我完成操作。"
                    )
                elif skill_data.get("format") == "markdown" and skill_data.get("sop_text"):
                    sop_text = skill_data["sop_text"]
                    study_request = "請根據以下 SOP 指南，從第一步開始引導我完成操作。"
                else:
                    sop_text = skill_library.get_skill_summary(sop_name)
                    study_request = "請根據以下 SOP 指南，從第一步開始引導我完成操作。"

                print(f"\n{Colors.CYAN}📘 Study Mode: {sop_name}{Colors.RESET}")
                if skill_found:
                    print(f"{Colors.DIM}  AI 教練將根據 SOP 一步步引導你操作 SAP{Colors.RESET}\n")
                else:
                    print(f"{Colors.DIM}  AI 教練將以探索草稿方式引導；請把每一步視為待確認建議{Colors.RESET}\n")

                # 切換到 Study Mode 並啟動 ReAct Loop
                agent.set_mode("study")
                response = ""
                try:
                    session = sap.get_session()
                    response = agent.process_message(
                        session,
                        study_request,
                        extra_context=sop_text,
                    )
                    print(f"\n{Colors.MAGENTA}  AI > {Colors.RESET}{response}\n")
                    if not skill_found and save_draft_skill:
                        guidance_steps = extract_study_guidance_steps(agent)
                        learned_markdown = build_learned_skill_markdown(
                            canonical_sop_name,
                            response,
                            guidance_steps,
                            initial_screen,
                        )
                        saved_path = skill_library.save_markdown_skill(sop_name, learned_markdown)
                        print(f"{Colors.GREEN}  ✅ 已將本次探索草稿記錄為 skill: {saved_path}{Colors.RESET}")
                        print(f"{Colors.DIM}     下次可直接使用 /study {canonical_sop_name}，或沿用原本說法 /study {sop_name}{Colors.RESET}\n")
                    elif not skill_found:
                        print(f"{Colors.YELLOW}  草稿教學未保存為 skill。確認流程正確後，建議用 /record 建立正式 SOP。{Colors.RESET}")
                        print(f"{Colors.DIM}     若仍要保存探索結果，請下次使用 /study --save-draft {sop_name}{Colors.RESET}\n")
                except ConnectionError as e:
                    print(f"{Colors.RED}  SAP 連線已斷開: {e}{Colors.RESET}")
                except Exception as e:
                    import traceback
                    print(f"\n{Colors.RED}  ❌ Study Mode 錯誤: {e}{Colors.RESET}")
                    print(f"{Colors.DIM}{traceback.format_exc()}{Colors.RESET}\n")
                print(f"{Colors.CYAN}  📘 Study Mode 仍保持啟用；輸入 /auto、/ask 或 /solve 可切換模式。{Colors.RESET}\n")

            # --- /ask ---
            elif cmd == "/ask":
                agent.set_mode("ask")
                print(f"{Colors.GREEN}  ✅ 已切換至 🟢 Ask Mode（問答模式）{Colors.RESET}")
                print(f"{Colors.DIM}     AI 將根據當前畫面回答問題，不會執行任何操作{Colors.RESET}")

            # --- /solve ---
            elif cmd == "/solve":
                agent.set_mode("solve")
                print(f"{Colors.GREEN}  ✅ 已切換至 🟡 Solve Mode（問題排解模式）{Colors.RESET}")
                print(f"{Colors.DIM}     AI 會根據當前畫面、彈窗與錯誤訊息告訴你如何處理，不會執行任何操作{Colors.RESET}")

            # --- /auto ---
            elif cmd == "/auto":
                agent.set_mode("auto")
                print(f"{Colors.GREEN}  ✅ 已切換至 🟣 Auto Mode（自動代操）{Colors.RESET}")
                print(f"{Colors.DIM}     AI 可呼叫工具操作 SAP 畫面{Colors.RESET}")

            # --- /mcp ---
            elif cmd == "/mcp":
                print_mcp_diagnostics()

            # --- 未知指令 ---
            elif user_input.startswith("/"):
                print(f"{Colors.YELLOW}  未知指令: {user_input}{Colors.RESET}")
                print(f"{Colors.DIM}  可用指令: /scan, /record, /stop, /recordings, /play, /study, /macro, /knowledge, /skills, /ask, /solve, /auto, /mcp, /connect, /login, /reset, /quit{Colors.RESET}")

            # ===== 自然語言指令 → AI Agent =====
            else:
                try:
                    session = sap.get_session()
                except ConnectionError as e:
                    print(f"{Colors.RED}  SAP 連線已斷開: {e}{Colors.RESET}")
                    continue

                print()
                try:
                    if try_run_matched_macro(agent, sap, macro_library, user_input):
                        continue

                    # 如果是 Ask/Solve Mode 且有錄製紀錄，附上最近的 SOP 摘要作為上下文
                    extra_context = ""
                    if agent.mode in ("ask", "solve"):
                        recordings = skill_library.list_recordings()
                        if recordings:
                            # 附上最近一筆 SOP 的摘要
                            latest = recordings[-1]
                            try:
                                extra_context = skill_library.get_recording_summary(latest["name"])
                            except FileNotFoundError:
                                pass

                    response = agent.process_message(session, user_input, extra_context=extra_context)
                    print(f"\n{Colors.MAGENTA}  AI > {Colors.RESET}{response}\n")
                except RuntimeError as e:
                    print(f"\n{Colors.RED}  ❌ 執行失敗: {e}{Colors.RESET}\n")
                except Exception as e:
                    import traceback
                    print(f"\n{Colors.RED}  ❌ 未預期錯誤: {e}{Colors.RESET}")
                    print(f"{Colors.DIM}{traceback.format_exc()}{Colors.RESET}\n")

        except KeyboardInterrupt:
            print(f"\n\n{Colors.DIM}  (按 Ctrl+C 中斷，輸入 /quit 結束程式){Colors.RESET}\n")
            continue

        except EOFError:
            # 確保清理
            if recorder.is_recording:
                recorder.stop_recording()
            if monitor and monitor.is_running:
                monitor.stop()
            print(f"\n{Colors.DIM}  👋 再見！{Colors.RESET}\n")
            break


if __name__ == "__main__":
    main()
