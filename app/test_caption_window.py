"""Synthetic local regression tests; no real phone credentials or transcripts are loaded.

合成本机回归测试，不加载真实手机凭据或字幕。
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import psutil

from caption_window import CaptionWindow, ViewState, caption_text, changed_span, copy_both_text, format_beijing_time, owner_alive, read_history_view, read_live_overlay, read_view


class CaptionWindowDataTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.output = Path(self.directory.name)
        self.now = datetime(2001, 1, 2, 8, tzinfo=timezone.utc)
        self.status = {"state": "connected", "run_id": "new", "started_at": self.now.isoformat(), "last_event_at": self.now.isoformat()}
        self.caption = {"type": "caption_snapshot", "run_id": "new", "received_at": self.now.isoformat(), "items": [
            {"role": "original", "text": "How much?"}, {"role": "translation", "text": "多少钱？"}]}

    def save(self):
        for name, value in (("status.json", self.status), ("caption_latest.json", self.caption)):
            (self.output / name).write_text(json.dumps(value), encoding="utf-8")
        return read_view(self.output, self.now)

    def test_live_and_copy_both(self):
        state = self.save()
        self.assertEqual(state.state, "live")
        self.assertEqual(copy_both_text(state.original, state.translation), "英文原文 / Original\nHow much?\n\n中文翻译 / Translation\n多少钱？")

    def test_beijing_display_handles_offsets_and_date_rollover(self):
        for source, expected in (
            ("2001-01-02T08:00:00Z", "2001-01-02 16:00:00"),
            ("2001-01-02T04:00:00-04:00", "2001-01-02 16:00:00"),
            ("2001-01-02T13:30:00+05:30", "2001-01-02 16:00:00"),
            ("2001-01-02T16:00:00+08:00", "2001-01-02 16:00:00"),
            ("2001-12-31T20:30:01Z", "2002-01-01 04:30:01"),
            ("2001-01-01T01:00:00+14:00", "2000-12-31 19:00:00"),
            ("2001-01-02T08:00:00", "2001-01-02 16:00:00"),
        ):
            with self.subTest(source=source):
                self.assertEqual(format_beijing_time(source), expected)
        self.assertEqual(format_beijing_time(self.now), "2001-01-02 16:00:00")
        self.assertEqual(format_beijing_time(self.now.replace(tzinfo=None)), "2001-01-02 16:00:00")
        for source in (None, 123, "", "invalid timestamp"):
            with self.subTest(source=source):
                self.assertEqual(format_beijing_time(source), "")

    def test_caption_display_converts_time_but_preserves_raw_timestamp(self):
        self.caption["received_at"] = "2001-01-02T04:00:00-04:00"
        state = self.save()
        self.assertEqual(state.state, "live")
        self.assertEqual(state.captured_at, "2001-01-02 16:00:00")
        self.assertEqual(state.received_at, self.caption["received_at"])
        self.assertEqual(json.loads((self.output / "caption_latest.json").read_text())["received_at"],
                         self.caption["received_at"])
        app = self.status_window(state)
        app.render_status()
        self.assertIn("2001-01-02 16:00:00（北京时间 UTC+8）", app.status.set.call_args.args[0])

    def test_history_display_converts_time_without_rewriting_history(self):
        current = self.save()
        history = {"original": "Past text", "translation": "旧字幕", "entries": [],
                   "updated_at": "2001-12-31T20:30:01Z"}
        path = self.output / "history.json"
        path.write_text(json.dumps(history), encoding="utf-8")
        before = path.read_bytes()
        displayed = read_history_view(self.output, current)
        self.assertEqual(displayed.captured_at, "2002-01-01 04:30:01")
        self.assertEqual(displayed.updated_at, history["updated_at"])
        self.assertEqual(displayed.received_at, current.received_at)
        self.assertEqual(path.read_bytes(), before)

    def test_previous_run_never_shown_live(self):
        self.caption["run_id"] = "old"
        state = self.save()
        self.assertEqual(state.state, "waiting")
        self.assertEqual((state.original, state.translation), ("", ""))

    def test_mobile_transport_label_and_stale_heartbeat(self):
        self.status["transport"] = "mobile_https"
        state = self.save()
        self.assertEqual(state.transport, "mobile_https")
        self.assertIn("Network connected", state.message)
        app = self.status_window(state)
        app.render_status()
        self.assertTrue(app.status.set.call_args.args[0].startswith("网络 已连接"))
        self.status["last_event_at"] = (self.now - timedelta(seconds=21)).isoformat()
        state = self.save()
        self.assertEqual(state.state, "offline")
        self.assertIn("Network not responding", state.message)

    def test_mobile_backlog_is_not_presented_as_live(self):
        self.status["transport"] = "mobile_https"
        self.caption["freshness"] = "delayed_or_clock_skew"
        state = self.save()
        self.assertEqual(state.state, "delayed")
        self.assertEqual(state.original, "How much?")
        self.assertIn("clock mismatch", state.message)

    def test_snapshot_before_start_rejected(self):
        self.caption["received_at"] = (self.now - timedelta(seconds=1)).isoformat()
        self.assertEqual(self.save().original, "")

    def test_dead_heartbeat_marks_existing_text_not_live(self):
        self.status["last_event_at"] = (self.now - timedelta(seconds=21)).isoformat()
        state = self.save()
        self.assertEqual(state.state, "offline")
        self.assertIn("last captured text", state.message)
        self.assertEqual(state.original, "How much?")

    def test_unchanged_snapshot_valid_when_heartbeat_fresh(self):
        self.status["started_at"] = (self.now - timedelta(minutes=4)).isoformat()
        self.caption["received_at"] = (self.now - timedelta(minutes=3)).isoformat()
        self.assertEqual(self.save().state, "live")

    def test_stopped_and_error_not_live(self):
        for state in ("stopped", "stopped_by_user", "error"):
            with self.subTest(state=state):
                self.status["state"] = state
                self.assertEqual(self.save().state, "offline")

    def test_actionable_ios_error_reaches_status_without_changing_history_mode(self):
        self.status.update(state="error", error="iPhone 未信任此电脑。请解锁手机并选择“信任”。 / Unlock the iPhone and tap Trust This Computer.")
        current = self.save()
        app = self.status_window(current, follow=False, notice="已复制 / Copied")
        app.render_status()
        rendered = app.status.set.call_args.args[0]
        self.assertTrue(rendered.startswith("USB 已断开 / Offline"))
        self.assertIn(self.status["error"], rendered)
        self.assertIn(current.message, rendered)
        self.assertIn("浏览历史，持续更新 / Browsing; updating", rendered)
        self.assertIn(current.captured_at, rendered)
        self.assertIn("已复制 / Copied", rendered)
        self.assertEqual(app.displayed.original, "How much?")
        app.status_label.configure.assert_called_once_with(foreground="#946116")
        app.write_window_status.assert_called_once_with()

    def test_status_shows_current_reason_in_each_connection_state(self):
        for state, detail, prefix in (
            ("live", "USB connected — showing the phone's caption screen.", "USB 已连接 / Connected"),
            ("empty", "USB connected — no captions visible on the phone.", "USB 已连接 / Connected"),
            ("waiting", "Enable Developer Mode on the iPhone to continue.", "等待连接 / Waiting"),
            ("offline", "USB not responding — last captured text only.", "USB 已断开 / Offline"),
        ):
            with self.subTest(state=state):
                app = self.status_window(ViewState(state, detail))
                app.render_status()
                rendered = app.status.set.call_args.args[0]
                self.assertTrue(rendered.startswith(prefix))
                self.assertTrue(rendered.endswith("\n" + detail))
                self.assertIn("跟随最新 / Following", rendered)

    def status_window(self, current, follow=True, notice=""):
        app = CaptionWindow.__new__(CaptionWindow)
        app.latest = app.displayed = current
        app.follow = Mock()
        app.follow.get.return_value = follow
        app.notice = Mock()
        app.notice.get.return_value = notice
        app.status = Mock()
        app.status_label = Mock()
        app.write_window_status = Mock()
        return app

    def test_absent_translations_not_filled(self):
        self.caption["items"] = [{"role": "original", "text": "one"}, {"role": "original", "text": "two"}]
        state = self.save()
        self.assertEqual(state.original, "one\n\ntwo")
        self.assertEqual(state.translation, "")

    def test_duplicates_and_role_order_preserved(self):
        text = caption_text({"items": [{"role": "original", "text": "same"}, {"role": "original", "text": "same"}, {"role": "translation", "text": "相同"}]})
        self.assertEqual(text, ("same\n\nsame", "相同"))

    def test_incomplete_files_do_not_crash_or_display_old_text(self):
        self.save()
        (self.output / "caption_latest.json").write_text("{", encoding="utf-8")
        self.assertEqual(read_view(self.output, self.now).original, "")
        (self.output / "status.json").write_text("{", encoding="utf-8")
        self.assertEqual(read_view(self.output, self.now).state, "waiting")

    def test_missing_files(self):
        self.assertEqual(read_view(self.output, self.now).state, "waiting")

    def test_empty_caption(self):
        self.caption["items"] = []
        self.assertEqual(self.save().state, "empty")

    def test_malformed_items_ignored(self):
        self.assertEqual(caption_text({"items": [None, "text", {"role": "original", "text": []}, {"role": [], "text": "bad"}]}), ("", ""))
        self.assertEqual(caption_text({"items": None}), ("", ""))

    def test_bad_clock_not_live(self):
        self.status["last_event_at"] = "not a clock"
        self.assertEqual(self.save().state, "offline")
        self.status["last_event_at"] = (self.now + timedelta(minutes=5)).isoformat()
        self.assertEqual(self.save().state, "offline")

    def test_owner_absent_or_exact_identity(self):
        self.assertTrue(owner_alive(None, None))
        process = Mock()
        process.is_running.return_value = True
        process.create_time.return_value = 1000.25
        with patch("caption_window.psutil.Process", return_value=process):
            self.assertTrue(owner_alive(1234, 1000.25))
            self.assertFalse(owner_alive(1234, 999.25))
            process.is_running.return_value = False
            self.assertFalse(owner_alive(1234, 1000.25))

    def test_owner_disappeared(self):
        with patch("caption_window.psutil.Process", side_effect=psutil.NoSuchProcess(1234)):
            self.assertFalse(owner_alive(1234, 1000.25))

    def test_history_preserves_previous_captions_when_reader_stops(self):
        self.status["state"] = "stopped"
        current = self.save()
        (self.output / "history.json").write_text(json.dumps({"original": "Past text\nNew text", "translation": "旧字幕\n新字幕", "updated_at": self.now.isoformat(), "entries": []}), encoding="utf-8")
        history = read_history_view(self.output, current)
        self.assertEqual(history.original, "Past text\nNew text")
        self.assertEqual(history.translation, "旧字幕\n新字幕")
        self.assertEqual(history.state, "offline")

    def test_bad_history_is_not_fabricated(self):
        current = self.save()
        self.assertIsNone(read_history_view(self.output, current))
        (self.output / "history.json").write_text('{"original": []}', encoding="utf-8")
        self.assertIsNone(read_history_view(self.output, current))

    def test_minimal_updates_preserve_unchanged_prefix_and_suffix(self):
        examples = [("first", "first\nsecond"), ("old middle tail", "new middle tail"),
                    ("one middle end", "one revised end"), ("中文原文", "中文原文\n新句"),
                    ("abc", ""), ("", "new"), ("unchanged", "unchanged")]
        for before, after in examples:
            with self.subTest(before=before, after=after):
                start, end, replacement = changed_span(before, after)
                self.assertEqual(before[:start] + replacement + before[end:], after)
        self.assertEqual(changed_span("first", "first\nsecond"), (5, 5, "\nsecond"))
        self.assertEqual(changed_span("old middle tail", "new middle tail"), (0, 3, "new"))

    def live_fixture(self):
        self.status.update(transport="mobile_https", source="mobile")
        self.caption["captured_at"] = (self.now - timedelta(minutes=10)).isoformat()
        self.save()
        live = {"type": "caption_snapshot", "preview": True, "run_id": "new",
                "received_at": self.now.isoformat(),
                "captured_at": (self.now + timedelta(seconds=55)).isoformat(),
                "published_at": self.now.isoformat(), "items": [
                    {"role": "original", "text": "NEW LIVE"},
                    {"role": "translation", "text": "即时译文"}]}
        self.live_path = self.output / "caption_live.json"
        self.live_path.write_text(json.dumps(live), encoding="utf-8")
        return live, ViewState("delayed", "backlog", original="Old saved text", translation="旧历史")

    def test_live_bypasses_old_history_without_mutating_it(self):
        live, historical = self.live_fixture()
        history = {"original": historical.original, "translation": historical.translation, "entries": []}
        path = self.output / "history.json"
        path.write_text(json.dumps(history), encoding="utf-8")
        before = path.read_bytes()
        shown = read_live_overlay(self.output, historical, self.now)
        self.assertEqual(shown.state, "live")
        self.assertTrue(shown.original.startswith("Old saved text"))
        self.assertTrue(shown.original.endswith("NEW LIVE"))
        self.assertTrue(shown.translation.endswith("即时译文"))
        self.assertEqual(path.read_bytes(), before)
        self.assertNotIn("55", shown.message)  # Never label phone clock skew as network delay.

    def test_reliable_catchup_removes_preview_without_duplicate_lines(self):
        live, historical = self.live_fixture()
        self.caption["captured_at"] = live["captured_at"]
        self.save()
        historical = ViewState("delayed", "clock skew", original="Old saved text\n\nNEW LIVE", translation="旧历史\n\n即时译文")
        shown = read_live_overlay(self.output, historical, self.now)
        self.assertEqual(shown.original, historical.original)
        self.assertEqual(shown.translation, historical.translation)
        self.assertEqual(shown.state, "live")

    def test_live_cannot_override_stopped_stale_foreign_run_or_nonmobile(self):
        for mutation in ("stopped", "old_run", "stale", "before_start", "usb", "unmarked"):
            with self.subTest(mutation=mutation):
                live, historical = self.live_fixture()
                if mutation == "stopped":
                    self.status["state"] = "stopped_by_user"
                elif mutation == "old_run":
                    live["run_id"] = "old"
                elif mutation == "stale":
                    live["received_at"] = (self.now - timedelta(seconds=21)).isoformat()
                elif mutation == "before_start":
                    live["received_at"] = (self.now - timedelta(seconds=1)).isoformat()
                elif mutation == "usb":
                    self.status.update(source="android", transport="usb")
                elif mutation == "unmarked":
                    live["preview"] = False
                (self.output / "status.json").write_text(json.dumps(self.status), encoding="utf-8")
                self.live_path.write_text(json.dumps(live), encoding="utf-8")
                self.assertIsNone(read_live_overlay(self.output, historical, self.now))
                self.status["state"] = "connected"

    def test_live_refresh_works_while_archival_heartbeat_is_stale(self):
        live, historical = self.live_fixture()
        self.status["last_event_at"] = (self.now - timedelta(minutes=3)).isoformat()
        self.save()
        self.assertEqual(read_view(self.output, self.now).state, "offline")
        self.assertEqual(read_live_overlay(self.output, historical, self.now).state, "live")

    def test_live_does_not_invent_translation_or_erase_history_on_empty_screen(self):
        live, historical = self.live_fixture()
        live["items"] = [{"role": "original", "text": "Source only"}]
        self.live_path.write_text(json.dumps(live), encoding="utf-8")
        shown = read_live_overlay(self.output, historical, self.now)
        self.assertTrue(shown.original.endswith("Source only"))
        self.assertEqual(shown.translation, historical.translation)
        live["items"] = []
        self.live_path.write_text(json.dumps(live), encoding="utf-8")
        self.assertIsNone(read_live_overlay(self.output, historical, self.now))


if __name__ == "__main__":
    unittest.main()
