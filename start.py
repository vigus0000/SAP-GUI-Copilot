"""
SAP GUI Copilot startup launcher.

流程：
1. 確認 SAP GUI 是否已有登入完成的 session
2. 若尚未登入，執行 sap_login.py
3. 確認 GitHub Copilot 已授權；未授權則執行 device login
4. 啟動 main.py
"""

import subprocess
import sys
import time
from pathlib import Path

from copilot_auth import CopilotAuth
from sap_core import SAPConnection


ROOT = Path(__file__).resolve().parent
SAP_LOGIN_SCRIPT = ROOT / "sap_login.py"
MAIN_SCRIPT = ROOT / "main.py"
UI_SCRIPT = ROOT / "ui_app.py"


def _run_python(script: Path) -> int:
    process = subprocess.Popen([sys.executable, str(script)], cwd=str(ROOT))
    while True:
        try:
            return process.wait()
        except KeyboardInterrupt:
            exit_code = process.poll()
            if exit_code is not None:
                return exit_code
            print("[Start] 已收到中斷訊號，等待子程式處理...")


def _sap_is_logged_in() -> bool:
    try:
        sap = SAPConnection()
        session = sap.get_session()
        info = sap.get_session_info(session)
        user = str(info.get("user", "") or "").strip()
        client = str(info.get("client", "") or "").strip()
        transaction = str(info.get("transaction", "") or "").strip()
        return bool(user and client and transaction)
    except Exception:
        return False


def ensure_sap_login() -> bool:
    if _sap_is_logged_in():
        print("[Start] SAP GUI 已登入")
        return True

    print("[Start] 尚未偵測到已登入的 SAP session，執行 sap_login.py...")
    exit_code = _run_python(SAP_LOGIN_SCRIPT)
    if exit_code != 0:
        print(f"[Start] sap_login.py 執行失敗，exit code={exit_code}")
        return False

    # SAP GUI 登入後 COM session 可能需要一點時間才可讀。
    for _ in range(10):
        if _sap_is_logged_in():
            print("[Start] SAP GUI 登入確認完成")
            return True
        time.sleep(1)

    print("[Start] 無法確認 SAP GUI 已登入")
    return False


def ensure_copilot_login() -> bool:
    auth = CopilotAuth()
    if not auth.is_logged_in():
        print("[Start] 尚未登入 GitHub Copilot，開始授權...")
        return bool(auth.login())

    try:
        auth.get_token()
        print("[Start] GitHub Copilot 授權有效")
        return True
    except RuntimeError as exc:
        print(f"[Start] GitHub Copilot Token 無效: {exc}")
        print("[Start] 重新執行授權...")
        return bool(auth.login())


def main() -> int:
    use_ui = any(arg.lower() in {"--ui", "ui", "/ui"} for arg in sys.argv[1:])

    if not ensure_sap_login():
        return 1
    if not ensure_copilot_login():
        return 1

    target = UI_SCRIPT if use_ui else MAIN_SCRIPT
    print(f"[Start] 登入檢查完成，啟動 {target.name}")
    return _run_python(target)


if __name__ == "__main__":
    raise SystemExit(main())
