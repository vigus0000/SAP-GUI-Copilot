import os
import subprocess
import sys
import time

import dotenv
import pywintypes
import win32com.client


def _env(name: str, fallback: str = "") -> str:
    return (os.getenv(name) or fallback or "").strip().strip('"')


def _require_env(name: str, value: str, hint: str) -> None:
    if value:
        return
    print(f"[SAP Login] 缺少 .env 設定: {name}")
    print(f"[SAP Login] {hint}")
    sys.exit(1)


def _open_sap_logon(path: str) -> None:
    if not os.path.exists(path):
        print(f"[SAP Login] SAP_GUI_PATH 不存在: {path}")
        print("[SAP Login] 請到 SAP-GUI-Copilot/.env 確認 SAP_GUI_PATH 是否指向 saplogon.exe。")
        sys.exit(1)

    try:
        subprocess.Popen([path])
    except OSError as exc:
        print(f"[SAP Login] 無法啟動 SAP Logon: {exc}")
        print("[SAP Login] 若路徑有空白，.env 仍只要填完整 exe 路徑即可，不需要額外加指令參數。")
        sys.exit(1)


def _get_sap_application(timeout_seconds: int = 30):
    deadline = time.time() + timeout_seconds
    last_error = None

    while time.time() < deadline:
        try:
            sapgui_auto = win32com.client.GetObject("SAPGUI")
            return sapgui_auto.GetScriptingEngine
        except Exception as exc:
            last_error = exc
            time.sleep(1)

    print("[SAP Login] 無法取得 SAP GUI Scripting Engine。")
    print("[SAP Login] 請確認 SAP Logon 已開啟，且 SAP GUI Scripting 已啟用。")
    if last_error:
        print(f"[SAP Login] 最後錯誤: {last_error}")
    sys.exit(1)


def _open_connection(application, connection_name: str):
    try:
        return application.OpenConnection(connection_name, True)
    except pywintypes.com_error as exc:
        print("[SAP Login] 找不到 SAP Logon 連線項目。")
        print(f"[SAP Login] 目前 .env 設定的連線名稱: {connection_name}")
        print("[SAP Login] 請打開 SAP Logon，複製左側清單中的連線名稱，完全一致填到 .env：")
        print("[SAP Login] SAP_CONNECTION=你的 SAP Logon 連線名稱")
        print("[SAP Login] 若你的 .env 還在用舊欄位 connection=，也可以保留，但建議改成 SAP_CONNECTION。")
        print(f"[SAP Login] SAP 原始錯誤: {exc}")
        sys.exit(1)


def main() -> None:
    dotenv.load_dotenv()

    sap_gui_path = _env("SAP_GUI_PATH")
    connection_name = _env("SAP_CONNECTION", _env("connection"))
    mandt = _env("MANDT")
    bname = _env("BNAME")
    bcode = _env("BCODE")

    _require_env("SAP_GUI_PATH", sap_gui_path, "請填 saplogon.exe 的完整路徑。")
    _require_env("SAP_CONNECTION", connection_name, "請填 SAP Logon 左側連線清單中的完整名稱。")
    _require_env("MANDT", mandt, "請填 SAP Client，例如 600。")
    _require_env("BNAME", bname, "請填 SAP 使用者帳號。")
    _require_env("BCODE", bcode, "請填 SAP 密碼。")

    _open_sap_logon(sap_gui_path)
    application = _get_sap_application()
    connection = _open_connection(application, connection_name)

    session = connection.Children(0)
    session.findByID("wnd[0]/usr/txtRSYST-MANDT").text = mandt
    session.findByID("wnd[0]/usr/txtRSYST-BNAME").text = bname
    session.findByID("wnd[0]/usr/pwdRSYST-BCODE").text = bcode
    session.findByID("wnd[0]").sendVKey(0)


if __name__ == "__main__":
    main()
