"""Synthetic local regression tests; no real phone credentials or transcripts are loaded.

合成本机回归测试，不加载真实手机凭据或字幕。
"""
import copy
import json
from pathlib import Path
import unittest

from captions import PACKAGE, SENTENCE_ID, extract, format_text


def node(role, text, top, **overrides):
    result = {"package": PACKAGE, "id": PACKAGE + ":id/" +
              ("message_body" if role == "S" else "tv_dest_message"),
              "text": text, "bounds": [10, top, 300, top + 50]}
    result.update(overrides)
    return result


def snapshot(nodes):
    return {"type": "snapshot", "windows": [{"package": PACKAGE, "id": 1, "nodes": nodes}]}


class CaptionTests(unittest.TestCase):
    def test_compact_same_node_id_resolves_from_exact_container(self):
        source = node("S", "源语言也可能是中文", 100, id=SENTENCE_ID,
                      container_id=PACKAGE + ":id/recyclerView_source")
        target = node("T", "The translation can be English.", 200, id=SENTENCE_ID,
                      container_id=PACKAGE + ":id/recyclerView_dest")
        result = extract(snapshot([source, target]))
        self.assertEqual([(item["role"], item["text"]) for item in result["items"]],
                         [("original", source["text"]), ("translation", target["text"])])
        self.assertEqual(result["original_count"], 1)
        self.assertEqual(result["translation_count"], 1)
        self.assertEqual(result["items"][0]["node_id"], SENTENCE_ID)
        self.assertEqual(result["items"][1]["container_id"], target["container_id"])
        self.assertIsNone(result["speaker"])
        self.assertIsNone(result["is_final"])

    def test_compact_missing_or_foreign_container_is_not_language_guessed(self):
        for container in (None, "other.app:id/recyclerView_source", PACKAGE + ":id/unknown", [], ""):
            with self.subTest(container=container):
                unresolved = node("S", "English source with 中文 text", 100, id=SENTENCE_ID)
                if container is not None:
                    unresolved["container_id"] = container
                result = extract(snapshot([unresolved]))
                self.assertEqual(result["items"], [])
                self.assertFalse(result["has_captions"])

    def test_compact_original_without_dest_stays_unpaired(self):
        source = node("S", "Only source", 100, id=SENTENCE_ID,
                      container_id=PACKAGE + ":id/recyclerView_source")
        result = extract(snapshot([source]))
        self.assertEqual(result["paragraphs"][0]["original"], "Only source")
        self.assertIsNone(result["paragraphs"][0]["translation"])
        self.assertEqual(result["paragraphs"][0]["pairing"], "unpaired")

    def test_expanded_and_compact_rows_can_coexist(self):
        event = snapshot([node("S", "legacy", 100), node("T", "旧式", 200),
                          node("S", "compact", 300, id=SENTENCE_ID,
                               container_id=PACKAGE + ":id/recyclerView_source"),
                          node("T", "浮窗", 400, id=SENTENCE_ID,
                               container_id=PACKAGE + ":id/recyclerView_dest")])
        result = extract(event)
        self.assertEqual([(p["original"], p["translation"]) for p in result["paragraphs"]],
                         [("legacy", "旧式"), ("compact", "浮窗")])
        mismatch = node("S", "wrong metadata", 100, container_id=PACKAGE + ":id/recyclerView_dest")
        self.assertEqual(extract(snapshot([mismatch]))["items"], [])

    def test_missing_middle_translation_never_zip_shifts(self):
        result = extract(snapshot([node("S", "one", 100), node("S", "two", 300),
                                   node("T", "two translation", 400)]))
        self.assertEqual([(p["original"], p["translation"]) for p in result["paragraphs"]],
                         [("one", None), ("two", "two translation")])

    def test_empty_translation_does_not_borrow_later_text(self):
        result = extract(snapshot([node("S", "one", 100), node("T", "", 200),
                                   node("S", "two", 300), node("T", "second", 400)]))
        self.assertEqual(result["paragraphs"][0]["translation"], "")
        self.assertEqual(result["paragraphs"][1]["translation"], "second")

    def test_duplicate_occurrences_preserved(self):
        result = extract(snapshot([node("S", "same", 100), node("T", "repeat", 200),
                                   node("S", "same", 300), node("T", "repeat", 400)]))
        self.assertEqual(result["original_count"], 2)
        self.assertEqual(result["paired_count"], 2)
        self.assertEqual(format_text(result).count("原文：same"), 2)

    def test_non_caption_and_foreign_nodes_rejected(self):
        result = extract(snapshot([node("S", "00:03", 100, id=PACKAGE + ":id/message_separator"),
                                   node("S", "foreign", 200, package="other.app")]))
        self.assertFalse(result["has_captions"])
        self.assertEqual(result["items"], [])

    def test_visual_order_and_geometry_mismatch(self):
        result = extract(snapshot([node("T", "later", 400), node("S", "source", 300),
                                   node("T", "orphan", 100)]))
        self.assertEqual(result["paragraphs"][0]["translation"], "orphan")
        self.assertEqual(result["paragraphs"][1]["translation"], "later")
        invalid = extract(snapshot([node("S", "clipped", 700, bounds=[10, 700, 300, 500]),
                                    node("T", "visible", 700)]))
        self.assertEqual(invalid["paired_count"], 0)

    def test_windows_never_cross_pair(self):
        event = snapshot([node("S", "source", 100)])
        event["windows"].append({"package": PACKAGE, "id": 2, "nodes": [node("T", "other", 200)]})
        self.assertEqual(extract(event)["paired_count"], 0)

    def test_signature_ignores_metadata_but_keeps_multiplicity(self):
        event = snapshot([node("S", "same", 100), node("T", "repeat", 200)])
        shifted = copy.deepcopy(event)
        shifted["timestamp"] = "later"
        shifted["windows"][0]["id"] = 22
        for item in shifted["windows"][0]["nodes"]:
            item["bounds"] = [value + 40 for value in item["bounds"]]
        self.assertEqual(extract(event)["signature"], extract(shifted)["signature"])
        shifted["windows"][0]["nodes"].extend([node("S", "same", 500), node("T", "repeat", 600)])
        self.assertNotEqual(extract(event)["signature"], extract(shifted)["signature"])



if __name__ == "__main__":
    unittest.main()
