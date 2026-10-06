"""Synthetic local regression tests; no real phone credentials or transcripts are loaded.

合成本机回归测试，不加载真实手机凭据或字幕。
"""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from caption_history import CaptionHistory


def snapshot(*pairs, run_id="one", source=None):
    return {"run_id": run_id, "source": source, "paragraphs": [
        {"original": original, "translation": translation} for original, translation in pairs]}


class CaptionHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.markdown = self.directory / "captions.md"
        self.history = CaptionHistory(self.directory, self.markdown)

    def pairs(self):
        return [(entry["original"], entry["translation"]) for entry in self.history.entries]

    def test_dropped_prefix_and_appended_suffix_keep_all_rows(self):
        self.history.update(snapshot(("one", "一"), ("two", "二")))
        self.history.update(snapshot(("two", "二"), ("three", "三")))
        self.assertEqual(self.pairs(), [("one", "一"), ("two", "二"), ("three", "三")])
        self.assertEqual(self.history.as_dict()["original"], "one\n\ntwo\n\nthree")

    def test_progressive_tail_revised_in_place_with_late_translation(self):
        self.history.update(snapshot(("Earlier sentence", "上一句"), ("This is", None)))
        self.history.update(snapshot(("Earlier sentence", "上一句"), ("This is a sample", "这是示例")))
        self.history.update(snapshot(("This is a sample.", "这是示例。")))
        self.assertEqual(self.pairs(), [("Earlier sentence", "上一句"), ("This is a sample.", "这是示例。")])
        text = self.markdown.read_text(encoding="utf-8")
        self.assertIn("This is a sample.", text)
        self.assertIn("这是示例。", text)
        self.assertEqual(text.count("### "), 2)

    def test_repeated_occurrences_not_globally_deduplicated(self):
        self.history.update(snapshot(("Yes", "是"), ("Yes", "是")))
        self.history.update(snapshot(("Yes", "是"), ("Yes", "是"), ("No", "否")))
        self.history.update(snapshot(("No", "否"), ("Yes", "是")))
        self.assertEqual(self.pairs(), [("Yes", "是"), ("Yes", "是"), ("No", "否"), ("Yes", "是")])
        self.assertEqual(len({entry["id"] for entry in self.history.entries}), 4)

    def test_empty_snapshots_and_new_collector_run_do_not_reset_history(self):
        self.history.update(snapshot(("Hello", "你好")))
        with patch.object(self.history, "_write_markdown") as write:
            self.assertFalse(self.history.update(snapshot()))
            self.assertFalse(self.history.update(snapshot(("Hello", "你好"), run_id="two")))
            write.assert_not_called()
        self.assertEqual(self.pairs(), [("Hello", "你好")])

    def test_resume_and_scroll_only_preserve_mapping_without_md_rewrite(self):
        self.history.update(snapshot(("one", "一"), ("two", "二")))
        with patch.object(self.history, "_write_markdown") as write:
            self.assertFalse(self.history.update(snapshot(("two", "二"))))
            write.assert_not_called()
        self.history = CaptionHistory(self.directory, self.markdown)
        self.history.update(snapshot(("two", "二"), ("three", "三"), run_id="renewed"))
        self.assertEqual(self.pairs(), [("one", "一"), ("two", "二"), ("three", "三")])
        saved = json.loads((self.directory / "history.json").read_text(encoding="utf-8"))
        self.assertEqual(saved["translation"], "一\n\n二\n\n三")

    def test_unrelated_tail_appends_instead_of_overwriting(self):
        self.history.update(snapshot(("What is the price?", "价格是多少？")))
        self.history.update(snapshot(("Call me tomorrow.", "明天给我打电话。")))
        self.assertEqual(len(self.history.entries), 2)

    def test_missing_translation_not_borrowed_from_next_row(self):
        self.history.update(snapshot(("One", None), ("Two", "二")))
        self.assertEqual(self.pairs(), [("One", None), ("Two", "二")])

    def test_unchanged_snapshot_does_no_writes(self):
        event = snapshot(("Hello", "你好"))
        self.history.update(event)
        with patch("caption_history._atomic_write") as write:
            self.assertFalse(self.history.update(event))
            write.assert_not_called()

    def test_source_switch_does_not_revise_or_recover_previous_phone_rows(self):
        android = {"platform": "android", "device_id": "xiaomi", "method": "ui_automation"}
        ios = {"platform": "ios", "device_id": "iphone", "method": "screenshot_ocr"}
        self.history.update(snapshot(("Earlier", "上一句"), ("Hello", "你好"), source=android))
        self.history.update(snapshot(("Hello there", None), source=ios))
        self.history.update(snapshot(("Earlier", None), ("Hello there", None), source=ios))
        self.assertEqual(self.pairs(), [("Earlier", "上一句"), ("Hello", "你好"),
                                       ("Earlier", None), ("Hello there", None)])
        self.assertEqual([entry["source"] for entry in self.history.entries],
                         [android, android, ios, ios])

    def test_identical_text_from_different_sources_is_not_display_compacted(self):
        for field, value in (("platform", "android"), ("device_id", "second"), ("stream_id", "second")):
            with self.subTest(field=field):
                directory = self.directory / field
                history = CaptionHistory(directory, directory / "captions.md")
                source = {"platform": "ios", "device_id": "first"}
                history.update(snapshot(("Hello", None), source=source))
                history.update(snapshot(("Hello", None), source={**source, field: value}))
                self.assertEqual(len(history.entries), 2)
                self.assertEqual(history.as_dict()["original"], "Hello\n\nHello")
                self.assertNotEqual(history.entries[0]["source_key"], history.entries[1]["source_key"])

    def test_same_source_reconnect_and_metadata_change_preserve_dedup(self):
        source = {"platform": "ios", "device_id": "iphone", "app": "Live Captions", "method": "screenshot_ocr"}
        self.history.update(snapshot(("Hello", None), source=source))
        source_key = self.history.as_dict()["source_key"]
        self.history = CaptionHistory(self.directory, self.markdown)
        with patch("caption_history._atomic_write") as write:
            self.assertFalse(self.history.update(snapshot(("Hello", None), run_id="two",
                                     source={**source, "app": "Updated label", "method": "ocr"})))
            write.assert_not_called()
        self.history.update(snapshot(("Hello there", None), source=source, run_id="two"))
        self.assertEqual(self.pairs(), [("Hello there", None)])
        self.assertEqual(self.history.as_dict()["source_key"], source_key)
        self.assertEqual(self.history.entries[0]["source"], source)

    def test_empty_source_switch_survives_resume_without_deleting_history(self):
        source = {"platform": "ios", "device_id": "iphone"}
        self.history.update(snapshot(("Hello", "你好")))
        previous_markdown = self.markdown.read_text(encoding="utf-8")
        self.assertFalse(self.history.update(snapshot(source=source)))
        self.assertEqual(self.pairs(), [("Hello", "你好")])
        self.assertEqual(self.markdown.read_text(encoding="utf-8"), previous_markdown)
        self.history = CaptionHistory(self.directory, self.markdown)
        self.history.update(snapshot(("Hello", None), source=source))
        self.assertEqual(self.pairs(), [("Hello", "你好"), ("Hello", None)])

    def test_returning_source_cannot_recover_rows_before_source_switch(self):
        first = {"platform": "ios", "device_id": "first"}
        second = {"platform": "ios", "device_id": "second"}
        self.history.update(snapshot(("Earlier", None), ("Anchor", None), source=first))
        self.history.update(snapshot(("Other phone", None), source=second))
        self.history.update(snapshot(("Anchor", None), source=first))
        self.history = CaptionHistory(self.directory, self.markdown)
        self.history.update(snapshot(("Earlier", None), ("Anchor", None), source=first))
        self.assertEqual(self.pairs(), [("Earlier", None), ("Anchor", None), ("Other phone", None),
                                       ("Earlier", None), ("Anchor", None)])

    def test_legacy_history_without_source_metadata_still_resumes(self):
        self.history.update(snapshot(("Hello", "你好")))
        path = self.directory / "history.json"
        saved = json.loads(path.read_text(encoding="utf-8"))
        saved.pop("source_key")
        saved.pop("_source_start_id")
        for entry in saved["entries"]:
            entry.pop("source")
            entry.pop("source_key")
        path.write_text(json.dumps(saved), encoding="utf-8")
        self.history = CaptionHistory(self.directory, self.markdown)
        self.assertFalse(self.history.update(snapshot(("Hello", "你好"), run_id="two")))
        self.history.update(snapshot(("Hello there", "你好啊"), run_id="two"))
        self.assertEqual(self.pairs(), [("Hello there", "你好啊")])

    def test_ocr_markdown_identifies_source_without_native_pairing_claim(self):
        source = {"platform": "ios", "device_id": "iphone", "app": "Live Captions", "method": "screenshot_ocr"}
        self.history.update(snapshot(("Hello", None), source=source))
        markdown = self.markdown.read_text(encoding="utf-8")
        self.assertIn("platform=ios", markdown)
        self.assertIn("device_id=iphone", markdown)
        self.assertIn("app=Live Captions", markdown)
        self.assertIn("method=screenshot_ocr", markdown)
        self.assertIn("OCR text may contain recognition errors", markdown)
        self.assertNotIn("Pairing follows the phone UI", markdown)
        self.assertNotIn("手机界面相邻", markdown)
        self.assertNotIn("**Translation / 译文:**", markdown)

    def test_non_tail_visible_row_revisions_do_not_accumulate_fragments(self):
        self.history.update(snapshot(("Show me", "给我看看"), ("Hello", "你好"), ("Hello", "你好")))
        self.history.update(snapshot(("Show me", "给我看看"), ("Hello this I work", "您好，我是"), ("Hello this is", "您好，我是")))
        self.history.update(snapshot(("Show me", "给我看看"), ("Hello this I work from best", "您好，来自最佳"), ("Hello this is error from", "您好，这是错误")))
        self.assertEqual(len(self.history.entries), 3)
        self.assertEqual(self.history.entries[1]["original"], "Hello this I work from best")

    def test_briefly_hidden_row_reappears_without_new_occurrence(self):
        self.history.update(snapshot(("one", "一"), ("two", "二"), ("three", "三")))
        self.history.update(snapshot(("two", "二"), ("three", "三")))
        self.history.update(snapshot(("one", "一"), ("two", "二"), ("three", "三")))
        self.assertEqual(len(self.history.entries), 3)

    def test_compaction_is_display_only_and_non_adjacent_repeats_remain(self):
        self.history.update(snapshot(("Yes", "是"), ("Yes", "是"), ("No", "否"), ("Yes", "是")))
        self.assertEqual(len(self.history.entries), 4)
        self.assertEqual(self.history.as_dict()["original"], "Yes\n\nNo\n\nYes")
        self.assertEqual(self.markdown.read_text(encoding="utf-8").count("### "), 3)

    def test_unrelated_later_repeat_is_not_recovered_from_history(self):
        self.history.update(snapshot(("Yes", "是")))
        self.history.update(snapshot(("No", "否")))
        self.history.update(snapshot(("Yes", "是")))
        self.assertEqual(self.pairs(), [("Yes", "是"), ("No", "否"), ("Yes", "是")])

    def test_missing_side_does_not_erase_already_captured_text(self):
        self.history.update(snapshot(("Hello", "你好")))
        self.history.update(snapshot(("Hello", None)))
        self.assertEqual(self.pairs(), [("Hello", "你好")])

    def test_traversal_pairs_survive_reversed_and_clipped_bounds(self):
        def item(role, text, index, top, bottom):
            return {"role": role, "text": text, "node_index": index,
                    "window_index": 0, "window_id": 1233,
                    "bounds": [170, top, 1000, bottom]}
        self.history.update({"items": [
            item("translation", "给我看看", 13, 1432, 1503),
            item("original", "Show me", 12, 1448, 1519),
            item("original", "First", 10, 700, 667),
            item("translation", "第一", 11, 744, 815),
        ]})
        self.assertEqual(self.pairs(), [("First", "第一"), ("Show me", "给我看看")])

    def test_overlapping_animation_clone_does_not_create_occurrence(self):
        items = []
        for index, top in ((12, 1192), (16, 1102)):
            for offset, role, text in ((0, "original", "Same row"), (1, "translation", "同一行")):
                y = top + offset * 148
                items.append({"role": role, "text": text, "node_index": index + offset,
                              "window_index": 0, "window_id": 1233,
                              "bounds": [170, y, 1000, y + 71]})
        self.history.update({"items": items})
        self.assertEqual(len(self.history.entries), 1)



if __name__ == "__main__":
    unittest.main()
