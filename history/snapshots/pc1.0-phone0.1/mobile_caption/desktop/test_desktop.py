from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import tkinter as tk
import unittest
from unittest.mock import patch

import entrypoint
import manager
from build_portable import clean_payload


class DesktopTests(unittest.TestCase):
    def test_frozen_dispatch_command_uses_known_module_name(self):
        with patch.object(sys, "frozen", True, create=True), patch.object(sys, "executable", "X:/portable/CaptionRelayCaptions.exe"):
            self.assertEqual(manager.command("status"),
                             ["X:/portable/CaptionRelayCaptions.exe", "mobile_caption_control.py", "status"])

    def test_script_dispatch_calls_existing_main_with_adjusted_argv(self):
        import mobile_caption_control
        previous = sys.argv[:]
        try:
            with patch.object(mobile_caption_control, "main", return_value=7) as mocked:
                self.assertEqual(entrypoint.dispatch("mobile_caption_control.py", ["status"]), 7)
                self.assertEqual(sys.argv[1:], ["status"])
                mocked.assert_called_once()
        finally:
            sys.argv = previous

    def test_bundle_scan_rejects_live_config_and_private_signing_keys(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "CaptionRelayCaptions.exe").write_bytes(b"test")
            self.assertEqual(len(clean_payload(root)), 1)
            forbidden = root / "mobile_caption" / "runtime"
            forbidden.mkdir(parents=True)
            (forbidden / "config.json").write_text("{}")
            with self.assertRaisesRegex(RuntimeError, "forbidden"):
                clean_payload(root)

    def test_status_never_calls_stale_capture_connected(self):
        root = tk.Tk()
        root.withdraw()
        try:
            app = manager.Manager(root, schedule=False)
            stale = (datetime.now(timezone.utc) - timedelta(minutes=2)).isoformat()
            result = {"viewer": {"running": True, "source": "mobile", "capture": {
                "state": "connected", "last_event_at": stale}}, "tunnel_running": True}
            app.show_status(result)
            self.assertIn("Waiting", app.summary.get())
            result["viewer"]["capture"]["last_event_at"] = datetime.now(timezone.utc).isoformat()
            app.show_status(result)
            self.assertIn("Connected", app.summary.get())
            result["viewer"]["capture"]["caption_freshness"] = "delayed_or_clock_skew"
            app.show_status(result)
            self.assertNotIn("Connected", app.summary.get())
        finally:
            root.destroy()


if __name__ == "__main__":
    unittest.main()
