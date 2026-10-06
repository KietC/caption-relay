"""Durable inbox: ACK follows SQLite FULL commit; retry is exact/idempotent."""
from datetime import datetime, timezone
import json
from pathlib import Path
import sqlite3
import threading

from .protocol import ProtocolError


def now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class Inbox:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY, device TEXT NOT NULL, stream TEXT NOT NULL,
                seq INTEGER NOT NULL, nonce TEXT NOT NULL, fingerprint TEXT NOT NULL,
                event TEXT NOT NULL, received_at TEXT NOT NULL,
                UNIQUE(device, stream, seq), UNIQUE(nonce));
            CREATE TABLE IF NOT EXISTS consumers (
                consumer TEXT PRIMARY KEY, cursor INTEGER NOT NULL DEFAULT 0);
        """)

    def accept(self, envelope, event, fingerprint):
        identity = (envelope["device_id"], envelope["stream_id"], envelope["seq"])
        with self.lock, self.db:
            row = self.db.execute("SELECT id,fingerprint FROM events WHERE device=? AND stream=? AND seq=?",
                                  identity).fetchone()
            if row:
                if row["fingerprint"] != fingerprint:
                    raise ProtocolError("conflicting_duplicate", 409)
                return row["id"], False
            previous = self.db.execute("SELECT MAX(seq) FROM events WHERE device=? AND stream=?",
                                       identity[:2]).fetchone()[0] or 0
            if identity[2] != previous + 1:
                raise ProtocolError("sequence_gap", 409)
            try:
                cursor = self.db.execute(
                    "INSERT INTO events(device,stream,seq,nonce,fingerprint,event,received_at) VALUES (?,?,?,?,?,?,?)",
                    (*identity, envelope["nonce"], fingerprint,
                     json.dumps(event, ensure_ascii=False, separators=(",", ":")), now()))
            except sqlite3.IntegrityError:
                raise ProtocolError("nonce_reuse", 409) from None
            row_id = cursor.lastrowid
        return row_id, True

    def pending(self, consumer, limit=100):
        with self.lock:
            row = self.db.execute("SELECT cursor FROM consumers WHERE consumer=?", (consumer,)).fetchone()
            cursor = row[0] if row else 0
            return [dict(row) for row in self.db.execute(
                "SELECT * FROM events WHERE id>? ORDER BY id LIMIT ?", (cursor, limit)).fetchall()]

    def mark_applied(self, consumer, row_id):
        with self.lock, self.db:
            self.db.execute("INSERT INTO consumers(consumer,cursor) VALUES (?,?) "
                            "ON CONFLICT(consumer) DO UPDATE SET cursor=MAX(cursor,excluded.cursor)",
                            (consumer, row_id))

    def recent_gaps(self):
        with self.lock:
            return [dict(row) for row in self.db.execute(
                "SELECT id,device,stream,seq,event,received_at FROM events "
                "WHERE json_extract(event,'$.type')='gap' ORDER BY id").fetchall()]

    def close(self):
        with self.lock:
            self.db.close()
