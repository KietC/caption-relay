"""Volatile latest-only caption preview, isolated from the durable inbox/history.

仅保留最新值的易失字幕预览，与可靠收件库及历史隔离。
"""
from collections import OrderedDict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import threading
import time

from captions import extract
from .protocol import ProtocolError


def _now():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


class LivePreview:
    CACHE_LIMIT = 64

    def __init__(self, output, run_id):
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id
        self.path = self.output / "caption_live.json"
        self.condition = threading.Condition()
        self._pending = None
        self._closed = False
        self._generation = 0
        self._identity = None
        self._seq = 0
        self._captured_max = None
        self._retired = OrderedDict()
        self._fingerprints = OrderedDict()
        self._nonces = OrderedDict()
        self._stats = {"accepted": 0, "duplicates": 0, "coalesced": 0,
                       "published": 0, "publish_errors": 0, "last_error": None,
                       "last_publish_ms": None, "published_seq": None}
        self.thread = threading.Thread(target=self._run, name="caption-live-publisher", daemon=True)
        self.thread.start()

    def _remember(self, cache, key, value):
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > self.CACHE_LIMIT:
            cache.popitem(last=False)

    def offer(self, envelope, event, fingerprint, *, received_monotonic=None):
        """Admit an authenticated snapshot without SQLite or filesystem waits.

        接纳已认证的快照，不等待 SQLite 或文件系统写入。
        """
        received_monotonic = time.monotonic() if received_monotonic is None else received_monotonic
        received_at = _now()
        if event.get("type") != "snapshot":
            raise ProtocolError("live_snapshot_required")
        captured = datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00"))
        identity = (envelope["device_id"], envelope["stream_id"])
        seq = envelope["seq"]
        key = (*identity, seq)
        with self.condition:
            if self._closed:
                raise ProtocolError("live_unavailable", 503)
            if self._identity is not None and identity[0] != self._identity[0]:
                raise ProtocolError("wrong_device", 403)
            if identity in self._retired:
                raise ProtocolError("retired_live_session", 409)
            known = self._fingerprints.get(key)
            if known is not None:
                if known != fingerprint:
                    raise ProtocolError("conflicting_duplicate", 409)
                self._stats["duplicates"] += 1
                return False
            same_session = identity == self._identity
            if same_session and seq <= self._seq:
                raise ProtocolError("stale_live_sequence", 409)
            if (self._identity is not None and not same_session
                    and captured <= self._captured_max):
                raise ProtocolError("stale_live_session", 409)
            nonce_owner = self._nonces.get(envelope["nonce"])
            if nonce_owner is not None and nonce_owner != (key, fingerprint):
                raise ProtocolError("nonce_reuse", 409)
            if self._identity is not None and not same_session:
                self._remember(self._retired, self._identity, True)
            self._identity, self._seq = identity, seq
            self._captured_max = max(captured, self._captured_max) if self._captured_max else captured
            self._remember(self._fingerprints, key, fingerprint)
            self._remember(self._nonces, envelope["nonce"], (key, fingerprint))
            self._generation += 1
            if self._pending is not None:
                self._stats["coalesced"] += 1
            # Decoded objects belong exclusively to this request; no caption text
            # is copied into logs or the metadata-only stats dictionary.
            # 解码对象只属于本次请求；字幕文字不写入日志或仅含元数据的统计字典。
            self._pending = (self._generation, envelope, event, received_at, received_monotonic)
            self._stats["accepted"] += 1
            self.condition.notify()
            return True

    def stats(self):
        with self.condition:
            return {**self._stats, "pending": int(self._pending is not None), "closed": self._closed,
                    "latest_seq": self._seq, "generation": self._generation,
                    "retired_sessions": len(self._retired), "fingerprints": len(self._fingerprints),
                    "nonce_cache": len(self._nonces)}

    def _publish(self, job):
        generation, envelope, event, received_at, received_monotonic = job
        caption = extract(event)
        caption.update(run_id=self.run_id,
                       source={"platform": "android", "device_id": envelope["device_id"],
                               "stream_id": envelope["stream_id"], "method": "accessibility_https_live"},
                       seq=envelope["seq"], captured_at=event["timestamp"], received_at=received_at,
                       preview=True, published_at=_now(),
                       server_publish_ms=round((time.monotonic() - received_monotonic) * 1000, 3))
        temporary = self.path.with_name(self.path.name + ".tmp")
        try:
            temporary.write_text(json.dumps(caption, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
            with self.condition:
                obsolete = self._closed or generation != self._generation
            if obsolete:
                return False
            # Only this thread publishes; filesystem calls never hold the offer
            # lock. An in-flight publication can finish before the newest slot,
            # but a lower sequence can never replace an already-published newer one.
            # 仅此线程发布，文件操作不持有 offer 锁；正在发布的旧项可能先完成，但较低序号不能覆盖已经发布的新项。
            os.replace(temporary, self.path)
            return True
        finally:
            temporary.unlink(missing_ok=True)

    # One publisher drains a replaceable slot so filesystem latency never blocks request admission.
    # 单个发布线程读取可替换槽位，使文件延迟不阻塞请求接纳。
    def _run(self):
        while True:
            with self.condition:
                self.condition.wait_for(lambda: self._closed or self._pending is not None)
                if self._closed:
                    return
                job, self._pending = self._pending, None
            try:
                published = self._publish(job)
            except (OSError, ValueError, TypeError) as error:
                with self.condition:
                    self._stats["publish_errors"] += 1
                    self._stats["last_error"] = type(error).__name__
                    if not self._closed and job[0] == self._generation and self._pending is None:
                        self._pending = job
                        self.condition.wait(timeout=0.05)
                continue
            if published:
                with self.condition:
                    self._stats["published"] += 1
                    self._stats["published_seq"] = job[1]["seq"]
                    self._stats["last_publish_ms"] = round((time.monotonic() - job[4]) * 1000, 3)
                    self._stats["last_error"] = None

    def close(self):
        with self.condition:
            self._closed = True
            self._pending = None
            self.condition.notify_all()
        if threading.current_thread() is not self.thread:
            self.thread.join(timeout=5)
            if self.thread.is_alive():
                raise RuntimeError("live_publisher_stop_timeout")
