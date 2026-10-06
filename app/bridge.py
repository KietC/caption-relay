"""Read native Android caption nodes over USB; no OCR or network upload.

通过 USB 读取 Android 原生字幕节点，不进行 OCR 或网络上传。
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import msvcrt
from datetime import datetime, timezone
from captions import extract, format_text
from atomic_io import replace_with_retry

from runtime_paths import BASE, ADB as DEFAULT_ADB
REMOTE = "/data/local/tmp/captionrelay_caption_bridge.jar"


def now():
    return datetime.now(timezone.utc).isoformat()


def write_text(path, value):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(value, encoding="utf-8")
    replace_with_retry(temp, path)


def write_json(path, value):
    write_text(path, json.dumps(value, ensure_ascii=False, indent=2))


def snapshot_text(event):
    lines = ["手机原生界面文字（未经语音校正；不推断说话人和原文/译文配对）"]
    lines.append("接收时间：" + now())
    for window in event.get("windows", []):
        for node in window.get("nodes", []):
            text = node.get("text") or node.get("description") or ""
            if text:
                lines.append(f"[{node.get('package', '')} | {node.get('id', '')}] {text}")
    if len(lines) == 2:
        lines.append("当前未找到白名单应用的可读文字节点。")
    return "\n".join(lines) + "\n"


def prepare_device(args):
    devices = subprocess.run([args.adb, "devices"], capture_output=True, text=True, check=True, timeout=15)
    available = [line.split()[0] for line in devices.stdout.splitlines() if len(line.split()) == 2 and line.split()[1] == "device"]
    serial = args.serial or (available[0] if len(available) == 1 else None)
    if serial not in available:
        raise RuntimeError("ADB device unavailable/unauthorized, or multiple devices; use --serial.\n" + devices.stdout)
    adb = [args.adb, "-s", serial]
    if not args.no_push:
        jar = BASE / "build" / "caption-bridge.jar"
        if not jar.is_file():
            raise RuntimeError("Run build.ps1 first: compiled caption-bridge.jar is missing.")
        subprocess.run(adb + ["shell", "chmod", "600", REMOTE], timeout=10, capture_output=True)
        subprocess.run(adb + ["push", str(jar), REMOTE], check=True, timeout=30, stdout=sys.stderr)
        subprocess.run(adb + ["shell", "chmod", "444", REMOTE], check=True, timeout=10)
    return adb


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", default=DEFAULT_ADB)
    parser.add_argument("--serial")
    parser.add_argument("--duration", type=int, default=120, help="Capture seconds, 1 to 3600")
    parser.add_argument("--packages", default="com.xiaomi.aiasst.vision,com.android.incallui")
    parser.add_argument("--output", type=Path, default=BASE / "output")
    parser.add_argument("--no-push", action="store_true")
    parser.add_argument("--owner-pid", type=int, help="Stop if the owning caption viewer exits")
    parser.add_argument("--owner-created", type=float)
    parser.add_argument("--history-md", type=Path, help="Continuously save cumulative bilingual captions")
    parser.add_argument("--lean", action="store_true", help="Skip raw UI-tree and text debug copies")
    args = parser.parse_args()
    if not 1 <= args.duration <= 3600:
        parser.error("duration must be 1..3600 seconds")
    if not all(part and all(c.isalnum() or c in "._" for c in part) for part in args.packages.split(",")):
        parser.error("invalid package names")
    args.output.mkdir(parents=True, exist_ok=True)
    capture_lock = (args.output / ".capture.lock").open("a+b")
    if capture_lock.tell() == 0:
        capture_lock.write(b"0")
        capture_lock.flush()
    capture_lock.seek(0)
    try:
        msvcrt.locking(capture_lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        raise SystemExit("Another capture is already running for this output directory.")
    stop_path = args.output / "stop.request"
    stop_path.unlink(missing_ok=True)
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    log_path = args.output / ("nodes_" + run_id + ".jsonl")
    stderr_path = args.output / ("device_" + run_id + ".stderr.log")
    caption_path = args.output / ("captions_" + run_id + ".jsonl")
    status = {"state": "starting", "run_id": run_id, "started_at": now(), "packages": args.packages.split(","), "log": str(log_path), "text_node_count": 0}
    write_json(args.output / "status.json", status)
    write_json(args.output / "latest.json", {"type": "waiting", "run_id": run_id, "received_at": now(), "windows": []})
    write_text(args.output / "latest.txt", "新采集已启动，等待本次字幕节点；请同时检查 status.json。\n")
    empty_caption = extract({"windows": [], "received_at": now()})
    empty_caption["run_id"] = run_id
    write_json(args.output / "caption_latest.json", empty_caption)
    write_text(args.output / "caption_latest.txt", "等待本次字幕节点。\n")
    process = None
    watchdog = None
    timed_out = threading.Event()
    ready_received = False
    device_pid = None
    monitor_done = threading.Event()
    stop_requested = threading.Event()
    previous_signature = None
    history = None
    try:
        if args.history_md is not None:
            from caption_history import CaptionHistory
            history = CaptionHistory(args.output, args.history_md)
        adb = prepare_device(args)
        command = adb + ["shell", "-T", "env", "CLASSPATH=" + REMOTE, "app_process", "/system/bin", "CaptionBridge", "--duration", str(args.duration), "--packages", args.packages]
        with log_path.open("a", encoding="utf-8") as log, stderr_path.open("w", encoding="utf-8") as errors, caption_path.open("a", encoding="utf-8") as caption_log:
            process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=errors, text=True, encoding="utf-8", errors="replace")
            def watch_stop():
                while not monitor_done.wait(0.5):
                    owner_gone = False
                    if args.owner_pid:
                        import psutil
                        try:
                            owner = psutil.Process(args.owner_pid)
                            owner_gone = (args.owner_created is not None and
                                          abs(owner.create_time() - args.owner_created) > 0.01)
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            owner_gone = True
                    if stop_path.exists() or owner_gone:
                        stop_requested.set()
                        if process.poll() is None:
                            process.terminate()
                        return
            threading.Thread(target=watch_stop, daemon=True).start()
            def expire():
                timed_out.set()
                if process.poll() is None:
                    process.terminate()
            watchdog = threading.Timer(args.duration + 30, expire)
            watchdog.daemon = True
            watchdog.start()
            for line in process.stdout:
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    errors.write(line)
                    errors.flush()
                    continue
                event["received_at"] = now()
                event["run_id"] = run_id
                if event.get("type") == "ready":
                    ready_received = True
                    device_pid = event.get("pid")
                    status["device_pid"] = device_pid
                    status["host_pid"] = os.getpid()
                if not args.lean:
                    log.write(json.dumps(event, ensure_ascii=False) + "\n")
                    log.flush()
                status.update(state="connected", last_event_at=now(), last_event_type=event.get("type"))
                if event.get("type") == "snapshot":
                    status["text_node_count"] = sum(len(window.get("nodes", [])) for window in event.get("windows", []))
                    if not args.lean:
                        write_json(args.output / "latest.json", event)
                        text = snapshot_text(event)
                        write_text(args.output / "latest.txt", text)
                    caption = extract(event)
                    caption["run_id"] = run_id
                    write_json(args.output / "caption_latest.json", caption)
                    if history is not None:
                        history.update(caption)
                    caption_text = format_text(caption)
                    write_text(args.output / "caption_latest.txt", caption_text)
                    status.update(original_count=caption["original_count"], translation_count=caption["translation_count"], has_captions=caption["has_captions"])
                    write_json(args.output / "status.json", status)
                    if caption["signature"] != previous_signature:
                        caption_log.write(json.dumps(caption, ensure_ascii=False) + "\n")
                        caption_log.flush()
                        print(caption_text, flush=True)
                        previous_signature = caption["signature"]
                else:
                    print(json.dumps(event, ensure_ascii=False), flush=True)
                    write_json(args.output / "status.json", status)
            return_code = process.wait(timeout=10)
            if stop_requested.is_set():
                status["state"] = "stopped_by_user"
                return
            if timed_out.is_set():
                raise RuntimeError("Capture exceeded its duration plus the 30-second connection allowance.")
            if not ready_received:
                raise RuntimeError(f"Device bridge never reported ready; see {stderr_path}")
            status.update(state="stopped" if return_code == 0 else "error", return_code=return_code)
            if return_code:
                raise RuntimeError(f"Device bridge exited {return_code}; see {stderr_path}")
    except KeyboardInterrupt:
        status["state"] = "stopped_by_user"
    except Exception as error:
        status.update(state="error", error=str(error))
        raise
    finally:
        monitor_done.set()
        if watchdog is not None:
            watchdog.cancel()
        if process is not None and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        if isinstance(device_pid, int) and device_pid > 1:
            try:
                identity = subprocess.run(adb + ["shell", "cat", f"/proc/{device_pid}/cmdline"], capture_output=True, timeout=5)
                argv = identity.stdout.rstrip(b"\0").split(b"\0")
                matching_process = argv[:1] == [b"CaptionBridge"] or argv[:3] == [b"app_process", b"/system/bin", b"CaptionBridge"]
                if identity.returncode == 0 and matching_process:
                    result = subprocess.run(adb + ["shell", "kill", "-TERM", str(device_pid)], capture_output=True, timeout=5)
                    status["device_cleanup"] = "termination_requested" if result.returncode == 0 else "termination_failed"
                else:
                    status["device_cleanup"] = "no_matching_process_observed"
            except subprocess.TimeoutExpired:
                status["device_cleanup"] = "unverified_device_unreachable"
        status["ended_at"] = now()
        write_json(args.output / "status.json", status)
        capture_lock.close()


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
