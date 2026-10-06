"""Durable inbox: ACK follows SQLite FULL commit; retry is exact/idempotent.

可靠收件库在 SQLite FULL 提交完成后才允许返回确认；原样重试具有幂等性。
"""
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
        with self.lock, self.db:
            return self._accept(envelope, event, fingerprint)

    def accept_batch(self, decoded):
        """Commit a prevalidated batch atomically; failures roll back every row.

        在单个事务中原子提交预验证批次，任意失败回滚整批。
        """
        if not decoded:
            raise ProtocolError("invalid_batch_schema")
        with self.lock, self.db:
            # Acquire the SQLite write lock before reads, including when a batch
            # overlaps an already-committed prefix after a lost ACK.
            # 先取得 SQLite 写锁再读取；确认丢失后，批次可能与已经提交的前缀重叠，仍需保持一致。
            self.db.execute("BEGIN IMMEDIATE")
            results = []
            previous = None
            for envelope, event, fingerprint in decoded:
                identity = (envelope["device_id"], envelope["stream_id"])
                if previous is not None:
                    if identity != previous[:2]:
                        raise ProtocolError("mixed_batch_streams")
                    if envelope["seq"] != previous[2] + 1:
                        raise ProtocolError("sequence_gap", 409)
                results.append(self._accept(envelope, event, fingerprint))
                previous = (*identity, envelope["seq"])
        # Returning outside the context guarantees COMMIT completed first.
        # 离开事务上下文后才返回，保证 COMMIT 已先完成。
        return results

    # An existing identity is a retry only when its ciphertext fingerprint is identical.
    # 只有密文指纹完全相同，已存在的身份元组才视为重试。
    def _accept(self, envelope, event, fingerprint):
        """Accept one decoded record inside the caller's lock and transaction.

        在调用者已经持有的锁和事务中接纳一条解码记录。
        """
        identity = (envelope["device_id"], envelope["stream_id"], envelope["seq"])
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
        return cursor.lastrowid, True

    def pending(self, consumer, limit=100):
        with self.lock:
            row = self.db.execute("SELECT cursor FROM consumers WHERE consumer=?", (consumer,)).fetchone()
            cursor = row[0] if row else 0
            return [dict(row) for row in self.db.execute(
                "SELECT * FROM events WHERE id>? ORDER BY id LIMIT ?", (cursor, limit)).fetchall()]

    # Consumer cursors are monotonic and are advanced only after output persistence succeeds.
    # 消费游标只前进不后退，并且只在输出持久保存成功后推进。
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
