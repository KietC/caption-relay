"""Explicit owner-run live recovery check; restores Wi-Fi and never controls audio.

仅由使用者主动运行的真机恢复检查；还原 Wi-Fi 状态且不控制音频。
"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time

import mobile_caption_control as control


def probe(serial):
    raw = json.loads(control.adb(serial, "exec-out", "run-as", control.PACKAGE, "cat", "files/probe.json"))
    return {key: raw.get(key) for key in ("timestamp", "accessibility_connected", "capture_enabled", "capture_state",
                                         "original_count", "translation_count", "last_capture_error", "outbox", "network")}


def until(serial, condition, seconds=30):
    end = time.monotonic() + seconds
    last = {}
    while time.monotonic() < end:
        last = probe(serial)
        if condition(last):
            return last
        time.sleep(1)
    return last


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    options = parser.parse_args()
    serial = control.resolve_serial()
    before = probe(serial)
    wifi_enabled = b"Wifi is enabled" in control.adb(serial, "shell", "cmd", "wifi", "status")
    report = {"started_at": datetime.now(timezone.utc).isoformat(), "before": before,
              "wifi_initially_enabled": wifi_enabled, "audio_touched": False}
    stream = before["outbox"]["stream_id"]
    ack = before["outbox"]["last_ack_seq"]
    try:
        control.adb(serial, "shell", "svc", "wifi", "enable" if not wifi_enabled else "disable")
        changed = until(serial, lambda p: p["network"].get("default_network", {}).get("transports") !=
                        before["network"]["default_network"]["transports"] and p["outbox"]["last_ack_seq"] > ack)
        report["changed_network"] = changed
    finally:
        control.adb(serial, "shell", "svc", "wifi", "enable" if wifi_enabled else "disable")
    returned = until(serial, lambda p: p["network"].get("default_network", {}).get("transports") ==
                     before["network"]["default_network"]["transports"] and
                     p["outbox"]["last_ack_seq"] > report["changed_network"]["outbox"]["last_ack_seq"] and
                     p["outbox"]["queue_count"] == 0)
    report["restored_network"] = returned
    report["network_switch_passed"] = (
        report["changed_network"]["network"]["default_network"]["transports"] != before["network"]["default_network"]["transports"]
        and returned["network"]["default_network"]["transports"] == before["network"]["default_network"]["transports"]
        and returned["outbox"]["stream_id"] == stream and returned["outbox"]["queue_count"] == 0
        and returned["outbox"]["last_ack_seq"] > report["changed_network"]["outbox"]["last_ack_seq"])
    previous = control.viewer.status(control.viewer.OUTPUT)
    if previous.get("running") and previous.get("source") == "mobile":
        folder = Path(previous["capture_output"])
        # Graceful worker retirement exercises the existing supervisor recovery.
        # 让工作进程平稳退出，以检查既有监督器的恢复路径。
        (folder / "stop.request").write_text("recovery verification\n", encoding="utf-8")
        end = time.monotonic() + 35
        while time.monotonic() < end:
            current = control.viewer.status(control.viewer.OUTPUT)
            if (current.get("capture_pid") != previous.get("capture_pid") and
                    current.get("capture", {}).get("state") == "connected" and
                    current["capture"].get("last_seq", 0) > previous.get("capture", {}).get("last_seq", 0)):
                break
            time.sleep(1)
        report["receiver_restart"] = {"old_pid": previous.get("capture_pid"), "new_pid": current.get("capture_pid"),
                                      "old_seq": previous.get("capture", {}).get("last_seq"),
                                      "new_seq": current.get("capture", {}).get("last_seq"),
                                      "state": current.get("capture", {}).get("state"),
                                      "same_history_directory": current.get("capture_output") == previous.get("capture_output")}
        report["receiver_restart_passed"] = (report["receiver_restart"]["old_pid"] != report["receiver_restart"]["new_pid"]
                                              and report["receiver_restart"]["state"] == "connected"
                                              and report["receiver_restart"]["same_history_directory"])
    report["after"] = probe(serial)
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    target = control.BASE / "output" / "mobile_recovery_verification.json"
    control.save_json(target, report)
    print(json.dumps({"report": str(target), "network_switch_passed": report["network_switch_passed"],
                      "receiver_restart_passed": report.get("receiver_restart_passed"),
                      "queue_count": report["after"]["outbox"]["queue_count"],
                      "stream_preserved": report["after"]["outbox"]["stream_id"] == stream}, ensure_ascii=False))


if __name__ == "__main__":
    main()
