"""Caption-only window lifecycle. Does not open or change any audio device.

管理仅显示字幕的窗口生命周期，不打开或改变任何音频设备。
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import msvcrt
from pathlib import Path
import subprocess
import sys
import time

import psutil

from runtime_paths import BASE, PYTHON, PYTHONW, ADB
OUTPUT = BASE / "output" / "caption_viewer"
IOS_PYTHON = BASE / ".venv-ios" / "Scripts" / "python.exe"
IOS_CONFIG = BASE / "ios_caption_config.json"
MOBILE_CONFIG = BASE / "mobile_caption" / "runtime" / "config.json"
SERIAL = ""  # Select the sole authorized ADB device, or pass --serial.
HIDDEN = subprocess.CREATE_NO_WINDOW


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        return {}


def save(output, state):
    temporary = output / "viewer_status.tmp"
    temporary.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(output / "viewer_status.json")


# PID reuse is possible; compare creation time before treating a process as owned.
# PID 可能复用；认定进程归属之前必须比较创建时间。
def same_process(pid, created):
    try:
        return abs(psutil.Process(pid).create_time() - created) < 0.01
    except (psutil.Error, TypeError, ValueError):
        return False


def status(output):
    result = read_json(output / "viewer_status.json")
    result["running"] = same_process(result.get("pid", -1), result.get("created", -1))
    if not result["running"]:
        result["state"] = "stopped"
    capture_output = Path(result.get("capture_output", str(output)))
    result["capture"] = read_json(capture_output / "status.json")
    return result


def active_collectors():
    names = {"bridge.py", "ios_bridge.py", "mobile_bridge.py"}
    targets = {(BASE / name).resolve() for name in names}
    found = []
    for process in psutil.process_iter(["pid", "cmdline"]):
        try:
            for arg in (process.info["cmdline"] or [])[1:]:
                if Path(arg).name.lower() not in names:
                    continue
                path = Path(arg)
                if not path.is_absolute():
                    path = Path(process.cwd()) / path
                if path.resolve() in targets:
                    found.append(process.pid)
                    break
        except (psutil.Error, OSError, ValueError):
            continue
    return found


def source_selection(previous, source=None, ios_udid=None, ios_config=None, mobile_config=None):
    selected = source or previous.get("source", "android")
    if selected not in {"android", "ios", "mobile"}:
        raise ValueError(f"Unknown caption source: {selected}")
    result = {"source": selected,
            "ios_udid": ios_udid if ios_udid is not None else previous.get("ios_udid"),
            "ios_config": str(Path(ios_config or previous.get("ios_config") or IOS_CONFIG).resolve())}
    if selected == "mobile":
        result["mobile_config"] = str(Path(mobile_config or previous.get("mobile_config") or MOBILE_CONFIG).resolve())
    return result


def collector_command(selection, adb, serial, duration, capture_output, markdown_path, owner_pid, owner_created):
    if selection["source"] == "ios":
        command = [str(IOS_PYTHON), str(BASE / "ios_bridge.py"),
                   "--config", selection["ios_config"]]
        if selection["ios_udid"]:
            command.extend(["--udid", selection["ios_udid"]])
    elif selection["source"] == "mobile":
        command = [str(PYTHON), str(BASE / "mobile_bridge.py"),
                   "--config", selection["mobile_config"]]
    else:
        command = [str(PYTHON), str(BASE / "bridge.py"), "--adb", adb,
                   "--serial", serial, "--lean"]
    return command + ["--duration", str(duration), "--output", str(capture_output),
                      "--history-md", str(markdown_path), "--owner-pid", str(owner_pid),
                      "--owner-created", str(owner_created)]


def run(output, adb, serial, duration, topmost, resume_last=False,
        source=None, ios_udid=None, ios_config=None, mobile_config=None):
    output.mkdir(parents=True, exist_ok=True)
    lock = (output / "viewer.lock").open("a+b")
    if lock.tell() == 0:
        lock.write(b"0")
        lock.flush()
    lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        lock.close()
        return
    gui = capture = None
    previous = read_json(output / "viewer_status.json")
    selection = source_selection(previous, source, ios_udid, ios_config, mobile_config)
    previous_folder = Path(previous.get("capture_output", str(output)))
    if resume_last and previous_folder.parent == output and previous_folder.is_dir():
        capture_output = previous_folder
    else:
        capture_output = output / ("session_" + datetime.now().strftime("%Y%m%d_%H%M%S_%f"))
    capture_output.mkdir(parents=True, exist_ok=True)
    markdown_path = capture_output / "captions.md"
    state = {"state": "starting", "pid": psutil.Process().pid,
             "created": psutil.Process().create_time(), "output": str(output),
             "capture_output": str(capture_output), "markdown_path": str(markdown_path),
             "audio_control": False, "automatic_send": False, "capture_starts": 0, "serial": serial}
    state.update(selection)
    stop_file = output / "viewer.stop"
    try:
        if selection["source"] == "ios":
            for prerequisite in (IOS_PYTHON, BASE / "ios_bridge.py", Path(selection["ios_config"])):
                if not prerequisite.is_file():
                    raise RuntimeError(f"iPhone caption setup is incomplete; missing file: {prerequisite}")
        if selection["source"] == "mobile":
            for prerequisite in (BASE / "mobile_bridge.py", Path(selection["mobile_config"])):
                if not prerequisite.is_file():
                    raise RuntimeError(f"Mobile caption setup is incomplete; missing file: {prerequisite}")
        conflicts = active_collectors()
        if conflicts:
            raise RuntimeError(f"An existing caption collector must be stopped first: {conflicts}")
        stop_file.unlink(missing_ok=True)
        (capture_output / "stop.request").unlink(missing_ok=True)
        save(output, state)
        # Blank current state before the UI can display a previous session.
        # 在 UI 可能显示上次会话前，先清空当前状态。
        (capture_output / "status.json").write_text(json.dumps({"state": "starting"}), encoding="utf-8")
        gui_args = [str(PYTHONW), str(BASE / "caption_window.py"), "--output", str(capture_output),
                    "--stop-file", str(stop_file), "--owner-pid", str(state["pid"]),
                    "--owner-created", str(state["created"])]
        if not topmost:
            gui_args.append("--no-topmost")
        with (output / "window.log").open("a", encoding="utf-8") as ui_log, \
             (output / "collector.log").open("a", encoding="utf-8") as capture_log:
            gui = subprocess.Popen(gui_args, cwd=BASE, stdout=ui_log, stderr=ui_log, creationflags=HIDDEN)
            state.update(state="running", window_pid=gui.pid)
            save(output, state)
            retry_at = 0.0
            while gui.poll() is None and not stop_file.exists():
                if capture is not None and capture.poll() is not None:
                    state["last_capture_exit"] = capture.returncode
                    delay = 0.25 if capture.returncode == 0 else 5
                    capture = None
                    retry_at = time.monotonic() + delay
                    save(output, state)
                if capture is None and time.monotonic() >= retry_at:
                    command = collector_command(selection, adb, serial, duration, capture_output,
                                                markdown_path, state["pid"], state["created"])
                    capture = subprocess.Popen(command, cwd=BASE, stdout=capture_log,
                                               stderr=capture_log, creationflags=HIDDEN)
                    state.update(capture_pid=capture.pid, capture_starts=state["capture_starts"] + 1)
                    save(output, state)
                time.sleep(0.25)
    except Exception as error:
        state.update(state="error", error=str(error))
        save(output, state)
        raise
    finally:
        stop_file.write_text("stop\n", encoding="utf-8")
        if capture is not None and capture.poll() is None:
            (capture_output / "stop.request").write_text("stop\n", encoding="utf-8")
            try:
                capture.wait(timeout=25)
            except subprocess.TimeoutExpired:
                state["cleanup_pending"] = True
        if gui is not None and gui.poll() is None:
            try:
                gui.wait(timeout=5)
            except subprocess.TimeoutExpired:
                gui.terminate()
                gui.wait(timeout=5)
        if state["state"] != "error":
            state["state"] = "stopped"
        state["ended_at"] = datetime.now(timezone.utc).isoformat()
        save(output, state)
        lock.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["start", "stop", "status", "run"])
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--adb", default=ADB)
    parser.add_argument("--serial", default=SERIAL)
    parser.add_argument("--duration", type=int, default=3600)
    parser.add_argument("--no-topmost", action="store_true")
    parser.add_argument("--resume-last", action="store_true", help="Resume the last saved viewer session")
    parser.add_argument("--source", choices=["android", "ios", "mobile"],
                        help="Caption source; defaults to the last selection, then Android")
    parser.add_argument("--ios-udid", help="iPhone UDID; defaults to the last saved iPhone")
    parser.add_argument("--ios-config", type=Path,
                        help=f"iPhone caption configuration; defaults to the saved path or {IOS_CONFIG}")
    parser.add_argument("--mobile-config", type=Path, help="Encrypted mobile caption receiver configuration")
    args = parser.parse_args()
    if not 1 <= args.duration <= 3600:
        parser.error("duration must be between 1 and 3600 seconds")
    output = args.output.resolve()
    if args.action == "run":
        run(output, args.adb, args.serial, args.duration, not args.no_topmost, args.resume_last,
            args.source, args.ios_udid, args.ios_config, args.mobile_config)
        return
    output.mkdir(parents=True, exist_ok=True)
    current = status(output)
    selection = source_selection(current, args.source, args.ios_udid, args.ios_config, args.mobile_config)
    if args.action == "start" and current["running"]:
        if selection["source"] == "android" and current.get("serial", SERIAL) != args.serial:
            parser.error("Another USB phone is active. Stop it before switching --serial.")
        active = source_selection(current)
        keys = ("source", "ios_udid", "ios_config") if selection["source"] == "ios" else ("source",)
        if selection["source"] == "mobile":
            keys = ("source", "mobile_config")
        if any(selection[key] != active[key] for key in keys):
            parser.error("A different caption source/device/configuration is running. "
                         "Run stop first, then start with the requested --source.")
    if args.action == "start" and not current["running"]:
        command = [str(PYTHONW), str(BASE / "caption_viewer_control.py"), "run", "--output", str(output),
                   "--adb", args.adb, "--serial", args.serial, "--duration", str(args.duration)]
        command.extend(["--source", selection["source"], "--ios-config", selection["ios_config"]])
        if selection["source"] == "mobile":
            command.extend(["--mobile-config", selection["mobile_config"]])
        if selection["ios_udid"]:
            command.extend(["--ios-udid", selection["ios_udid"]])
        if args.no_topmost:
            command.append("--no-topmost")
        if args.resume_last:
            command.append("--resume-last")
        with (output / "supervisor.log").open("a", encoding="utf-8") as log:
            process = subprocess.Popen(command, cwd=BASE, stdout=log, stderr=log,
                                       creationflags=HIDDEN | subprocess.DETACHED_PROCESS)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            current = status(output)
            if current["running"] and current.get("state") == "running":
                break
            if process.poll() is not None:
                break
            time.sleep(0.2)
    elif args.action == "stop":
        (output / "viewer.stop").write_text("stop\n", encoding="utf-8")
        deadline = time.monotonic() + 35
        while current["running"] and time.monotonic() < deadline:
            time.sleep(0.25)
            current = status(output)
    print(json.dumps(current, ensure_ascii=False, indent=2))
    if args.action == "start" and not current["running"]:
        raise SystemExit(1)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
