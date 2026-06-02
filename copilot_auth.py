"""
GitHub Copilot OAuth Device Flow 認證模組

透過 GitHub OAuth Device Flow 取得 Copilot API Token，
讓使用者無需額外 API Key，直接使用 GitHub Copilot 訂閱的 LLM 能力。

認證流程：
1. 向 GitHub 請求 device code
2. 使用者在瀏覽器中授權
3. 取得 GitHub access token (ghu_xxx)
4. 用 access token 換取 Copilot session token
5. 快取 token 至本地檔案
"""

import json
import time
import os
import requests

# GitHub OAuth Device Flow 常數
# 此為 VS Code / GitHub Copilot CLI 共用的 Client ID
GITHUB_CLIENT_ID = "Iv1.b507a08c87ecfe98"
DEVICE_CODE_URL = "https://github.com/login/device/code"
ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
COPILOT_TOKEN_URL = "https://api.github.com/copilot_internal/v2/token"

# Token 快取檔案路徑
TOKEN_CACHE_FILE = os.path.join(os.path.expanduser("~"), ".sap_copilot_token.json")


class CopilotAuth:
    """GitHub Copilot 認證管理器"""

    def __init__(self):
        self._github_token = None  # GitHub access token (ghu_xxx)
        self._copilot_token = None  # Copilot session token
        self._copilot_token_expires_at = 0  # Copilot token 過期時間 (unix timestamp)
        self._load_cached_token()

    def _load_cached_token(self):
        """從本地快取檔案載入已儲存的 token"""
        try:
            if os.path.exists(TOKEN_CACHE_FILE):
                with open(TOKEN_CACHE_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self._github_token = data.get("github_token")
                self._copilot_token = data.get("copilot_token")
                self._copilot_token_expires_at = data.get("copilot_token_expires_at", 0)
                if self._github_token:
                    print("\033[90m[Auth] 已載入快取的 GitHub Token\033[0m")
        except (json.JSONDecodeError, IOError) as e:
            print(f"\033[33m[Auth] 載入快取失敗: {e}\033[0m")

    def _save_cached_token(self):
        """將 token 儲存至本地快取檔案"""
        try:
            data = {
                "github_token": self._github_token,
                "copilot_token": self._copilot_token,
                "copilot_token_expires_at": self._copilot_token_expires_at,
            }
            with open(TOKEN_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except IOError as e:
            print(f"\033[33m[Auth] 儲存快取失敗: {e}\033[0m")

    def login(self):
        """
        執行 GitHub OAuth Device Flow 登入。

        會在終端機顯示 user code，引導使用者前往 GitHub 授權頁面。
        授權完成後，自動取得 GitHub access token。
        """
        print("\n\033[1;36m══════════════════════════════════════════\033[0m")
        print("\033[1;36m   GitHub Copilot 授權登入\033[0m")
        print("\033[1;36m══════════════════════════════════════════\033[0m\n")

        # Step 1: 請求 device code
        print("\033[90m[Auth] 正在請求 Device Code...\033[0m")
        resp = requests.post(
            DEVICE_CODE_URL,
            data={"client_id": GITHUB_CLIENT_ID, "scope": "read:user"},
            headers={"Accept": "application/json"},
        )

        if resp.status_code != 200:
            raise RuntimeError(f"請求 Device Code 失敗: {resp.status_code} - {resp.text}")

        device_data = resp.json()
        device_code = device_data["device_code"]
        user_code = device_data["user_code"]
        verification_uri = device_data["verification_uri"]
        interval = device_data.get("interval", 5)
        expires_in = device_data.get("expires_in", 900)

        # Step 2: 顯示授權指引
        print(f"\n\033[1;33m  📋 請在瀏覽器中開啟: \033[4m{verification_uri}\033[0m")
        print(f"\033[1;33m  🔑 輸入授權碼: \033[1;37;42m {user_code} \033[0m\n")

        # 嘗試自動開啟瀏覽器
        try:
            import webbrowser
            webbrowser.open(verification_uri)
            print("\033[90m[Auth] 已自動開啟瀏覽器\033[0m")
        except Exception:
            pass

        # Step 3: 輪詢等待使用者授權
        print("\033[90m[Auth] 等待授權中... (按 Ctrl+C 取消)\033[0m")
        deadline = time.time() + expires_in

        while time.time() < deadline:
            time.sleep(interval)

            token_resp = requests.post(
                ACCESS_TOKEN_URL,
                data={
                    "client_id": GITHUB_CLIENT_ID,
                    "device_code": device_code,
                    "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                },
                headers={"Accept": "application/json"},
            )

            token_data = token_resp.json()

            if "access_token" in token_data:
                self._github_token = token_data["access_token"]
                print("\n\033[1;32m  ✅ GitHub 授權成功！\033[0m")

                # 立即嘗試換取 Copilot Token
                print("\033[90m[Auth] 正在換取 Copilot API Token...\033[0m")
                try:
                    self._refresh_copilot_token()
                    print("\033[1;32m  ✅ Copilot API Token 取得成功！\033[0m\n")
                except RuntimeError as e:
                    print(f"\033[33m[Auth] 首次換取 Copilot Token 失敗: {e}\033[0m")
                    print("\033[33m[Auth] 將在首次使用時重試\033[0m\n")

                self._save_cached_token()
                return True

            error = token_data.get("error")
            if error == "authorization_pending":
                continue  # 使用者尚未授權，繼續等待
            elif error == "slow_down":
                interval += 5  # GitHub 要求降低輪詢頻率
            elif error == "expired_token":
                print("\n\033[1;31m  ❌ Device Code 已過期，請重新執行登入\033[0m")
                return False
            elif error == "access_denied":
                print("\n\033[1;31m  ❌ 使用者拒絕授權\033[0m")
                return False
            else:
                print(f"\n\033[1;31m  ❌ 未預期的錯誤: {token_data}\033[0m")
                return False

        print("\n\033[1;31m  ❌ 授權逾時\033[0m")
        return False

    def _refresh_copilot_token(self, max_retries=3):
        """
        使用 GitHub access token 換取 Copilot session token。

        Copilot session token 約 30 分鐘過期，需要定期刷新。
        包含重試邏輯，因為剛完成 OAuth 授權後可能需要短暫等待。

        Args:
            max_retries: 最大重試次數
        """
        if not self._github_token:
            raise RuntimeError("尚未登入 GitHub，請先執行 login()")

        last_error = None
        for attempt in range(max_retries):
            try:
                resp = requests.get(
                    COPILOT_TOKEN_URL,
                    headers={
                        "Authorization": f"token {self._github_token}",
                        "Accept": "application/json",
                        "User-Agent": "GitHubCopilotChat/0.24.0",
                    },
                )

                if resp.status_code == 401:
                    print("\033[33m[Auth] GitHub Token 已失效，需要重新登入\033[0m")
                    self._github_token = None
                    self._save_cached_token()
                    raise RuntimeError("GitHub Token 已失效，請執行 /login 重新授權")

                if resp.status_code != 200:
                    last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                    if attempt < max_retries - 1:
                        wait_time = (attempt + 1) * 2
                        print(f"\033[33m[Auth] 換取 Token 失敗 (嘗試 {attempt + 1}/{max_retries})，{wait_time}秒後重試...\033[0m")
                        time.sleep(wait_time)
                        continue
                    raise RuntimeError(f"取得 Copilot Token 失敗: {last_error}")

                data = resp.json()
                self._copilot_token = data.get("token")
                self._copilot_token_expires_at = data.get("expires_at", 0)
                self._save_cached_token()

                print("\033[90m[Auth] Copilot Token 已刷新\033[0m")
                return

            except requests.RequestException as e:
                last_error = str(e)
                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 2
                    print(f"\033[33m[Auth] 網路錯誤 (嘗試 {attempt + 1}/{max_retries})，{wait_time}秒後重試...\033[0m")
                    time.sleep(wait_time)
                    continue
                raise RuntimeError(f"網路請求失敗: {last_error}")

    def get_token(self) -> str:
        """
        取得有效的 Copilot API Token。

        自動檢查過期狀態，如已過期則自動刷新。

        Returns:
            str: 有效的 Copilot session token

        Raises:
            RuntimeError: 如果未登入或無法取得 token
        """
        if not self._github_token:
            raise RuntimeError("尚未登入 GitHub，請先執行 login()")

        # 檢查 Copilot token 是否過期（提前 60 秒刷新）
        if not self._copilot_token or time.time() >= (self._copilot_token_expires_at - 60):
            self._refresh_copilot_token()

        return self._copilot_token

    def is_logged_in(self) -> bool:
        """檢查是否已登入（有 GitHub access token）"""
        return self._github_token is not None

    def logout(self):
        """清除所有快取的 token"""
        self._github_token = None
        self._copilot_token = None
        self._copilot_token_expires_at = 0
        if os.path.exists(TOKEN_CACHE_FILE):
            os.remove(TOKEN_CACHE_FILE)
        print("\033[90m[Auth] 已登出並清除快取\033[0m")
