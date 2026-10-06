"""Controller safety tests. All process/device changes are mocked."""
import base64
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import secrets
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, call, patch

import mobile_caption_control as control


class MobileCaptionControlTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        (self.base / "mobile_bridge.py").write_text("# test-only placeholder")
        self.runtime = self.base / "runtime"
        self.config_path = self.runtime / "config.json"
        self.state_path = self.runtime / "tunnel_status.json"
        self.patch_paths = patch.multiple(control, BASE=self.base, RUNTIME=self.runtime,
                                          CONFIG=self.config_path, STATE=self.state_path,
                                          CLOUDFLARED=self.base / "cloudflared.exe")
        self.patch_paths.start()
        self.addCleanup(self.patch_paths.stop)
        self.config = {"endpoint": "https://test.trycloudflare.com/v1/captions", "device_id": "phone-test",
                       "token": secrets.token_urlsafe(32), "key_b64": base64.b64encode(secrets.token_bytes(32)).decode()}
        control.save_json(self.config_path, self.config)
        self.usb = {"running": True, "source": "android", "capture_pid": 100}
        self.mobile = {"running": True, "source": "mobile", "capture_pid": 101}

    def test_pair_config_written_to_temporary_then_atomically_moved(self):
        with patch.object(control, "adb") as adb, patch.object(control, "phone_action") as phone, \
             patch.object(control, "check_phone_pairing"):
            control.pair_usb("serial")
        calls = adb.call_args_list
        self.assertEqual(calls[1].args[-1], "files/config.json.tmp")
        self.assertEqual(json.loads(calls[1].kwargs["data"]), self.config)
        self.assertEqual(calls[2].args[-3:], ("mv", "files/config.json.tmp", "files/config.json"))
        phone.assert_called_once_with("serial", "reload")
        state = json.loads(self.state_path.read_text())
        self.assertEqual(state["paired_endpoint"], self.config["endpoint"])

    def test_failed_pairing_write_does_not_replace_existing_config(self):
        with patch.object(control, "adb", side_effect=[b"", RuntimeError("USB disconnected")]) as adb, \
             patch.object(control, "phone_action") as phone, patch.object(control, "check_phone_pairing"):
            with self.assertRaises(RuntimeError):
                control.pair_usb("serial")
        self.assertEqual(adb.call_count, 2)
        phone.assert_not_called()
        self.assertFalse(self.state_path.exists())

    def test_invalid_config_rejected_before_current_viewer_is_touched(self):
        bad = {**self.config, "key_b64": "bad-key"}
        control.save_json(self.config_path, bad)
        original = self.config_path.read_bytes()
        with patch.object(control.viewer, "status", return_value=self.usb), \
             patch.object(control, "run_viewer") as viewer, patch.object(control, "start_tunnel") as tunnel:
            with self.assertRaisesRegex(RuntimeError, "invalid"):
                control.start_service("serial")
        viewer.assert_not_called()
        tunnel.assert_not_called()
        self.assertEqual(self.config_path.read_bytes(), original)

    def test_foreign_or_shadow_listener_preserves_usb(self):
        probe = Mock()
        probe.bind.side_effect = OSError("already used")
        with patch.object(control.socket, "socket") as factory, \
             patch.object(control.viewer, "status", return_value=self.usb), \
             patch.object(control, "run_viewer") as viewer, patch.object(control, "start_tunnel") as tunnel:
            factory.return_value.__enter__.return_value = probe
            with self.assertRaisesRegex(RuntimeError, "18765"):
                control.start_service("serial")
        viewer.assert_not_called()
        tunnel.assert_not_called()

    def test_viewer_stop_must_actually_finish(self):
        result = subprocess.CompletedProcess([], 0, b"{}", b"")
        with patch.object(control.subprocess, "run", return_value=result), \
             patch.object(control.viewer, "status", return_value=self.mobile):
            with self.assertRaisesRegex(RuntimeError, "still running"):
                control.run_viewer("stop")

    def test_mobile_readiness_requires_new_packet_from_current_worker(self):
        current_time = datetime.now(timezone.utc)
        fresh_time = current_time.isoformat()
        stale_time = (current_time - timedelta(minutes=4)).isoformat()
        capture = {"state": "connected", "transport": "mobile_https", "host_pid": 101,
                   "started_at": fresh_time, "last_event_at": stale_time}
        old = {**self.mobile, "capture": capture}
        fresh = {**self.mobile, "capture": {**capture, "last_event_at": fresh_time}}
        with patch.object(control.viewer, "status", side_effect=[old, fresh]), \
             patch.object(control.time, "sleep"):
            self.assertEqual(control.wait_mobile_ready(timeout=2), fresh)

    def test_supervisor_alone_is_not_mobile_readiness(self):
        current = {**self.mobile, "capture": {"state": "error", "error": "port occupied"}}
        with patch.object(control.viewer, "status", return_value=current):
            with self.assertRaisesRegex(RuntimeError, "collector failed"):
                control.wait_mobile_ready(timeout=1)

    def test_mobile_start_failure_restores_previous_usb_source(self):
        with patch.object(control.viewer, "status", side_effect=[self.usb, self.mobile]), \
             patch.object(control, "preflight"), patch.object(control, "pair_usb"), \
             patch.object(control, "phone_action"), \
             patch.object(control, "start_tunnel", return_value={"endpoint": self.config["endpoint"]}), \
             patch.object(control, "wait_mobile_ready", side_effect=RuntimeError("receiver failed")), \
             patch.object(control, "run_viewer") as viewer:
            with self.assertRaisesRegex(RuntimeError, "previous caption source restored"):
                control.start_service("serial")
        self.assertEqual(viewer.call_args_list, [
            call("stop"), call("start", "--source", "mobile", "--resume-last", "--mobile-config", str(self.config_path)),
            call("stop"), call("start", "--source", "android", "--resume-last", "--serial", "serial")])

    def test_existing_mobile_failure_does_not_stop_existing_viewer(self):
        with patch.object(control.viewer, "status", return_value=self.mobile), \
             patch.object(control, "preflight"), patch.object(control, "pair_usb"), \
             patch.object(control, "phone_action"), \
             patch.object(control, "start_tunnel", return_value={"endpoint": self.config["endpoint"]}), \
             patch.object(control, "wait_mobile_ready", side_effect=RuntimeError("offline")), \
             patch.object(control, "run_viewer") as viewer:
            with self.assertRaisesRegex(RuntimeError, "offline"):
                control.start_service("serial")
        self.assertEqual(viewer.call_count, 1)
        self.assertEqual(viewer.call_args.args[0], "start")

    def test_pairing_state_is_reloaded_after_phone_start_error(self):
        def pair(serial):
            control.save_json(self.state_path, {"paired_endpoint": self.config["endpoint"]})
        with patch.object(control.viewer, "status", return_value=self.usb), \
             patch.object(control, "preflight"), patch.object(control, "pair_usb", side_effect=pair), \
             patch.object(control, "phone_action", side_effect=RuntimeError("phone left USB")), \
             patch.object(control, "start_tunnel", return_value={"endpoint": self.config["endpoint"]}), \
             patch.object(control, "wait_mobile_ready", return_value=self.mobile), \
             patch.object(control, "run_viewer") as viewer:
            self.assertEqual(control.start_service("serial"), self.mobile)
        self.assertEqual(viewer.call_count, 2)

    def test_unpaired_offline_phone_keeps_usb_untouched(self):
        with patch.object(control.viewer, "status", return_value=self.usb), \
             patch.object(control, "preflight"), patch.object(control, "pair_usb", side_effect=RuntimeError("offline")), \
             patch.object(control, "start_tunnel", return_value={"endpoint": self.config["endpoint"]}), \
             patch.object(control, "run_viewer") as viewer:
            with self.assertRaisesRegex(RuntimeError, "Current viewer left untouched"):
                control.start_service("serial")
        viewer.assert_not_called()

    def test_manual_phone_endpoint_is_saved_only_after_fresh_authentication(self):
        old_endpoint = "https://previous.trycloudflare.com/v1/captions"
        control.save_json(self.state_path, {"paired_endpoint": old_endpoint,
                                            "endpoint": self.config["endpoint"]})
        def authenticated():
            self.assertEqual(json.loads(self.state_path.read_text())["paired_endpoint"], old_endpoint)
            return self.mobile
        with patch.object(control.viewer, "status", return_value=self.usb), \
             patch.object(control, "preflight"), patch.object(control, "pair_usb", side_effect=RuntimeError("no USB")), \
             patch.object(control, "start_tunnel", return_value={"endpoint": self.config["endpoint"]}), \
             patch.object(control, "wait_mobile_ready", side_effect=authenticated) as ready, \
             patch.object(control, "run_viewer"):
            self.assertEqual(control.start_service("serial", phone_configured=True), self.mobile)
        ready.assert_called_once()
        saved = json.loads(self.state_path.read_text())
        self.assertEqual(saved["paired_endpoint"], self.config["endpoint"])
        self.assertEqual(saved["pairing_method"], "authenticated_packet_after_manual_endpoint")

    def test_manual_phone_without_authenticated_packet_does_not_claim_pairing(self):
        original = {"paired_endpoint": "https://previous.trycloudflare.com/v1/captions",
                    "endpoint": self.config["endpoint"]}
        control.save_json(self.state_path, original)
        with patch.object(control.viewer, "status", side_effect=[self.usb, self.mobile]), \
             patch.object(control, "preflight"), patch.object(control, "pair_usb", side_effect=RuntimeError("no USB")), \
             patch.object(control, "start_tunnel", return_value={"endpoint": self.config["endpoint"]}), \
             patch.object(control, "wait_mobile_ready", side_effect=RuntimeError("no authenticated packet")), \
             patch.object(control, "run_viewer") as viewer:
            with self.assertRaisesRegex(RuntimeError, "previous caption source restored"):
                control.start_service("serial", phone_configured=True)
        self.assertEqual(json.loads(self.state_path.read_text()), original)
        self.assertEqual(viewer.call_args, call("start", "--source", "android", "--resume-last", "--serial", "serial"))

    def test_empty_owned_tunnel_endpoint_recovers_only_from_registered_log(self):
        log = self.runtime / "tunnel.log"
        log.write_text("https://recovered.trycloudflare.com\nRegistered tunnel connection\n")
        control.save_json(self.state_path, {"pid": 123, "endpoint": "", "log_path": str(log)})
        with patch.object(control, "tunnel_alive", return_value=True), patch.object(control.subprocess, "Popen") as popen:
            result = control.start_tunnel()
        self.assertEqual(result["endpoint"], "https://recovered.trycloudflare.com/v1/captions")
        popen.assert_not_called()

    def test_empty_owned_tunnel_endpoint_does_not_start_duplicate(self):
        control.save_json(self.state_path, {"pid": 123, "endpoint": ""})
        with patch.object(control, "tunnel_alive", return_value=True), patch.object(control.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(RuntimeError, "no confirmed public endpoint"):
                control.start_tunnel()
        popen.assert_not_called()

    def test_stop_attempts_every_cleanup_even_if_one_fails(self):
        with patch.object(control.viewer, "status", return_value=self.mobile), \
             patch.object(control, "run_viewer", side_effect=RuntimeError("viewer stop failed")) as viewer, \
             patch.object(control, "stop_tunnel", side_effect=RuntimeError("tunnel stop failed")) as tunnel, \
             patch.object(control, "phone_action") as phone:
            with self.assertRaisesRegex(RuntimeError, "viewer stop failed; tunnel stop failed"):
                control.stop_service("serial", restore_usb=True)
        viewer.assert_called_once_with("stop")
        tunnel.assert_called_once()
        phone.assert_called_once_with("serial", "stop")

    def test_stop_does_not_stop_current_usb_viewer(self):
        with patch.object(control.viewer, "status", return_value=self.usb), \
             patch.object(control, "run_viewer") as viewer, patch.object(control, "stop_tunnel"), \
             patch.object(control, "phone_action"):
            control.stop_service("serial")
        viewer.assert_not_called()

    def test_single_new_usb_phone_is_auto_detected(self):
        result = subprocess.CompletedProcess([], 0, b"List of devices attached\nNEWPHONE device\n", b"")
        with patch.object(control.subprocess, "run", return_value=result):
            self.assertEqual(control.resolve_serial(), "NEWPHONE")

    def test_ambiguous_usb_phone_is_not_guessed(self):
        result = subprocess.CompletedProcess([], 0, b"A device\nB device\n", b"")
        with patch.object(control.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(RuntimeError, "exactly one"):
                control.resolve_serial()

    def test_other_pairing_is_never_silently_overwritten(self):
        foreign = {**self.config, "device_id": "someone-else"}
        with patch.object(control, "adb", side_effect=[("package:" + control.PACKAGE).encode(), b"", b"config.json", json.dumps(foreign).encode()]) as adb:
            with self.assertRaisesRegex(RuntimeError, "another pairing"):
                control.check_phone_pairing("serial", self.config)
        self.assertFalse(any("tee" in c.args for c in adb.call_args_list))

    def test_same_phone_can_update_endpoint_with_queue_preserved(self):
        old = {**self.config, "endpoint": "https://old.example/v1/captions"}
        with patch.object(control, "adb", side_effect=[("package:" + control.PACKAGE).encode(), b"", b"config.json", json.dumps(old).encode()]) as adb:
            control.check_phone_pairing("serial", self.config)
        self.assertEqual(adb.call_count, 4)

    def test_new_phone_with_missing_package_installs_then_checks_config(self):
        with patch.object(control, "adb", side_effect=[b"", b"", b""]) as adb, \
             patch.object(control, "install_phone") as install:
            control.check_phone_pairing("new-phone", self.config)
        install.assert_called_once_with("new-phone")
        self.assertEqual(adb.call_args_list[0].args, ("new-phone", "shell", "pm", "list", "packages", control.PACKAGE))

    def test_pairing_code_output_only_exposes_file_location(self):
        target = self.base / "private-code.txt"
        with patch.object(control, "start_tunnel"):
            result = control.pairing_code(target)
        self.assertNotIn(self.config["token"], json.dumps(result))
        prefix, encoded = target.read_text().split(":", 1)
        self.assertEqual(prefix, "CRCP1")
        payload = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        self.assertEqual(payload, self.config)

    def test_import_blocked_while_service_running(self):
        with patch.object(control.viewer, "status", return_value=self.mobile):
            with self.assertRaisesRegex(RuntimeError, "Stop"):
                control.require_stopped()


if __name__ == "__main__":
    unittest.main()
