import unittest
from types import SimpleNamespace
from unittest.mock import patch

import sap_monitor


class _FakePythonCom:
    IID_IDispatch = "IID_IDispatch"

    def __init__(self):
        self.calls = []

    def CoInitialize(self):
        self.calls.append(("init",))

    def CoUninitialize(self):
        self.calls.append(("uninit",))

    def CoMarshalInterThreadInterfaceInStream(self, iid, dispatch):
        self.calls.append(("marshal", iid, dispatch))
        return ("stream", dispatch)

    def CoGetInterfaceAndReleaseStream(self, stream, iid):
        self.calls.append(("unmarshal", stream, iid))
        return ("dispatch", stream)


class _FakeClient:
    def __init__(self):
        self.calls = []

    def Dispatch(self, value):
        self.calls.append(("dispatch", value))
        return ("wrapped", value)


class _FakeSession:
    _oleobj_ = "session-dispatch"


class SAPMonitorThreadingTests(unittest.TestCase):
    def test_selected_session_is_marshaled_into_monitor_thread(self):
        fake_pythoncom = _FakePythonCom()
        fake_client = _FakeClient()
        fake_win32com = SimpleNamespace(client=fake_client)

        with (
            patch.object(sap_monitor, "pythoncom", fake_pythoncom),
            patch.object(sap_monitor, "win32com", fake_win32com),
            patch.object(sap_monitor, "MCP_MONITOR_ENABLED", False),
        ):
            monitor = sap_monitor.SAPMonitor(_FakeSession())
            monitor._marshal_session_for_thread()
            resolved = monitor._resolve_session_for_thread()

        self.assertEqual(
            resolved,
            ("wrapped", ("dispatch", ("stream", "session-dispatch"))),
        )
        self.assertIsNone(monitor._marshaled_session_stream)
        self.assertIn(
            ("marshal", "IID_IDispatch", "session-dispatch"),
            fake_pythoncom.calls,
        )

    def test_native_failure_uses_same_session_for_com_polling(self):
        fake_pythoncom = _FakePythonCom()
        fake_win32com = SimpleNamespace(client=_FakeClient())
        selected_session = object()
        calls = []

        with (
            patch.object(sap_monitor, "pythoncom", fake_pythoncom),
            patch.object(sap_monitor, "win32com", fake_win32com),
            patch.object(sap_monitor, "SAP_NATIVE_RECORD_EVENTS", True),
            patch.object(sap_monitor, "MCP_MONITOR_ENABLED", False),
        ):
            monitor = sap_monitor.SAPMonitor(_FakeSession())
            monitor.use_mcp = False
            monitor._resolve_session_for_thread = lambda: selected_session
            monitor._monitor_loop_native = (
                lambda session: calls.append(("native", session)) or False
            )
            monitor._monitor_loop_legacy = (
                lambda session=None: calls.append(("legacy", session))
            )
            monitor._monitor_loop()

        self.assertEqual(
            calls,
            [("native", selected_session), ("legacy", selected_session)],
        )
        self.assertEqual(
            [call for call in fake_pythoncom.calls if call[0] in {"init", "uninit"}],
            [("init",), ("uninit",)],
        )


if __name__ == "__main__":
    unittest.main()
