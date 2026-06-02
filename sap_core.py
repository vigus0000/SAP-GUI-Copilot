"""
SAP GUI COM 連線管理器

負責安全地取得已存在的 SAP Session，並提供防禦性的 COM 呼叫包裝。
遵循 plan.md 指引：不在全域變數快取 UI 元件，需要時隨時重新 Scan。
"""

import win32com.client
from pywintypes import com_error


def _safe_get_attr(obj, attr, default=None):
    """
    安全地從 COM 物件取得屬性值。

    win32com 的動態 dispatch 物件不支援 hasattr()，
    可能觸發內部 IndexError。用 try-except 全捕獲取代。
    """
    try:
        return getattr(obj, attr)
    except Exception:
        return default


class SAPConnection:
    """
    SAP GUI 連線管理器

    設計原則：
    - 重用已存在的 SAP Session（不啟動新連線）
    - 每次操作都重新取得物件（不快取 UI 元件）
    - 所有 COM 呼叫都用 try-except 包裝
    """

    def __init__(self):
        self._sap_gui = None
        self._application = None

    def _get_application(self):
        """
        取得 SAP GUI Application 物件。

        每次呼叫都重新取得，確保連線狀態最新。
        """
        try:
            sap_gui = win32com.client.GetObject("SAPGUI")
            if sap_gui is None:
                raise ConnectionError("無法取得 SAPGUI COM 物件，請確認 SAP GUI 已啟動")

            application = sap_gui.GetScriptingEngine
            if application is None:
                raise ConnectionError(
                    "無法取得 SAP Scripting Engine，"
                    "請確認 SAP GUI 已啟用 Scripting 功能\n"
                    "設定路徑：SAP GUI > Options > Accessibility & Scripting > Scripting > Enable Scripting"
                )

            return application

        except com_error as e:
            raise ConnectionError(
                f"SAP GUI COM 連線失敗: {e}\n"
                "請確認：\n"
                "  1. SAP GUI 已啟動且登入\n"
                "  2. SAP GUI Scripting 已啟用\n"
                "  3. 沒有其他程式佔用 SAP COM 介面"
            )

    def get_session(self, connection_index=0, session_index=0):
        """
        取得可用的 SAP Session。

        Args:
            connection_index: 連線索引（預設 0，第一個連線）
            session_index: Session 索引（預設 0，第一個 Session）

        Returns:
            SAP Session COM 物件

        Raises:
            ConnectionError: 無法取得 Session
        """
        try:
            application = self._get_application()

            if application.Children.Count == 0:
                raise ConnectionError("找不到任何 SAP 連線，請先登入 SAP GUI")

            if connection_index >= application.Children.Count:
                raise ConnectionError(
                    f"連線索引 {connection_index} 超出範圍 "
                    f"(共 {application.Children.Count} 個連線)"
                )

            connection = application.Children(connection_index)

            if connection.Children.Count == 0:
                raise ConnectionError("找不到任何 SAP Session")

            if session_index >= connection.Children.Count:
                raise ConnectionError(
                    f"Session 索引 {session_index} 超出範圍 "
                    f"(共 {connection.Children.Count} 個 Session)"
                )

            session = connection.Children(session_index)
            return session

        except com_error as e:
            raise ConnectionError(f"取得 SAP Session 失敗: {e}")

    def get_active_window(self, session=None):
        """
        取得當前活動視窗物件。

        Args:
            session: SAP Session 物件（如果為 None，自動取得）

        Returns:
            SAP Window COM 物件
        """
        try:
            if session is None:
                session = self.get_session()
            return session.FindById("wnd[0]")
        except com_error as e:
            raise RuntimeError(f"取得活動視窗失敗: {e}")

    def get_status_bar(self, session=None):
        """
        讀取狀態列訊息。

        Args:
            session: SAP Session 物件（如果為 None，自動取得）

        Returns:
            dict: {"type": "S/W/E/I/A", "text": "狀態列訊息"}
                  type: S=Success, W=Warning, E=Error, I=Info, A=Abort
        """
        try:
            if session is None:
                session = self.get_session()
            sbar = session.FindById("wnd[0]/sbar")
            return {
                "type": _safe_get_attr(sbar, "MessageType", ""),
                "text": _safe_get_attr(sbar, "Text", ""),
            }
        except Exception:
            return {"type": "", "text": ""}

    def get_session_info(self, session=None):
        """
        取得當前 Session 的基本資訊。

        Args:
            session: SAP Session 物件（如果為 None，自動取得）

        Returns:
            dict: 包含 T-Code、程式名稱、畫面編號等
        """
        try:
            if session is None:
                session = self.get_session()
            info = session.Info
            return {
                "transaction": _safe_get_attr(info, "Transaction", ""),
                "program": _safe_get_attr(info, "Program", ""),
                "screen_number": _safe_get_attr(info, "ScreenNumber", ""),
                "system_name": _safe_get_attr(info, "SystemName", ""),
                "client": _safe_get_attr(info, "Client", ""),
                "user": _safe_get_attr(info, "User", ""),
            }
        except Exception:
            return {}
