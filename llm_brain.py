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
import requests

from copilot_auth import CopilotAuth
from sap_agent_tools import (
    scan_sap_screen,
    TOOL_SCHEMAS,
    TOOL_FUNCTIONS,
    confirmed_click,
    confirmed_send_vkey,
)

# GitHub Copilot Chat Completions API
COPILOT_CHAT_URL = "https://api.githubcopilot.com/chat/completions"

# 預設模型
DEFAULT_MODEL = "gpt-4o"

# 最大 ReAct 迴圈次數（防止無限迴圈）
MAX_ITERATIONS = 10

# System Prompt - Auto Mode (可執行操作)
SYSTEM_PROMPT_AUTO = """你是一個專業的 SAP GUI 操作助手。你可以透過工具來操作 SAP 系統。

## 你的能力
1. 閱讀 SAP 畫面的結構化 JSON，理解當前畫面的狀態、欄位、按鈕
2. 使用工具 (set_text, click, send_vkey, set_tcode) 來操作 SAP 畫面
3. 根據狀態列訊息判斷操作是否成功

## 工作流程 (ReAct Loop)
1. **觀察 (Scan)**：閱讀提供的畫面 JSON，理解當前在哪個交易、有哪些欄位和按鈕
2. **思考 (Think)**：分析使用者的需求，決定需要執行哪些操作
3. **行動 (Act)**：呼叫適當的工具執行操作
4. **驗證 (Verify)**：根據工具回傳的結果判斷是否成功

## 重要規則
- 操作前先仔細閱讀畫面 JSON，確認元件 ID 正確
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
        token = self.auth.get_token()

        headers = {
            "Authorization": f"Bearer {token}",
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
                token = self.auth.get_token()
                headers["Authorization"] = f"Bearer {token}"
                resp = requests.post(
                    COPILOT_CHAT_URL,
                    headers=headers,
                    json=payload,
                    timeout=60,
                )

            if resp.status_code != 200:
                raise RuntimeError(
                    f"Copilot API 呼叫失敗: {resp.status_code}\n{resp.text}"
                )

            result = resp.json()

            # 偵錯：檢查回應結構
            if "choices" not in result:
                print(f"\033[33m[Agent] API 回應缺少 choices 欄位: {json.dumps(result, ensure_ascii=False)[:500]}\033[0m")
            elif len(result["choices"]) == 0:
                print(f"\033[33m[Agent] API 回應 choices 為空陣列\033[0m")

            return result

        except requests.Timeout:
            raise RuntimeError("Copilot API 回應逾時 (60s)")
        except requests.RequestException as e:
            raise RuntimeError(f"網路請求失敗: {e}")

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
                    return confirmed_send_vkey(session, tool_args["vkey"])
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
        screen_json = json.dumps(screen_state, ensure_ascii=False, indent=2)

        # 組合訊息
        combined_parts = [
            f"## 當前 SAP 畫面狀態\n```json\n{screen_json}\n```",
        ]

        if extra_context:
            combined_parts.append(f"\n## 相關 SOP 參考資料\n{extra_context}")

        combined_parts.append(f"\n## 使用者問題\n{user_message}")

        combined_message = "\n".join(combined_parts)
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
        screen_json = json.dumps(screen_state, ensure_ascii=False, indent=2)

        # 組合訊息：畫面狀態 + 使用者指令
        combined_message = (
            f"## 當前 SAP 畫面狀態\n"
            f"```json\n{screen_json}\n```\n\n"
            f"## 使用者指令\n{user_message}"
        )

        # 加入對話歷史
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

                print(f"\033[90m[Agent] 工具結果: {json.dumps(tool_result, ensure_ascii=False)}\033[0m")

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
