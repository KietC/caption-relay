import contextlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import caption_viewer_control as control


class CaptionViewerControlTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name)

    def test_default_source_remains_android(self):
        selected = control.source_selection({})
        self.assertEqual(selected["source"], "android")
        self.assertIsNone(selected["ios_udid"])
        self.assertEqual(selected["ios_config"], str(control.IOS_CONFIG.resolve()))

    def test_saved_selection_and_explicit_override(self):
        previous = {"source": "ios", "ios_udid": "saved-phone",
                    "ios_config": str(self.output / "custom.json")}
        self.assertEqual(control.source_selection(previous), previous)
        selected = control.source_selection(previous, "android", "new-phone")
        self.assertEqual(selected["source"], "android")
        self.assertEqual(selected["ios_udid"], "new-phone")

    def command(self, source, udid=None):
        selected = control.source_selection({}, source, udid)
        return control.collector_command(selected, "adb-test", "android-test", 120,
                                         self.output, self.output / "captions.md", 123, 1.25)

    def test_ios_dispatch_has_no_android_arguments(self):
        command = self.command("ios", "phone-test")
        self.assertEqual(command[:2], [str(control.IOS_PYTHON), str(control.BASE / "ios_bridge.py")])
        self.assertEqual(command[command.index("--udid") + 1], "phone-test")
        self.assertEqual(command[command.index("--owner-pid") + 1], "123")
        self.assertEqual(command[command.index("--owner-created") + 1], "1.25")
        for arg in ("--adb", "--serial", "--lean", "adb-test", "android-test"):
            self.assertNotIn(arg, command)

    def test_unspecified_ios_udid_uses_backend_enumeration(self):
        self.assertNotIn("--udid", self.command("ios"))

    def test_android_arguments_are_preserved(self):
        command = self.command("android")
        self.assertEqual(command[:2], [str(control.PYTHON), str(control.BASE / "bridge.py")])
        self.assertEqual(command[command.index("--adb") + 1], "adb-test")
        self.assertEqual(command[command.index("--serial") + 1], "android-test")
        self.assertIn("--lean", command)
        self.assertNotIn("--config", command)

    def test_mobile_dispatch_has_no_usb_dependencies(self):
        command = self.command("mobile")
        self.assertEqual(command[:2], [str(control.PYTHON), str(control.BASE / "mobile_bridge.py")])
        self.assertEqual(command[command.index("--config") + 1], str(control.MOBILE_CONFIG.resolve()))
        for arg in ("--adb", "--serial", "--udid", "adb-test", "android-test"):
            self.assertNotIn(arg, command)

    def test_mobile_config_path_retained(self):
        previous = {"source": "mobile", "mobile_config": str(self.output / "paired.json")}
        self.assertEqual(control.source_selection(previous)["mobile_config"], previous["mobile_config"])

    def test_missing_mobile_config_never_starts_usb_fallback(self):
        with patch.object(Path, "is_file", return_value=False), \
             patch.object(control.subprocess, "Popen") as launch:
            with self.assertRaisesRegex(RuntimeError, "Mobile caption setup is incomplete"):
                control.run(self.output, "adb-test", "android-test", 60, True, source="mobile")
        launch.assert_not_called()

    def test_active_collector_requires_exact_script_path(self):
        processes = []
        for pid, script, cwd in (
            (1, str(control.BASE / "bridge.py"), str(control.BASE)),
            (2, "ios_bridge.py", str(control.BASE)),
            (3, str(self.output / "ios_bridge.py"), str(self.output)),
            (4, "other.py", str(control.BASE)),
        ):
            process = Mock(pid=pid, info={"cmdline": ["python.exe", script]})
            process.cwd.return_value = cwd
            processes.append(process)
        with patch.object(control.psutil, "process_iter", return_value=processes):
            self.assertEqual(control.active_collectors(), [1, 2])

    def test_source_switch_while_running_fails_without_launch(self):
        current = {"running": True, "state": "running", "source": "android"}
        argv = ["control", "start", "--output", str(self.output), "--source", "ios"]
        with patch.object(control.sys, "argv", argv), \
             patch.object(control, "status", return_value=current), \
             patch.object(control.subprocess, "Popen") as launch, \
             contextlib.redirect_stderr(io.StringIO()) as error:
            with self.assertRaises(SystemExit) as raised:
                control.main()
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("Run stop first", error.getvalue())
        launch.assert_not_called()

    def test_start_forwards_persisted_ios_selection_to_supervisor(self):
        previous = {"running": False, "source": "ios", "ios_udid": "saved-phone",
                    "ios_config": str(self.output / "custom.json")}
        running = {**previous, "running": True, "state": "running"}
        argv = ["control", "start", "--output", str(self.output)]
        with patch.object(control.sys, "argv", argv), \
             patch.object(control, "status", side_effect=[previous, running]), \
             patch.object(control.subprocess, "Popen") as launch, \
             contextlib.redirect_stdout(io.StringIO()):
            control.main()
        command = launch.call_args.args[0]
        self.assertEqual(command[command.index("--source") + 1], "ios")
        self.assertEqual(command[command.index("--ios-udid") + 1], "saved-phone")
        self.assertEqual(command[command.index("--ios-config") + 1], previous["ios_config"])

    def test_run_persists_selection_and_keeps_existing_history(self):
        folder = self.output / "session_existing"
        folder.mkdir()
        markdown = folder / "captions.md"
        markdown.write_text("Existing captions", encoding="utf-8")
        previous = {"capture_output": str(folder), "source": "ios", "ios_udid": "saved-phone"}
        (self.output / "viewer_status.json").write_text(json.dumps(previous), encoding="utf-8")
        gui = Mock(pid=100)
        gui.poll.side_effect = [None, 0, 0]
        capture = Mock(pid=101)
        capture.poll.return_value = 0
        with patch.object(control, "active_collectors", return_value=[]), \
             patch.object(Path, "is_file", return_value=True), \
             patch.object(control.subprocess, "Popen", side_effect=[gui, capture]) as launch, \
             patch.object(control.time, "sleep"):
            control.run(self.output, "adb-test", "android-test", 60, True, True)
        saved = json.loads((self.output / "viewer_status.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["source"], "ios")
        self.assertEqual(saved["ios_udid"], "saved-phone")
        self.assertEqual(saved["capture_output"], str(folder))
        self.assertEqual(saved["state"], "stopped")
        self.assertEqual(markdown.read_text(encoding="utf-8"), "Existing captions")
        collector = launch.call_args_list[1].args[0]
        self.assertEqual(collector[1], str(control.BASE / "ios_bridge.py"))
        self.assertNotIn("--adb", collector)

    def test_missing_ios_setup_reports_error_without_android_fallback(self):
        with patch.object(Path, "is_file", return_value=False), \
             patch.object(control.subprocess, "Popen") as launch:
            with self.assertRaisesRegex(RuntimeError, "iPhone caption setup is incomplete"):
                control.run(self.output, "adb-test", "android-test", 60, True, source="ios")
        launch.assert_not_called()
        saved = json.loads((self.output / "viewer_status.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["source"], "ios")
        self.assertEqual(saved["state"], "error")
        self.assertIn("missing file", saved["error"])


if __name__ == "__main__":
    unittest.main()
