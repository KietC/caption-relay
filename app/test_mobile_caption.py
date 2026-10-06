"""Synthetic local regression tests; no real phone credentials or transcripts are loaded.

合成本机回归测试，不加载真实手机凭据或字幕。
"""
import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from http.client import HTTPConnection
import json
from pathlib import Path
import secrets
import socket
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from mobile_caption.collector import Collector
from mobile_caption.protocol import Config, MAX_ENVELOPE, PACKAGE, ProtocolError, aad, ack, decode
from mobile_caption.receiver import CaptionServer
from mobile_caption.store import Inbox, now


def snapshot(original="How much is it?", translation="多少钱？"):
    return {"type": "snapshot", "timestamp": now(), "windows": [{
        "id": 3, "package": PACKAGE, "bounds": [0, 0, 1080, 2400], "nodes": [
            {"package": PACKAGE, "id": PACKAGE + ":id/message_body", "text": original,
             "bounds": [10, 10, 1000, 30]},
            {"package": PACKAGE, "id": PACKAGE + ":id/tv_dest_message", "text": translation,
             "bounds": [10, 31, 1000, 60]}]}]}


def seal(config, event=None, seq=1, stream="test-stream", nonce=None):
    nonce = nonce or secrets.token_bytes(12)
    event = event or {"type": "heartbeat", "timestamp": now()}
    plaintext = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    cipher = AESGCM(config.key).encrypt(nonce, plaintext, aad(config.device_id, stream, seq))
    return {"v": 1, "device_id": config.device_id, "stream_id": stream, "seq": seq,
            "nonce": base64.b64encode(nonce).decode(), "ciphertext": base64.b64encode(cipher).decode()}


class MobileCaptionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.config = Config("xiaomi-test", secrets.token_urlsafe(32), secrets.token_bytes(32))
        self.inbox = Inbox(self.base / "inbox.db")
        self.addCleanup(lambda: self.inbox.close())

    def decode(self, envelope, token=None):
        return decode(self.config, "Bearer " + (token or self.config.token), json.dumps(envelope).encode())

    def accept(self, event=None, seq=1, stream="test-stream", envelope=None):
        envelope = envelope or seal(self.config, event, seq, stream)
        values = self.decode(envelope)
        return self.inbox.accept(*values)

    def collector(self, name="output", run_id="test-run"):
        output = self.base / name
        status = {"state": "starting", "run_id": run_id, "started_at": now(),
                  "source": "mobile", "transport": "mobile_https"}
        return Collector(self.inbox, output, output / "captions.md", run_id, status)

    def assert_protocol(self, code, function, *args):
        with self.assertRaises(ProtocolError) as caught:
            function(*args)
        self.assertEqual(caught.exception.code, code)

    def test_cipher_roundtrip_and_ack_mac(self):
        envelope, event, digest = self.decode(seal(self.config, snapshot()))
        self.assertEqual(event["windows"][0]["nodes"][1]["text"], "多少钱？")
        response = ack(self.config, "test-stream", 1)
        expected = hmac.new(self.config.key,
                            b"caption-relay-ack-v1|xiaomi-test|test-stream|1", hashlib.sha256).hexdigest()
        self.assertEqual(response["mac"], expected)
        self.assertEqual(len(digest), 64)

    def test_wrong_token_and_device_are_rejected(self):
        envelope = seal(self.config)
        self.assert_protocol("unauthorized", self.decode, envelope, "bad-token")
        envelope["device_id"] = "different-phone"
        self.assert_protocol("wrong_device", self.decode, envelope)

    def test_tampered_cipher_and_aad_are_rejected(self):
        envelope = seal(self.config)
        cipher = bytearray(base64.b64decode(envelope["ciphertext"]))
        cipher[0] ^= 1
        envelope["ciphertext"] = base64.b64encode(cipher).decode()
        self.assert_protocol("authentication_failed", self.decode, envelope)
        envelope = seal(self.config)
        envelope["seq"] = 2
        self.assert_protocol("authentication_failed", self.decode, envelope)

    def test_no_other_accessibility_nodes_or_packages(self):
        event = snapshot()
        event["windows"][0]["nodes"][0]["id"] = PACKAGE + ":id/private_contact"
        self.assert_protocol("unapproved_caption_node", self.decode, seal(self.config, event))
        event["windows"][0]["package"] = "com.some.bank"
        self.assert_protocol("unapproved_caption_package", self.decode, seal(self.config, event))

    def test_compact_nodes_use_strict_container_roles_end_to_end(self):
        event = snapshot("compact source", "浮窗译文")
        nodes = event["windows"][0]["nodes"]
        for item, container in zip(nodes, ("recyclerView_source", "recyclerView_dest")):
            item["id"] = PACKAGE + ":id/sentence_id"
            item["container_id"] = PACKAGE + ":id/" + container
        self.accept(event)
        collector = self.collector()
        collector.apply_pending()
        self.assertEqual(collector.history.entries[0]["original"], "compact source")
        self.assertEqual(collector.history.entries[0]["translation"], "浮窗译文")
        latest = json.loads((collector.output / "caption_latest.json").read_text(encoding="utf-8"))
        self.assertEqual(latest["items"][0]["container_id"], PACKAGE + ":id/recyclerView_source")

    def test_compact_missing_foreign_and_legacy_mismatched_containers_rejected(self):
        for container in (None, "other.app:id/recyclerView_source", PACKAGE + ":id/unknown", [], ""):
            with self.subTest(container=container):
                event = snapshot()
                item = event["windows"][0]["nodes"][0]
                item["id"] = PACKAGE + ":id/sentence_id"
                if container is not None:
                    item["container_id"] = container
                self.assert_protocol("unapproved_caption_container", self.decode, seal(self.config, event))
        for container in (PACKAGE + ":id/recyclerView_dest", "other.app:id/recyclerView_source"):
            event = snapshot()
            event["windows"][0]["nodes"][0]["container_id"] = container
            self.assert_protocol("unapproved_caption_container", self.decode, seal(self.config, event))

    def test_malformed_json_duplicate_key_and_size(self):
        authorization = "Bearer " + self.config.token
        self.assert_protocol("invalid_json", decode, self.config, authorization, b"{broken")
        self.assert_protocol("duplicate_json_key", decode, self.config, authorization, b'{"v":1,"v":1}')
        self.assert_protocol("payload_too_large", decode, self.config, authorization, b" " * (MAX_ENVELOPE + 1))

    def test_snapshot_missing_translation_is_not_invented(self):
        event = snapshot()
        event["windows"][0]["nodes"].pop()
        self.accept(event)
        collector = self.collector()
        collector.apply_pending()
        self.assertIsNone(collector.history.entries[0]["translation"])

    def test_exact_duplicate_is_durable_and_not_republished(self):
        envelope = seal(self.config, snapshot())
        first_id, fresh = self.accept(envelope=envelope)
        self.assertTrue(fresh)
        collector = self.collector()
        self.assertEqual(collector.apply_pending(), 1)
        self.assertEqual(self.accept(envelope=envelope), (first_id, False))
        self.assertEqual(collector.apply_pending(), 0)
        self.assertEqual(len(collector.history.entries), 1)
        self.inbox.close()
        self.inbox = Inbox(self.base / "inbox.db")
        self.assertEqual(self.accept(envelope=envelope), (first_id, False))
        self.assertEqual(self.inbox.pending(collector.consumer), [])

    def test_conflicting_duplicate_and_nonce_reuse_are_rejected(self):
        envelope = seal(self.config, snapshot())
        self.accept(envelope=envelope)
        self.assert_protocol("conflicting_duplicate", self.accept, snapshot("changed", "改变"))
        same_nonce = base64.b64decode(envelope["nonce"])
        other = seal(self.config, snapshot(), seq=2, nonce=same_nonce)
        self.assert_protocol("nonce_reuse", self.accept, None, 1, "test-stream", other)

    def test_sequence_skips_and_first_seq_not_one_are_rejected(self):
        self.assert_protocol("sequence_gap", self.accept, None, 2)
        self.accept()
        self.assert_protocol("sequence_gap", self.accept, None, 3)
        self.accept(seq=2)
        self.accept(stream="new-stream")

    def test_source_metadata_and_markdown_history_persist(self):
        self.accept(snapshot())
        collector = self.collector()
        collector.apply_pending()
        latest = json.loads((collector.output / "caption_latest.json").read_text(encoding="utf-8"))
        self.assertEqual(latest["source"], {"platform": "android", "device_id": "xiaomi-test",
                                         "stream_id": "test-stream", "method": "accessibility_https"})
        self.assertIsNone(latest["speaker"])
        self.assertIsNone(latest["is_final"])
        self.assertIn("多少钱？", (collector.output / "captions.md").read_text(encoding="utf-8"))
        self.accept(snapshot("Do you have sample caption?", "有示例字幕吗？"), seq=2)
        collector.apply_pending()
        self.assertEqual(len(collector.history.entries), 2)

    def test_two_consumer_outputs_replay_independently(self):
        self.accept(snapshot())
        shadow = self.collector("shadow")
        viewer = self.collector("viewer")
        self.assertEqual(shadow.apply_pending(), 1)
        self.assertEqual(viewer.apply_pending(), 1)
        self.assertEqual(shadow.apply_pending(), 0)
        self.assertEqual(shadow.history.as_dict()["original"], viewer.history.as_dict()["original"])

    def test_old_inbox_events_do_not_refresh_connection_time(self):
        self.accept(snapshot())
        stale = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        with self.inbox.db:
            self.inbox.db.execute("UPDATE events SET received_at=?", (stale,))
        collector = self.collector()
        collector.apply_pending()
        self.assertEqual(collector.status["last_event_at"], stale)
        latest = json.loads((collector.output / "caption_latest.json").read_text(encoding="utf-8"))
        self.assertEqual(latest["received_at"], stale)

    def test_gap_is_recorded_as_gap_not_fabricated_caption(self):
        gap = {"type": "gap", "timestamp": now(), "dropped_snapshots": 6,
               "dropped": 6, "first_dropped_at": now(), "last_dropped_at": now(), "reason": "payload_too_large"}
        self.accept(gap)
        collector = self.collector()
        collector.apply_pending()
        self.assertEqual(collector.history.entries, [])
        self.assertEqual(collector.status["gap_count"], 1)
        self.assertIn("Dropped snapshots: 6", (collector.output / "transmission_gaps.md").read_text(encoding="utf-8"))

    def test_phone_backlog_is_not_classified_as_current(self):
        event = snapshot()
        event["timestamp"] = (datetime.now(timezone.utc) - timedelta(minutes=3)).isoformat()
        self.accept(event)
        collector = self.collector()
        collector.apply_pending()
        self.assertEqual(collector.status["caption_freshness"], "delayed_or_clock_skew")
        self.assertGreater(collector.status["snapshot_age_at_receive_seconds"], 170)

    def test_actual_android_heartbeat_bool_and_gap_alias_validation(self):
        self.accept({"type": "heartbeat", "timestamp": now(), "captions_visible": True})
        self.assert_protocol("invalid_captions_visible", self.decode,
                             seal(self.config, {"type": "heartbeat", "timestamp": now(), "captions_visible": "true"}))
        gap = {"type": "gap", "timestamp": now(), "dropped_snapshots": 2, "dropped": 3,
               "first_dropped_at": now(), "last_dropped_at": now(), "reason": "outbox_full"}
        self.assert_protocol("inconsistent_gap_count", self.decode, seal(self.config, gap))

    def test_history_failure_does_not_advance_consumer_cursor(self):
        self.accept(snapshot())
        collector = self.collector()
        with patch.object(collector.history, "update", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                collector.apply_pending()
        self.assertEqual(len(self.inbox.pending(collector.consumer)), 1)
        collector.apply_pending()
        self.assertEqual(self.inbox.pending(collector.consumer), [])

    def test_replay_after_history_saved_but_cursor_failed_is_idempotent(self):
        self.accept(snapshot())
        collector = self.collector()
        with patch.object(self.inbox, "mark_applied", side_effect=OSError("crash")):
            with self.assertRaises(OSError):
                collector.apply_pending()
        collector = self.collector()
        collector.apply_pending()
        self.assertEqual(len(collector.history.entries), 1)


class HTTPReceiverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.config = Config("test-phone", secrets.token_urlsafe(32), secrets.token_bytes(32))
        self.inbox = Inbox(Path(self.temp.name) / "inbox.db")
        self.addCleanup(self.inbox.close)
        self.server = CaptionServer(("127.0.0.1", 0), self.config, self.inbox)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.shutdown)

    def shutdown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def post(self, body, headers=None, path="/v1/captions"):
        conn = HTTPConnection(*self.server.server_address, timeout=3)
        request_headers = {"Authorization": "Bearer " + self.config.token,
                           "Content-Type": "application/json"}
        request_headers.update(headers or {})
        conn.request("POST", path, body=body, headers=request_headers)
        response = conn.getresponse()
        data = response.read()
        status = response.status
        conn.close()
        return status, json.loads(data)

    def test_ack_only_after_sqlite_commit_and_retry_idempotent(self):
        body = json.dumps(seal(self.config, snapshot())).encode()
        status, response = self.post(body)
        self.assertEqual(status, 200)
        self.assertEqual(response, ack(self.config, "test-stream", 1))
        other = sqlite3.connect(str(Path(self.temp.name) / "inbox.db"))
        try:
            self.assertEqual(other.execute("SELECT COUNT(*) FROM events").fetchone()[0], 1)
        finally:
            other.close()
        self.assertEqual(self.post(body), (status, response))

    def test_persistence_failure_returns_503_without_ack(self):
        with patch.object(self.inbox, "accept", side_effect=OSError("sensitive/path")):
            status, response = self.post(json.dumps(seal(self.config)).encode())
        self.assertEqual(status, 503)
        self.assertEqual(response, {"error": "receiver_unavailable"})

    def test_wrong_token_malformed_body_and_unknown_route(self):
        body = json.dumps(seal(self.config)).encode()
        self.assertEqual(self.post(body, {"Authorization": "Bearer wrong"})[0], 401)
        self.assertEqual(self.post(b"not-json")[0], 400)
        self.assertEqual(self.post(body, path="/other")[0], 404)

    def test_unauthorized_slow_body_rejected_before_read(self):
        with socket.create_connection(self.server.server_address, timeout=2) as connection:
            connection.sendall(b"POST /v1/captions HTTP/1.1\r\nHost: localhost\r\n"
                               b"Content-Type: application/json\r\nAuthorization: Bearer wrong\r\n"
                               b"Content-Length: 100000\r\n\r\n")
            response = connection.recv(4096)
            self.assertIn(b"401", response.split(b"\r\n")[0])

    def test_reject_public_bind(self):
        with self.assertRaises(ValueError):
            CaptionServer(("0.0.0.0", 0), self.config, self.inbox)


if __name__ == "__main__":
    unittest.main()
