import unittest
from types import SimpleNamespace
from unittest.mock import patch

import sap_monitor
import sap_recorder
from sap_skill_library import SAPSkillLibrary


def _snapshot(fields=None, status_type="", status_text=""):
    snap = sap_monitor.ScreenSnapshot()
    snap.tcode = "VA01"
    snap.screen_number = "4001"
    snap.field_values = dict(fields or {})
    snap.field_metadata = {
        field_id: {
            "element_id": field_id,
            "control_type": "GuiTextField",
            "name": field_id.rsplit("/", 1)[-1],
            "source": "com_field",
        }
        for field_id in snap.field_values
    }
    snap.status_type = status_type
    snap.status_text = status_text
    return snap


class SnapshotSaveClassificationTests(unittest.TestCase):
    def test_save_success_groups_cleared_values_as_system_reset(self):
        old = _snapshot({
            "wnd[0]/usr/txtA": "value",
            "wnd[0]/usr/chkB": "True",
            "wnd[0]/usr/txtC": "before",
        })
        new = _snapshot(
            {
                "wnd[0]/usr/txtA": "",
                "wnd[0]/usr/chkB": "False",
                "wnd[0]/usr/txtC": "after",
            },
            status_type="S",
            status_text="已儲存 標準訂單 11139",
        )

        events = sap_monitor._diff_snapshots(old, new)
        field_changes = [e for e in events if e["event_type"] == "FIELD_CHANGE"]
        save_actions = [e for e in events if e["event_type"] == "SAVE_ACTION"]
        resets = [e for e in events if e["event_type"] == "SYSTEM_RESET"]

        self.assertEqual([e["details"]["element_id"] for e in field_changes], ["wnd[0]/usr/txtC"])
        self.assertEqual(len(save_actions), 1)
        self.assertEqual(save_actions[0]["details"]["method"], "unknown")
        self.assertTrue(save_actions[0]["details"]["inferred"])
        self.assertEqual(len(resets), 1)
        self.assertEqual(resets[0]["details"]["change_count"], 2)
        self.assertTrue(all(not change["user_action"] for change in resets[0]["details"]["changes"]))

    def test_save_success_without_clear_still_emits_save_action(self):
        events = sap_monitor._diff_snapshots(
            _snapshot({"wnd[0]/usr/txtA": "value"}),
            _snapshot(
                {"wnd[0]/usr/txtA": "value"},
                status_type="S",
                status_text="Document 42 saved",
            ),
        )
        self.assertEqual(sum(e["event_type"] == "SAVE_ACTION" for e in events), 1)
        self.assertFalse(any(e["event_type"] == "SYSTEM_RESET" for e in events))

    def test_manual_clear_without_save_remains_field_change(self):
        events = sap_monitor._diff_snapshots(
            _snapshot({"wnd[0]/usr/txtA": "value"}),
            _snapshot({"wnd[0]/usr/txtA": ""}),
        )
        self.assertEqual(sum(e["event_type"] == "FIELD_CHANGE" for e in events), 1)
        self.assertFalse(any(e["event_type"] in {"SAVE_ACTION", "SYSTEM_RESET"} for e in events))

    def test_non_save_success_message_does_not_reclassify_clear(self):
        events = sap_monitor._diff_snapshots(
            _snapshot({"wnd[0]/usr/txtA": "value"}),
            _snapshot(
                {"wnd[0]/usr/txtA": ""},
                status_type="S",
                status_text="PPR0 500.00 USD OK",
            ),
        )
        self.assertEqual(sum(e["event_type"] == "FIELD_CHANGE" for e in events), 1)
        self.assertFalse(any(e["event_type"] in {"SAVE_ACTION", "SYSTEM_RESET"} for e in events))


class NativeSaveEventTests(unittest.TestCase):
    def _monitor_and_events(self):
        with patch.object(sap_monitor, "MCP_MONITOR_ENABLED", False):
            monitor = sap_monitor.SAPMonitor(None)
        events = []
        monitor._dispatch_native_event = lambda event_type, details: events.append((event_type, details))
        session = SimpleNamespace(Info=SimpleNamespace(Transaction="VA01", ScreenNumber="4001"))
        component = SimpleNamespace(Id="wnd[0]", Type="GuiMainWindow", Name="", Tooltip="")
        return monitor, session, component, events

    def test_native_save_button_is_exact_save_action(self):
        monitor, session, component, events = self._monitor_and_events()
        monitor._handle_native_change(
            session,
            component,
            [
                ["M", "findById", ["wnd[0]/tbar[0]/btn[11]"]],
                ["M", "press"],
            ],
        )
        self.assertEqual(events[0][0], "SAVE_ACTION")
        self.assertEqual(events[0][1]["method"], "button")
        self.assertFalse(events[0][1]["inferred"])

    def test_native_vkey_11_is_exact_save_action(self):
        monitor, session, component, events = self._monitor_and_events()
        monitor._handle_native_change(session, component, [["M", "sendVKey", [11]]])
        self.assertEqual(events[0][0], "SAVE_ACTION")
        self.assertEqual(events[0][1]["method"], "vkey")
        self.assertEqual(events[0][1]["vkey"], 11)
        self.assertFalse(events[0][1]["inferred"])


    def test_native_context_uses_inferred_save_when_exact_event_is_missing(self):
        monitor, _, _, _ = self._monitor_and_events()
        dispatched = []
        monitor._dispatch_event = dispatched.append
        inferred = {
            "event_type": "SAVE_ACTION",
            "details": {"action": "save", "method": "unknown", "inferred": True},
        }
        monitor._dispatch_native_context_events([inferred])
        self.assertEqual(dispatched, [inferred])

    def test_native_context_suppresses_inferred_duplicate_after_exact_save(self):
        monitor, _, _, _ = self._monitor_and_events()
        dispatched = []
        monitor._dispatch_event = dispatched.append
        monitor._last_exact_save_at = sap_monitor.time.monotonic()
        monitor._dispatch_native_context_events([{
            "event_type": "SAVE_ACTION",
            "details": {"action": "save", "method": "unknown", "inferred": True},
        }])
        self.assertEqual(dispatched, [])
        self.assertEqual(monitor._last_exact_save_at, 0.0)


class RecorderSaveEventTests(unittest.TestCase):
    def test_save_is_operation_and_system_reset_is_context_only(self):
        save = {
            "timestamp": "2026-06-24T21:23:14",
            "event_type": "SAVE_ACTION",
            "details": {
                "action": "save",
                "method": "unknown",
                "inferred": True,
                "user_action": True,
            },
        }
        reset = {
            "timestamp": "2026-06-24T21:23:14",
            "event_type": "SYSTEM_RESET",
            "details": {
                "reason": "save_success",
                "change_count": 2,
                "changes": [],
                "system_default": True,
                "user_action": False,
            },
        }

        self.assertEqual(sap_recorder.SAPRecorder._compact_events([save, reset]), [save])
        self.assertEqual(sap_recorder.SAPRecorder._compact_context_events([save, reset]), [reset])
        self.assertIn("實際觸發方式未記錄", SAPSkillLibrary._event_step_text(save))


if __name__ == "__main__":
    unittest.main()
