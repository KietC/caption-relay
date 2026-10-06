"""Exercise a copied frozen package with synthetic data and loopback only.

Never points a process at the production config, inbox, phone, or tunnel.
The supplied package remains unchanged; all runtime state lives in a private
temporary copy, and only the requested verification JSON is retained.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import closing, ExitStack
import ctypes
from datetime import datetime, timezone
import hashlib
import hmac
from http.client import HTTPConnection
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import time
import uuid

import psutil
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from mobile_caption.migration import _private
from mobile_caption.protocol import Config, PACKAGE, aad, ack


ENGLISH = "Portable loopback verification."
CHINESE = "便携版本机链路验证。"
STREAM = "portable-verification-stream"
HIDDEN = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def read_json(path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def wait_for(check, message, process=None, timeout=12):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if process is not None and process.poll() is not None:
            raise RuntimeError(f"Owned test process exited ({process.returncode}): {message}")
        value = check()
        if value:
            return value
        time.sleep(0.1)
    raise TimeoutError(message)


def unused_loopback_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as reservation:
        reservation.bind(("127.0.0.1", 0))
        return reservation.getsockname()[1]


def make_packet(config):
    stamp = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
    event = {"type": "snapshot", "timestamp": stamp, "windows": [{
        "id": 3, "package": PACKAGE, "bounds": [0, 0, 1080, 2400], "nodes": [
            {"package": PACKAGE, "id": PACKAGE + ":id/message_body", "text": ENGLISH,
             "bounds": [10, 10, 1000, 30]},
            {"package": PACKAGE, "id": PACKAGE + ":id/tv_dest_message", "text": CHINESE,
             "bounds": [10, 31, 1000, 60]}]}]}
    nonce = secrets.token_bytes(12)
    payload = json.dumps(event, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ciphertext = AESGCM(config.key).encrypt(nonce, payload, aad(config.device_id, STREAM, 1))
    envelope = {"v": 1, "device_id": config.device_id, "stream_id": STREAM, "seq": 1,
                "nonce": base64.b64encode(nonce).decode(),
                "ciphertext": base64.b64encode(ciphertext).decode()}
    return json.dumps(envelope, separators=(",", ":")).encode("utf-8")


def send_packet(port, config, packet, token=None):
    connection = HTTPConnection("127.0.0.1", port, timeout=4)
    try:
        connection.request("POST", "/v1/captions", packet,
                           {"Content-Type": "application/json", "Authorization": "Bearer " + (token or config.token)})
        response = connection.getresponse()
        return response.status, json.loads(response.read().decode("utf-8"))
    finally:
        connection.close()


def listener_ready(port):
    connection = HTTPConnection("127.0.0.1", port, timeout=0.5)
    try:
        connection.request("GET", "/isolated-verification")
        response = connection.getresponse()
        body = response.read()
        return response.status == 404 and b"not_found" in body and response.getheader("Server", "").startswith("CaptionRelay")
    except (OSError, TimeoutError):
        return False
    finally:
        connection.close()


def database_state(path, consumer):
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as database:
        count = database.execute("SELECT COUNT(*) FROM events").fetchone()[0]
        cursor = database.execute("SELECT cursor FROM consumers WHERE consumer=?", (consumer,)).fetchone()
        return count, cursor[0] if cursor else None


def visible_window(pid):
    """Check only windows owned by the launched test process; never focus one."""
    if os.name != "nt":
        return None
    from ctypes import wintypes
    user = ctypes.WinDLL("user32", use_last_error=True)
    callback_type = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    user.EnumWindows.argtypes = [callback_type, wintypes.LPARAM]
    user.EnumWindows.restype = wintypes.BOOL
    user.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    user.IsWindowVisible.argtypes = [wintypes.HWND]
    user.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    windows = []

    @callback_type
    def inspect(handle, _):
        owner = wintypes.DWORD()
        user.GetWindowThreadProcessId(handle, ctypes.byref(owner))
        if owner.value == pid and user.IsWindowVisible(handle):
            rectangle = wintypes.RECT()
            if user.GetWindowRect(handle, ctypes.byref(rectangle)):
                width, height = rectangle.right - rectangle.left, rectangle.bottom - rectangle.top
                if width >= 480 and height >= 240:
                    windows.append({"width": width, "height": height})
        return True

    user.EnumWindows(inspect, 0)
    return windows[0] if windows else None


def finish_owned(process, stop_file):
    if process.poll() is None:
        stop_file.parent.mkdir(parents=True, exist_ok=True)
        stop_file.write_text("stop\n", encoding="utf-8")
        process.wait(timeout=8)
    require(process.returncode == 0, f"Owned test process failed to stop cleanly ({process.returncode})")


def run_cli(executable, directory, *arguments):
    completed = subprocess.run([str(executable), *arguments], cwd=directory, capture_output=True,
                               text=True, encoding="utf-8", errors="replace", timeout=30,
                               creationflags=HIDDEN)
    if completed.returncode:
        # Commands here handle only generated test data, never phone credentials.
        detail = completed.stderr.strip()[-2000:]
        raise RuntimeError(f"Frozen command {arguments[0]} exited {completed.returncode}: {detail}")
    try:
        return json.loads(completed.stdout)
    except json.JSONDecodeError as error:
        raise RuntimeError("Frozen command did not provide JSON on redirected stdout") from error


def verify(directory, result_path):
    directory, result_path = Path(directory).resolve(), Path(result_path).resolve()
    require(os.name == "nt", "This frozen Windows integration check runs on Windows")
    require((directory / "CaptionRelayCaptions.exe").is_file(), "Directory must contain CaptionRelayCaptions.exe")
    require(not result_path.is_relative_to(directory), "Place the result outside the original package directory")
    # Refuse a live installation. Copying a clean distribution never touches or
    # even reads production pairing credentials/transcripts.
    require(not (directory / "mobile_caption/runtime/config.json").exists(),
            "Provide a clean distribution without runtime configuration")
    require(not (directory / "mobile_caption/runtime/inbox.sqlite3").exists(),
            "Provide a clean distribution without a personal inbox")
    require(not (directory / "output").exists(), "Provide a clean distribution without runtime output")
    result_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    result = {"passed": False, "directory": str(directory), "steps": {},
              "external_network": False, "phone_access": False, "audio_touched": False,
              "production_state_touched": False, "source_package_modified": False}
    processes = []
    step = "copy_package"
    try:
        with tempfile.TemporaryDirectory(prefix="字幕 冻结 验证 ", dir=result_path.parent) as temporary, ExitStack() as files:
            scratch = Path(temporary)
            _private(scratch)
            portable = scratch / "中文 空格 电脑端"
            shutil.copytree(directory, portable)
            executable = portable / "CaptionRelayCaptions.exe"
            result["executable_sha256"] = hashlib.sha256(executable.read_bytes()).hexdigest()
            result["steps"][step] = {"passed": True, "chinese_and_space_path": True}
            runtime = portable / "mobile_caption/runtime"
            runtime.mkdir(parents=True)
            _private(runtime)
            inbox_path = runtime / "inbox.sqlite3"
            config_path = runtime / "config.json"
            config = Config("verify-" + uuid.uuid4().hex, secrets.token_urlsafe(32), secrets.token_bytes(32), inbox_path)
            save_json(config_path, {"device_id": config.device_id, "token": config.token,
                                    "key_b64": base64.b64encode(config.key).decode(), "endpoint": "",
                                    "inbox_path": str(inbox_path)})
            output = portable / "output/caption_viewer/session_synthetic_caption"
            output.mkdir(parents=True)
            consumer = str(output.resolve()).casefold()
            port = unused_loopback_port()
            require(port != 18765, "Ephemeral test port must differ from the production port")
            owner_created = psutil.Process().create_time()
            owner_args = ["--owner-pid", str(os.getpid()), "--owner-created", str(owner_created)]

            def launch(name, arguments, stop_file):
                stdout = files.enter_context((scratch / (name + ".stdout.log")).open("w", encoding="utf-8"))
                stderr = files.enter_context((scratch / (name + ".stderr.log")).open("w", encoding="utf-8"))
                process = subprocess.Popen([str(executable), *arguments], cwd=portable,
                                           stdout=stdout, stderr=stderr, creationflags=HIDDEN)
                processes.append((process, stop_file))
                return process

            try:
                step = "frozen_receiver"
                receiver = launch("receiver", ["mobile_bridge.py", "--config", str(config_path), "--port", str(port),
                                                "--output", str(output), "--duration", "30", *owner_args], output / "stop.request")
                wait_for(lambda: read_json(output / "status.json").get("host_pid") == receiver.pid
                         and listener_ready(port), "Frozen loopback receiver did not become ready", receiver)
                packet = make_packet(config)
                response_code, response = send_packet(port, config, packet)
                expected = ack(config, STREAM, 1)
                require(response_code == 200 and all(response.get(key) == value for key, value in expected.items()
                                                   if key != "mac")
                        and hmac.compare_digest(response.get("mac", ""), expected["mac"]),
                        "Frozen receiver did not return an authenticated acknowledgement")
                wait_for(lambda: len(read_json(output / "history.json").get("entries", [])) == 1
                         and database_state(inbox_path, consumer) == (1, 1),
                         "Frozen receiver did not persist one applied bilingual event", receiver)
                markdown = (output / "captions.md").read_text(encoding="utf-8")
                require(ENGLISH in markdown and CHINESE in markdown, "Markdown is missing a language")
                entries = read_json(output / "history.json")["entries"]
                require(entries[0].get("original") == ENGLISH and entries[0].get("translation") == CHINESE,
                        "Saved history does not match the synthetic bilingual event")
                result["steps"][step] = {"passed": True, "authenticated_ack": True,
                                          "bilingual_markdown": True, "history_entries": 1, "loopback_port": port}

                step = "duplicate_and_authentication"
                duplicate_code, duplicate = send_packet(port, config, packet)
                require(duplicate_code == 200 and duplicate == response, "Duplicate packet was not acknowledged identically")
                bad_code, bad = send_packet(port, config, packet, token="deliberately-invalid-verification-token")
                require(bad_code == 401 and bad.get("error") == "unauthorized", "Receiver accepted an invalid token")
                require(database_state(inbox_path, consumer) == (1, 1), "Duplicate packet added an inbox event")
                require(len(read_json(output / "history.json")["entries"]) == 1, "Duplicate packet added history")
                result["steps"][step] = {"passed": True, "inbox_events": 1, "consumer_cursor": 1}
                finish_owned(receiver, output / "stop.request")
                require(read_json(output / "status.json").get("state") == "stopped_by_user",
                        "Receiver stop.request was not observed")
                result["steps"]["receiver_graceful_stop"] = {"passed": True}

                step = "frozen_caption_window"
                window_stop = scratch / "caption_window.stop"
                window = launch("window", ["caption_window.py", "--output", str(output), "--no-topmost",
                                            "--stop-file", str(window_stop), *owner_args], window_stop)

                def displayed():
                    state = read_json(output / "window_status.json")
                    if (state.get("viewer_pid") == window.pid
                            and state.get("original_chars", 0) >= len(ENGLISH)
                            and state.get("translation_chars", 0) >= len(CHINESE)):
                        return visible_window(window.pid)
                    return None

                dimensions = wait_for(displayed, "Frozen caption window did not display the bilingual fixture", window)
                finish_owned(window, window_stop)
                result["steps"][step] = {"passed": True, "actual_visible_window": True,
                                          "graceful_stop": True, **dimensions}

                step = "frozen_export_import"
                save_json(output.parent / "viewer_status.json",
                          {"state": "stopped", "source": "mobile", "output": str(output.parent),
                           "capture_output": str(output), "markdown_path": str(output / "captions.md"),
                           "mobile_config": str(config_path), "audio_control": False, "automatic_send": False})
                archive = scratch / "合成迁移 测试.zip"
                exported = run_cli(executable, portable, "mobile_caption_control.py", "export-migration", "--file", str(archive))
                require(exported.get("event_count") == 1 and archive.is_file(), "Frozen export did not package the synthetic inbox")
                (output / "captions.md").write_text("SYNTHETIC CHANGE TO VERIFY RESTORATION", encoding="utf-8")
                imported = run_cli(executable, portable, "mobile_caption_control.py", "import-migration", "--file", str(archive))
                require(imported.get("state") == "imported_stopped" and imported.get("event_count") == 1,
                        "Frozen import did not restore stopped synthetic state")
                require(database_state(inbox_path, consumer) == (1, 1), "Frozen migration lost the consumer cursor")
                require((output / "captions.md").read_text(encoding="utf-8") == markdown,
                        "Frozen import did not restore the original Markdown")
                require(read_json(config_path).get("endpoint") == "", "Imported configuration retained an endpoint")
                require(read_json(output.parent / "viewer_status.json").get("state") == "stopped",
                        "Imported viewer retained running state")
                result["steps"][step] = {"passed": True, "event_count": 1, "cursor_preserved": True,
                                          "markdown_restored": True, "existing_state_backup": Path(imported["backup_path"]).is_dir()}
                require(all(process.poll() is not None for process, _ in processes), "An owned test process is still running")
                result["passed"] = True
            finally:
                for process, stop_file in reversed(processes):
                    if process.poll() is None:
                        try:
                            finish_owned(process, stop_file)
                        except (OSError, RuntimeError, subprocess.TimeoutExpired):
                            # Popen retains the exact owned process handle; this
                            # never selects a process by name or touches others.
                            if process.poll() is None:
                                process.terminate()
                                process.wait(timeout=8)
    except Exception as error:
        result.update(failed_step=step, error=f"{type(error).__name__}: {error}")
    result["elapsed_seconds"] = round(time.monotonic() - started, 2)
    result["owned_processes_remaining"] = sum(process.poll() is None for process, _ in processes)
    result["passed"] = result["passed"] and result["owned_processes_remaining"] == 0
    save_json(result_path, result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--result", type=Path, required=True)
    args = parser.parse_args()
    result = verify(args.directory, args.result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
