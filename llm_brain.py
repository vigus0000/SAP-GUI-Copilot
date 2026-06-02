"""
LLM Agent 大腦 — GitHub Copilot 版

透過 GitHub Copilot API (api.githubcopilot.com) 進行意圖識別與 Tool Calling。
實作 ReAct Loop: Scan → Think → Act → Verify

注意：Copilot API 相容 OpenAI Chat Completions 格式，但回應可能有差異，
需要額外的錯誤處理。

架構解耦：
- LLM 呼叫邏輯與 SAP Tools 完全分離
- 未來如需切換 LLM，只需修改此檔案的 endpoint 與 headers
"""

import json
import os
import time
import requests

try:
    import dotenv
    dotenv.load_dotenv()
except Exception:
    pass

from copilot_auth import CopilotAuth
from sap_agent_tools import (
    scan_sap_screen,
    TOOL_SCHEMAS,
    TOOL_FUNCTIONS,
    confirmed_click,
    confirmed_send_vkey,
    confirmed_handle_popup,
)

# GitHub Copilot Chat Completions API
COPILOT_CHAT_URL = "https://api.githubcopilot.com/chat/completions"

# 預設模型。可用 .env 的 COPILOT_MODEL 覆寫；不要再硬寫已退場的 gpt-4o。
DEFAULT_MODEL = os.getenv("COPILOT_MODEL", "gpt-5-mini")

# 最大 ReAct 迴圈次數（防止無限迴圈）
MAX_ITERATIONS = int(os.getenv("COPILOT_MAX_ITERATIONS", "6"))

# Copilot API 節流/重試設定
COPILOT_MAX_RETRIES = int(os.getenv("COPILOT_MAX_RETRIES", "4"))
COPILOT_RETRY_BASE_SECONDS = float(os.getenv("COPILOT_RETRY_BASE_SECONDS", "2"))
COPILOT_RETRY_MAX_SECONDS = float(os.getenv("COPILOT_RETRY_MAX_SECONDS", "60"))
CONVERSATION_HISTORY_LIMIT = int(os.getenv("COPILOT_HISTORY_LIMIT", "14"))

# System Prompt - Auto Mode (可執行操作)
SYSTEM_PROMPT_AUTO = """你是一個專業的 SAP GUI 操作助手。你可以透過工具來操作 SAP 系統。

## 你的能力
1. 閱讀 SAP 畫面的結構化 JSON，理解當前畫面的狀態、欄位、按鈕
2. 使用工具 (set_text, select_combo, set_editor_text, click, send_vkey, set_tcode, handle_popup) 來操作 SAP 畫面
3. 根據狀態列訊息判斷操作是否成功

## 工作流程 (ReAct Loop)
1. **觀察 (Scan)**：閱讀提供的畫面 JSON，理解當前在哪個交易、有哪些欄位和按鈕
2. **思考 (Think)**：分析使用者的需求，決定需要執行哪些操作
3. **行動 (Act)**：呼叫適當的工具執行操作
4. **驗證 (Verify)**：根據工具回傳的結果判斷是否成功

## 重要規則
- 操作前先仔細閱讀畫面 JSON，確認元件 ID 正確
- 如果畫面 JSON 有 active_popup 或 popup_wnd1，代表目前有 SAP 彈出視窗；請先處理彈窗，再操作主視窗
- 處理彈窗時優先使用 handle_popup；例如填寫彈窗「標題」後按儲存，使用 handle_popup(action="save", field_label="標題", value="...")
- 如果 active_popup 的 title 是「錯誤」或 messages 有錯誤文字，先讀 messages 判斷原因；通常要先 handle_popup(action="ok") 關閉最上層錯誤，再依下一層彈窗的 fields 補齊空白/焦點欄位
- screen JSON 中的 fields 會把欄位 label 與元件 ID 配對；填欄位時優先使用 fields 裡的 id 或 handle_popup(field_label=...)
- 如果 fields 的 type 是 GuiComboBox、dropdown=true 或含 options，代表下拉式選單；必須使用 select_combo，或在彈窗中用 handle_popup 依 label 選值，不要把它當一般文字欄位 set_text
- 下拉式選單若有 options，優先用 option key；沒有 key 時才用顯示文字
- 如果 scan 結果有 editors、role=editor、或 type=GuiShell 的 ABAP editor，代表程式碼編輯器；寫入 ABAP 原始碼時必須使用 set_editor_text，不要用 set_text
- set_editor_text 可不傳 element_id，工具會自動尋找目前畫面的 editor；如果 screen_after.editors 有 id，優先傳該 id
- 對彈窗發送 Enter/F12 等按鍵時，若不用 handle_popup，send_vkey 會自動送到活動彈窗，也可明確指定 window_id="wnd[1]"
        - 每次工具結果都可能包含 screen_after；後續操作必須以 screen_after 的最新元件 ID 為準，不要沿用舊畫面的 ID 或猜測不存在的 ID
- 如果操作失敗，嘗試分析原因並提出替代方案
- 遇到不確定的情況，向使用者詢問
- 使用繁體中文回覆使用者
- 回覆時簡潔明確，說明你做了什麼、結果如何

## SAP 基礎知識
- T-Code (Transaction Code) 是 SAP 交易代碼，如 VA01=建立銷售訂單, MM01=建立物料主檔
- wnd[0] 是主視窗，wnd[1] 是彈出視窗
- sbar 是狀態列，顯示操作結果訊息
- VKey 0=Enter, 3=F3(返回), 8=F8(執行), 11=Ctrl+S(儲存), 12=F12(取消)
"""

# System Prompt - Ask Mode (僅回答問題，不執行操作)
SYSTEM_PROMPT_ASK = """你是一個專業的 SAP GUI 問答助手。你**只回答問題，絕對不執行任何操作**。

## 你的能力
1. 閱讀 SAP 畫面的結構化 JSON，理解當前畫面的狀態
2. 結合 SAP 知識，回答使用者關於操作流程的問題
3. 解讀狀態列的錯誤訊息，提供解決方案
4. 參考已錄製的 SOP 操作紀錄，提供步驟引導

## 重要規則
- **你不能呼叫任何工具**，只能用文字回答
- 根據當前畫面 JSON 分析「這格該填什麼」「為何報錯」等問題
- 如果提供了 SOP 紀錄，參考其步驟來引導使用者
- 使用繁體中文回覆
- 回覆時結構清晰，使用列表或步驟說明

## SAP 基礎知識
- T-Code (Transaction Code) 是 SAP 交易代碼
- 狀態列類型: S=成功, W=警告, E=錯誤, I=資訊, A=中止
- 常見欄位: VBELN=單號, MATNR=物料, BUKRS=公司代碼, WERKS=工廠
"""


class SAPAgent:
    """
    SAP GUI AI Agent

    透過 GitHub Copilot API 實現自然語言操作 SAP 的能力。
    """

    def __init__(self, auth: CopilotAuth, model: str = DEFAULT_MODEL):
        """
        初始化 Agent。

        Args:
            auth: CopilotAuth 認證物件
            model: 使用的模型名稱 (預設 gpt-4o)
        """
        self.auth = auth
        self.model = model
        self._mode = "auto"  # "auto" 或 "ask"
        self.conversation_history = [
            {"role": "system", "content": SYSTEM_PROMPT_AUTO}
        ]

    @property
    def mode(self):
        """當前模式: 'auto' 或 'ask'"""
        return self._mode

    def set_mode(self, mode: str):
        """
        切換 Agent 模式。

        Args:
            mode: 'auto' 或 'ask'
        """
        if mode not in ("auto", "ask"):
            raise ValueError(f"不支援的模式: {mode}，請使用 'auto' 或 'ask'")

        self._mode = mode
        prompt = SYSTEM_PROMPT_AUTO if mode == "auto" else SYSTEM_PROMPT_ASK
        self.conversation_history = [
            {"role": "system", "content": prompt}
        ]
        mode_name = "🟣 Auto Mode（自動代操）" if mode == "auto" else "🟢 Ask Mode（問答模式）"
        print(f"\033[90m[Agent] 已切換至 {mode_name}\033[0m")

    def _call_copilot_api(self, messages, tools=None):
        """
        呼叫 GitHub Copilot Chat Completions API。

        Args:
            messages: 對話歷史
            tools: Function Calling 工具定義（可選）

        Returns:
            dict: API 回應

        Raises:
            RuntimeError: API 呼叫失敗
        """
        headers = {
            "Authorization": f"Bearer {self.auth.get_token()}",
            "Content-Type": "application/json",
            "Editor-Version": "vscode/1.100.0",
            "Editor-Plugin-Version": "copilot-chat/0.24.0",
            "Copilot-Integration-Id": "vscode-chat",
            "Openai-Intent": "conversation-panel",
        }

        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.1,  # 低溫度，確保操作穩定
        }

        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        last_error = None
        for attempt in range(COPILOT_MAX_RETRIES + 1):
            try:
                resp = requests.post(
                    COPILOT_CHAT_URL,
                    headers=headers,
                    json=payload,
                    timeout=60,
                )

                if resp.status_code == 401:
                    # Token 過期，嘗試刷新後重試
                    print("\033[33m[Agent] Copilot Token 過期，正在刷新...\033[0m")
                    self.auth._refresh_copilot_token()
                    headers["Authorization"] = f"Bearer {self.auth.get_token()}"
                    continue

                if resp.status_code in (429, 500, 502, 503, 504):
                    last_error = self._format_api_error(resp)
                    if attempt < COPILOT_MAX_RETRIES:
                        wait_time = self._retry_delay_seconds(resp, attempt)
                        print(
                            "\033[33m"
                            f"[Agent] Copilot API {resp.status_code}，"
                            f"{wait_time:.1f} 秒後重試 "
                            f"({attempt + 1}/{COPILOT_MAX_RETRIES})，model={self.model}"
                            "\033[0m"
                        )
                        time.sleep(wait_time)
                        continue

                if resp.status_code != 200:
                    raise RuntimeError(self._format_api_error(resp))

                result = resp.json()

                # 偵錯：檢查回應結構
                if "choices" not in result:
                    print(f"\033[33m[Agent] API 回應缺少 choices 欄位: {json.dumps(result, ensure_ascii=False)[:500]}\033[0m")
                elif len(result["choices"]) == 0:
                    print(f"\033[33m[Agent] API 回應 choices 為空陣列\033[0m")

                return result

            except requests.Timeout:
                last_error = "Copilot API 回應逾時 (60s)"
                if attempt < COPILOT_MAX_RETRIES:
                    wait_time = self._retry_delay_seconds(None, attempt)
                    print(f"\033[33m[Agent] {last_error}，{wait_time:.1f} 秒後重試 ({attempt + 1}/{COPILOT_MAX_RETRIES})\033[0m")
                    time.sleep(wait_time)
                    continue
                raise RuntimeError(last_error)

            except requests.RequestException as e:
                last_error = f"網路請求失敗: {e}"
                if attempt < COPILOT_MAX_RETRIES:
                    wait_time = self._retry_delay_seconds(None, attempt)
                    print(f"\033[33m[Agent] {last_error}，{wait_time:.1f} 秒後重試 ({attempt + 1}/{COPILOT_MAX_RETRIES})\033[0m")
                    time.sleep(wait_time)
                    continue
                raise RuntimeError(last_error)

        raise RuntimeError(last_error or "Copilot API 呼叫失敗")

    def _retry_delay_seconds(self, resp, attempt):
        """依 Retry-After 或 exponential backoff 計算等待時間。"""
        if resp is not None:
            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                try:
                    return min(float(retry_after), COPILOT_RETRY_MAX_SECONDS)
                except ValueError:
                    pass

        delay = COPILOT_RETRY_BASE_SECONDS * (2 ** attempt)
        return min(delay, COPILOT_RETRY_MAX_SECONDS)

    def _format_api_error(self, resp):
        retry_after = resp.headers.get("Retry-After")
        retry_part = f"\nRetry-After: {retry_after}" if retry_after else ""
        return (
            f"Copilot API 呼叫失敗: {resp.status_code}"
            f"\nmodel: {self.model}"
            f"{retry_part}"
            f"\n{resp.text}"
        )

    def _execute_tool_call(self, session, tool_name, tool_args):
        """
        執行 LLM 回傳的 Tool Call。

        Args:
            session: SAP Session COM 物件
            tool_name: 工具名稱
            tool_args: 工具參數 dict

        Returns:
            dict: 工具執行結果
        """
        if tool_name not in TOOL_FUNCTIONS:
            return {"success": False, "error": f"未知的工具: {tool_name}"}

        tool_func = TOOL_FUNCTIONS[tool_name]

        # 呼叫工具函數（所有工具的第一個參數都是 session）
        return tool_func(session, **tool_args)

    def _screen_summary(self, screen_state):
        """產生給終端機看的簡短畫面摘要。"""
        active_popup = screen_state.get("active_popup") or {}
        return {
            "tcode": screen_state.get("tcode", ""),
            "title": screen_state.get("title", ""),
            "screen_number": screen_state.get("screen_number", ""),
            "active_window": screen_state.get("active_window", ""),
            "focused_element": screen_state.get("focused_element"),
            "status_bar": screen_state.get("status_bar", {}),
            "fields": self._compact_fields(screen_state.get("fields", [])),
            "messages": screen_state.get("messages", [])[:10],
            "editors": screen_state.get("editors", [])[:10],
            "active_popup": {
                "id": active_popup.get("id", ""),
                "title": active_popup.get("title", ""),
                "actions": active_popup.get("actions", []),
                "messages": active_popup.get("messages", [])[:10],
                "fields": self._compact_fields(active_popup.get("fields", [])),
                "editors": active_popup.get("editors", [])[:10],
                "focused_element": active_popup.get("focused_element"),
                "element_count": len(active_popup.get("elements", [])),
            } if active_popup else None,
            "element_count": len(screen_state.get("elements", [])),
        }

    def _compact_fields(self, fields, max_fields=20, max_options=12):
        compacted = []
        for field in fields[:max_fields]:
            item = dict(field)
            if "options" in item:
                item["options"] = item.get("options", [])[:max_options]
                item["options_truncated"] = len(field.get("options", [])) > max_options
            compacted.append(item)
        return compacted

    def _screen_context(self, screen_state):
        """給 LLM 的畫面上下文，保留可操作摘要，避免傳完整 SAP DOM。"""
        return self._screen_summary(screen_state)

    def _attach_screen_after_tool(self, session, tool_result):
        """工具執行後重新掃描 SAP，讓下一輪推理看到最新畫面。"""
        try:
            screen_state = scan_sap_screen(session)
            screen_summary = self._screen_summary(screen_state)
            tool_result["screen_after"] = screen_summary
            tool_result["screen_summary"] = screen_summary
        except Exception as e:
            tool_result["screen_after_error"] = f"工具執行後重新掃描失敗: {e}"
        return tool_result

    def _trim_conversation_history(self):
        """跨使用者請求時裁切歷史，丟掉舊 tool trace，避免 payload 不斷膨脹。"""
        if len(self.conversation_history) <= CONVERSATION_HISTORY_LIMIT:
            return

        system_messages = [
            msg for msg in self.conversation_history
            if msg.get("role") == "system"
        ][:1]

        compactable_messages = [
            msg for msg in self.conversation_history
            if (
                msg.get("role") != "system"
                and msg.get("role") != "tool"
                and not msg.get("tool_calls")
            )
        ]
        recent_messages = [
            msg for msg in compactable_messages
        ][-(CONVERSATION_HISTORY_LIMIT - len(system_messages)):]
        self.conversation_history = system_messages + recent_messages

    def _handle_confirmation(self, session, tool_result, tool_name, tool_args):
        """
        處理需要使用者確認的敏感操作。

        Args:
            session: SAP Session COM 物件
            tool_result: 工具回傳的結果（包含 requires_confirmation）
            tool_name: 工具名稱
            tool_args: 工具參數

        Returns:
            dict: 確認後的執行結果，或取消的結果
        """
        message = tool_result.get("message", "需要確認的操作")
        print(f"\n\033[1;33m  {message}\033[0m")
        print(f"\033[1;33m  工具: {tool_name}, 參數: {tool_args}\033[0m")

        while True:
            user_input = input("\n\033[1;33m  確認執行？(y/n): \033[0m").strip().lower()
            if user_input in ("y", "yes", "是"):
                # 使用者確認，執行對應的 confirmed 版本
                if tool_name == "click":
                    return confirmed_click(session, tool_args["element_id"])
                elif tool_name == "send_vkey":
                    return confirmed_send_vkey(
                        session,
                        tool_args["vkey"],
                        tool_args.get("window_id", ""),
                    )
                elif tool_name == "handle_popup":
                    return confirmed_handle_popup(
                        session,
                        action=tool_args.get("action", "ok"),
                        field_label=tool_args.get("field_label", ""),
                        value=tool_args.get("value", ""),
                        window_id=tool_args.get("window_id", ""),
                    )
                else:
                    # 其他工具直接執行
                    return self._execute_tool_call(session, tool_name, tool_args)
            elif user_input in ("n", "no", "否"):
                return {
                    "success": False,
                    "action": tool_name,
                    "message": "使用者取消了操作",
                }
            else:
                print("\033[33m  請輸入 y 或 n\033[0m")

    def process_message(self, session, user_message: str, extra_context: str = "") -> str:
        """
        處理使用者的自然語言訊息。

        根據當前模式決定行為：
        - Auto Mode: 執行 ReAct Loop（可呼叫工具操作 SAP）
        - Ask Mode: 僅回答問題（不呼叫工具）

        Args:
            session: SAP Session COM 物件
            user_message: 使用者輸入的自然語言指令
            extra_context: 額外的上下文資訊（如 SOP 紀錄摘要）

        Returns:
            str: AI 的最終回應文字
        """
        if self._mode == "ask":
            return self._process_ask(session, user_message, extra_context)
        else:
            return self._process_auto(session, user_message)

    def _process_ask(self, session, user_message: str, extra_context: str = "") -> str:
        """
        Ask Mode: 結合畫面狀態回答問題（不執行操作）。
        """
        # 掃描當前畫面
        print("\033[90m[Agent] 正在掃描 SAP 畫面...\033[0m")
        screen_state = scan_sap_screen(session)
        screen_json = json.dumps(self._screen_context(screen_state), ensure_ascii=False, indent=2)

        # 組合訊息
        combined_parts = [
            f"## 當前 SAP 畫面狀態\n```json\n{screen_json}\n```",
        ]

        if extra_context:
            combined_parts.append(f"\n## 相關 SOP 參考資料\n{extra_context}")

        combined_parts.append(f"\n## 使用者問題\n{user_message}")

        combined_message = "\n".join(combined_parts)
        self._trim_conversation_history()
        self.conversation_history.append({"role": "user", "content": combined_message})

        # 呼叫 LLM（不傳 tools，禁止操作）
        try:
            response = self._call_copilot_api(
                messages=self.conversation_history,
                tools=None,  # Ask Mode 不提供工具
            )
        except RuntimeError as e:
            return f"LLM 呼叫失敗: {e}"

        choices = response.get("choices", [])
        if not choices:
            error_info = response.get("error", {})
            if error_info:
                return f"Copilot API 錯誤: {error_info.get('message', '')}"
            return "Copilot API 回應異常，請稍後重試。"

        message = choices[0].get("message", {})
        if not message:
            return "Copilot API 回應格式異常"

        self.conversation_history.append(message)
        return message.get("content", "(AI 未回傳訊息)")

    def _process_auto(self, session, user_message: str) -> str:
        """
        Auto Mode: 執行 ReAct Loop（可呼叫工具操作 SAP）。
        """
        # Step 1: 掃描當前畫面
        print("\033[90m[Agent] 正在掃描 SAP 畫面...\033[0m")
        screen_state = scan_sap_screen(session)
        screen_json = json.dumps(self._screen_context(screen_state), ensure_ascii=False, indent=2)

        # 組合訊息：畫面狀態 + 使用者指令
        combined_message = (
            f"## 當前 SAP 畫面狀態\n"
            f"```json\n{screen_json}\n```\n\n"
            f"## 使用者指令\n{user_message}"
        )

        # 加入對話歷史
        self._trim_conversation_history()
        self.conversation_history.append({"role": "user", "content": combined_message})

        # Step 2-4: ReAct Loop (Think → Act → Verify)
        for iteration in range(MAX_ITERATIONS):
            print(f"\033[90m[Agent] ReAct 迭代 {iteration + 1}/{MAX_ITERATIONS}\033[0m")

            # 呼叫 LLM
            try:
                response = self._call_copilot_api(
                    messages=self.conversation_history,
                    tools=TOOL_SCHEMAS,
                )
            except RuntimeError as e:
                error_msg = f"LLM 呼叫失敗: {e}"
                print(f"\033[31m[Agent] {error_msg}\033[0m")
                return error_msg

            # 解析 LLM 回應
            choices = response.get("choices", [])
            if not choices:
                error_info = response.get("error", {})
                if error_info:
                    return f"Copilot API 錯誤: {error_info.get('message', json.dumps(error_info, ensure_ascii=False))}"
                return f"Copilot API 回應異常（無 choices），請稍後重試。"

            choice = choices[0]
            message = choice.get("message", {})
            finish_reason = choice.get("finish_reason", "")

            if not message:
                return "Copilot API 回應格式異常（空 message）"

            self.conversation_history.append(message)

            # 情況 A：LLM 直接回應文字
            if finish_reason == "stop" or not message.get("tool_calls"):
                final_text = message.get("content", "")
                if final_text:
                    return final_text
                return "(AI 未回傳任何訊息)"

            # 情況 B：LLM 要求呼叫工具
            tool_calls = message.get("tool_calls", [])
            for tool_call in tool_calls:
                func = tool_call.get("function", {})
                tool_name = func.get("name", "")
                tool_call_id = tool_call.get("id", "")

                try:
                    tool_args = json.loads(func.get("arguments", "{}"))
                except json.JSONDecodeError:
                    tool_args = {}

                print(f"\033[36m[Agent] 呼叫工具: {tool_name}({tool_args})\033[0m")

                tool_result = self._execute_tool_call(session, tool_name, tool_args)

                if tool_result.get("requires_confirmation"):
                    tool_result = self._handle_confirmation(
                        session, tool_result, tool_name, tool_args
                    )

                tool_result = self._attach_screen_after_tool(session, tool_result)

                console_result = dict(tool_result)
                if "screen_after" in console_result:
                    console_result["screen_after"] = console_result.get("screen_summary")
                print(f"\033[90m[Agent] 工具結果: {json.dumps(console_result, ensure_ascii=False)}\033[0m")

                self.conversation_history.append({
                    "role": "tool",
                    "tool_call_id": tool_call_id,
                    "content": json.dumps(tool_result, ensure_ascii=False),
                })

        return "⚠️ 已達最大操作次數限制，請確認當前畫面狀態是否正確。"

    def reset_conversation(self):
        """重置對話歷史（保持當前模式）"""
        prompt = SYSTEM_PROMPT_AUTO if self._mode == "auto" else SYSTEM_PROMPT_ASK
        self.conversation_history = [
            {"role": "system", "content": prompt}
        ]
        print("\033[90m[Agent] 對話歷史已重置\033[0m")
