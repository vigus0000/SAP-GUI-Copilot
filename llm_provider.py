"""LLM provider adapters for SAP Copilot.

The rest of the application consumes Chat Completions-like responses. Provider
adapters keep authentication, transport, and compatibility shims isolated.
"""

import json
import os
import re
import time
import uuid
from pathlib import Path

import requests

from codex_auth import CodexAuth
from copilot_auth import CopilotAuth

try:
    import dotenv
    dotenv.load_dotenv()
except Exception:
    pass


COPILOT_CHAT_URL = "https://api.githubcopilot.com/chat/completions"
CODEX_OAUTH_CHAT_URL = os.getenv("CODEX_OAUTH_CHAT_URL", "https://chatgpt.com/backend-api/codex/responses")
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "github_copilot").strip().lower()
COPILOT_MODEL = os.getenv("COPILOT_MODEL", "gpt-5-mini")

LLM_MAX_RETRIES = int(os.getenv("COPILOT_MAX_RETRIES", "4"))
LLM_RETRY_BASE_SECONDS = float(os.getenv("COPILOT_RETRY_BASE_SECONDS", "2"))
LLM_RETRY_MAX_SECONDS = float(os.getenv("COPILOT_RETRY_MAX_SECONDS", "60"))


def env_enabled(name, default="false"):
    return str(os.getenv(name, default)).strip().lower() in {"1", "true", "yes", "y", "on"}


def normalize_provider_name(value=None):
    """Normalize user/env provider names."""
    if value is None:
        value = os.getenv("LLM_PROVIDER", LLM_PROVIDER)
    text = str(value or "github_copilot").strip().lower().replace("-", "_")
    aliases = {
        "github": "github_copilot",
        "githubcopilot": "github_copilot",
        "copilot": "github_copilot",
        "github_copilot": "github_copilot",
        "codex": "codex_oauth",
        "codex_oauth": "codex_oauth",
    }
    return aliases.get(text, text)


def provider_display_name(provider_name):
    provider = normalize_provider_name(provider_name)
    if provider == "codex_oauth":
        return "Codex OAuth"
    return "GitHub Copilot"


def copilot_model():
    return os.getenv("COPILOT_MODEL", COPILOT_MODEL)


def codex_oauth_model():
    return os.getenv("CODEX_OAUTH_MODEL") or "gpt-5.5"


def default_model_for_provider(provider_name):
    provider = normalize_provider_name(provider_name)
    if provider == "codex_oauth":
        return codex_oauth_model()
    return copilot_model()


def retry_delay_seconds(resp, attempt):
    """Calculate delay from Retry-After or exponential backoff."""
    if resp is not None:
        retry_after = resp.headers.get("Retry-After")
        if retry_after:
            try:
                return min(float(retry_after), LLM_RETRY_MAX_SECONDS)
            except ValueError:
                pass
    delay = LLM_RETRY_BASE_SECONDS * (2 ** attempt)
    return min(delay, LLM_RETRY_MAX_SECONDS)


def _json_dumps(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _message_content_to_text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict):
                if isinstance(item.get("text"), str):
                    parts.append(item["text"])
                else:
                    parts.append(_json_dumps(item))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    if content is None:
        return ""
    return _json_dumps(content)


def _messages_to_prompt(messages):
    parts = []
    for message in messages or []:
        role = message.get("role", "user")
        content = _message_content_to_text(message.get("content"))
        if role == "tool":
            name = message.get("name") or message.get("tool_call_id") or "tool"
            parts.append(f"[tool:{name}]\n{content}")
        elif message.get("tool_calls"):
            calls = _json_dumps(message.get("tool_calls"))
            if content:
                parts.append(f"[assistant]\n{content}\nTool calls: {calls}")
            else:
                parts.append(f"[assistant]\nTool calls: {calls}")
        else:
            parts.append(f"[{role}]\n{content}")
    return "\n\n".join(parts).strip()


def _tool_prompt(tools):
    if not tools:
        return ""
    return (
        "You are running behind a Chat Completions compatibility adapter.\n"
        "Return exactly one JSON object and no markdown.\n"
        "If you need a tool, use this shape:\n"
        "{\"tool_calls\":[{\"name\":\"tool_name\",\"arguments\":{}}]}\n"
        "If you can answer finally, use this shape:\n"
        "{\"final\":\"answer text\"}\n"
        "Available tools:\n"
        f"{_json_dumps(tools)}"
    )


def _extract_first_json_object(text):
    text = str(text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        pass

    start = text.find("{")
    if start < 0:
        return None
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                try:
                    return json.loads(text[start:index + 1])
                except Exception:
                    return None
    return None


def _extract_cli_text(value):
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        texts = [_extract_cli_text(item) for item in value]
        return "\n".join(text for text in texts if text)
    if isinstance(value, dict):
        preferred = (
            "text",
            "content",
            "message",
            "response",
            "output",
            "result",
            "final",
            "stdout",
        )
        for key in preferred:
            item = value.get(key)
            if isinstance(item, str) and item.strip():
                return item
        for key in ("outputs", "choices", "items", "data"):
            if key in value:
                text = _extract_cli_text(value[key])
                if text:
                    return text
        for item in value.values():
            text = _extract_cli_text(item)
            if text:
                return text
    return ""


def _chat_response(content="", tool_calls=None, finish_reason=None, model=None):
    message = {"role": "assistant", "content": content}
    if tool_calls:
        message["tool_calls"] = tool_calls
    return {
        "model": model or "",
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason or ("tool_calls" if tool_calls else "stop"),
            }
        ],
    }


def _normalize_synthetic_tool_calls(payload):
    calls = payload.get("tool_calls") or payload.get("tools") or payload.get("tool")
    if not calls:
        return []
    if isinstance(calls, dict):
        calls = [calls]
    normalized = []
    for call in calls:
        if not isinstance(call, dict):
            continue
        function = call.get("function") if isinstance(call.get("function"), dict) else {}
        name = call.get("name") or function.get("name")
        arguments = call.get("arguments", function.get("arguments", {}))
        if not name:
            continue
        if isinstance(arguments, str):
            args_text = arguments
        else:
            args_text = _json_dumps(arguments)
        normalized.append(
            {
                "id": call.get("id") or f"call_{uuid.uuid4().hex[:24]}",
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": args_text,
                },
            }
        )
    return normalized


def _messages_to_responses_parts(messages):
    instructions = []
    conversation = []
    for message in messages or []:
        role = message.get("role", "user")
        if role in {"system", "developer"}:
            content = _message_content_to_text(message.get("content"))
            if content:
                instructions.append(content)
        else:
            conversation.append(message)

    transcript = _messages_to_prompt(conversation)
    return "\n\n".join(instructions).strip(), transcript


def _tools_to_responses_tools(tools):
    converted = []
    for tool in tools or []:
        if not isinstance(tool, dict):
            continue
        if tool.get("type") != "function":
            continue
        function = tool.get("function") or {}
        name = function.get("name")
        if not name:
            continue
        item = {
            "type": "function",
            "name": name,
            "description": function.get("description") or "",
            "parameters": function.get("parameters") or {"type": "object", "properties": {}},
        }
        converted.append(item)
    return converted


def _responses_to_chat_response(result, model=None):
    texts = []
    tool_calls = []
    output = result.get("output") if isinstance(result, dict) else None
    if isinstance(output, list):
        for item in output:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type == "function_call":
                arguments = item.get("arguments") or "{}"
                if not isinstance(arguments, str):
                    arguments = _json_dumps(arguments)
                tool_calls.append(
                    {
                        "id": item.get("call_id") or item.get("id") or f"call_{uuid.uuid4().hex[:24]}",
                        "type": "function",
                        "function": {
                            "name": item.get("name") or "",
                            "arguments": arguments,
                        },
                    }
                )
                continue
            if item_type == "message":
                for content in item.get("content") or []:
                    if not isinstance(content, dict):
                        continue
                    if content.get("type") in {"output_text", "text"}:
                        text = content.get("text")
                        if isinstance(text, str):
                            texts.append(text)

    output_text = result.get("output_text") if isinstance(result, dict) else None
    if isinstance(output_text, str) and output_text and not texts:
        texts.append(output_text)

    return _chat_response(
        content="\n".join(texts).strip(),
        tool_calls=tool_calls or None,
        model=model or (result.get("model") if isinstance(result, dict) else ""),
    )


def _iter_sse_events(resp):
    event_name = None
    data_lines = []
    for raw_line in resp.iter_lines(decode_unicode=True):
        if raw_line is None:
            continue
        if isinstance(raw_line, bytes):
            raw_line = raw_line.decode("utf-8", errors="replace")
        elif not isinstance(raw_line, str):
            raw_line = str(raw_line)
        line = raw_line.rstrip("\r")
        if not line:
            if data_lines:
                yield event_name, "\n".join(data_lines)
            event_name = None
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line[6:].strip()
            continue
        if line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
            continue
    if data_lines:
        yield event_name, "\n".join(data_lines)


def _responses_result_from_sse(resp):
    completed_response = None
    output_items = []
    text_deltas = []
    items_by_key = {}
    argument_deltas = {}

    def item_key(data, item=None):
        item = item or {}
        return (
            data.get("item_id")
            or item.get("id")
            or item.get("call_id")
            or str(data.get("output_index", ""))
            or f"item_{len(items_by_key)}"
        )

    for event_name, data_text in _iter_sse_events(resp):
        if not data_text or data_text == "[DONE]":
            continue
        try:
            data = json.loads(data_text)
        except Exception:
            continue
        event_type = data.get("type") or event_name or ""

        if event_type in {"error", "response.failed"}:
            error = data.get("error") or data.get("response", {}).get("error") or data
            raise RuntimeError(f"Codex OAuth streaming response failed: {_json_dumps(error)}")

        if event_type == "response.completed":
            completed_response = data.get("response") or data
            continue

        if event_type == "response.output_text.delta":
            delta = data.get("delta")
            if isinstance(delta, str):
                text_deltas.append(delta)
            continue

        if event_type == "response.output_text.done":
            text = data.get("text")
            if isinstance(text, str) and not text_deltas:
                text_deltas.append(text)
            continue

        if event_type == "response.output_item.added":
            item = data.get("item") or {}
            if isinstance(item, dict):
                items_by_key[item_key(data, item)] = dict(item)
            continue

        if event_type == "response.function_call_arguments.delta":
            key = item_key(data)
            argument_deltas[key] = argument_deltas.get(key, "") + str(data.get("delta") or "")
            continue

        if event_type == "response.function_call_arguments.done":
            key = item_key(data)
            arguments = data.get("arguments")
            if isinstance(arguments, str):
                argument_deltas[key] = arguments
            continue

        if event_type == "response.output_item.done":
            item = data.get("item") or {}
            if isinstance(item, dict):
                key = item_key(data, item)
                if key in argument_deltas and item.get("type") == "function_call":
                    item["arguments"] = argument_deltas[key]
                items_by_key[key] = dict(item)
                output_items.append(dict(item))
            continue

    merged_output_text = "".join(text_deltas)
    if isinstance(completed_response, dict):
        if output_items and not completed_response.get("output"):
            completed_response["output"] = output_items
        if merged_output_text and not completed_response.get("output_text"):
            completed_response["output_text"] = merged_output_text
        return completed_response

    if not output_items and items_by_key:
        for key, item in items_by_key.items():
            if key in argument_deltas and item.get("type") == "function_call":
                item["arguments"] = argument_deltas[key]
            output_items.append(item)

    return {
        "output": output_items,
        "output_text": merged_output_text,
    }


class BaseLLMProvider:
    """Chat Completions provider base."""

    name = "base"
    display_name = "LLM"

    def __init__(self, model=None):
        self.model = model or default_model_for_provider(self.name)

    def build_headers(self):
        raise NotImplementedError

    def refresh_after_unauthorized(self):
        return False

    def format_api_error(self, resp):
        retry_after = resp.headers.get("Retry-After")
        retry_part = f"\nRetry-After: {retry_after}" if retry_after else ""
        return (
            f"{self.display_name} API 呼叫失敗: {resp.status_code}"
            f"\nprovider: {self.name}"
            f"\nmodel: {self.model}"
            f"{retry_part}"
            f"\n{resp.text}"
        )

    def chat_completions(self, messages, tools=None, timing_callback=None):
        payload = {
            "model": self.model,
            "messages": messages,
            "temperature": 0.1,
        }
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        headers = self.build_headers()
        last_error = None
        for attempt in range(LLM_MAX_RETRIES + 1):
            try:
                started_at = time.perf_counter()
                resp = requests.post(
                    self.chat_url,
                    headers=headers,
                    json=payload,
                    timeout=60,
                )
                if timing_callback:
                    timing_callback(f"{self.display_name} API call", started_at)

                if resp.status_code == 401 and self.refresh_after_unauthorized():
                    headers = self.build_headers()
                    continue

                if resp.status_code in (429, 500, 502, 503, 504):
                    last_error = self.format_api_error(resp)
                    if attempt < LLM_MAX_RETRIES:
                        wait_time = retry_delay_seconds(resp, attempt)
                        print(
                            "\033[33m"
                            f"[Agent] {self.display_name} API {resp.status_code}，"
                            f"{wait_time:.1f} 秒後重試 "
                            f"({attempt + 1}/{LLM_MAX_RETRIES})，model={self.model}"
                            "\033[0m"
                        )
                        time.sleep(wait_time)
                        continue

                if resp.status_code != 200:
                    raise RuntimeError(self.format_api_error(resp))

                result = resp.json()
                if "choices" not in result:
                    print(f"\033[33m[Agent] API 回應缺少 choices 欄位: {str(result)[:500]}\033[0m")
                elif len(result["choices"]) == 0:
                    print("\033[33m[Agent] API 回應 choices 為空陣列\033[0m")
                return result

            except requests.Timeout:
                last_error = f"{self.display_name} API 回應逾時 (60s)"
                if attempt < LLM_MAX_RETRIES:
                    wait_time = retry_delay_seconds(None, attempt)
                    print(f"\033[33m[Agent] {last_error}，{wait_time:.1f} 秒後重試 ({attempt + 1}/{LLM_MAX_RETRIES})\033[0m")
                    time.sleep(wait_time)
                    continue
                raise RuntimeError(last_error)

            except requests.RequestException as exc:
                last_error = f"網路請求失敗: {exc}"
                if attempt < LLM_MAX_RETRIES:
                    wait_time = retry_delay_seconds(None, attempt)
                    print(f"\033[33m[Agent] {last_error}，{wait_time:.1f} 秒後重試 ({attempt + 1}/{LLM_MAX_RETRIES})\033[0m")
                    time.sleep(wait_time)
                    continue
                raise RuntimeError(last_error)

        raise RuntimeError(last_error or f"{self.display_name} API 呼叫失敗")


class GitHubCopilotProvider(BaseLLMProvider):
    name = "github_copilot"
    display_name = "GitHub Copilot"
    chat_url = COPILOT_CHAT_URL

    def __init__(self, auth=None, model=None):
        super().__init__(model or copilot_model())
        self.auth = auth or CopilotAuth()

    def ensure_login(self):
        if not self.auth.is_logged_in():
            return bool(self.auth.login())
        self.auth.get_token()
        return True

    def build_headers(self):
        return {
            "Authorization": f"Bearer {self.auth.get_token()}",
            "Content-Type": "application/json",
            "Editor-Version": "vscode/1.100.0",
            "Editor-Plugin-Version": "copilot-chat/0.24.0",
            "Copilot-Integration-Id": "vscode-chat",
            "Openai-Intent": "conversation-panel",
        }

    def refresh_after_unauthorized(self):
        print("\033[33m[Agent] Copilot Token 過期，正在刷新...\033[0m")
        self.auth._refresh_copilot_token()
        return True


class CodexOAuthProvider(BaseLLMProvider):
    name = "codex_oauth"
    display_name = "Codex OAuth"

    def __init__(self, auth=None, model=None):
        super().__init__(model or codex_oauth_model())
        self.auth = auth or CodexAuth()
        self.chat_url = CODEX_OAUTH_CHAT_URL

    def ensure_login(self):
        return self.auth.ensure_login()

    def login(self):
        return self.auth.login()

    def build_headers(self):
        headers = {
            "Authorization": f"Bearer {self.auth.get_access_token()}",
            "Content-Type": "application/json",
            "OpenAI-Beta": "responses_websockets=2026-02-06",
            "x-responsesapi-include-timing-metrics": "true",
            "User-Agent": "SAP_Copilot/0.13.0 codex-oauth",
        }
        installation_id = self._installation_id()
        if installation_id:
            headers["x-codex-installation-id"] = installation_id
        return headers

    def refresh_after_unauthorized(self):
        print("\033[33m[Agent] Codex OAuth Token 過期或失效，正在重新讀取 OAuth 登入狀態...\033[0m")
        return self.auth.refresh_after_unauthorized()

    def _installation_id(self):
        try:
            path = Path.home() / ".codex" / "installation_id"
            if path.exists():
                return path.read_text(encoding="utf-8").strip()
        except Exception:
            pass
        return ""

    def _responses_payload(self, messages, tools=None):
        instructions, transcript = _messages_to_responses_parts(messages)
        payload = {
            "model": self.model,
            "instructions": instructions or "You are SAP GUI Copilot. Answer or call tools based on the user request.",
            "input": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "input_text",
                            "text": transcript or "",
                        }
                    ],
                }
            ],
            "store": False,
            "stream": True,
        }
        response_tools = _tools_to_responses_tools(tools)
        if response_tools:
            payload["tools"] = response_tools
            payload["tool_choice"] = "auto"
            payload["parallel_tool_calls"] = True
        return payload

    def chat_completions(self, messages, tools=None, timing_callback=None):
        payload = self._responses_payload(messages, tools=tools)
        headers = self.build_headers()
        last_error = None
        for attempt in range(LLM_MAX_RETRIES + 1):
            try:
                started_at = time.perf_counter()
                resp = requests.post(
                    self.chat_url,
                    headers=headers,
                    json=payload,
                    stream=True,
                    timeout=60,
                )
                if timing_callback:
                    timing_callback(f"{self.display_name} Responses API call", started_at)

                if resp.status_code == 401 and self.refresh_after_unauthorized():
                    headers = self.build_headers()
                    continue

                if resp.status_code in (429, 500, 502, 503, 504):
                    last_error = self.format_api_error(resp)
                    if attempt < LLM_MAX_RETRIES:
                        wait_time = retry_delay_seconds(resp, attempt)
                        print(
                            "\033[33m"
                            f"[Agent] {self.display_name} API {resp.status_code}，"
                            f"{wait_time:.1f} 秒後重試 "
                            f"({attempt + 1}/{LLM_MAX_RETRIES})，model={self.model}"
                            "\033[0m"
                        )
                        time.sleep(wait_time)
                        continue

                if resp.status_code != 200:
                    raise RuntimeError(self.format_api_error(resp))

                result = _responses_result_from_sse(resp)
                return _responses_to_chat_response(result, model=self.model)

            except requests.Timeout:
                last_error = f"{self.display_name} API 回應逾時 (60s)"
                if attempt < LLM_MAX_RETRIES:
                    wait_time = retry_delay_seconds(None, attempt)
                    print(f"\033[33m[Agent] {last_error}，{wait_time:.1f} 秒後重試 ({attempt + 1}/{LLM_MAX_RETRIES})\033[0m")
                    time.sleep(wait_time)
                    continue
                raise RuntimeError(last_error)

            except requests.RequestException as exc:
                last_error = f"網路請求失敗: {exc}"
                if attempt < LLM_MAX_RETRIES:
                    wait_time = retry_delay_seconds(None, attempt)
                    print(f"\033[33m[Agent] {last_error}，{wait_time:.1f} 秒後重試 ({attempt + 1}/{LLM_MAX_RETRIES})\033[0m")
                    time.sleep(wait_time)
                    continue
                raise RuntimeError(last_error)

        raise RuntimeError(last_error or f"{self.display_name} API 呼叫失敗")


def create_llm_provider(provider_name=None, auth=None, model=None):
    provider = normalize_provider_name(provider_name)
    if provider == "github_copilot":
        return GitHubCopilotProvider(auth=auth, model=model)
    if provider == "codex_oauth":
        return CodexOAuthProvider(auth=auth, model=model)
    raise ValueError(f"不支援的 LLM provider: {provider_name}")
