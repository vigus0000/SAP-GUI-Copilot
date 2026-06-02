"""
SAP GUI Copilot — CLI 入口 (Phase 2)

互動式 REPL 介面，支援：
- 自然語言指令 → AI 操作 SAP (Auto Mode)
- /scan        → 顯示當前畫面掃描結果
- /login       → 重新執行 GitHub Copilot 授權
- /reset       → 重置對話歷史
- /record [名稱] → 開始錄製操作（Record Mode）
- /stop        → 停止錄製
- /recordings  → 列出所有已錄製的 SOP
- /play [名稱]  → 顯示指定 SOP 的操作步驟
- /study [名稱] → 以 5 秒延遲執行錄製的 SOP
- /ask         → 切換到 Ask Mode（問答模式）
- /auto        → 切換回 Auto Mode（自動代操）
- /quit        → 結束程式
"""

import json
import os
import sys
import time

from copilot_auth import CopilotAuth
from sap_core import SAPConnection
from sap_agent_tools import click, scan_sap_screen, select_combo, send_vkey, set_tcode, set_text
from llm_brain import SAPAgent
from sap_monitor import SAPMonitor
from sap_recorder import SAPRecorder

STUDY_STEP_DELAY_SECONDS = 5
STUDY_PREFIX_TCODE_OUTSIDE_START = os.getenv(
    "STUDY_PREFIX_TCODE_OUTSIDE_START", "true"
).strip().lower() not in ("0", "false", "no", "off")
STUDY_INITIAL_TCODES = {
    value.strip().upper()
    for value in os.getenv("STUDY_INITIAL_TCODES", "SESSION_MANAGER,S000").split(",")
}


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
║   🤖 SAP GUI Copilot  v0.3.0  (Phase 2)         ║
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
    /study [名稱]   以 5 秒延遲執行錄製的 SOP
    /ask           切換到 Ask Mode（問答模式）
    /auto          切換回 Auto Mode（自動代操）
    /login         重新執行 GitHub Copilot 授權
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
                print(f"    {Colors.CYAN}{editor.get('id', '')}{Colors.RESET} │ {editor.get('type', '')}")
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
                    print(f"    {Colors.CYAN}editor: {editor.get('id', '')} │ {editor.get('type', '')}{Colors.RESET}")
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


def print_recordings_list(recorder):
    """列出所有已錄製的 SOP"""
    recordings = recorder.list_recordings()

    if not recordings:
        print(f"\n{Colors.YELLOW}  📂 尚無錄製紀錄{Colors.RESET}")
        print(f"{Colors.DIM}     使用 /record [名稱] 開始錄製{Colors.RESET}\n")
        return

    print(f"\n{Colors.CYAN}── 已錄製的 SOP ──{Colors.RESET}")
    print(f"  {Colors.DIM}{'─' * 70}{Colors.RESET}")

    for i, rec in enumerate(recordings, 1):
        name = rec.get("name", "未命名")
        created = rec.get("created_at", "N/A")[:19]  # 截短到秒
        events = rec.get("event_count", 0)
        duration = rec.get("duration_seconds", 0)
        summary = rec.get("summary", "")

        print(f"  {Colors.WHITE}{i}. {name}{Colors.RESET}")
        print(f"     {Colors.DIM}時間: {created} │ 操作: {events} 個 │ 持續: {duration:.0f} 秒{Colors.RESET}")
        if summary:
            print(f"     {Colors.DIM}摘要: {summary}{Colors.RESET}")
        print()

    print(f"  {Colors.DIM}{'─' * 70}{Colors.RESET}")
    print(f"  {Colors.DIM}使用 /play [名稱] 檢視步驟，或 /study [名稱] 延遲執行 SOP{Colors.RESET}\n")


def print_recording_steps(recorder, name):
    """顯示指定 SOP 的操作步驟"""
    try:
        summary_text = recorder.get_recording_summary(name)
        print(f"\n{Colors.CYAN}{summary_text}{Colors.RESET}\n")
    except FileNotFoundError:
        print(f"\n{Colors.RED}  找不到錄製: '{name}'{Colors.RESET}")
        print(f"{Colors.DIM}  使用 /recordings 查看所有錄製{Colors.RESET}\n")


def _event_title(event):
    """產生 /study 步驟標籤。"""
    event_type = event.get("event_type", "")
    details = event.get("details", {})

    if event_type == "TCODE_CHANGE":
        return f"T-Code: {details.get('from_tcode', '?')} → {details.get('to_tcode', '?')}"
    if event_type == "SCREEN_CHANGE":
        return f"畫面跳轉: {details.get('from_screen', '?')} → {details.get('to_screen', '?')}"
    if event_type == "FIELD_CHANGE":
        element_id = details.get("element_id", "?")
        short_id = element_id.split("/")[-1] if "/" in element_id else element_id
        return f"欄位: {short_id} = \"{details.get('to_value', '')}\""
    if event_type in ("ACTIVE_WINDOW_CHANGE", "WINDOW_OPEN", "WINDOW_CLOSE"):
        return f"{event_type}: {details.get('to_window') or details.get('window_id') or '?'}"
    if event_type == "STATUS_MESSAGE":
        return f"狀態列: [{details.get('type', '?')}] {details.get('text', '')}"
    return event_type or "UNKNOWN"


def _is_okcode_field(element_id):
    return element_id.endswith("/tbar[0]/okcd") or element_id.endswith("/okcd")


def _is_combo_field(element_id):
    return "/cmb" in element_id or element_id.split("/")[-1].startswith("cmb")


def _is_radio_field(element_id):
    return "/rad" in element_id or element_id.split("/")[-1].startswith("rad")


def _is_checkbox_field(element_id):
    return "/chk" in element_id or element_id.split("/")[-1].startswith("chk")


def _as_bool(value):
    return str(value).strip().lower() in ("true", "1", "x", "yes", "y")


def _get_current_tcode(session):
    try:
        info = getattr(session, "Info", None)
        return str(getattr(info, "Transaction", "") or "").strip().upper()
    except Exception:
        return ""


def _is_study_initial_screen(session):
    current_tcode = _get_current_tcode(session)
    return current_tcode in STUDY_INITIAL_TCODES


def _study_tcode_command(session, tcode):
    tcode = str(tcode or "").strip()
    if not tcode:
        return tcode, False, _get_current_tcode(session)

    current_tcode = _get_current_tcode(session)
    if (
        not STUDY_PREFIX_TCODE_OUTSIDE_START
        or tcode.startswith("/")
        or current_tcode in STUDY_INITIAL_TCODES
    ):
        return tcode, False, current_tcode

    return f"/n{tcode}", True, current_tcode


def _next_event_tcode(next_event):
    if not next_event or next_event.get("event_type") != "TCODE_CHANGE":
        return ""
    return str(next_event.get("details", {}).get("to_tcode", "")).strip()


def _is_vkey_disabled(result):
    error = str(result.get("error", "")).lower()
    return "virtual key is not enabled" in error or "vkey" in error and "not enabled" in error


def _set_toggle(session, element_id, value):
    """重放 radio/checkbox。Radio 的 False 通常是選中另一個 radio 的副作用，因此跳過。"""
    target = _as_bool(value)

    if _is_radio_field(element_id) and not target:
        return {
            "success": True,
            "action": "study_skip_radio_false",
            "element_id": element_id,
            "message": "Radio=False 通常由同群組其他 Radio=True 造成，已略過",
        }

    try:
        element = session.FindById(element_id)
        current = bool(getattr(element, "Selected", False))
        if current == target:
            return {
                "success": True,
                "action": "study_toggle_noop",
                "element_id": element_id,
                "value": value,
                "message": "目前狀態已符合錄製值",
            }
    except Exception:
        pass

    return click(session, element_id)


def _send_study_screen_change(session):
    """重放錄製到的畫面跳轉；F8 未啟用時改送 Enter。"""
    execute_result = send_vkey(session, 8)
    if execute_result.get("success"):
        execute_result["study_note"] = "錄製只偵測到畫面跳轉；Study Mode 已送出 F8/Execute"
        return execute_result

    if not _is_vkey_disabled(execute_result):
        return execute_result

    enter_result = send_vkey(session, 0)
    enter_result["fallback_from"] = execute_result
    if enter_result.get("success"):
        enter_result["study_note"] = "F8/Execute 未啟用，已改送 Enter 重放畫面跳轉"
    else:
        enter_result["error"] = (
            f"F8/Execute 未啟用，改送 Enter 仍失敗: {enter_result.get('error')}"
        )
    return enter_result


def execute_study_event(session, event, previous_event=None, next_event=None):
    """依照錄製事件的語意重放單一步驟。"""
    event_type = event.get("event_type", "")
    details = event.get("details", {})

    if event_type == "FIELD_CHANGE":
        element_id = details.get("element_id", "")
        value = str(details.get("to_value", ""))

        if not element_id:
            return {"success": False, "action": "study_field", "error": "缺少 element_id"}
        next_tcode = _next_event_tcode(next_event)
        if (
            _is_okcode_field(element_id)
            and next_tcode
            and value.strip().upper() == next_tcode.upper()
        ):
            command, used_prefix, current_tcode = _study_tcode_command(session, value)
            result = set_text(session, element_id=element_id, value=command)
            if result.get("success") and used_prefix:
                result["study_note"] = (
                    f"目前不在起始畫面 ({current_tcode or 'N/A'})，已將 T-Code 改為 {command}"
                )
            return result
        if _is_combo_field(element_id):
            return select_combo(session, element_id=element_id, value=value)
        if _is_radio_field(element_id) or _is_checkbox_field(element_id):
            return _set_toggle(session, element_id, value)
        return set_text(session, element_id=element_id, value=value)

    if event_type == "TCODE_CHANGE":
        to_tcode = str(details.get("to_tcode", "")).strip()
        if not to_tcode:
            return {"success": False, "action": "study_tcode", "error": "缺少 to_tcode"}

        prev_details = (previous_event or {}).get("details", {})
        prev_was_okcode = (
            (previous_event or {}).get("event_type") == "FIELD_CHANGE"
            and _is_okcode_field(prev_details.get("element_id", ""))
            and str(prev_details.get("to_value", "")).strip().upper() == to_tcode.upper()
        )
        if prev_was_okcode:
            return send_vkey(session, 0)
        command, used_prefix, current_tcode = _study_tcode_command(session, to_tcode)
        result = set_tcode(session, command)
        if result.get("success") and used_prefix:
            result["study_note"] = (
                f"目前不在起始畫面 ({current_tcode or 'N/A'})，已將 T-Code 改為 {command}"
            )
        return result

    if event_type == "SCREEN_CHANGE":
        return _send_study_screen_change(session)

    return {
        "success": True,
        "action": "study_observe",
        "event_type": event_type,
        "message": "此事件是觀察型事件，Study Mode 不執行 SAP 動作",
    }


def run_study(recorder, sap, name, delay_seconds=STUDY_STEP_DELAY_SECONDS):
    """執行已錄製 SOP，每個 SOP step 後固定等待。"""
    data = recorder.load_recording(name)
    events = data.get("events", [])

    if not events:
        print(f"\n{Colors.YELLOW}  SOP '{name}' 沒有可執行步驟{Colors.RESET}\n")
        return

    print(f"\n{Colors.CYAN}▶ Study Mode: {name}{Colors.RESET}")
    print(f"{Colors.DIM}  共 {len(events)} 個 SOP 步驟，每步間隔 {delay_seconds} 秒{Colors.RESET}\n")

    previous_event = None
    for index, event in enumerate(events, 1):
        next_event = events[index] if index < len(events) else None
        print(f"{Colors.WHITE}  [{index}/{len(events)}] {_event_title(event)}{Colors.RESET}")

        try:
            session = sap.get_session()
            result = execute_study_event(
                session,
                event,
                previous_event=previous_event,
                next_event=next_event,
            )
        except ConnectionError as e:
            print(f"{Colors.RED}     SAP 連線已斷開: {e}{Colors.RESET}")
            break
        except Exception as e:
            result = {"success": False, "action": "study", "error": str(e)}

        if result.get("success"):
            note = result.get("study_note") or result.get("message") or result.get("action", "完成")
            print(f"{Colors.GREEN}     ✓ {note}{Colors.RESET}")
        else:
            print(f"{Colors.RED}     ✗ {result.get('error') or result.get('message') or result}{Colors.RESET}")
            print(f"{Colors.YELLOW}     已停止 Study Mode，請先處理目前 SAP 畫面再重試{Colors.RESET}\n")
            return

        previous_event = event
        if index < len(events):
            print(f"{Colors.DIM}     等待 {delay_seconds} 秒...{Colors.RESET}")
            time.sleep(delay_seconds)

    print(f"\n{Colors.GREEN}  ✅ Study Mode 執行完成: {name}{Colors.RESET}\n")


def get_mode_indicator(agent_mode, recorder):
    """取得模式指示器"""
    if recorder.is_recording:
        return f"{Colors.RED}🔴 REC{Colors.RESET}"
    elif agent_mode == "ask":
        return f"{Colors.GREEN}🟢 ASK{Colors.RESET}"
    else:
        return f"{Colors.MAGENTA}🟣 AUTO{Colors.RESET}"


def main():
    """主程式入口"""
    print_banner()

    # ===== Step 1: GitHub Copilot 認證 =====
    print(f"{Colors.CYAN}[1/2] 檢查 GitHub Copilot 認證...{Colors.RESET}")
    auth = CopilotAuth()

    if not auth.is_logged_in():
        print(f"{Colors.YELLOW}  尚未登入，開始 GitHub Copilot 授權流程{Colors.RESET}")
        success = auth.login()
        if not success:
            print(f"{Colors.RED}  授權失敗，程式結束{Colors.RESET}")
            sys.exit(1)
    else:
        print(f"{Colors.GREEN}  ✅ 已登入 GitHub Copilot{Colors.RESET}")
        # 驗證 Copilot Token 可用（login() 流程中已自動換取）
        try:
            auth.get_token()
            print(f"{Colors.GREEN}  ✅ Copilot API Token 有效{Colors.RESET}")
        except RuntimeError as e:
            print(f"{Colors.YELLOW}  Copilot Token 無效: {e}{Colors.RESET}")
            print(f"{Colors.YELLOW}  嘗試重新登入...{Colors.RESET}")
            success = auth.login()
            if not success:
                print(f"{Colors.RED}  授權失敗，程式結束{Colors.RESET}")
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
    agent = SAPAgent(auth)
    recorder = SAPRecorder()

    # 初始化 Monitor（但不啟動，等使用者開始錄製時才啟動）
    monitor = None  # 延遲建立，因為 session 可能隨時需要刷新

    print(f"\n{Colors.GREEN}{'═' * 50}{Colors.RESET}")
    print(f"{Colors.GREEN}  🚀 SAP GUI Copilot 已就緒！{Colors.RESET}")
    print(f"{Colors.GREEN}  📍 當前模式: {Colors.MAGENTA}🟣 Auto Mode（自動代操）{Colors.RESET}")
    print(f"{Colors.GREEN}  🧠 Copilot Model: {Colors.WHITE}{agent.model}{Colors.RESET}")
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

            # --- /login ---
            elif cmd == "/login":
                auth.login()

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

                # 停止 Recorder 並儲存
                try:
                    filepath = recorder.stop_recording()
                except RuntimeError as e:
                    print(f"{Colors.RED}  {e}{Colors.RESET}")

            # --- /recordings ---
            elif cmd == "/recordings":
                print_recordings_list(recorder)

            # --- /play [名稱] ---
            elif cmd == "/play" or cmd.startswith("/play "):
                parts = user_input.split(maxsplit=1)
                if len(parts) < 2 or not parts[1].strip():
                    print(f"{Colors.YELLOW}  用法: /play [SOP名稱]{Colors.RESET}")
                    print(f"{Colors.DIM}  使用 /recordings 查看所有錄製{Colors.RESET}")
                    continue

                sop_name = parts[1].strip()
                print_recording_steps(recorder, sop_name)

            # --- /study [名稱] ---
            elif cmd == "/study" or cmd.startswith("/study "):
                if recorder.is_recording:
                    print(f"{Colors.YELLOW}  請先 /stop 結束錄製，再執行 /study{Colors.RESET}")
                    continue

                parts = user_input.split(maxsplit=1)
                if len(parts) < 2 or not parts[1].strip():
                    print(f"{Colors.YELLOW}  用法: /study [SOP名稱]{Colors.RESET}")
                    print(f"{Colors.DIM}  使用 /recordings 查看所有錄製{Colors.RESET}")
                    continue

                sop_name = parts[1].strip()
                agent.set_mode("auto")
                try:
                    run_study(recorder, sap, sop_name)
                except FileNotFoundError:
                    print(f"\n{Colors.RED}  找不到錄製: '{sop_name}'{Colors.RESET}")
                    print(f"{Colors.DIM}  使用 /recordings 查看所有錄製{Colors.RESET}\n")

            # --- /ask ---
            elif cmd == "/ask":
                agent.set_mode("ask")
                print(f"{Colors.GREEN}  ✅ 已切換至 🟢 Ask Mode（問答模式）{Colors.RESET}")
                print(f"{Colors.DIM}     AI 將根據當前畫面回答問題，不會執行任何操作{Colors.RESET}")

            # --- /auto ---
            elif cmd == "/auto":
                agent.set_mode("auto")
                print(f"{Colors.GREEN}  ✅ 已切換至 🟣 Auto Mode（自動代操）{Colors.RESET}")
                print(f"{Colors.DIM}     AI 可呼叫工具操作 SAP 畫面{Colors.RESET}")

            # --- 未知指令 ---
            elif user_input.startswith("/"):
                print(f"{Colors.YELLOW}  未知指令: {user_input}{Colors.RESET}")
                print(f"{Colors.DIM}  可用指令: /scan, /record, /stop, /recordings, /play, /study, /ask, /auto, /login, /reset, /quit{Colors.RESET}")

            # ===== 自然語言指令 → AI Agent =====
            else:
                try:
                    session = sap.get_session()
                except ConnectionError as e:
                    print(f"{Colors.RED}  SAP 連線已斷開: {e}{Colors.RESET}")
                    continue

                print()
                try:
                    # 如果是 Ask Mode 且有錄製紀錄，附上最近的 SOP 摘要作為上下文
                    extra_context = ""
                    if agent.mode == "ask":
                        recordings = recorder.list_recordings()
                        if recordings:
                            # 附上最近一筆 SOP 的摘要
                            latest = recordings[-1]
                            try:
                                extra_context = recorder.get_recording_summary(latest["name"])
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
