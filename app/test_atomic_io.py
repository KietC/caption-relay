"""Synthetic local regression tests; no real phone credentials or transcripts are loaded.

合成本机回归测试，不加载真实手机凭据或字幕。
"""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import atomic_io
from atomic_io import replace_with_retry


def locked(winerror):
    error = PermissionError(13, "Simulated Windows sharing/access error")
    error.winerror = winerror
    return error


class AtomicReplaceTests(unittest.TestCase):
    def test_success_has_no_sleep(self):
        with patch("atomic_io.os.replace") as replace, patch("atomic_io.time.sleep") as sleep:
            replace_with_retry("source", "destination")
        replace.assert_called_once_with("source", "destination")
        sleep.assert_not_called()

    def test_windows_access_sharing_and_lock_violations_retry(self):
        for code in (5, 32, 33):
            with self.subTest(winerror=code):
                with patch("atomic_io.os.replace", side_effect=[locked(code), None]) as replace, patch("atomic_io.time.sleep") as sleep:
                    replace_with_retry("source", "destination")
                self.assertEqual(replace.call_count, 2)
                sleep.assert_called_once_with(0.005)

    def test_unrelated_errors_are_immediate(self):
        for error in (FileNotFoundError("missing"), OSError("disk error"), PermissionError("no Windows code"), locked(1314)):
            with self.subTest(error=repr(error)):
                with patch("atomic_io.os.replace", side_effect=error) as replace, patch("atomic_io.time.sleep") as sleep:
                    with self.assertRaises(type(error)) as caught:
                        replace_with_retry("source", "destination")
                self.assertIs(caught.exception, error)
                replace.assert_called_once()
                sleep.assert_not_called()

    def test_retry_exhaustion_raises_original_error_with_bounded_delay(self):
        error = locked(5)
        with patch("atomic_io.os.replace", side_effect=error) as replace, patch("atomic_io.time.sleep") as sleep:
            with self.assertRaises(PermissionError) as caught:
                replace_with_retry("source", "destination")
        self.assertIs(caught.exception, error)
        self.assertEqual(replace.call_count, 7)
        self.assertEqual(sleep.call_count, 6)
        self.assertAlmostEqual(sum(call.args[0] for call in sleep.call_args_list), 0.275)

    def test_collector_json_and_history_text_remain_atomic_during_retry(self):
        from bridge import write_json
        from caption_history import _atomic_write
        original_replace = atomic_io.os.replace
        with TemporaryDirectory() as directory:
            target = Path(directory) / "caption.json"
            target.write_text("previous complete content", encoding="utf-8")
            attempts = []

            def transient_reader(source, destination):
                attempts.append((source, destination))
                if len(attempts) == 1:
                    self.assertEqual(target.read_text(encoding="utf-8"), "previous complete content")
                    self.assertTrue(Path(source).is_file())
                    raise locked(32)
                original_replace(source, destination)

            with patch("atomic_io.os.replace", side_effect=transient_reader), patch("atomic_io.time.sleep"):
                write_json(target, {"translation": "实时中文"})
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), {"translation": "实时中文"})
            self.assertFalse(target.with_suffix(".json.tmp").exists())
            target.write_text("previous complete content", encoding="utf-8")
            attempts.clear()
            with patch("atomic_io.os.replace", side_effect=transient_reader), patch("atomic_io.time.sleep"):
                _atomic_write(target, "# 完整的新字幕")
            self.assertEqual(target.read_text(encoding="utf-8"), "# 完整的新字幕")
            self.assertFalse(target.with_suffix(".json.tmp").exists())


if __name__ == "__main__":
    unittest.main()
