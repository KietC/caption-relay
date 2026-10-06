"""Synthetic-only tests for independent, non-durable live preview delivery.

仅使用合成数据，测试独立且不持久保存的即时预览传输。
"""
import base64
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import secrets
import socket
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from mobile_caption.live import LivePreview
from mobile_caption.protocol import (Config, ProtocolError, ack, decode, decode_live,
                                     live_aad, live_ack)
from mobile_caption.receiver import CaptionServer
from mobile_caption.store import Inbox, now
from test_mobile_caption import seal, snapshot


def live_seal(config, event=None, seq=1, stream="live-session", nonce=None):
    event = snapshot() if event is None else event
    nonce = secrets.token_bytes(12) if nonce is None else nonce
    cipher = AESGCM(config.key).encrypt(nonce, json.dumps(event, ensure_ascii=False).encode(),
                                         live_aad(config.device_id, stream, seq))
    return {"v": 1, "device_id": config.device_id, "stream_id": stream, "seq": seq,
            "nonce": base64.b64encode(nonce).decode(), "ciphertext": base64.b64encode(cipher).decode()}


class LivePreviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.config = Config("live-test-phone", secrets.token_urlsafe(32), secrets.token_bytes(32))
        self.inbox = Inbox(self.base / "inbox.sqlite3")
        self.addCleanup(self.inbox.close)
        self.preview = LivePreview(self.base / "output", "live-test-run")
        self.addCleanup(self.preview.close)
        self.server = CaptionServer(("127.0.0.1", 0), self.config, self.inbox, live=self.preview)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.shutdown)

    def shutdown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def values(self, envelope):
        return decode_live(self.config, "Bearer " + self.config.token, json.dumps(envelope).encode())

    def offer(self, envelope):
        return self.preview.offer(*self.values(envelope))

    def post(self, envelope, path="/v1/captions/live", token=None):
        connection = HTTPConnection(*self.server.server_address, timeout=3)
        try:
            connection.request("POST", path, json.dumps(envelope).encode(),
                               {"Authorization": "Bearer " + (token or self.config.token),
                                "Content-Type": "application/json"})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def wait_for(self, predicate, timeout=2):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            try:
                result = predicate()
                if result:
                    return result
            except (OSError, ValueError):
                pass
            time.sleep(0.005)
        self.fail("Synthetic preview did not reach expected state")

    def rendered(self, seq, stream=None):
        def check():
            value = json.loads(self.preview.path.read_text(encoding="utf-8"))
            return value if value["seq"] == seq and (stream is None or value["source"]["stream_id"] == stream) else None
        return self.wait_for(check)

    def assert_protocol(self, code, callback, *arguments):
        with self.assertRaises(ProtocolError) as caught:
            callback(*arguments)
        self.assertEqual(caught.exception.code, code)

    def test_crypto_domains_are_mutually_exclusive(self):
        authorization = "Bearer " + self.config.token
        durable = json.dumps(seal(self.config, snapshot())).encode()
        live = json.dumps(live_seal(self.config)).encode()
        self.assert_protocol("authentication_failed", decode_live, self.config, authorization, durable)
        self.assert_protocol("authentication_failed", decode, self.config, authorization, live)
        self.assertEqual(decode_live(self.config, authorization, live)[1]["type"], "snapshot")
        self.assertEqual(self.post(json.loads(live), path="/v1/captions")[0], 403)
        self.assertEqual(self.post(json.loads(durable))[0], 403)
        self.assertEqual(self.inbox.pending("test"), [])

    def test_live_ack_has_separate_signature_domain_and_live_only_marker(self):
        response = live_ack(self.config, "live-session", 55)
        expected = hmac.new(self.config.key,
                            b"caption-relay-live-ack-v1|live-test-phone|live-session|55", hashlib.sha256).hexdigest()
        self.assertEqual(response["mac"], expected)
        self.assertIs(response["live_only"], True)
        self.assertNotEqual(response["mac"], ack(self.config, "live-session", 55)["mac"])

    def test_live_accepts_snapshot_only(self):
        envelope = live_seal(self.config, {"type": "heartbeat", "timestamp": now()})
        self.assertEqual(self.post(envelope), (400, {"error": "live_snapshot_required"}))
        self.assertEqual(self.preview.stats()["accepted"], 0)

    def test_receipt_and_file_are_independent_of_inbox_and_history(self):
        history = self.preview.output / "history.json"
        history.write_bytes(b"synthetic-history-must-stay-unchanged")
        envelope = live_seal(self.config, seq=9000)
        with self.inbox.lock, patch.object(self.inbox, "accept", side_effect=AssertionError("must not call")), \
                patch.object(self.inbox, "accept_batch", side_effect=AssertionError("must not call")):
            started = time.monotonic()
            self.assertEqual(self.post(envelope), (200, live_ack(self.config, "live-session", 9000)))
            self.assertLess(time.monotonic() - started, 1)
            self.rendered(9000)
        self.assertEqual(self.inbox.pending("test"), [])
        self.assertEqual(self.inbox.db.execute("SELECT COUNT(*) FROM consumers").fetchone()[0], 0)
        self.assertEqual(history.read_bytes(), b"synthetic-history-must-stay-unchanged")
        self.assertFalse((self.preview.output / "caption_latest.json").exists())
        self.assertFalse((self.preview.output / "status.json").exists())

    def test_published_caption_has_required_source_and_timing_metadata(self):
        event = snapshot("synthetic source", "合成译文")
        self.offer(live_seal(self.config, event))
        value = self.rendered(1)
        self.assertEqual(value["run_id"], "live-test-run")
        self.assertEqual(value["source"], {"platform": "android", "device_id": "live-test-phone",
                         "stream_id": "live-session", "method": "accessibility_https_live"})
        self.assertTrue(value["preview"])
        self.assertEqual(value["captured_at"], event["timestamp"])
        self.assertEqual(value["original_count"], 1)
        self.assertEqual(value["translation_count"], 1)
        self.assertGreaterEqual(value["server_publish_ms"], 0)
        self.assertIsNotNone(datetime.fromisoformat(value["published_at"]).tzinfo)
        self.assertIsNotNone(datetime.fromisoformat(value["received_at"]).tzinfo)
        stats = self.preview.stats()
        self.assertNotIn("text", json.dumps(stats))
        self.assertNotIn("synthetic source", json.dumps(stats))

    def test_live_never_advances_durable_sequence_or_consumer(self):
        first = seal(self.config, snapshot(), seq=1, stream="durable-stream")
        self.assertEqual(self.post(first, path="/v1/captions")[0], 200)
        self.inbox.mark_applied("history", 1)
        self.assertEqual(self.post(live_seal(self.config, seq=900))[0], 200)
        self.assertEqual(self.inbox.db.execute("SELECT MAX(seq) FROM events").fetchone()[0], 1)
        self.assertEqual(self.inbox.db.execute("SELECT cursor FROM consumers").fetchone()[0], 1)
        second = seal(self.config, snapshot(), seq=2, stream="durable-stream")
        self.assertEqual(self.post(second, path="/v1/captions"), (200, ack(self.config, "durable-stream", 2)))

    def test_pending_slot_coalesces_and_slow_publisher_does_not_delay_ack(self):
        entered, release = threading.Event(), threading.Event()
        original_publish = self.preview._publish

        def slow_publish(job):
            entered.set()
            self.assertTrue(release.wait(2))
            return original_publish(job)

        with patch.object(self.preview, "_publish", side_effect=slow_publish):
            try:
                self.assertEqual(self.post(live_seal(self.config, seq=1))[0], 200)
                self.assertTrue(entered.wait(1))
                self.assertEqual(self.post(live_seal(self.config, seq=2))[0], 200)
                self.assertEqual(self.post(live_seal(self.config, seq=3))[0], 200)
                self.assertEqual(self.preview.stats()["pending"], 1)
                self.assertGreaterEqual(self.preview.stats()["coalesced"], 1)
                self.assertFalse(self.preview.path.exists())
            finally:
                release.set()
            self.rendered(3)
        self.assertEqual(self.preview.stats()["published"], 1)

    def test_exact_duplicate_does_not_refresh_or_republish_preview(self):
        envelope = live_seal(self.config, seq=7)
        self.assertTrue(self.offer(envelope))
        before = self.rendered(7)
        self.assertFalse(self.offer(envelope))
        self.assertEqual(self.rendered(7), before)
        self.assertEqual(self.preview.stats()["duplicates"], 1)
        self.assertEqual(self.preview.stats()["accepted"], 1)

    def test_conflicting_same_sequence_and_old_sequence_are_rejected(self):
        original = live_seal(self.config, seq=5)
        self.offer(original)
        self.assert_protocol("conflicting_duplicate", self.offer, live_seal(self.config, seq=5))
        self.assert_protocol("stale_live_sequence", self.offer, live_seal(self.config, seq=4))
        self.offer(live_seal(self.config, seq=8))
        self.assertFalse(self.offer(original))
        self.assertEqual(self.preview.stats()["latest_seq"], 8)
        self.rendered(8)

    def test_new_session_requires_newer_capture_and_retires_old_session(self):
        base = datetime.now(timezone.utc)
        def item(stream, seq, offset):
            event = snapshot()
            event["timestamp"] = (base + timedelta(seconds=offset)).isoformat()
            return live_seal(self.config, event, seq=seq, stream=stream)
        self.offer(item("old", 9, 0))
        self.assert_protocol("stale_live_session", self.offer, item("older-new", 1, -1))
        self.assert_protocol("stale_live_session", self.offer, item("equal-new", 1, 0))
        self.offer(item("new", 1, 1))
        self.assert_protocol("retired_live_session", self.offer, item("old", 10, 10))
        self.rendered(1, "new")
        # Within one stream seq wins; clock corrections cannot reorder its packets.
        self.offer(item("new", 2, -2))
        self.rendered(2, "new")

    def test_session_and_fingerprint_caches_are_bounded(self):
        base = datetime.now(timezone.utc)
        originals = []
        with self.preview.condition:
            for index in range(80):
                event = snapshot()
                event["timestamp"] = (base + timedelta(seconds=index)).isoformat()
                envelope = live_seal(self.config, event, stream=f"session-{index}")
                originals.append(envelope)
                self.offer(envelope)
            stats = self.preview.stats()
            self.assertEqual(stats["retired_sessions"], 64)
            self.assertEqual(stats["fingerprints"], 64)
            self.assertEqual(stats["nonce_cache"], 64)
            self.assertEqual(stats["pending"], 1)
        self.assert_protocol("stale_live_session", self.offer, originals[0])
        self.rendered(1, "session-79")

    def test_nonce_reuse_is_rejected(self):
        nonce = secrets.token_bytes(12)
        self.offer(live_seal(self.config, seq=1, nonce=nonce))
        self.assert_protocol("nonce_reuse", self.offer, live_seal(self.config, seq=2, nonce=nonce))
        self.assertEqual(self.preview.stats()["accepted"], 1)

    def test_transient_publication_failure_retries_latest_without_sensitive_error(self):
        replace = os.replace
        attempts = []
        def fail_once(source, target):
            attempts.append(1)
            if len(attempts) == 1:
                raise PermissionError("synthetic-sensitive-path-and-content")
            return replace(source, target)
        with patch("mobile_caption.live.os.replace", side_effect=fail_once):
            self.assertEqual(self.post(live_seal(self.config))[0], 200)
            self.wait_for(lambda: self.preview.stats()["publish_errors"] == 1)
            self.assertNotIn("synthetic-sensitive", json.dumps(self.preview.stats()))
            self.rendered(1)
        self.assertEqual(self.preview.stats()["publish_errors"], 1)

    def test_empty_live_snapshot_can_clear_preview_without_clearing_history(self):
        self.offer(live_seal(self.config, seq=1))
        self.rendered(1)
        empty = {"type": "snapshot", "timestamp": now(), "windows": []}
        self.offer(live_seal(self.config, empty, seq=2))
        value = self.rendered(2)
        self.assertEqual(value["items"], [])
        self.assertFalse(value["has_captions"])

    def test_auth_checked_before_slow_body_and_disabled_live(self):
        with socket.create_connection(self.server.server_address, timeout=2) as connection:
            connection.sendall(b"POST /v1/captions/live HTTP/1.1\r\nHost: localhost\r\n"
                               b"Authorization: Bearer wrong\r\nContent-Length: 100000\r\n\r\n")
            self.assertIn(b"401", connection.recv(4096).split(b"\r\n")[0])
        self.server.live = None
        self.assertEqual(self.post(live_seal(self.config)), (503, {"error": "live_unavailable"}))
        self.assertEqual(self.post(live_seal(self.config), token="forged")[0], 401)

    def test_client_disconnect_does_not_drop_latest_and_retry_is_idempotent(self):
        envelope = live_seal(self.config)
        body = json.dumps(envelope).encode()
        request = ("POST /v1/captions/live HTTP/1.1\r\nHost: localhost\r\n"
                   "Authorization: Bearer " + self.config.token + "\r\nContent-Type: application/json\r\n"
                   f"Content-Length: {len(body)}\r\n\r\n").encode() + body
        with socket.create_connection(self.server.server_address, timeout=2) as connection:
            connection.sendall(request)
            connection.shutdown(socket.SHUT_WR)
        self.rendered(1)
        self.assertEqual(self.post(envelope), (200, live_ack(self.config, "live-session", 1)))
        self.assertEqual(self.preview.stats()["accepted"], 1)
        self.assertEqual(self.inbox.pending("test"), [])

    def test_close_is_idempotent_stops_worker_and_rejects_later_offers(self):
        self.preview.close()
        self.preview.close()
        self.assertFalse(self.preview.thread.is_alive())
        self.assertEqual(self.post(live_seal(self.config)), (503, {"error": "live_unavailable"}))
        self.assertEqual(self.preview.stats()["pending"], 0)

    def test_close_discards_pending_and_inflight_frame_before_publication(self):
        entered, release = threading.Event(), threading.Event()
        publish = self.preview._publish
        def blocked(job):
            entered.set()
            self.assertTrue(release.wait(2))
            return publish(job)
        with patch.object(self.preview, "_publish", side_effect=blocked):
            self.offer(live_seal(self.config))
            self.assertTrue(entered.wait(1))
            self.offer(live_seal(self.config, seq=2))
            with ThreadPoolExecutor(max_workers=1) as pool:
                closing = pool.submit(self.preview.close)
                try:
                    self.wait_for(lambda: self.preview.stats()["closed"])
                finally:
                    release.set()
                closing.result(timeout=2)
        self.assertFalse(self.preview.path.exists())
        self.assertFalse(self.preview.thread.is_alive())

    def test_two_fixed_apk_routes_and_no_path_expansion(self):
        legacy = self.base / "CaptionRelayCaptionBridge-0.2.0.apk"
        current = self.base / "CaptionRelayCaptionBridge-0.3.0.apk"
        legacy.write_bytes(b"public-legacy-apk")
        current.write_bytes(b"public-current-apk")
        self.server.update_apk = legacy
        for route, expected in (("0.2.0", b"public-legacy-apk"), ("0.3.0", b"public-current-apk")):
            conn = HTTPConnection(*self.server.server_address, timeout=2)
            try:
                conn.request("GET", f"/downloads/CaptionRelayCaptionBridge-{route}.apk")
                response = conn.getresponse()
                self.assertEqual(response.status, 200)
                self.assertEqual(response.read(), expected)
                self.assertIn(f"{route}.apk", response.getheader("Content-Disposition"))
            finally:
                conn.close()
        for route in ("/downloads/", "/downloads/../config.json", "/downloads/CaptionRelayCaptionBridge-0.3.0.apk?x=1"):
            conn = HTTPConnection(*self.server.server_address, timeout=2)
            try:
                conn.request("GET", route)
                response = conn.getresponse()
                self.assertEqual(response.status, 404)
                response.read()
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
