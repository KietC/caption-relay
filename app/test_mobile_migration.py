"""Synthetic local regression tests; no real phone credentials or transcripts are loaded.

合成本机回归测试，不加载真实手机凭据或字幕。
"""
import base64
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from mobile_caption import migration
from mobile_caption.store import Inbox


class MigrationTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="迁移 测试 ")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.old = self.root / "旧 电脑" / "应用"
        self.new = self.root / "新 电脑" / "应用"
        self.bundle = self.root / "私有 字幕.zip"
        self.session = "session_中文 通话"
        self.inbox = self.fixture(self.old)
        self.addCleanup(self.inbox.close)

    def fixture(self, base, device="test-device"):
        runtime = base / "mobile_caption" / "runtime"
        runtime.mkdir(parents=True)
        config = {"device_id": device, "token": "private-token-for-migration-tests-only-123456",
                  "key_b64": base64.b64encode(b"k" * 32).decode(),
                  "endpoint": "https://old.example.invalid/v1/captions",
                  "inbox_path": str(runtime / "inbox.sqlite3")}
        self.write_json(runtime / "config.json", config)
        self.write_json(runtime / "tunnel_status.json", {"pid": 1234, "endpoint": config["endpoint"]})
        (runtime / "private_older_archive.zip").write_bytes(b"preserve this local backup")
        inbox = Inbox(runtime / "inbox.sqlite3")
        for seq in range(1, 4):
            envelope = {"device_id": device, "stream_id": "stream-1", "seq": seq,
                        "nonce": base64.b64encode(seq.to_bytes(12, "big")).decode()}
            event = {"type": "heartbeat", "timestamp": "2001-01-02T00:00:00+00:00"}
            inbox.accept(envelope, event, hashlib.sha256(str(seq).encode()).hexdigest())
        folder = base / "output" / "caption_viewer" / self.session
        folder.mkdir(parents=True)
        inbox.mark_applied(str(folder.resolve()).casefold(), 2)
        self.write_json(folder / "history.json", {"entries": [{"id": 1, "original": "Hello", "translation": "你好"}],
                                                  "markdown_path": str(folder / "captions.md")})
        (folder / "captions.md").write_text("# 旧字幕\nHello / 你好\n", encoding="utf-8")
        self.write_json(folder / "caption_latest.json", {"items": [{"role": "original", "text": "Hello"}]})
        self.write_json(folder / "status.json", {"state": "connected", "pid": 55, "owner_pid": 66,
                                                "run_id": "old-run", "captured_at": "2001-01-02T00:00:00+00:00"})
        self.write_json(folder.parent / "viewer_status.json",
                        {"state": "running", "source": "mobile", "pid": 10, "created": 1.2,
                         "window_pid": 11, "capture_pid": 12, "output": str(folder.parent),
                         "capture_output": str(folder), "markdown_path": str(folder / "captions.md"),
                         "mobile_config": str(runtime / "config.json"), "ios_config": str(base / "ios_caption_config.json")})
        (folder.parent / "collector.log").write_text("unrelated log", encoding="utf-8")
        (folder / "phone_current.png").write_bytes(b"unrelated screen")
        (base / "audio").mkdir()
        (base / "audio" / "private.wav").write_bytes(b"unrelated audio")
        return inbox

    @staticmethod
    def write_json(path, value):
        path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def export(self):
        return migration.export_bundle(self.old, self.bundle)

    def rewrite(self, changes=None, update_hashes=True, info=None):
        with zipfile.ZipFile(self.bundle) as archive:
            files = {name: archive.read(name) for name in archive.namelist()}
        files.update(changes or {})
        if update_hashes:
            manifest = json.loads(files[migration.MANIFEST])
            manifest["files"] = [{"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                                 for name, data in files.items() if name != migration.MANIFEST]
            files[migration.MANIFEST] = json.dumps(manifest).encode()
        with zipfile.ZipFile(self.bundle, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, data in files.items():
                archive.writestr(info if info is not None and info.filename == name else name, data)

    def assert_existing_preserved(self, action):
        marker = self.new / "mobile_caption" / "runtime" / "untouched.txt"
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_bytes(b"existing state")
        with self.assertRaises((ValueError, sqlite3.Error, zipfile.BadZipFile)):
            action()
        self.assertEqual(marker.read_bytes(), b"existing state")
        self.assertFalse((self.new / migration.CONFIG).exists())

    def test_wal_export_and_import_cursor_continuity_with_chinese_paths(self):
        result = self.export()
        self.assertEqual(result["event_count"], 3)
        self.assertNotIn("token", result)
        with zipfile.ZipFile(self.bundle) as archive:
            names = archive.namelist()
            self.assertIn(migration.INBOX, names)
            self.assertNotIn("mobile_caption/runtime/tunnel_status.json", names)
            self.assertFalse(any(name.endswith((".log", ".png", ".wav", "-wal", "-shm")) for name in names))
            self.assertFalse(any("private_older_archive" in name for name in names))
        result = migration.import_bundle(self.new, self.bundle)
        self.assertEqual(result["state"], "imported_stopped")
        self.assertEqual(result["remapped_consumers"], 1)
        config = json.loads((self.new / migration.CONFIG).read_text(encoding="utf-8"))
        self.assertEqual(config["endpoint"], "")
        self.assertEqual(config["inbox_path"], str(self.new / migration.INBOX))
        viewer = json.loads((self.new / migration.VIEWER / "viewer_status.json").read_text(encoding="utf-8"))
        folder = self.new / migration.VIEWER / self.session
        self.assertEqual(viewer["capture_output"], str(folder))
        self.assertEqual(viewer["state"], "stopped")
        self.assertFalse(viewer["running"])
        self.assertFalse(any(name in viewer for name in ("pid", "created", "window_pid", "capture_pid")))
        self.assertEqual(viewer["mobile_config"], str(self.new / migration.CONFIG))
        history = json.loads((folder / "history.json").read_text(encoding="utf-8"))
        self.assertEqual(history["markdown_path"], str(folder / "captions.md"))
        self.assertEqual(history["entries"][0]["translation"], "你好")
        restored = Inbox(self.new / migration.INBOX)
        try:
            self.assertEqual([row["id"] for row in restored.pending(str(folder.resolve()).casefold())], [3])
            restored.mark_applied(str(folder.resolve()).casefold(), 3)
            self.assertEqual(restored.pending(str(folder.resolve()).casefold()), [])
        finally:
            restored.close()

    def test_export_refuses_to_overwrite_archive(self):
        self.export()
        original = self.bundle.read_bytes()
        with self.assertRaises(FileExistsError):
            self.export()
        self.assertEqual(self.bundle.read_bytes(), original)

    def test_export_rejects_noncanonical_database(self):
        path = self.old / migration.CONFIG
        config = json.loads(path.read_text(encoding="utf-8"))
        config["inbox_path"] = str(self.root / "outside.sqlite3")
        self.write_json(path, config)
        with self.assertRaisesRegex(migration.MigrationError, "canonical"):
            self.export()
        self.assertFalse(self.bundle.exists())

    def test_existing_state_and_private_archives_are_backed_up(self):
        other = self.fixture(self.new, "previous-device")
        other.close()
        old_config = (self.new / migration.CONFIG).read_bytes()
        self.export()
        result = migration.import_bundle(self.new, self.bundle)
        backup = Path(result["backup_path"])
        self.assertEqual((backup / migration.CONFIG).read_bytes(), old_config)
        self.assertEqual((backup / "mobile_caption/runtime/private_older_archive.zip").read_bytes(), b"preserve this local backup")
        self.assertTrue((backup / migration.VIEWER / self.session / "captions.md").is_file())
        self.assertFalse((self.new / "mobile_caption/runtime/tunnel_status.json").exists())
        self.assertEqual((self.new / "audio/private.wav").read_bytes(), b"unrelated audio")

    def test_hash_corruption_does_not_touch_existing_state(self):
        self.export()
        self.rewrite({migration.CONFIG: b"{}"}, update_hashes=False)
        self.assert_existing_preserved(lambda: migration.import_bundle(self.new, self.bundle))

    def test_traversal_rejected_before_install(self):
        self.export()
        self.rewrite({"../escape.txt": b"escape"})
        self.assert_existing_preserved(lambda: migration.import_bundle(self.new, self.bundle))
        self.assertFalse((self.root / "escape.txt").exists())

    def test_zip_symlink_rejected_before_install(self):
        self.export()
        info = zipfile.ZipInfo(migration.CONFIG)
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        self.rewrite(info=info)
        self.assert_existing_preserved(lambda: migration.import_bundle(self.new, self.bundle))

    def test_oversize_limit_rejected_before_install(self):
        self.export()
        with patch.object(migration, "MAX_TOTAL_BYTES", 100):
            self.assert_existing_preserved(lambda: migration.import_bundle(self.new, self.bundle))

    def test_invalid_config_rejected_even_with_correct_hash(self):
        self.export()
        self.rewrite({migration.CONFIG: b'{"device_id":"valid"}'})
        self.assert_existing_preserved(lambda: migration.import_bundle(self.new, self.bundle))

    def test_corrupt_sqlite_rejected_even_with_correct_hash(self):
        self.export()
        self.rewrite({migration.INBOX: b"not sqlite"})
        self.assert_existing_preserved(lambda: migration.import_bundle(self.new, self.bundle))

    def test_relative_manifest_base_rejected(self):
        self.export()
        with zipfile.ZipFile(self.bundle) as archive:
            manifest = json.loads(archive.read(migration.MANIFEST))
        manifest["source_base"] = "relative/path"
        self.rewrite({migration.MANIFEST: json.dumps(manifest).encode()})
        self.assert_existing_preserved(lambda: migration.import_bundle(self.new, self.bundle))

    def test_failed_second_swap_rolls_back_both_directories(self):
        other = self.fixture(self.new, "previous-device")
        other.close()
        previous_config = (self.new / migration.CONFIG).read_bytes()
        previous_history = (self.new / migration.VIEWER / self.session / "history.json").read_bytes()
        self.export()
        original = migration._replace
        failed = False

        def fail_viewer_install(source, target):
            nonlocal failed
            if not failed and ".caption_import_" in str(source) and target == self.new / migration.VIEWER:
                failed = True
                raise OSError("simulated installation failure")
            original(source, target)

        with patch.object(migration, "_replace", side_effect=fail_viewer_install):
            with self.assertRaisesRegex(OSError, "simulated"):
                migration.import_bundle(self.new, self.bundle)
        self.assertTrue(failed)
        self.assertEqual((self.new / migration.CONFIG).read_bytes(), previous_config)
        self.assertEqual((self.new / migration.VIEWER / self.session / "history.json").read_bytes(), previous_history)
        self.assertTrue((self.new / "mobile_caption/runtime/tunnel_status.json").is_file())

    def test_windows_private_acl_is_protected(self):
        if os.name != "nt":
            self.skipTest("Windows ACL validation")
        self.export()
        # A protected DACL prevents inheriting a broader destination-folder ACL.
        import ctypes
        from ctypes import wintypes
        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        size = wintypes.DWORD()
        advapi.GetFileSecurityW(str(self.bundle), 4, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        self.assertTrue(advapi.GetFileSecurityW(str(self.bundle), 4, buffer, size, ctypes.byref(size)))
        control, revision = wintypes.WORD(), wintypes.DWORD()
        self.assertTrue(advapi.GetSecurityDescriptorControl(buffer, ctypes.byref(control), ctypes.byref(revision)))
        self.assertTrue(control.value & 0x1000)


if __name__ == "__main__":
    unittest.main()
