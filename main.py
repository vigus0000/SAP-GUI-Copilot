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
- /ask         → 切換到 Ask Mode（問答模式）
- /auto        → 切換回 Auto Mode（自動代操）
- /quit        → 結束程式
"""

import json
import sys

from copilot_auth import CopilotAuth
from sap_core import SAPConnection
from sap_agent_tools import scan_sap_screen
from llm_brain import SAPAgent
from sap_monitor import SAPMonitor
from sap_recorder import SAPRecorder


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
║   🤖 SAP GUI Copilot  v0.2  (Phase 2)           ║
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
                print(f"\n  {Colors.YELLOW}⚠ 彈出視窗: {popup.get('title', '')}{Colors.RESET}")
                for elem in popup.get("elements", []):
                    print(f"    {elem.get('type', ''):20s} │ {elem.get('text', '')}")

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
    print(f"  {Colors.DIM}使用 /play [名稱] 檢視步驟{Colors.RESET}\n")


def print_recording_steps(recorder, name):
    """顯示指定 SOP 的操作步驟"""
    try:
        summary_text = recorder.get_recording_summary(name)
        print(f"\n{Colors.CYAN}{summary_text}{Colors.RESET}\n")
    except FileNotFoundError:
        print(f"\n{Colors.RED}  找不到錄製: '{name}'{Colors.RESET}")
        print(f"{Colors.DIM}  使用 /recordings 查看所有錄製{Colors.RESET}\n")


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
            elif cmd.startswith("/record"):
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
            elif cmd.startswith("/play"):
                parts = user_input.split(maxsplit=1)
                if len(parts) < 2 or not parts[1].strip():
                    print(f"{Colors.YELLOW}  用法: /play [SOP名稱]{Colors.RESET}")
                    print(f"{Colors.DIM}  使用 /recordings 查看所有錄製{Colors.RESET}")
                    continue

                sop_name = parts[1].strip()
                print_recording_steps(recorder, sop_name)

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
                print(f"{Colors.DIM}  可用指令: /scan, /record, /stop, /recordings, /play, /ask, /auto, /login, /reset, /quit{Colors.RESET}")

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
