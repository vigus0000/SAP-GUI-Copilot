"""Codex OAuth cache reader and browser-login helper.

This module deliberately does not use command-backed inference.  The Codex CLI,
when available, is only used to open the official browser OAuth login flow and
populate ``~/.codex/auth.json``.
"""

import base64
import glob
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

try:
    import dotenv
    dotenv.load_dotenv()
except Exception:
    pass


def _env_enabled(name, default="false"):
    return str(os.getenv(name, default)).strip().lower() in {"1", "true", "yes", "y", "on"}


def _resolve_executable(command):
    command = str(command or "").strip().strip('"')
    if not command:
        return ""
    if os.path.isabs(command) and os.path.exists(command):
        return command
    found = shutil.which(command)
    if found:
        return found
    if os.name == "nt" and not command.lower().endswith(".exe"):
        found = shutil.which(f"{command}.exe")
        if found:
            return found
    return ""


def find_codex_login_command():
    """Find a Codex CLI binary suitable for opening the browser login flow."""
    explicit = os.getenv("CODEX_LOGIN_COMMAND", "").strip()
    for candidate in (explicit, "codex"):
        resolved = _resolve_executable(candidate)
        if resolved:
            return resolved

    home = os.path.expanduser("~")
    localappdata = os.getenv("LOCALAPPDATA", "")
    patterns = [
        os.path.join(home, ".vscode", "extensions", "openai.chatgpt-*", "bin", "windows-x86_64", "codex.exe"),
        os.path.join(home, ".vscode-insiders", "extensions", "openai.chatgpt-*", "bin", "windows-x86_64", "codex.exe"),
        os.path.join(localappdata, "Programs", "Codex", "codex.exe") if localappdata else "",
        os.path.join(home, ".codex", "bin", "codex.exe"),
    ]
    matches = []
    for pattern in patterns:
        if pattern:
            matches.extend(glob.glob(pattern))
    matches = [path for path in matches if os.path.exists(path)]
    if matches:
        return sorted(matches, reverse=True)[0]

    return explicit or "codex"


def _decode_jwt_payload(token):
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        raw = base64.urlsafe_b64decode(payload.encode("ascii"))
        return json.loads(raw.decode("utf-8"))
    except Exception:
        return {}


class CodexAuth:
    """Read Codex browser OAuth tokens from the local Codex auth cache."""

    def __init__(self, auth_path=None, login_command=None):
        self.auth_path = Path(auth_path or os.getenv("CODEX_AUTH_FILE") or Path.home() / ".codex" / "auth.json")
        self.login_command = login_command or find_codex_login_command()
        self._auth_data = None

    def _load_auth(self):
        if not self.auth_path.exists():
            self._auth_data = {}
            return self._auth_data
        with self.auth_path.open("r", encoding="utf-8") as handle:
            self._auth_data = json.load(handle)
        return self._auth_data

    def _tokens(self):
        data = self._auth_data if self._auth_data is not None else self._load_auth()
        tokens = data.get("tokens") if isinstance(data, dict) else {}
        return tokens if isinstance(tokens, dict) else {}

    def _access_token(self):
        token = self._tokens().get("access_token")
        return token if isinstance(token, str) and token.strip() else ""

    def _token_is_expired(self, token, margin_seconds=300):
        payload = _decode_jwt_payload(token)
        exp = payload.get("exp")
        if not exp:
            return False
        try:
            return time.time() >= (float(exp) - margin_seconds)
        except Exception:
            return False

    def is_logged_in(self):
        token = self._access_token()
        return bool(token and not self._token_is_expired(token))

    def _resolve_login_command(self):
        resolved = _resolve_executable(self.login_command) or find_codex_login_command()
        resolved = _resolve_executable(resolved)
        if resolved:
            return resolved
        raise RuntimeError(
            "找不到 Codex 瀏覽器登入 helper，無法開啟 Codex OAuth 登入頁。\n"
            "請先安裝/啟用 Codex CLI，或設定 CODEX_LOGIN_COMMAND 指向 codex.exe；"
            "此 helper 只用於登入，不會用於模型推理。"
        )

    def login(self):
        command = self._resolve_login_command()
        process = subprocess.run(
            [command, "login"],
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=False,
            timeout=int(os.getenv("CODEX_OAUTH_LOGIN_TIMEOUT_SECONDS", "900")),
            check=False,
        )
        if process.returncode != 0:
            raise RuntimeError(f"Codex OAuth 瀏覽器登入失敗，exit code={process.returncode}")
        self._load_auth()
        if not self.is_logged_in():
            raise RuntimeError("Codex OAuth 登入完成後仍讀不到有效的 ~/.codex/auth.json access token")
        return True

    def ensure_login(self):
        self._load_auth()
        if self.is_logged_in():
            return True
        if _env_enabled("CODEX_OAUTH_LOGIN_ON_CONNECT", "true"):
            return self.login()
        raise RuntimeError(
            "Codex OAuth 尚未登入或 token 已過期。\n"
            "請按 /login 觸發 Codex 瀏覽器登入，或先完成 Codex OAuth 登入。"
        )

    def get_access_token(self):
        self.ensure_login()
        token = self._access_token()
        if not token:
            raise RuntimeError("Codex OAuth access token 不存在")
        return token

    def refresh_after_unauthorized(self):
        self._load_auth()
        if self.is_logged_in():
            return True
        if _env_enabled("CODEX_OAUTH_LOGIN_ON_CONNECT", "true"):
            return self.login()
        return False
