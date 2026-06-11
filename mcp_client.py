"""
MCP client for SAP GUI tools.

Stage 2 migration goal:
- MCP (`mcp-sap-gui`) is the primary SAP operation path.
- Existing pywin32/COM tools remain available as the GUI fallback path.

The most common MCP startup failure is a stdio server that exits before MCP
initialization. This module keeps a stderr tail and exposes `/mcp` diagnostics
so "Connection closed" can be traced to the actual server-side error.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import json
import os
import shlex
import shutil
import tempfile
import threading
from contextlib import AsyncExitStack
from pathlib import Path
from typing import Any

try:
    import dotenv
    dotenv.load_dotenv()
except Exception:
    pass

try:
    from mcp import ClientSession
    from mcp.client.stdio import StdioServerParameters, stdio_client
except Exception as exc:  # pragma: no cover - depends on optional dependency
    ClientSession = None
    StdioServerParameters = None
    stdio_client = None
    MCP_IMPORT_ERROR = exc
else:
    MCP_IMPORT_ERROR = None


DEFAULT_MCP_PACKAGE = "mcp-sap-gui==0.2.0"


def env_enabled(name: str, default: str = "true") -> bool:
    return str(os.getenv(name, default)).strip().lower() in {"1", "true", "yes", "y", "on"}


def _env_args(name: str, default: str) -> list[str]:
    value = os.getenv(name, default)
    return shlex.split(value, posix=False) if value else []


def _default_uv_cache_dir() -> str:
    if os.name == "nt":
        return r"C:\tmp\sap-copilot-uv-cache"
    return "/tmp/sap-copilot-uv-cache"


class _StderrCapture:
    """Temporary stderr file with a tail reader and a real file descriptor."""

    def __init__(self, limit_chars: int = 12000):
        self.limit_chars = max(1000, int(limit_chars))
        self.file = tempfile.TemporaryFile(mode="w+t", encoding="utf-8", errors="replace")
        self._lock = threading.RLock()
        self._closed_tail = ""

    def tail(self) -> str:
        with self._lock:
            if self.file.closed:
                return self._closed_tail
            try:
                self.file.flush()
                pos = self.file.tell()
                self.file.seek(0)
                text = self.file.read()
                self.file.seek(pos)
                return text[-self.limit_chars :].strip()
            except Exception:
                return self._closed_tail

    def close(self) -> None:
        with self._lock:
            if self.file.closed:
                return
            self._closed_tail = self.tail()
            self.file.close()


class MCPClientUnavailable(RuntimeError):
    """Raised when the MCP SDK or server command is not available."""


class MCPStartupError(MCPClientUnavailable):
    """Raised when the MCP stdio server cannot initialize."""

    def __init__(self, message: str, diagnostics: dict[str, Any] | None = None):
        super().__init__(message)
        self.diagnostics = diagnostics or {}


class MCPSAPClient:
    """Async client for the `mcp-sap-gui` stdio server."""

    def __init__(
        self,
        command: str | None = None,
        args: list[str] | None = None,
        enabled: bool | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        server_dir: str | None = None,
        uv_cache_dir: str | None = None,
    ):
        self.enabled = env_enabled("MCP_SAP_ENABLED", "true") if enabled is None else enabled
        self.server_dir = (server_dir or os.getenv("MCP_SAP_SERVER_DIR") or "").strip() or None
        self.package = os.getenv("MCP_SAP_PACKAGE", DEFAULT_MCP_PACKAGE).strip() or DEFAULT_MCP_PACKAGE
        self.allow_package_mode = env_enabled("MCP_SAP_ALLOW_PACKAGE_MODE", "false")
        self.uv_cache_dir = (
            uv_cache_dir
            if uv_cache_dir is not None
            else os.getenv("MCP_SAP_UV_CACHE_DIR", _default_uv_cache_dir())
        )
        self.env = env

        if self.server_dir:
            self.command = command or os.getenv("MCP_SAP_LOCAL_COMMAND", "uv")
            self.args = args if args is not None else _env_args(
                "MCP_SAP_LOCAL_ARGS",
                "run python -m mcp_sap_gui.server",
            )
            self.cwd = cwd or os.getenv("MCP_SAP_CWD") or self.server_dir
        elif self.allow_package_mode:
            self.command = command or os.getenv("MCP_SAP_COMMAND", "uvx")
            self.args = args if args is not None else _env_args(
                "MCP_SAP_ARGS",
                f"--from {self.package} mcp-sap-gui",
            )
            self.cwd = cwd or os.getenv("MCP_SAP_CWD") or None
        else:
            self.command = command or os.getenv("MCP_SAP_COMMAND", "uvx")
            self.args = args if args is not None else _env_args(
                "MCP_SAP_ARGS",
                f"--from {self.package} mcp-sap-gui",
            )
            self.cwd = cwd or os.getenv("MCP_SAP_CWD") or None

        self._exit_stack: AsyncExitStack | None = None
        self._session: Any | None = None
        self._tool_cache: list[dict[str, Any]] | None = None
        self._stderr = _StderrCapture()
        self._sap_attached = False
        self.last_error = ""

    @property
    def is_connected(self) -> bool:
        return self._session is not None

    @property
    def stderr_tail(self) -> str:
        return self._stderr.tail()

    def launch_summary(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "mode": "local" if self.server_dir else ("package" if self.allow_package_mode else "unconfigured"),
            "command": self.command,
            "args": list(self.args),
            "cwd": self.cwd,
            "server_dir": self.server_dir,
            "allow_package_mode": self.allow_package_mode,
            "uv_cache_dir": self.uv_cache_dir,
            "package": self.package,
            "last_error": self.last_error,
            "stderr_tail": self.stderr_tail,
        }

    def check_available(self) -> None:
        """Validate local MCP prerequisites before launching the server."""
        if not self.enabled:
            raise MCPClientUnavailable("MCP SAP client is disabled by MCP_SAP_ENABLED=false")
        if MCP_IMPORT_ERROR is not None:
            raise MCPClientUnavailable(f"Python package `mcp` is not available: {MCP_IMPORT_ERROR}")
        if not self.server_dir and not self.allow_package_mode:
            raise MCPClientUnavailable(
                "MCP_SAP_SERVER_DIR is not configured. "
                "The upstream mcp-sap-gui project is intended to run from a local clone "
                "with `uv run python -m mcp_sap_gui.server`. "
                "Clone https://github.com/kts982/mcp-sap-gui and set MCP_SAP_SERVER_DIR, "
                "or set MCP_SAP_ALLOW_PACKAGE_MODE=true to explicitly try package mode."
            )
        if shutil.which(self.command) is None:
            raise MCPClientUnavailable(f"MCP server command not found on PATH: {self.command}")
        if self.server_dir and not Path(self.server_dir).exists():
            raise MCPClientUnavailable(f"MCP_SAP_SERVER_DIR does not exist: {self.server_dir}")

    def _server_env(self) -> dict[str, str]:
        env = dict(os.environ)
        if self.env:
            env.update(self.env)
        if self.uv_cache_dir:
            env.setdefault("UV_CACHE_DIR", self.uv_cache_dir)
            try:
                Path(self.uv_cache_dir).mkdir(parents=True, exist_ok=True)
            except OSError:
                # The server launch will surface the real error in stderr.
                pass
        return env

    async def connect(self) -> "MCPSAPClient":
        """Start `mcp-sap-gui` over stdio and initialize the MCP session."""
        if self._session is not None:
            return self

        self.check_available()
        assert StdioServerParameters is not None
        assert stdio_client is not None
        assert ClientSession is not None

        self._stderr.close()
        self._stderr = _StderrCapture()
        stack = AsyncExitStack()
        try:
            server = StdioServerParameters(
                command=self.command,
                args=self.args,
                cwd=self.cwd,
                env=self._server_env(),
                encoding="utf-8",
                encoding_error_handler="replace",
            )
            read_stream, write_stream = await stack.enter_async_context(
                stdio_client(server, errlog=self._stderr.file)
            )
            session = await stack.enter_async_context(ClientSession(read_stream, write_stream))
            await session.initialize()
        except Exception as exc:
            await stack.aclose()
            self._session = None
            self._exit_stack = None
            self._tool_cache = None
            self._sap_attached = False
            self.last_error = self._diagnostic_error("MCP server failed to initialize", exc)
            raise MCPStartupError(self.last_error, self.launch_summary()) from exc

        self._exit_stack = stack
        self._session = session
        self._tool_cache = None
        self._sap_attached = False
        self.last_error = ""
        return self

    async def close(self) -> None:
        """Close the MCP session and stdio transport."""
        stack = self._exit_stack
        self._session = None
        self._exit_stack = None
        self._tool_cache = None
        self._sap_attached = False
        if stack is not None:
            await stack.aclose()
        self._stderr.close()

    async def __aenter__(self) -> "MCPSAPClient":
        return await self.connect()

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def get_available_tools(self, refresh: bool = False) -> list[dict[str, Any]]:
        """
        Return MCP tools as GitHub Copilot/OpenAI-compatible tool schemas.

        Output format:
        {"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}
        """
        await self.connect()
        if self._tool_cache is not None and not refresh:
            return list(self._tool_cache)

        assert self._session is not None
        try:
            result = await self._session.list_tools()
        except Exception as exc:
            self.last_error = self._diagnostic_error("MCP list_tools failed", exc)
            await self.close()
            raise MCPClientUnavailable(self.last_error) from exc

        tools = [self._tool_to_openai_schema(tool) for tool in getattr(result, "tools", [])]
        self._tool_cache = tools
        return list(tools)

    async def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        """Call an MCP tool and return a plain string suitable for tool message content."""
        await self.connect()
        assert self._session is not None
        try:
            result = await self._session.call_tool(name, arguments or {})
        except Exception as exc:
            self.last_error = self._diagnostic_error(f"MCP call_tool failed: {name}", exc)
            await self.close()
            raise MCPClientUnavailable(self.last_error) from exc
        return self._format_tool_result(result)

    async def ensure_sap_connected(self, tool_names: list[str] | None = None) -> dict[str, Any]:
        """
        Ask mcp-sap-gui to attach to an existing SAP GUI session when the tool exists.

        The upstream server exposes `sap_connect_existing`; older/forked builds may
        expose a slightly different name, so this is intentionally tolerant.
        """
        if self._sap_attached:
            return {"attempted": False, "attached": True, "reason": "already_attached"}

        tools = tool_names or [schema["function"]["name"] for schema in await self.get_available_tools()]
        connect_tool = next(
            (
                name
                for name in (
                    "sap_connect_existing",
                    "sap_connect",
                    "connect_existing",
                    "connect_sap",
                )
                if name in tools
            ),
            "",
        )
        if not connect_tool:
            return {"attempted": False, "attached": False, "reason": "connect tool not exposed"}

        result_text = await self.call_tool(connect_tool, {})
        self._sap_attached = not str(result_text).startswith("MCP tool error")
        return {
            "attempted": True,
            "attached": self._sap_attached,
            "tool": connect_tool,
            "result": result_text,
        }

    async def probe(self, attach: bool = True) -> dict[str, Any]:
        """Return a diagnostic report for CLI/UI `/mcp`."""
        report = self.launch_summary()
        report.update(
            {
                "available": False,
                "initialized": False,
                "tool_count": 0,
                "tools": [],
                "attach": {"attempted": False, "attached": False, "reason": "not attempted"},
                "error": "",
            }
        )

        try:
            self.check_available()
            report["available"] = True
        except Exception as exc:
            report["error"] = str(exc)
            self.last_error = str(exc)
            report.update(self.launch_summary())
            return report

        try:
            await self.connect()
            report["initialized"] = True
            tools = await self.get_available_tools(refresh=True)
            tool_names = [schema["function"]["name"] for schema in tools]
            report["tools"] = tool_names
            report["tool_count"] = len(tool_names)
            if attach:
                report["attach"] = await self.ensure_sap_connected(tool_names)
        except Exception as exc:
            report["error"] = str(exc)
            self.last_error = str(exc)

        report.update(self.launch_summary())
        return report

    def _diagnostic_error(self, prefix: str, exc: Exception) -> str:
        command_line = " ".join([self.command] + list(self.args))
        parts = [
            f"{prefix}: {exc}",
            f"command: {command_line}",
        ]
        if self.cwd:
            parts.append(f"cwd: {self.cwd}")
        if self.uv_cache_dir:
            parts.append(f"UV_CACHE_DIR: {self.uv_cache_dir}")
        stderr = self.stderr_tail
        if stderr:
            parts.append(f"stderr:\n{stderr}")
        return "\n".join(parts)

    def _tool_to_openai_schema(self, tool: Any) -> dict[str, Any]:
        input_schema = getattr(tool, "inputSchema", None) or {}
        if not isinstance(input_schema, dict):
            input_schema = self._json_safe(input_schema)
        if not isinstance(input_schema, dict):
            input_schema = {}
        if not input_schema:
            input_schema = {
                "type": "object",
                "properties": {},
            }
        elif "type" not in input_schema and "properties" in input_schema:
            input_schema = dict(input_schema)
            input_schema["type"] = "object"
        elif input_schema.get("type") != "object":
            input_schema = {
                "type": "object",
                "properties": input_schema.get("properties", {}),
            }

        return {
            "type": "function",
            "function": {
                "name": str(getattr(tool, "name", "")),
                "description": str(getattr(tool, "description", "") or ""),
                "parameters": input_schema,
            },
        }

    def _format_tool_result(self, result: Any) -> str:
        content = getattr(result, "content", []) or []
        is_error = bool(getattr(result, "isError", False))
        text_parts = []
        structured_parts = []

        for item in content:
            item_type = getattr(item, "type", "")
            if item_type == "text" and hasattr(item, "text"):
                text_parts.append(str(getattr(item, "text", "")))
            else:
                structured_parts.append(self._json_safe(item))

        if structured_parts:
            payload = {
                "is_error": is_error,
                "text": "\n".join(part for part in text_parts if part),
                "content": structured_parts,
            }
            return json.dumps(payload, ensure_ascii=False, default=str)

        text = "\n".join(part for part in text_parts if part)
        if is_error:
            return f"MCP tool error: {text}" if text else "MCP tool error"
        return text

    def _json_safe(self, value: Any) -> Any:
        if hasattr(value, "model_dump"):
            return value.model_dump(mode="json")
        if hasattr(value, "dict"):
            return value.dict()
        return value


class SyncMCPSAPClient:
    """
    Synchronous bridge for the current CLI/worker architecture.

    It keeps MCP on a dedicated event-loop thread so a single initialized MCP
    session can be reused across multiple synchronous calls.
    """

    def __init__(self, client: MCPSAPClient | None = None, timeout_seconds: float = 60.0):
        self.client = client or MCPSAPClient()
        self.timeout_seconds = timeout_seconds
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._queue: asyncio.Queue | None = None
        self._lock = threading.Lock()
        self._run_lock = threading.Lock()

    @property
    def last_error(self) -> str:
        return self.client.last_error

    def launch_summary(self) -> dict[str, Any]:
        return self.client.launch_summary()

    def start(self) -> None:
        with self._lock:
            if self._loop is not None:
                return

            loop = asyncio.new_event_loop()
            ready = threading.Event()

            def run_loop() -> None:
                asyncio.set_event_loop(loop)
                queue: asyncio.Queue = asyncio.Queue()
                self._queue = queue
                ready.set()
                try:
                    loop.run_until_complete(self._worker(queue))
                finally:
                    loop.close()

            thread = threading.Thread(target=run_loop, name="mcp-sap-client", daemon=True)
            thread.start()
            ready.wait(timeout=5)
            self._loop = loop
            self._thread = thread

    async def _worker(self, queue: asyncio.Queue) -> None:
        while True:
            item = await queue.get()
            if item is None:
                await asyncio.sleep(0.1)
                break
            coro_factory, future = item
            if future.cancelled():
                continue
            try:
                result = await coro_factory()
            except BaseException as exc:
                future.set_exception(exc)
            else:
                future.set_result(result)

    def run(self, coro_factory):
        self.start()
        assert self._loop is not None
        assert self._queue is not None
        with self._run_lock:
            future: concurrent.futures.Future = concurrent.futures.Future()
            self._loop.call_soon_threadsafe(self._queue.put_nowait, (coro_factory, future))
            return future.result(timeout=self.timeout_seconds)

    def connect(self) -> MCPSAPClient:
        return self.run(lambda: self.client.connect())

    def close(self) -> None:
        loop = self._loop
        thread = self._thread
        queue = self._queue
        if loop is None:
            return
        try:
            self.run(lambda: self.client.close())
        finally:
            if queue is not None:
                loop.call_soon_threadsafe(queue.put_nowait, None)
            if thread is not None:
                thread.join(timeout=5)
            self._loop = None
            self._thread = None
            self._queue = None

    def get_available_tools(self, refresh: bool = False) -> list[dict[str, Any]]:
        return self.run(lambda: self.client.get_available_tools(refresh=refresh))

    def call_tool(self, name: str, arguments: dict[str, Any] | None = None) -> str:
        return self.run(lambda: self.client.call_tool(name, arguments or {}))

    def ensure_sap_connected(self, tool_names: list[str] | None = None) -> dict[str, Any]:
        return self.run(lambda: self.client.ensure_sap_connected(tool_names))

    def probe(self, attach: bool = True) -> dict[str, Any]:
        return self.run(lambda: self.client.probe(attach=attach))


_DEFAULT_SYNC_CLIENT: SyncMCPSAPClient | None = None


def get_default_sync_client() -> SyncMCPSAPClient:
    global _DEFAULT_SYNC_CLIENT
    if _DEFAULT_SYNC_CLIENT is None:
        _DEFAULT_SYNC_CLIENT = SyncMCPSAPClient()
    return _DEFAULT_SYNC_CLIENT
