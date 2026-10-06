"""Isolated batch receiver tests; only ephemeral loopback ports and temp data."""
import base64
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
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

from mobile_caption.collector import Collector
from mobile_caption.protocol import (Config, MAX_BATCH_BYTES, MAX_BATCH_ENVELOPES,
                                     MAX_ENVELOPE, ProtocolError, ack, decode_batch)
from mobile_caption.receiver import CaptionServer
from mobile_caption.store import Inbox, now
from test_mobile_caption import seal, snapshot


def batch_body(envelopes):
    return json.dumps({"v": 1, "envelopes": envelopes}, separators=(",", ":")).encode()


class BatchReceiverTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.path = self.base / "inbox.db"
        self.config = Config("batch-test-phone", secrets.token_urlsafe(32), secrets.token_bytes(32))
        self.inbox = Inbox(self.path)
        self.addCleanup(lambda: self.inbox.close())
        self.server = CaptionServer(("127.0.0.1", 0), self.config, self.inbox)
        self.thread = threading.Thread(target=self.server.serve_forever,
                                       kwargs={"poll_interval": 0.01}, daemon=True)
        self.thread.start()
        self.addCleanup(self.shutdown)

    def shutdown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(2)

    def envelopes(self, count=3, first=1, stream="batch-stream"):
        return [seal(self.config, snapshot(f"source {seq}", f"translation {seq}"), seq, stream)
                for seq in range(first, first + count)]

    def post(self, envelopes=None, raw=None, headers=None, path="/v1/captions/batch"):
        body = raw if raw is not None else batch_body(envelopes)
        conn = HTTPConnection(*self.server.server_address, timeout=3)
        request_headers = {"Authorization": "Bearer " + self.config.token,
                           "Content-Type": "application/json"}
        request_headers.update(headers or {})
        try:
            conn.request("POST", path, body=body, headers=request_headers)
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    def rows(self):
        # A separate connection sees committed records only.
        with closing(sqlite3.connect(self.path)) as db:
            return db.execute("SELECT device,stream,seq,nonce,fingerprint,event,received_at "
                              "FROM events ORDER BY id").fetchall()

    def get(self, path):
        conn = HTTPConnection(*self.server.server_address, timeout=3)
        try:
            conn.request("GET", path)
            response = conn.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            conn.close()

    def test_public_apk_exact_download_bytes_and_headers(self):
        public_apk = self.base / "public-update.apk"
        payload = b"PK\x03\x04synthetic-public-apk\x00\xff"
        public_apk.write_bytes(payload)
        self.server.update_apk = public_apk
        status, headers, body = self.get("/downloads/CaptionRelayCaptionBridge-0.2.0.apk")
        self.assertEqual(status, 200)
        self.assertEqual(body, payload)
        self.assertEqual(headers["Content-Length"], str(len(payload)))
        self.assertEqual(headers["Content-Type"], "application/vnd.android.package-archive")
        self.assertEqual(headers["Content-Disposition"],
                         'attachment; filename="CaptionRelayCaptionBridge-0.2.0.apk"')
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertEqual(self.rows(), [])

    def test_apk_only_exact_path_is_exposed_without_directory_or_traversal(self):
        public_apk = self.base / "public-update.apk"
        public_apk.write_bytes(b"synthetic-public-apk")
        self.server.update_apk = public_apk
        for path in ("/", "/downloads/", "/downloads", "/config.json", "/inbox.sqlite3",
                     "/downloads/../config.json", "/downloads/%2e%2e/config.json",
                     "/downloads/CaptionRelayCaptionBridge-0.2.0.apk/",
                     "/downloads/CaptionRelayCaptionBridge-0.2.0.apk?file=config.json",
                     "/downloads/CaptionRelayCaptionBridge-0.1.0.apk", "/v1/captions/batch"):
            with self.subTest(path=path):
                status, _, body = self.get(path)
                self.assertEqual(status, 404)
                self.assertEqual(json.loads(body), {"error": "not_found"})
        self.assertEqual(self.rows(), [])

    def test_apk_disabled_or_missing_does_not_expose_local_path(self):
        route = "/downloads/CaptionRelayCaptionBridge-0.2.0.apk"
        status, _, body = self.get(route)
        self.assertEqual((status, json.loads(body)), (404, {"error": "not_found"}))
        self.server.update_apk = self.base / "missing-private-path.apk"
        status, _, body = self.get(route)
        self.assertEqual((status, json.loads(body)), (404, {"error": "update_unavailable"}))
        self.assertNotIn(b"missing-private-path", body)

    def test_apk_empty_or_oversized_file_is_rejected(self):
        public_apk = self.base / "public-update.apk"
        self.server.update_apk = public_apk
        for size in (0, 16 * 1024 * 1024 + 1):
            with self.subTest(size=size):
                with public_apk.open("wb") as handle:
                    handle.truncate(size)
                status, _, body = self.get("/downloads/CaptionRelayCaptionBridge-0.2.0.apk")
                self.assertEqual((status, json.loads(body)), (503, {"error": "update_unavailable"}))

    def test_hundred_records_one_full_commit_ack_highest_sequence(self):
        envelopes = self.envelopes(MAX_BATCH_ENVELOPES)
        traced = []
        self.inbox.db.set_trace_callback(traced.append)
        self.assertEqual(self.inbox.db.execute("PRAGMA synchronous").fetchone()[0], 2)
        self.assertEqual(self.post(envelopes), (200, ack(self.config, "batch-stream", 100)))
        self.assertEqual([row[2] for row in self.rows()], list(range(1, 101)))
        self.assertEqual(sum(line == "BEGIN IMMEDIATE" for line in traced), 1)
        self.assertEqual(sum(line == "COMMIT" for line in traced), 1)

    def test_exact_retry_and_committed_prefix_are_idempotent_after_reopen(self):
        envelopes = self.envelopes(5)
        self.assertEqual(self.post(envelopes[:3])[0], 200)
        before = self.rows()
        expected = (200, ack(self.config, "batch-stream", 5))
        self.assertEqual(self.post(envelopes), expected)
        committed = self.rows()
        self.assertEqual(committed[:3], before)
        self.assertEqual(len(committed), 5)
        self.inbox.close()
        self.inbox = Inbox(self.path)
        self.server.inbox = self.inbox
        self.assertEqual(self.post(envelopes), expected)
        self.assertEqual(self.rows(), committed)

    def test_invalid_final_cipher_authenticates_entire_batch_before_store(self):
        envelopes = self.envelopes()
        cipher = bytearray(base64.b64decode(envelopes[-1]["ciphertext"]))
        cipher[0] ^= 1
        envelopes[-1]["ciphertext"] = base64.b64encode(cipher).decode()
        with patch.object(self.inbox, "accept_batch", wraps=self.inbox.accept_batch) as accept:
            self.assertEqual(self.post(envelopes), (403, {"error": "authentication_failed"}))
            accept.assert_not_called()
        self.assertEqual(self.rows(), [])

    def test_invalid_final_plaintext_schema_has_no_partial_commit(self):
        envelopes = self.envelopes(2)
        envelopes.append(seal(self.config, {"type": "heartbeat", "timestamp": now(),
                                            "queue_depth": True}, 3, "batch-stream"))
        self.assertEqual(self.post(envelopes), (400, {"error": "invalid_integer"}))
        self.assertEqual(self.rows(), [])

    def test_sequence_gap_and_descending_rows_are_rejected_without_commit(self):
        envelopes = self.envelopes()
        for values in (envelopes[1:], [envelopes[0], envelopes[2]],
                       list(reversed(envelopes)), [envelopes[0], envelopes[0]]):
            with self.subTest(sequences=[value["seq"] for value in values]):
                self.assertEqual(self.post(values), (409, {"error": "sequence_gap"}))
                self.assertEqual(self.rows(), [])

    def test_mixed_stream_or_device_rejected_without_commit(self):
        mixed = [self.envelopes(1)[0], self.envelopes(1, first=2, stream="other-stream")[0]]
        self.assertEqual(self.post(mixed), (400, {"error": "mixed_batch_streams"}))
        envelopes = self.envelopes()
        envelopes[-1]["device_id"] = "other-phone"
        self.assertEqual(self.post(envelopes), (403, {"error": "wrong_device"}))
        self.assertEqual(self.rows(), [])

    def test_conflicting_duplicate_preserves_original_records(self):
        envelopes = self.envelopes()
        self.assertEqual(self.post(envelopes[:2])[0], 200)
        before = self.rows()
        changed = [envelopes[0], self.envelopes(1, first=2)[0], envelopes[2]]
        self.assertEqual(self.post(changed), (409, {"error": "conflicting_duplicate"}))
        self.assertEqual(self.rows(), before)

    def test_nonce_reuse_inside_batch_rolls_back_inserted_prefix(self):
        envelopes = self.envelopes()
        reused = base64.b64decode(envelopes[1]["nonce"])
        envelopes[-1] = seal(self.config, seq=3, stream="batch-stream", nonce=reused)
        self.assertEqual(self.post(envelopes), (409, {"error": "nonce_reuse"}))
        self.assertEqual(self.rows(), [])

    def test_nonce_reuse_against_other_stream_rolls_back_only_new_batch(self):
        original = self.envelopes(1, stream="old-stream")[0]
        self.assertEqual(self.post([original])[0], 200)
        before = self.rows()
        envelopes = self.envelopes(2)
        envelopes[-1] = seal(self.config, seq=2, stream="batch-stream",
                             nonce=base64.b64decode(original["nonce"]))
        self.assertEqual(self.post(envelopes), (409, {"error": "nonce_reuse"}))
        self.assertEqual(self.rows(), before)

    def test_commit_failure_returns_no_ack_and_rolls_back_every_record(self):
        # Force SQLite itself to reject COMMIT after all INSERTs, not merely mock
        # the whole accept call. sqlite3's context manager must then ROLLBACK.
        def deny_commit(action, argument, unused, database, source):
            if action == sqlite3.SQLITE_TRANSACTION and argument == "COMMIT":
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        self.inbox.db.set_authorizer(deny_commit)
        envelopes = self.envelopes()
        try:
            self.assertEqual(self.post(envelopes), (503, {"error": "receiver_unavailable"}))
            self.assertFalse(self.inbox.db.in_transaction)
            self.assertEqual(self.rows(), [])
        finally:
            self.inbox.db.set_authorizer(None)
        self.assertEqual(self.post(envelopes), (200, ack(self.config, "batch-stream", 3)))

    def test_no_ack_is_sent_while_commit_is_still_pending(self):
        commit_started = threading.Event()
        allow_commit = threading.Event()

        def hold_commit(action, argument, unused, database, source):
            if action == sqlite3.SQLITE_TRANSACTION and argument == "COMMIT":
                commit_started.set()
                if not allow_commit.wait(2):
                    return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK

        self.inbox.db.set_authorizer(hold_commit)
        try:
            with ThreadPoolExecutor(max_workers=1) as pool:
                response = pool.submit(self.post, self.envelopes())
                try:
                    self.assertTrue(commit_started.wait(1))
                    self.assertFalse(response.done())
                    self.assertEqual(self.rows(), [])
                finally:
                    allow_commit.set()
                self.assertEqual(response.result(timeout=2),
                                 (200, ack(self.config, "batch-stream", 3)))
                self.assertEqual(len(self.rows()), 3)
        finally:
            allow_commit.set()
            self.inbox.db.set_authorizer(None)

    def test_collector_is_separate_from_ack_and_replays_committed_batch(self):
        texts = (("How much is it?", "多少钱？"),
                 ("Do you have sample caption?", "有示例字幕吗？"),
                 ("Please send a catalogue.", "请发目录。"))
        envelopes = [seal(self.config, snapshot(original, translation), seq, "batch-stream")
                     for seq, (original, translation) in enumerate(texts, 1)]
        with patch.object(Collector, "apply_pending", side_effect=OSError("history unavailable")) as apply:
            self.assertEqual(self.post(envelopes)[0], 200)
            apply.assert_not_called()
        output = self.base / "captions"
        collector = Collector(self.inbox, output, output / "captions.md", "batch-run",
                              {"state": "starting", "run_id": "batch-run", "started_at": now(),
                               "source": "mobile", "transport": "mobile_https"})
        self.assertEqual(collector.apply_pending(), 3)
        self.assertEqual(len(collector.history.entries), 3)
        self.assertEqual(self.post(envelopes)[0], 200)
        self.assertEqual(collector.apply_pending(), 0)

    def test_legacy_single_route_and_batch_share_same_order_and_ack(self):
        envelopes = self.envelopes()
        self.assertEqual(self.post(raw=json.dumps(envelopes[0]).encode(), path="/v1/captions"),
                         (200, ack(self.config, "batch-stream", 1)))
        self.assertEqual(self.post(envelopes), (200, ack(self.config, "batch-stream", 3)))
        self.assertEqual(self.post(raw=json.dumps(envelopes[-1]).encode(), path="/v1/captions"),
                         (200, ack(self.config, "batch-stream", 3)))
        self.assertEqual(len(self.rows()), 3)

    def test_concurrent_retries_commit_each_record_once(self):
        envelopes = self.envelopes(20)
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda _: self.post(envelopes), range(4)))
        self.assertEqual(results, [(200, ack(self.config, "batch-stream", 20))] * 4)
        self.assertEqual(len(self.rows()), 20)

    def test_batch_count_and_individual_legacy_envelope_limits(self):
        self.assertEqual(self.post(self.envelopes(MAX_BATCH_ENVELOPES + 1)),
                         (413, {"error": "payload_too_large"}))
        envelope = self.envelopes(1)[0]
        envelope["ciphertext"] = "A" * MAX_ENVELOPE
        self.assertEqual(self.post([envelope]), (413, {"error": "payload_too_large"}))
        self.assertEqual(self.rows(), [])

    def test_raw_request_limit_checked_before_body_read(self):
        request = ("POST /v1/captions/batch HTTP/1.1\r\nHost: localhost\r\n"
                   "Content-Type: application/json\r\nAuthorization: Bearer " + self.config.token +
                   f"\r\nContent-Length: {MAX_BATCH_BYTES + 1}\r\n\r\n")
        with socket.create_connection(self.server.server_address, timeout=2) as connection:
            connection.sendall(request.encode())
            self.assertIn(b"413", connection.recv(4096).split(b"\r\n")[0])
        self.assertEqual(self.rows(), [])

    def test_exact_raw_byte_limit_is_accepted(self):
        body = batch_body(self.envelopes(1))
        body += b" " * (MAX_BATCH_BYTES - len(body))
        self.assertEqual(self.post(raw=body), (200, ack(self.config, "batch-stream", 1)))

    def test_forged_token_missing_auth_and_duplicate_auth_rejected_before_body(self):
        for authorization in (b"", b"Authorization: Bearer wrong\r\n",
                              ("Authorization: Bearer " + self.config.token + "\r\n").encode() * 2):
            with self.subTest(authorization_count=authorization.count(b"Authorization")):
                with socket.create_connection(self.server.server_address, timeout=2) as connection:
                    # Deliberately omit both content type and body. Batch auth is first.
                    connection.sendall(b"POST /v1/captions/batch HTTP/1.1\r\nHost: localhost\r\n" +
                                       authorization + b"Content-Length: 1048576\r\n\r\n")
                    self.assertIn(b"401", connection.recv(4096).split(b"\r\n")[0])
        self.assertEqual(self.rows(), [])

    def test_strict_batch_schema_and_legacy_scalar_types(self):
        valid = self.envelopes(1)
        cases = [({"v": True, "envelopes": valid}, "unsupported_version"),
                 ({"v": 2, "envelopes": valid}, "unsupported_version"),
                 ({"v": 1, "envelopes": []}, "invalid_batch_schema"),
                 ({"v": 1, "envelopes": {}}, "invalid_batch_schema"),
                 ({"v": 1, "envelopes": valid, "plaintext": "bad"}, "invalid_batch_schema"),
                 ({"v": 1, "envelopes": [None]}, "expected_json_object"),
                 ({"v": 1, "envelopes": [{"type": "heartbeat", "timestamp": now()}]},
                  "invalid_event_schema")]
        for field, error in (("seq", "invalid_integer"), ("v", "unsupported_version")):
            item = dict(valid[0], **{field: True})
            cases.append(({"v": 1, "envelopes": [item]}, error))
        for body, error in cases:
            with self.subTest(error=error, keys=list(body)):
                self.assertEqual(self.post(raw=json.dumps(body).encode()), (400, {"error": error}))
        self.assertEqual(self.rows(), [])

    def test_duplicate_json_keys_and_malformed_json_rejected(self):
        for raw, error in ((b'{"v":1,"v":1,"envelopes":[]}', "duplicate_json_key"),
                           (b'{"v":1,"envelopes":[{"seq":1,"seq":2}]}', "duplicate_json_key"),
                           (b'{"v":1,"envelopes":[NaN]}', "invalid_json"),
                           (b"not-json", "invalid_json")):
            with self.subTest(error=error):
                self.assertEqual(self.post(raw=raw), (400, {"error": error}))
        self.assertEqual(self.rows(), [])

    def test_protocol_decoder_authenticates_before_size_or_json(self):
        with self.assertRaises(ProtocolError) as caught:
            decode_batch(self.config, "Bearer forged", b" " * (MAX_BATCH_BYTES + 1))
        self.assertEqual((caught.exception.code, caught.exception.status), ("unauthorized", 401))
        with self.assertRaises(ProtocolError) as caught:
            decode_batch(self.config, "Bearer " + self.config.token, b" " * (MAX_BATCH_BYTES + 1))
        self.assertEqual((caught.exception.code, caught.exception.status), ("payload_too_large", 413))


if __name__ == "__main__":
    unittest.main()
