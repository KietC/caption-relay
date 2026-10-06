"""Adapt durable Android packets to the existing native caption window files.

把已持久接收的 Android 数据包转换为既有原生字幕窗口文件。
"""
import json
from datetime import datetime
from pathlib import Path

from bridge import write_json, write_text
from caption_history import CaptionHistory
from captions import extract, format_text
from .store import now


class Collector:
    def __init__(self, inbox, output, markdown, run_id, status):
        self.inbox = inbox
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.consumer = str(self.output.resolve()).casefold()
        self.run_id = run_id
        self.status = status
        self.history = CaptionHistory(self.output, markdown)
        # A new output consumes the durable backlog; an existing one resumes
        # only after its own saved cursor. The USB history is not overwritten.
        # 新输出读取可靠积压记录；已有输出从各自保存的游标之后继续，不覆盖 USB 历史。
        empty = extract({"windows": [], "received_at": now()})
        empty["run_id"] = run_id
        write_json(self.output / "caption_latest.json", empty)
        write_text(self.output / "caption_latest.txt", "等待手机 App 字幕 / Waiting for phone captions.\n")
        self.write_status()

    def write_status(self):
        write_json(self.output / "status.json", self.status)

    def _write_gaps(self):
        gaps = self.inbox.recent_gaps()
        lines = ["# 字幕传输缺口 / Caption delivery gaps", "",
                 "以下缺口由手机队列报告，不能推断丢失的内容或说话人。", ""]
        for row in gaps:
            event = json.loads(row["event"])
            lines += [f"## {row['device']} / {row['stream']} / seq {row['seq']}", "",
                      f"Dropped snapshots: {event['dropped_snapshots']}",
                      f"From: {event['first_dropped_at']}",
                      f"To: {event['last_dropped_at']}", f"Reason: {event['reason']}", ""]
        write_text(self.output / "transmission_gaps.md", "\n".join(lines))
        self.status["gap_count"] = len(gaps)

    # Replay durable records in database order; preview traffic never advances this cursor.
    # 按数据库顺序重放可靠记录；预览流量绝不推进此游标。
    def apply_pending(self):
        count = 0
        for row in self.inbox.pending(self.consumer):
            event = json.loads(row["event"])
            source = {"platform": "android", "device_id": row["device"],
                      "stream_id": row["stream"], "method": "accessibility_https"}
            event.update(received_at=row["received_at"], run_id=self.run_id, source=source)
            self.status.update(state="connected", last_event_at=row["received_at"],
                               last_phone_timestamp=event["timestamp"], last_event_type=event["type"],
                               device_id=row["device"], stream_id=row["stream"], last_seq=row["seq"],
                               inbox_cursor=row["id"])
            if event["type"] == "snapshot":
                received_time = datetime.fromisoformat(row["received_at"].replace("Z", "+00:00"))
                captured_time = datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00"))
                age = round((received_time - captured_time).total_seconds(), 3)
                freshness = "current" if -5 <= age <= 15 else "delayed_or_clock_skew"
                caption = extract(event)
                caption.update(run_id=self.run_id, source=source, seq=row["seq"],
                               captured_at=event["timestamp"], freshness=freshness,
                               age_at_receive_seconds=age)
                # Mark applied only after BOTH the history and latest files
                # have been atomically replaced. A crash may replay this same
                # snapshot; CaptionHistory's consecutive dedup is idempotent.
                # 历史和最新状态两个文件均原子替换后才推进游标；崩溃可能重放同一快照，CaptionHistory 的连续去重具备幂等性。
                self.history.update(caption)
                write_json(self.output / "caption_latest.json", caption)
                write_text(self.output / "caption_latest.txt", format_text(caption))
                self.status.update(last_snapshot_at=row["received_at"],
                                   captured_at=event["timestamp"],
                                   caption_freshness=freshness,
                                   snapshot_age_at_receive_seconds=age,
                                   original_count=caption["original_count"],
                                   translation_count=caption["translation_count"],
                                   has_captions=caption["has_captions"],
                                   text_node_count=len(caption["items"]))
            elif event["type"] == "gap":
                self._write_gaps()
                self.status["last_gap"] = {key: value for key, value in event.items()
                                           if key not in ("run_id", "source")}
            self.write_status()
            self.inbox.mark_applied(self.consumer, row["id"])
            count += 1
        return count
