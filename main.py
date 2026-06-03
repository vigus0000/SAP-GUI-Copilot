"""
SAP GUI Copilot — CLI 入口 (Phase 3)

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

import os
import sys
import time

from copilot_auth import CopilotAuth
from sap_core import SAPConnection
from sap_agent_tools import click, scan_sap_screen, select_combo, send_vkey, set_tcode, set_text, visualize_element
from llm_brain import SAPAgent
from sap_monitor import SAPMonitor
from sap_recorder import SAPRecorder
from sap_skill_library import SAPSkillLibrary

STUDY_STEP_DELAY_SECONDS = 5
STUDY_PREFIX_TCODE_OUTSIDE_START = os.getenv(
    "STUDY_PREFIX_TCODE_OUTSIDE_START", "true"
).strip().lower() not in ("0", "false", "no", "off")
STUDY_HUMAN_FIELD_INPUT = os.getenv(
    "STUDY_HUMAN_FIELD_INPUT", "true"
).strip().lower() not in ("0", "false", "no", "off")
STUDY_FOCUS_HUMAN_FIELDS = os.getenv(
    "STUDY_FOCUS_HUMAN_FIELDS", "true"
).strip().lower() not in ("0", "false", "no", "off")
STUDY_PROMPT_FIELD_VALUES = os.getenv(
    "STUDY_PROMPT_FIELD_VALUES", "true"
).strip().lower() not in ("0", "false", "no", "off")
STUDY_AUTOFILL_PROMPTED_VALUES = os.getenv(
    "STUDY_AUTOFILL_PROMPTED_VALUES", "false"
).strip().lower() not in ("0", "false", "no", "off")
STUDY_ADAPTIVE_MODE = os.getenv(
    "STUDY_ADAPTIVE_MODE", "true"
).strip().lower() not in ("0", "false", "no", "off")
STUDY_VISUALIZE_SECONDS = float(os.getenv("STUDY_VISUALIZE_SECONDS", "1.2"))
STUDY_SCREEN_CHANGE_TIMEOUT_SECONDS = float(os.getenv("STUDY_SCREEN_CHANGE_TIMEOUT_SECONDS", "8"))
STUDY_SCREEN_CHANGE_POLL_SECONDS = float(os.getenv("STUDY_SCREEN_CHANGE_POLL_SECONDS", "0.5"))
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
║   🤖 SAP GUI Copilot  V0.4.1  (Phase 3)         ║
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
    print(f"  {Colors.DIM}使用 /play [名稱] 檢視步驟，或 /study [名稱] 延遲執行 SOP{Colors.RESET}\n")


def print_recording_steps(skill_library, name):
    """顯示指定 SOP / skill 的操作步驟。"""
    try:
        summary_text = skill_library.get_recording_summary(name)
        print(f"\n{Colors.CYAN}{summary_text}{Colors.RESET}\n")
    except FileNotFoundError:
        print(f"\n{Colors.RED}  找不到 SOP / skill: '{name}'{Colors.RESET}")
        print(f"{Colors.DIM}  使用 /recordings 查看所有可用項目{Colors.RESET}\n")


def _event_title(event):
    """產生 /study 步驟標籤。"""
    return _event_title_with_context(event)


def _event_title_with_context(event, field_context=None):
    """產生 /study 步驟標籤，可帶入畫面掃描出的欄位語意。"""
    event_type = event.get("event_type", "")
    details = event.get("details", {})

    if event_type == "TCODE_CHANGE":
        return f"T-Code: {details.get('from_tcode', '?')} → {details.get('to_tcode', '?')}"
    if event_type == "SCREEN_CHANGE":
        return f"畫面跳轉: {details.get('from_screen', '?')} → {details.get('to_screen', '?')}"
    if event_type == "FIELD_CHANGE":
        element_id = details.get("element_id", "?")
        short_id = element_id.split("/")[-1] if "/" in element_id else element_id
        display_name = (field_context or {}).get("display_name") or short_id
        if display_name != short_id:
            return f"欄位: {display_name} ({short_id}) = \"{details.get('to_value', '')}\""
        return f"欄位: {display_name} = \"{details.get('to_value', '')}\""
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


STUDY_FIELD_LABEL_OVERRIDES = {
    "FACOM-FKDAT": "請款文件開始",
    "FACOM-FKDAT_BIS": "請款文件結束",
    "FACOM-KUNDE": "付款人",
    "FACOM-VBOFF": "未結請款文件",
    "FACOM-VBALL": "所有請款文件",
    "VBAK-AUART": "銷售文件類型",
    "VBAK-VKORG": "銷售組織",
    "VBAK-VTWEG": "配銷通路",
    "VBAK-SPART": "產品組",
    "RV45A-KUNNR": "售達方",
    "RV45A-KUNWE": "送達方",
}


def _clean_study_label(value):
    text = str(value or "").strip()
    return text.strip(":：* ").strip()


def _technical_field_name(element_id):
    short_id = element_id.split("/")[-1] if "/" in element_id else element_id
    for prefix in ("ctxt", "txt", "cmb", "chk", "rad", "btn"):
        if short_id.startswith(prefix):
            return short_id[len(prefix):]
    return short_id


def _fallback_study_display_name(element_id):
    technical_name = _technical_field_name(element_id)
    if technical_name in STUDY_FIELD_LABEL_OVERRIDES:
        return STUDY_FIELD_LABEL_OVERRIDES[technical_name]
    if "-" in technical_name:
        return technical_name.split("-", 1)[1].replace("_", " ")
    return technical_name.replace("_", " ")


def _study_element_ids_match(left, right):
    left = str(left or "")
    right = str(right or "")
    return left == right or left.endswith(right) or right.endswith(left)


def _field_context_from_item(item, element_id, source, screen_title="", popup_title=""):
    short_id = element_id.split("/")[-1] if "/" in element_id else element_id
    label = _clean_study_label(item.get("label", ""))
    tooltip = _clean_study_label(item.get("tooltip", ""))
    name = _clean_study_label(item.get("name", ""))
    fallback_name = _fallback_study_display_name(element_id)
    display_name = label or tooltip or name or fallback_name or short_id
    current_value = ""
    for key in ("value", "text", "key"):
        if item.get(key) not in (None, ""):
            current_value = str(item.get(key))
            break

    return {
        "id": item.get("id", element_id),
        "short_id": short_id,
        "display_name": display_name,
        "label": label,
        "tooltip": tooltip,
        "name": name,
        "field_type": item.get("type", ""),
        "current_value": current_value,
        "required": item.get("required"),
        "dropdown": bool(item.get("dropdown")),
        "options": item.get("options", []),
        "screen_title": screen_title,
        "popup_title": popup_title,
        "source": source,
    }


def _study_item_text(item):
    return _clean_study_label(
        item.get("label")
        or item.get("text")
        or item.get("value")
        or item.get("tooltip")
        or item.get("name")
        or ""
    )


def _study_item_position(item):
    return item.get("position") or {}


def _nearest_study_label_for_item(field_item, elements):
    field_pos = _study_item_position(field_item)
    field_left = field_pos.get("left")
    field_top = field_pos.get("top")
    if field_left is None or field_top is None:
        return ""

    best_text = ""
    best_score = None
    for item in elements:
        item_type = item.get("type", "")
        if "Label" not in item_type:
            continue
        text = _study_item_text(item)
        if not text:
            continue

        label_pos = _study_item_position(item)
        label_left = label_pos.get("left")
        label_top = label_pos.get("top")
        label_width = label_pos.get("width") or 0
        if label_left is None or label_top is None:
            continue

        label_right = label_left + label_width
        if label_right > field_left + 8:
            continue

        row_gap = abs(label_top - field_top)
        if row_gap > 16:
            continue

        horizontal_gap = abs(field_left - label_right)
        score = row_gap * 80 + horizontal_gap
        if label_top > field_top + 8:
            score += 500
        if best_score is None or score < best_score:
            best_text = text
            best_score = score

    return best_text


def _iter_screen_field_candidates(screen):
    screen_title = screen.get("title", "")
    for field in screen.get("fields", []):
        yield field, "screen.fields", screen_title, ""
    for elem in screen.get("elements", []):
        yield elem, "screen.elements", screen_title, ""

    active_popup = screen.get("active_popup") or {}
    popup_title = active_popup.get("title", "")
    for field in active_popup.get("fields", []):
        yield field, "active_popup.fields", screen_title, popup_title
    for elem in active_popup.get("elements", []):
        yield elem, "active_popup.elements", screen_title, popup_title

    for key, value in screen.items():
        if not key.startswith("popup_wnd") or not isinstance(value, dict):
            continue
        popup_title = value.get("title", "")
        for field in value.get("fields", []):
            yield field, f"{key}.fields", screen_title, popup_title
        for elem in value.get("elements", []):
            yield elem, f"{key}.elements", screen_title, popup_title


def _enhance_study_context_with_near_label(context, screen):
    if context.get("label"):
        return context

    source = context.get("source", "")
    popup_key = source.split(".", 1)[0] if source.startswith("popup_wnd") else ""
    if source.startswith("active_popup"):
        container = screen.get("active_popup") or {}
    elif popup_key:
        container = screen.get(popup_key) or {}
    else:
        container = screen

    elements = container.get("elements", [])
    target_item = None
    for item in list(container.get("fields", [])) + list(elements):
        if _study_element_ids_match(item.get("id", ""), context.get("id", "")):
            target_item = item
            break

    if not target_item:
        return context

    near_label = _nearest_study_label_for_item(target_item, elements)
    if not near_label:
        return context

    enhanced = dict(context)
    enhanced["label"] = near_label
    enhanced["display_name"] = near_label
    enhanced["source"] = f"{context.get('source', '')}+near_label"
    return enhanced



def _resolve_study_field_context(session, element_id):
    short_id = element_id.split("/")[-1] if "/" in element_id else element_id
    fallback_name = _fallback_study_display_name(element_id)
    fallback = {
        "id": element_id,
        "short_id": short_id,
        "display_name": fallback_name or short_id,
        "label": "",
        "tooltip": "",
        "name": "",
        "field_type": "",
        "current_value": "",
        "required": None,
        "dropdown": False,
        "options": [],
        "screen_title": "",
        "popup_title": "",
        "source": "fallback",
    }
    try:
        screen = scan_sap_screen(session)
    except Exception as exc:
        fallback["error"] = str(exc)
        return fallback

    best = None
    best_score = None
    for item, source, screen_title, popup_title in _iter_screen_field_candidates(screen):
        item_id = item.get("id", "")
        if not item_id or not _study_element_ids_match(item_id, element_id):
            continue
        context = _field_context_from_item(item, element_id, source, screen_title, popup_title)
        score = 0
        if source.endswith(".fields"):
            score -= 20
        if context.get("label"):
            score -= 10
        if context.get("tooltip"):
            score -= 3
        if best_score is None or score < best_score:
            best = context
            best_score = score

    if best:
        return _enhance_study_context_with_near_label(best, screen)
    return fallback


def _focus_study_element(session, element_id):
    if not STUDY_FOCUS_HUMAN_FIELDS:
        return {
            "success": False,
            "skipped": True,
            "message": "STUDY_FOCUS_HUMAN_FIELDS=false",
        }

    return visualize_element(
        session,
        element_id=element_id,
        duration_seconds=STUDY_VISUALIZE_SECONDS,
        set_focus=True,
    )


def _human_field_instruction(session, element_id, value):
    short_id = element_id.split("/")[-1] if "/" in element_id else element_id
    field_context = _resolve_study_field_context(session, element_id)
    focus_result = _focus_study_element(session, element_id)
    display_name = field_context.get("display_name") or short_id
    current_value = field_context.get("current_value", "")
    default_value = current_value if STUDY_ADAPTIVE_MODE and current_value else value

    return {
        "success": True,
        "action": "study_guided_field",
        "requires_human": True,
        "requires_value": STUDY_PROMPT_FIELD_VALUES,
        "element_id": element_id,
        "target_value": value,
        "default_value": default_value,
        "focus": focus_result,
        "field_context": field_context,
        "message": f"請提供本次要使用的值: {display_name}，參考錄製值為 \"{value}\"",
    }


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
            "skip_delay": True,
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
                "skip_delay": True,
                "message": "目前狀態已符合錄製值",
            }
    except Exception:
        pass

    return click(session, element_id)


def _popup_summary(screen):
    popup = screen.get("active_popup") or {}
    if not popup:
        return ""
    title = popup.get("title", "") or popup.get("text", "") or popup.get("id", "")
    messages = popup.get("messages") or []
    message_text = ""
    if messages:
        first_message = messages[0]
        message_text = first_message.get("text", "") if isinstance(first_message, dict) else str(first_message)
    return "，".join(part for part in (title, message_text) if part)


def _verify_study_screen_state(screen, expected_details):
    expected_details = expected_details or {}
    expected_tcode = str(expected_details.get("tcode", "") or "").strip().upper()
    expected_from_screen = str(expected_details.get("from_screen", "") or "").strip()
    expected_screen = str(expected_details.get("to_screen", "") or "").strip()
    expected_title = str(expected_details.get("title", "") or "").strip()

    current_tcode = str(screen.get("tcode", "") or "").strip().upper()
    current_screen = str(screen.get("screen_number", "") or "").strip()
    current_title = str(screen.get("title", "") or "").strip()
    status = screen.get("status_bar") or {}
    status_type = str(status.get("type", "") or "").strip().upper()
    status_text = str(status.get("text", "") or "").strip()
    popup_text = _popup_summary(screen)

    errors = []
    if expected_tcode and current_tcode != expected_tcode:
        errors.append(f"T-Code 仍為 {current_tcode or 'N/A'}，預期 {expected_tcode}")
    if expected_screen and current_screen != expected_screen:
        errors.append(f"畫面仍為 {current_screen or 'N/A'}，預期 {expected_screen}")
    if popup_text:
        errors.append(f"出現彈窗: {popup_text}")
    if status_type in ("E", "A"):
        errors.append(f"狀態列錯誤 [{status_type}] {status_text}")

    matched = not errors
    return {
        "success": matched,
        "expected_tcode": expected_tcode,
        "expected_from_screen": expected_from_screen,
        "expected_screen": expected_screen,
        "expected_title": expected_title,
        "actual_tcode": current_tcode,
        "actual_screen": current_screen,
        "actual_title": current_title,
        "status_bar": status,
        "active_popup": screen.get("active_popup"),
        "error": "" if matched else "；".join(errors),
    }


def _wait_for_study_screen_change(session, expected_details, action_result):
    if not action_result.get("success"):
        return action_result
    if not expected_details:
        return action_result

    deadline = time.time() + STUDY_SCREEN_CHANGE_TIMEOUT_SECONDS
    last_verify = None
    while True:
        try:
            screen = scan_sap_screen(session)
            last_verify = _verify_study_screen_state(screen, expected_details)
        except Exception as exc:
            return {
                **action_result,
                "success": False,
                "action": "study_screen_change_verify",
                "error": f"畫面跳轉後掃描失敗: {exc}",
            }

        if last_verify.get("success"):
            note = action_result.get("study_note") or action_result.get("message") or action_result.get("action", "畫面跳轉")
            return {
                **action_result,
                "screen_verify": last_verify,
                "study_note": f"{note}，已驗證到畫面 {last_verify.get('actual_screen')}",
            }

        if last_verify.get("active_popup") or status_is_error(last_verify.get("status_bar")):
            return {
                **action_result,
                "success": False,
                "action": "study_screen_change_verify",
                "screen_verify": last_verify,
                "error": f"送出成功但畫面跳轉未完成: {last_verify.get('error')}",
            }

        actual_screen = str(last_verify.get("actual_screen", "") or "")
        from_screen = str(last_verify.get("expected_from_screen", "") or "")
        screen_left_start = bool(actual_screen and from_screen and actual_screen != from_screen)
        if STUDY_ADAPTIVE_MODE and last_verify and screen_left_start:
            note = action_result.get("study_note") or action_result.get("message") or action_result.get("action", "畫面跳轉")
            return {
                **action_result,
                "success": True,
                "action": "study_screen_change_review",
                "screen_verify": last_verify,
                "requires_review": True,
                "study_note": (
                    f"{note}；目前畫面 {last_verify.get('actual_screen') or 'N/A'} "
                    f"與參考錄製目標 {last_verify.get('expected_screen') or 'N/A'} 不同"
                ),
            }

        if time.time() >= deadline:
            if STUDY_ADAPTIVE_MODE and last_verify and not last_verify.get("active_popup") and not status_is_error(last_verify.get("status_bar")):
                note = action_result.get("study_note") or action_result.get("message") or action_result.get("action", "畫面跳轉")
                return {
                    **action_result,
                    "success": True,
                    "action": "study_screen_change_review",
                    "screen_verify": last_verify,
                    "requires_review": True,
                    "study_note": (
                        f"{note}；逾時仍未達參考錄製目標 {last_verify.get('expected_screen') or 'N/A'}，"
                        f"目前畫面為 {last_verify.get('actual_screen') or 'N/A'}"
                    ),
                }
            return {
                **action_result,
                "success": False,
                "action": "study_screen_change_verify",
                "screen_verify": last_verify,
                "error": f"送出成功但逾時未達目標畫面: {last_verify.get('error')}",
            }

        time.sleep(STUDY_SCREEN_CHANGE_POLL_SECONDS)


def status_is_error(status):
    return str((status or {}).get("type", "") or "").strip().upper() in ("E", "A")


def _send_study_screen_change(session, expected_details=None):
    """重放錄製到的畫面跳轉；送出後必須驗證是否到達錄製目標畫面。"""
    execute_result = send_vkey(session, 8)
    if execute_result.get("success"):
        execute_result["study_note"] = "錄製只偵測到畫面跳轉；Study Mode 已送出 F8/Execute"
        return _wait_for_study_screen_change(session, expected_details, execute_result)

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
    return _wait_for_study_screen_change(session, expected_details, enter_result)


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
            current_tcode = _get_current_tcode(session)
            if STUDY_ADAPTIVE_MODE and current_tcode == next_tcode.upper():
                return {
                    "success": True,
                    "action": "study_skip_okcode_current_tcode",
                    "skip_delay": True,
                    "message": f"目前已在目標 T-Code {current_tcode}，此 OKCode 參考步驟已略過",
                }
            command, used_prefix, current_tcode = _study_tcode_command(session, value)
            result = set_text(session, element_id=element_id, value=command)
            if result.get("success") and used_prefix:
                result["study_note"] = (
                    f"目前不在起始畫面 ({current_tcode or 'N/A'})，已將 T-Code 改為 {command}"
                )
            if result.get("success"):
                result["skip_delay"] = True
            return result
        if _is_combo_field(element_id):
            return select_combo(session, element_id=element_id, value=value)
        if _is_radio_field(element_id) or _is_checkbox_field(element_id):
            return _set_toggle(session, element_id, value)
        if STUDY_HUMAN_FIELD_INPUT:
            return _human_field_instruction(session, element_id, value)
        return set_text(session, element_id=element_id, value=value)

    if event_type == "TCODE_CHANGE":
        to_tcode = str(details.get("to_tcode", "")).strip()
        if not to_tcode:
            return {"success": False, "action": "study_tcode", "error": "缺少 to_tcode"}

        current_tcode = _get_current_tcode(session)
        if STUDY_ADAPTIVE_MODE and current_tcode == to_tcode.upper():
            return {
                "success": True,
                "action": "study_skip_tcode_current",
                "skip_delay": True,
                "message": f"目前已在目標 T-Code {current_tcode}，T-Code 參考步驟已略過",
            }

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
        return _send_study_screen_change(session, expected_details=details)

    return {
        "success": True,
        "action": "study_observe",
        "event_type": event_type,
        "message": "此事件是觀察型事件，Study Mode 不執行 SAP 動作",
    }


def _print_study_focus_result(result):
    focus = result.get("focus") or {}
    if focus.get("success"):
        cues = []
        if focus.get("focused"):
            cues.append("游標已移到欄位")
        if focus.get("visualized"):
            cues.append("已嘗試高亮")
        print(f"{Colors.GREEN}     ✓ {'，'.join(cues) or '已定位到欄位'}{Colors.RESET}")
    elif not focus.get("skipped"):
        print(f"{Colors.YELLOW}     無法自動定位欄位: {focus.get('error', 'N/A')}{Colors.RESET}")


def _prompt_study_field_value(result):
    reference_value = str(result.get("target_value", ""))
    field_context = result.get("field_context") or {}
    display_name = field_context.get("display_name") or result.get("element_id", "")
    short_id = field_context.get("short_id") or result.get("element_id", "").split("/")[-1]
    current_value = field_context.get("current_value", "")
    default_value = str(result.get("default_value", "") or current_value or reference_value)
    field_type = field_context.get("field_type", "")
    required = field_context.get("required")
    location = field_context.get("popup_title") or field_context.get("screen_title") or ""

    print(f"{Colors.YELLOW}     欄位步驟: 請提供本次要使用的值: {display_name}{Colors.RESET}")
    meta_parts = []
    if field_type:
        meta_parts.append(field_type)
    if required is True:
        meta_parts.append("必填")
    if reference_value:
        meta_parts.append(f"參考錄製值: {reference_value}")
    if current_value:
        meta_parts.append(f"目前值: {current_value}")
    if location:
        meta_parts.append(f"畫面: {location.strip()}")
    if meta_parts:
        print(f"{Colors.DIM}     欄位資訊: {' │ '.join(meta_parts)}{Colors.RESET}")
    print(f"{Colors.DIM}     元件: {short_id} │ {result.get('element_id', '')}{Colors.RESET}")
    print(f"{Colors.DIM}     直接 Enter 使用本次預設值；輸入新值可覆寫；/manual 手動完成；/skip 略過；/abort 中止{Colors.RESET}")

    raw_value = input(f"{Colors.BLUE}     本次值 [{default_value}] > {Colors.RESET}")
    command = raw_value.strip().lower()
    if command in ("/skip", "skip"):
        return {"mode": "skip"}
    if command in ("/manual", "manual"):
        return {"mode": "manual", "value": default_value}
    if command in ("/abort", "abort", "/quit", "quit"):
        return {"mode": "abort"}
    return {
        "mode": "value",
        "value": default_value if raw_value == "" else raw_value,
    }


def _normalize_study_value(value):
    return str(value or "").strip()


def _value_from_scanned_item(item):
    for key in ("value", "text", "key"):
        value = item.get(key)
        if value is not None and str(value) != "":
            return str(value)
    selected = item.get("selected")
    if selected is not None:
        return str(bool(selected))
    return ""


def _read_study_element_value(session, element_id):
    try:
        element = session.FindById(element_id)
        for attr in ("Text", "Key", "Value"):
            try:
                value = getattr(element, attr)
                if value is not None:
                    return {"success": True, "value": str(value), "source": f"COM.{attr}"}
            except Exception:
                pass
        try:
            selected = getattr(element, "Selected")
            return {"success": True, "value": str(bool(selected)), "source": "COM.Selected"}
        except Exception:
            pass
    except Exception as exc:
        direct_error = str(exc)
    else:
        direct_error = "找不到可讀取的值屬性"

    try:
        screen = scan_sap_screen(session)
        candidates = list(screen.get("fields", [])) + list(screen.get("elements", []))
        for key, value in screen.items():
            if key.startswith("popup_wnd") and isinstance(value, dict):
                candidates.extend(value.get("fields", []))
                candidates.extend(value.get("elements", []))

        for item in candidates:
            if item.get("id") == element_id:
                return {
                    "success": True,
                    "value": _value_from_scanned_item(item),
                    "source": "scan",
                }
    except Exception as exc:
        return {
            "success": False,
            "error": f"COM 讀取失敗: {direct_error}; scan 也失敗: {exc}",
        }

    return {"success": False, "error": f"找不到欄位或無法讀值: {direct_error}"}


def _verify_guided_field_value(sap, result, expected_value):
    element_id = result.get("element_id", "")
    try:
        session = sap.get_session()
        read_result = _read_study_element_value(session, element_id)
    except Exception as exc:
        return {"success": False, "error": str(exc)}

    if not read_result.get("success"):
        return read_result

    actual = _normalize_study_value(read_result.get("value", ""))
    expected = _normalize_study_value(expected_value)
    return {
        "success": actual == expected,
        "actual": actual,
        "expected": expected,
        "source": read_result.get("source", ""),
        "error": "" if actual == expected else f"欄位目前值為 \"{actual}\"，預期為 \"{expected}\"",
    }


def _wait_for_manual_field_and_verify(sap, result, expected_value):
    while True:
        print(f"{Colors.YELLOW}     請在 SAP GUI 手動輸入本次值: {expected_value}{Colors.RESET}")
        user_action = input(
            f"{Colors.BLUE}     完成後按 Enter 驗證；/retry 重新定位；/skip 略過；/abort 中止 > {Colors.RESET}"
        ).strip().lower()

        if user_action in ("/abort", "abort", "/quit", "quit"):
            return {"success": False, "abort": True}
        if user_action in ("/skip", "skip"):
            print(f"{Colors.YELLOW}     已略過此欄位{Colors.RESET}")
            return {"success": True, "manual_step": True, "skipped": True}
        if user_action in ("/retry", "retry", "r"):
            try:
                session = sap.get_session()
                focus_result = _focus_study_element(session, result.get("element_id", ""))
                result["focus"] = focus_result
                _print_study_focus_result(result)
            except Exception as exc:
                print(f"{Colors.YELLOW}     重新定位失敗: {exc}{Colors.RESET}")
            continue

        verify_result = _verify_guided_field_value(sap, result, expected_value)
        if verify_result.get("success"):
            print(
                f"{Colors.GREEN}     ✓ 已驗證欄位值: {verify_result.get('actual')} "
                f"({verify_result.get('source')}){Colors.RESET}"
            )
            return {"success": True, "manual_step": True}

        print(f"{Colors.RED}     驗證失敗: {verify_result.get('error') or verify_result}{Colors.RESET}")
        choice = input(
            f"{Colors.BLUE}     輸入 r 重試輸入、a 接受目前值、s 略過、q 中止 > {Colors.RESET}"
        ).strip().lower()
        if choice in ("a", "accept"):
            print(f"{Colors.YELLOW}     已接受目前欄位值，繼續執行{Colors.RESET}")
            return {"success": True, "manual_step": True, "accepted_mismatch": True}
        if choice in ("s", "skip"):
            print(f"{Colors.YELLOW}     已略過此欄位{Colors.RESET}")
            return {"success": True, "manual_step": True, "skipped": True}
        if choice in ("q", "abort", "/abort", "quit", "/quit"):
            return {"success": False, "abort": True}


def _handle_guided_field_step(sap, result):
    _print_study_focus_result(result)

    if not result.get("requires_value"):
        input(f"{Colors.BLUE}     請在 SAP 完成此步驟後按 Enter 繼續... {Colors.RESET}")
        return {"success": True, "manual_step": True}

    choice = _prompt_study_field_value(result)
    mode = choice.get("mode")

    if mode == "abort":
        return {"success": False, "abort": True}
    if mode == "skip":
        print(f"{Colors.YELLOW}     已略過此欄位{Colors.RESET}")
        return {"success": True, "manual_step": True, "skipped": True}
    if mode == "manual" or not STUDY_AUTOFILL_PROMPTED_VALUES:
        value = choice.get("value") or result.get("target_value", "")
        return _wait_for_manual_field_and_verify(sap, result, value)

    value = choice.get("value", "")
    element_id = result.get("element_id", "")
    try:
        session = sap.get_session()
        _focus_study_element(session, element_id)
        fill_result = set_text(session, element_id=element_id, value=value)
    except Exception as exc:
        fill_result = {"success": False, "error": str(exc)}

    if fill_result.get("success"):
        verify_result = _verify_guided_field_value(sap, result, value)
        if verify_result.get("success"):
            print(f"{Colors.GREEN}     ✓ 已填入並驗證本次值: {value}{Colors.RESET}")
            return {"success": True, "manual_step": True}
        print(f"{Colors.YELLOW}     自動填入後驗證失敗: {verify_result.get('error') or verify_result}{Colors.RESET}")
        return _wait_for_manual_field_and_verify(sap, result, value)

    print(f"{Colors.YELLOW}     自動填入失敗: {fill_result.get('error') or fill_result}{Colors.RESET}")
    return _wait_for_manual_field_and_verify(sap, result, value)


def _verify_study_event_achieved(sap, event):
    if event.get("event_type") != "SCREEN_CHANGE":
        return {"success": True}
    session = sap.get_session()
    screen = scan_sap_screen(session)
    return _verify_study_screen_state(screen, event.get("details", {}))


def _popup_recovery_fields_from_screen(screen):
    popup = screen.get("active_popup") or {}
    if not popup:
        return None, []

    fields = []
    for field in popup.get("fields", []):
        field_id = field.get("id", "")
        if not field_id:
            continue
        field_type = field.get("type", "")
        if any(token in field_type for token in ("Button", "Label", "Statusbar", "Toolbar")):
            continue
        fields.append(field)

    empty_required = [
        field for field in fields
        if field.get("required") is True and not _normalize_study_value(_value_from_scanned_item(field))
    ]
    empty_fields = [
        field for field in fields
        if field not in empty_required and not _normalize_study_value(_value_from_scanned_item(field))
    ]
    return popup, empty_required + empty_fields


def _popup_recovery_context(screen, field):
    popup = screen.get("active_popup") or {}
    return _field_context_from_item(
        field,
        field.get("id", ""),
        "active_popup.fields",
        screen_title=screen.get("title", ""),
        popup_title=popup.get("title", ""),
    )


def _prompt_popup_field_value(field_result):
    field_context = field_result.get("field_context") or {}
    display_name = field_context.get("display_name") or field_result.get("element_id", "")
    current_value = field_context.get("current_value", "")
    required = field_context.get("required")

    print(f"{Colors.YELLOW}     彈窗欄位: {display_name}{Colors.RESET}")
    meta_parts = []
    if field_context.get("field_type"):
        meta_parts.append(field_context["field_type"])
    if required is True:
        meta_parts.append("必填")
    if current_value:
        meta_parts.append(f"目前值: {current_value}")
    if field_context.get("popup_title"):
        meta_parts.append(f"彈窗: {field_context['popup_title'].strip()}")
    if meta_parts:
        print(f"{Colors.DIM}     欄位資訊: {' │ '.join(meta_parts)}{Colors.RESET}")
    print(f"{Colors.DIM}     元件: {field_context.get('short_id', '')} │ {field_result.get('element_id', '')}{Colors.RESET}")
    print(f"{Colors.DIM}     輸入本次值後在 SAP GUI 手動填入；/manual 手動完成；/skip 略過；/abort 中止{Colors.RESET}")

    while True:
        raw_value = input(f"{Colors.BLUE}     本次值 [{current_value}] > {Colors.RESET}")
        command = raw_value.strip().lower()
        if command in ("/skip", "skip"):
            return {"mode": "skip"}
        if command in ("/manual", "manual"):
            return {"mode": "manual", "value": ""}
        if command in ("/abort", "abort", "/quit", "quit"):
            return {"mode": "abort"}
        value = current_value if raw_value == "" else raw_value
        if required is True and not _normalize_study_value(value):
            print(f"{Colors.YELLOW}     此欄位為必填，請輸入值或使用 /manual 手動完成{Colors.RESET}")
            continue
        return {"mode": "value", "value": value}


def _wait_for_popup_field_manual_and_verify(sap, field_result, expected_value=""):
    required = (field_result.get("field_context") or {}).get("required") is True
    display_name = (field_result.get("field_context") or {}).get("display_name") or field_result.get("element_id", "")

    while True:
        if expected_value:
            print(f"{Colors.YELLOW}     請在 SAP GUI 手動輸入 {display_name}: {expected_value}{Colors.RESET}")
        else:
            print(f"{Colors.YELLOW}     請在 SAP GUI 手動完成欄位: {display_name}{Colors.RESET}")
        user_action = input(
            f"{Colors.BLUE}     完成後按 Enter 驗證；/retry 重新定位；/skip 略過；/abort 中止 > {Colors.RESET}"
        ).strip().lower()

        if user_action in ("/abort", "abort", "/quit", "quit"):
            return {"success": False, "abort": True}
        if user_action in ("/skip", "skip"):
            return {"success": True, "skipped": True}
        if user_action in ("/retry", "retry", "r"):
            try:
                session = sap.get_session()
                field_result["focus"] = _focus_study_element(session, field_result.get("element_id", ""))
                _print_study_focus_result(field_result)
            except Exception as exc:
                print(f"{Colors.YELLOW}     重新定位失敗: {exc}{Colors.RESET}")
            continue

        if expected_value:
            verify_result = _verify_guided_field_value(sap, field_result, expected_value)
            if verify_result.get("success"):
                print(f"{Colors.GREEN}     ✓ 已驗證欄位值: {verify_result.get('actual')} ({verify_result.get('source')}){Colors.RESET}")
                return {"success": True}
            print(f"{Colors.RED}     驗證失敗: {verify_result.get('error') or verify_result}{Colors.RESET}")
            continue

        read_result = _read_study_element_value(sap.get_session(), field_result.get("element_id", ""))
        if read_result.get("success") and (not required or _normalize_study_value(read_result.get("value", ""))):
            print(f"{Colors.GREEN}     ✓ 已讀取欄位值: {read_result.get('value')} ({read_result.get('source')}){Colors.RESET}")
            return {"success": True}
        print(f"{Colors.RED}     驗證失敗: {read_result.get('error') or '必填欄位仍為空白'}{Colors.RESET}")


def _run_popup_guided_recovery(sap, event):
    session = sap.get_session()
    screen = scan_sap_screen(session)
    popup, fields = _popup_recovery_fields_from_screen(screen)
    if not popup:
        return {"success": False, "error": "目前沒有可引導處理的彈窗"}
    if not fields:
        return {"success": False, "error": "彈窗沒有偵測到空白或必填欄位"}

    print(f"{Colors.CYAN}     Guided Popup Recovery: {popup.get('title', popup.get('id', '彈窗'))}{Colors.RESET}")
    for index, field in enumerate(fields, 1):
        field_context = _popup_recovery_context(screen, field)
        field_result = {
            "success": True,
            "action": "study_popup_guided_field",
            "requires_human": True,
            "element_id": field.get("id", ""),
            "target_value": field_context.get("current_value", ""),
            "field_context": field_context,
            "focus": _focus_study_element(session, field.get("id", "")),
        }
        print(f"{Colors.WHITE}     [{index}/{len(fields)}] {field_context.get('display_name')}{Colors.RESET}")
        _print_study_focus_result(field_result)

        choice = _prompt_popup_field_value(field_result)
        mode = choice.get("mode")
        if mode == "abort":
            return {"success": False, "abort": True}
        if mode == "skip":
            print(f"{Colors.YELLOW}     已略過彈窗欄位{Colors.RESET}")
            continue
        expected = choice.get("value", "")
        verify_result = _wait_for_popup_field_manual_and_verify(sap, field_result, expected)
        if verify_result.get("abort"):
            return {"success": False, "abort": True}
        if not verify_result.get("success"):
            return verify_result

    submit_choice = input(
        f"{Colors.BLUE}     彈窗欄位已處理。Enter 送出彈窗並驗證；/manual 自行送出；/abort 中止 > {Colors.RESET}"
    ).strip().lower()
    if submit_choice in ("/abort", "abort", "q", "quit", "/quit"):
        return {"success": False, "abort": True}
    if submit_choice in ("/manual", "manual", "m"):
        input(f"{Colors.BLUE}     請在 SAP 手動送出彈窗後按 Enter 驗證... {Colors.RESET}")
    else:
        session = sap.get_session()
        submit_result = send_vkey(session, 0, window_id=popup.get("id", ""))
        if not submit_result.get("success"):
            return {
                "success": False,
                "error": f"彈窗 Enter 送出失敗: {submit_result.get('error') or submit_result}",
            }

    verify_result = _verify_study_event_achieved(sap, event)
    if verify_result.get("success"):
        note = "彈窗已完成並驗證"
        if event.get("event_type") == "SCREEN_CHANGE":
            note = f"彈窗已完成，已驗證到畫面 {verify_result.get('actual_screen')}"
        print(f"{Colors.GREEN}     ✓ {note}{Colors.RESET}")
        return {"success": True, "manual_step": True}
    return {
        "success": False,
        "error": f"彈窗處理後仍未達成目標: {verify_result.get('error') or verify_result}",
    }


def _has_popup_recovery_fields(sap):
    try:
        screen = scan_sap_screen(sap.get_session())
        _, fields = _popup_recovery_fields_from_screen(screen)
        return bool(fields)
    except Exception:
        return False


def _recover_failed_study_step(sap, event, previous_event, next_event, result):
    print(f"{Colors.RED}     ✗ {result.get('error') or result.get('message') or result}{Colors.RESET}")
    while True:
        has_popup_fields = _has_popup_recovery_fields(sap)
        if has_popup_fields:
            prompt = "偵測到彈窗欄位。Enter/g 引導補彈窗、r 重試、m 手動完成、s 略過、a 中止 > "
        else:
            prompt = "輸入 r 重試、m 手動完成、s 略過、a 中止 > "
        choice = input(
            f"{Colors.BLUE}     {prompt}{Colors.RESET}"
        ).strip().lower()

        if has_popup_fields and choice in ("", "g", "guided", "popup"):
            popup_result = _run_popup_guided_recovery(sap, event)
            if popup_result.get("abort"):
                return popup_result
            if popup_result.get("success"):
                return popup_result
            print(f"{Colors.RED}     Guided popup recovery 失敗: {popup_result.get('error') or popup_result}{Colors.RESET}")
            continue

        if choice in ("r", "retry", ""):
            try:
                session = sap.get_session()
                retry_result = execute_study_event(
                    session,
                    event,
                    previous_event=previous_event,
                    next_event=next_event,
                )
            except Exception as exc:
                retry_result = {"success": False, "error": str(exc)}

            if retry_result.get("success"):
                note = retry_result.get("study_note") or retry_result.get("message") or "重試完成"
                print(f"{Colors.GREEN}     ✓ {note}{Colors.RESET}")
                return {"success": True}
            print(f"{Colors.RED}     重試仍失敗: {retry_result.get('error') or retry_result}{Colors.RESET}")
            continue

        if choice in ("m", "manual"):
            input(f"{Colors.BLUE}     請在 SAP 手動完成此步驟後按 Enter 驗證... {Colors.RESET}")
            verify_result = _verify_study_event_achieved(sap, event)
            if verify_result.get("success"):
                note = "手動完成並已驗證"
                if event.get("event_type") == "SCREEN_CHANGE":
                    note = f"手動完成並已驗證到畫面 {verify_result.get('actual_screen')}"
                print(f"{Colors.GREEN}     ✓ {note}{Colors.RESET}")
                return {"success": True, "manual_step": True}
            print(f"{Colors.RED}     驗證失敗: {verify_result.get('error') or verify_result}{Colors.RESET}")
            continue

        if choice in ("s", "skip"):
            print(f"{Colors.YELLOW}     已略過此步驟{Colors.RESET}")
            return {"success": True, "skipped": True}

        if choice in ("a", "abort", "q", "quit"):
            return {"success": False, "abort": True}


def _handle_study_review(sap, event, previous_event, next_event, result):
    print(f"{Colors.YELLOW}     {result.get('study_note') or result.get('message') or '目前狀態與參考錄製不同'}{Colors.RESET}")
    verify = result.get("screen_verify") or {}
    if verify:
        print(
            f"{Colors.DIM}     參考: T-Code {verify.get('expected_tcode') or 'N/A'} / "
            f"畫面 {verify.get('expected_screen') or 'N/A'}；目前: "
            f"{verify.get('actual_tcode') or 'N/A'} / {verify.get('actual_screen') or 'N/A'}{Colors.RESET}"
        )

    while True:
        choice = input(
            f"{Colors.BLUE}     Enter 接受目前狀態繼續；r 重試此步；m 手動完成並驗證；s 略過；a 中止 > {Colors.RESET}"
        ).strip().lower()
        if choice in ("", "accept", "y", "yes"):
            print(f"{Colors.YELLOW}     已接受目前 SAP 狀態，繼續下一步{Colors.RESET}")
            return {"success": True, "accepted_review": True}
        if choice in ("r", "retry"):
            retry_result = execute_study_event(
                sap.get_session(),
                event,
                previous_event=previous_event,
                next_event=next_event,
            )
            if retry_result.get("success") and not retry_result.get("requires_review"):
                note = retry_result.get("study_note") or retry_result.get("message") or "重試完成"
                print(f"{Colors.GREEN}     ✓ {note}{Colors.RESET}")
                return {"success": True}
            if retry_result.get("requires_review"):
                result = retry_result
                print(f"{Colors.YELLOW}     重試後仍需確認: {result.get('study_note')}{Colors.RESET}")
                continue
            print(f"{Colors.RED}     重試失敗: {retry_result.get('error') or retry_result}{Colors.RESET}")
            return _recover_failed_study_step(sap, event, previous_event, next_event, retry_result)
        if choice in ("m", "manual"):
            input(f"{Colors.BLUE}     請在 SAP 手動完成此步驟後按 Enter 驗證... {Colors.RESET}")
            verify_result = _verify_study_event_achieved(sap, event)
            if verify_result.get("success"):
                print(f"{Colors.GREEN}     ✓ 手動完成並已驗證{Colors.RESET}")
                return {"success": True, "manual_step": True}
            print(f"{Colors.YELLOW}     驗證仍與參考不同: {verify_result.get('error') or verify_result}{Colors.RESET}")
            result = {"requires_review": True, "screen_verify": verify_result, "study_note": "手動完成後目前狀態仍與參考錄製不同"}
            continue
        if choice in ("s", "skip"):
            print(f"{Colors.YELLOW}     已略過此參考步驟{Colors.RESET}")
            return {"success": True, "skipped": True}
        if choice in ("a", "abort", "q", "quit", "/abort", "/quit"):
            return {"success": False, "abort": True}


def run_study(skill_library, sap, name, delay_seconds=STUDY_STEP_DELAY_SECONDS):
    """執行 SOP / skill，每個自動 step 後固定等待。"""
    data = skill_library.load_recording(name)
    events = data.get("events", [])

    if not events:
        print(f"\n{Colors.YELLOW}  SOP / skill '{name}' 沒有可執行步驟{Colors.RESET}\n")
        return

    print(f"\n{Colors.CYAN}▶ Study Mode: {name}{Colors.RESET}")
    print(
        f"{Colors.DIM}  來源: {data.get('source', 'recording')} │ "
        f"共 {len(events)} 個 SOP 步驟，每個自動步驟間隔 {delay_seconds} 秒{Colors.RESET}\n"
    )
    if STUDY_ADAPTIVE_MODE:
        print(f"{Colors.DIM}  模式: 互動式參考引導。錄製值與畫面只作為參考，會依目前 SAP 狀態調整。{Colors.RESET}\n")

    previous_event = None
    for index, event in enumerate(events, 1):
        next_event = events[index] if index < len(events) else None
        title_context = None
        if event.get("event_type") == "FIELD_CHANGE":
            try:
                title_session = sap.get_session()
                title_context = _resolve_study_field_context(
                    title_session,
                    event.get("details", {}).get("element_id", ""),
                )
            except Exception:
                title_context = None
        print(f"{Colors.WHITE}  [{index}/{len(events)}] {_event_title_with_context(event, title_context)}{Colors.RESET}")

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

        guided_step = bool(result.get("requires_human"))
        if guided_step:
            handled = _handle_guided_field_step(sap, result)
            if handled.get("abort"):
                print(f"{Colors.YELLOW}     已中止 Study Mode{Colors.RESET}\n")
                return
        elif result.get("requires_review"):
            reviewed = _handle_study_review(
                sap,
                event,
                previous_event=previous_event,
                next_event=next_event,
                result=result,
            )
            if reviewed.get("abort") or not reviewed.get("success"):
                print(f"{Colors.YELLOW}     已中止 Study Mode{Colors.RESET}\n")
                return
            guided_step = bool(reviewed.get("manual_step") or reviewed.get("skipped") or reviewed.get("accepted_review"))
        elif result.get("success"):
            note = result.get("study_note") or result.get("message") or result.get("action", "完成")
            print(f"{Colors.GREEN}     ✓ {note}{Colors.RESET}")
        else:
            recovered = _recover_failed_study_step(
                sap,
                event,
                previous_event=previous_event,
                next_event=next_event,
                result=result,
            )
            if not recovered.get("success"):
                print(f"{Colors.YELLOW}     已中止 Study Mode{Colors.RESET}\n")
                return
            guided_step = bool(recovered.get("manual_step") or recovered.get("skipped"))

        previous_event = event
        if index < len(events) and not guided_step and not result.get("skip_delay"):
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
    skill_library = SAPSkillLibrary()

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
                    run_study(skill_library, sap, sop_name)
                except FileNotFoundError:
                    print(f"\n{Colors.RED}  找不到 SOP / skill: '{sop_name}'{Colors.RESET}")
                    print(f"{Colors.DIM}  使用 /recordings 查看所有可用項目{Colors.RESET}\n")

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
