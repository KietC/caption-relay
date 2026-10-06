"""On-demand mobile-caption transport. No audio changes or startup service."""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import secrets
import socket
import subprocess
import sys
import time
import uuid
from urllib.parse import urlsplit
import sqlite3
import zipfile

import psutil

from atomic_io import replace_with_retry
import caption_viewer_control as viewer
from mobile_caption.protocol import Config, ProtocolError

from runtime_paths import BASE
RUNTIME = BASE / "mobile_caption" / "runtime"
CONFIG = RUNTIME / "config.json"
STATE = RUNTIME / "tunnel_status.json"
CLOUDFLARED = BASE / "mobile_caption" / "tools" / "cloudflared.exe"
PACKAGE = "org.captionrelay.bridge"
HIDDEN = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def save_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    replace_with_retry(temporary, path)


def ensure_config():
    if CONFIG.is_file():
        config = viewer.read_json(CONFIG)
        if not isinstance(config, dict) or not all(config.get(key) for key in ("token", "key_b64", "device_id")):
            raise RuntimeError("Existing pairing config is incomplete; preserve it and inspect before replacing.")
        try:
            Config.load(CONFIG)
        except ProtocolError as error:
            raise RuntimeError("Existing pairing config is invalid: " + error.code) from error
        return config
    from mobile_caption.migration import _private
    RUNTIME.mkdir(parents=True, exist_ok=True)
    _private(RUNTIME)
    config = {"endpoint": "", "token": secrets.token_urlsafe(32),
              "key_b64": base64.b64encode(secrets.token_bytes(32)).decode(),
              "device_id": "xiaomi-" + uuid.uuid4().hex,
              "inbox_path": str(RUNTIME / "inbox.sqlite3")}
    save_json(CONFIG, config)
    return config


def tunnel_alive(state):
    if not viewer.same_process(state.get("pid", -1), state.get("created", -1)):
        return False
    try:
        return Path(psutil.Process(state["pid"]).exe()).resolve() == CLOUDFLARED.resolve()
    except (psutil.Error, OSError):
        return False


def start_tunnel():
    config = ensure_config()
    state = viewer.read_json(STATE)
    if tunnel_alive(state):
        if not state.get("endpoint"):
            # A previous launch may have been interrupted between process spawn
            # and URL persistence. Recover only from its own completed log.
            log_path = Path(state.get("log_path", ""))
            log_text = log_path.read_text(encoding="utf-8", errors="replace") if log_path.is_file() else ""
            matches = re.findall(r"https://[a-z0-9-]+\.trycloudflare\.com", log_text)
            if not matches or "Registered tunnel connection" not in log_text:
                raise RuntimeError("Owned tunnel is running but has no confirmed public endpoint; current viewer left untouched.")
            state["endpoint"] = matches[0] + "/v1/captions"
            save_json(STATE, state)
        if config.get("endpoint") != state["endpoint"]:
            config["endpoint"] = state["endpoint"]
            save_json(CONFIG, config)
        return state
    if not CLOUDFLARED.is_file():
        raise RuntimeError(f"Missing signed Cloudflare tunnel binary: {CLOUDFLARED}")
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = RUNTIME / f"tunnel_{stamp}.log"
    with log_path.open("wb") as log:
        process = subprocess.Popen([str(CLOUDFLARED), "tunnel", "--no-autoupdate",
                                    "--url", "http://127.0.0.1:18765", "--protocol", "http2",
                                    "--edge-ip-version", "4",
                                    "--metrics", "127.0.0.1:18766"],
                                   cwd=BASE, stdout=log, stderr=log, creationflags=HIDDEN)
    state = {"pid": process.pid, "created": psutil.Process(process.pid).create_time(),
             "log_path": str(log_path), "kind": "temporary_quick_tunnel", "endpoint": ""}
    save_json(STATE, state)
    deadline = time.monotonic() + 45
    while time.monotonic() < deadline and process.poll() is None:
        log_text = log_path.read_text(encoding="utf-8", errors="replace")
        matches = re.findall(r"https://[a-z0-9-]+\.trycloudflare\.com", log_text)
        if matches and "Registered tunnel connection" in log_text:
            state["endpoint"] = matches[0] + "/v1/captions"
            config["endpoint"] = state["endpoint"]
            save_json(CONFIG, config)
            save_json(STATE, state)
            return state
        time.sleep(0.3)
    if process.poll() is None:
        process.terminate()
        process.wait(timeout=10)
    raise RuntimeError(f"Public tunnel was not established; inspect {log_path}")


def stop_tunnel():
    state = viewer.read_json(STATE)
    if tunnel_alive(state):
        process = psutil.Process(state["pid"])
        process.terminate()
        try:
            process.wait(timeout=10)
        except psutil.TimeoutExpired:
            raise RuntimeError("Owned tunnel did not stop in time")
    state["stopped_at"] = datetime.now(timezone.utc).isoformat()
    save_json(STATE, state)


def adb(serial, *args, data=None, timeout=20):
    result = subprocess.run([viewer.ADB, "-s", serial, *args], input=data,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            timeout=timeout, creationflags=HIDDEN)
    if result.returncode:
        # Configuration bytes can be echoed by adb tee; never log stdout here.
        raise RuntimeError("Phone command failed: " + result.stderr.decode(errors="replace")[:500])
    return result.stdout


def resolve_serial(serial=None):
    if serial:
        return serial
    result = subprocess.run([viewer.ADB, "devices"], capture_output=True, timeout=15, creationflags=HIDDEN)
    if result.returncode:
        raise RuntimeError("ADB device discovery failed")
    devices = [line.split()[0] for line in result.stdout.decode(errors="replace").splitlines()
               if len(line.split()) >= 2 and line.split()[1] == "device"]
    if len(devices) != 1:
        raise RuntimeError("Connect and authorize exactly one Android phone, or specify --serial.")
    return devices[0]


def install_phone(serial):
    apk = BASE / "Android" / "CaptionRelayCaptionBridge.apk"
    if not apk.is_file():
        apk = BASE / "mobile_caption" / "android" / "build" / "CaptionRelayCaptionBridge.apk"
    if not apk.is_file():
        raise RuntimeError("Bundled Android APK is missing")
    adb(serial, "install", "--no-incremental", "-r", str(apk), timeout=90)


def check_phone_pairing(serial, config):
    installed = adb(serial, "shell", "pm", "list", "packages", PACKAGE)
    if ("package:" + PACKAGE) not in installed.decode(errors="replace").splitlines():
        install_phone(serial)
    adb(serial, "shell", "run-as", PACKAGE, "mkdir", "-p", "files")
    files = adb(serial, "shell", "run-as", PACKAGE, "ls", "files")
    if "config.json" not in files.decode(errors="replace").split():
        return
    try:
        old = json.loads(adb(serial, "exec-out", "run-as", PACKAGE, "cat", "files/config.json"))
    except (ValueError, TypeError):
        raise RuntimeError("Phone pairing is unreadable; preserved. Inspect the phone app before re-pairing.") from None
    if not isinstance(old, dict) or any(old.get(key) != config[key] for key in ("device_id", "key_b64")):
        raise RuntimeError("Phone belongs to another pairing. Import the old computer migration first, "
                           "or explicitly import the new pairing code in the phone app after its pending queue is empty.")


def phone_action(serial, action):
    adb(resolve_serial(serial), "shell", "am", "start", "-n", PACKAGE + "/.MainActivity", "--es", "action", action)


def pair_usb(serial):
    serial = resolve_serial(serial)
    config = ensure_config()
    if not config.get("endpoint", "").startswith("https://"):
        raise RuntimeError("Start a public tunnel or configure an HTTPS endpoint before pairing")
    check_phone_pairing(serial, config)
    phone_config = {key: config[key] for key in ("endpoint", "token", "key_b64", "device_id")}
    adb(serial, "shell", "run-as", PACKAGE, "mkdir", "-p", "files")
    adb(serial, "exec-in", "run-as", PACKAGE, "tee", "files/config.json.tmp",
        data=json.dumps(phone_config).encode("utf-8"))
    adb(serial, "shell", "run-as", PACKAGE, "mv", "files/config.json.tmp", "files/config.json")
    phone_action(serial, "reload")
    state = viewer.read_json(STATE)
    state.update(paired_endpoint=config["endpoint"], paired_at=datetime.now(timezone.utc).isoformat())
    save_json(STATE, state)


def pairing_code(destination):
    start_tunnel()
    config = ensure_config()
    address = urlsplit(config["endpoint"])
    if (address.scheme != "https" or not address.hostname or address.username or address.password
            or address.query or address.fragment):
        raise RuntimeError("Pairing requires a plain HTTPS endpoint without credentials/query/fragment")
    payload = {key: config[key] for key in ("endpoint", "token", "key_b64", "device_id")}
    code = "CRCP1:" + base64.urlsafe_b64encode(json.dumps(payload, separators=(",", ":")).encode()).decode().rstrip("=")
    destination = Path(destination).resolve()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(code, encoding="utf-8")
    from mobile_caption.migration import _private
    _private(destination)
    return {"pairing_code_file": str(destination), "private": True}


def require_stopped():
    current = viewer.status(viewer.OUTPUT)
    if current.get("running") or viewer.active_collectors() or tunnel_alive(viewer.read_json(STATE)):
        raise RuntimeError("Stop the local caption receiver and tunnel before importing migration data.")


def export_migration(destination):
    destination = Path(destination).resolve()
    if destination.exists():
        raise RuntimeError("Choose a new migration ZIP filename; existing archives are preserved.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # Leave the phone queue enabled: unsent packets must continue on the new PC.
    if viewer.status(viewer.OUTPUT).get("running"):
        run_viewer("stop")
    stop_tunnel()
    require_stopped()
    from mobile_caption.migration import export_bundle
    return export_bundle(BASE, Path(destination))


def import_migration(source):
    require_stopped()
    from mobile_caption.migration import import_bundle
    return import_bundle(BASE, Path(source))


def run_viewer(*args):
    result = subprocess.run([str(viewer.PYTHON), str(BASE / "caption_viewer_control.py"), *args],
                            cwd=BASE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            creationflags=HIDDEN, timeout=50)
    if result.returncode:
        raise RuntimeError(result.stderr.decode("utf-8", errors="replace")[-1000:])
    current = viewer.status(viewer.OUTPUT)
    if args and args[0] == "stop" and current.get("running"):
        raise RuntimeError("Caption viewer is still running after stop; source was not switched.")
    if args and args[0] == "start" and not current.get("running"):
        raise RuntimeError("Caption viewer did not start.")
    return current


def preflight(current):
    ensure_config()  # Validates key/device/token before touching the USB viewer.
    if not (BASE / "mobile_bridge.py").is_file():
        raise RuntimeError("Mobile collector is missing; current viewer left untouched.")
    if current.get("running") and current.get("source") == "mobile":
        pid = current.get("capture_pid")
        try:
            process = psutil.Process(pid) if type(pid) is int and pid > 0 else None
            arguments = process.cmdline() if process else []
            owned = any(Path(arg).resolve() == (BASE / "mobile_bridge.py").resolve()
                        for arg in arguments[1:] if arg.endswith("mobile_bridge.py"))
            if owned and any(connection.status == psutil.CONN_LISTEN
                             and connection.laddr.ip == "127.0.0.1" and connection.laddr.port == 18765
                             for connection in process.net_connections(kind="tcp")):
                return
        except (psutil.Error, OSError, ValueError):
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            probe.bind(("127.0.0.1", 18765))
        except OSError as error:
            raise RuntimeError("Port 18765 is occupied by another receiver. Stop the shadow probe explicitly; current viewer left untouched.") from error


def wait_mobile_ready(timeout=25):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        current = viewer.status(viewer.OUTPUT)
        capture = current.get("capture", {})
        if not current.get("running") or current.get("source") != "mobile":
            raise RuntimeError("Mobile caption supervisor did not stay running.")
        if capture.get("state") == "error":
            raise RuntimeError("Mobile collector failed: " + str(capture.get("error", "unknown error"))[:300])
        if (capture.get("state") == "connected" and capture.get("transport") == "mobile_https"
                and capture.get("host_pid") == current.get("capture_pid")):
            try:
                received = datetime.fromisoformat(capture["last_event_at"].replace("Z", "+00:00"))
                started = datetime.fromisoformat(capture["started_at"].replace("Z", "+00:00"))
                age = (datetime.now(timezone.utc) - received).total_seconds()
                if received >= started and -5 <= age <= 15:
                    return current
            except (KeyError, TypeError, ValueError):
                pass
        time.sleep(0.2)
    raise RuntimeError("No newly authenticated phone packet reached the mobile collector within 25 seconds.")


def restore_previous(previous, serial):
    source = previous.get("source", "android")
    command = ["start", "--source", source, "--resume-last"]
    if source == "android":
        command += ["--serial", serial or previous.get("serial") or viewer.SERIAL]
    elif source == "ios":
        if previous.get("ios_udid"):
            command += ["--ios-udid", previous["ios_udid"]]
        if previous.get("ios_config"):
            command += ["--ios-config", previous["ios_config"]]
    elif previous.get("mobile_config"):
        command += ["--mobile-config", previous["mobile_config"]]
    run_viewer(*command)


def start_service(serial, phone_configured=False):
    previous = viewer.status(viewer.OUTPUT)
    preflight(previous)
    tunnel = start_tunnel()
    try:
        pair_usb(serial)
        phone_action(serial, "start")
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
        # pair_usb may have succeeded before phone-start failed. Read the saved
        # state again; offline use of an already paired phone remains supported.
        pairing = viewer.read_json(STATE)
        if pairing.get("paired_endpoint") != tunnel["endpoint"] and not phone_configured:
            raise RuntimeError("New public address requires phone setup: reconnect USB and run pair-usb, "
                               "or enter the endpoint in the phone app. Current viewer left untouched.") from error
    prior_stopped = False
    try:
        if previous.get("running") and previous.get("source") != "mobile":
            run_viewer("stop")
            prior_stopped = True
        run_viewer("start", "--source", "mobile", "--resume-last", "--mobile-config", str(CONFIG))
        ready = wait_mobile_ready()
        if phone_configured:
            # A manual endpoint update is a user claim until a new packet
            # proves the configured device/key can actually reach this PC.
            pairing = viewer.read_json(STATE)
            pairing.update(paired_endpoint=tunnel["endpoint"],
                           paired_at=datetime.now(timezone.utc).isoformat(),
                           pairing_method="authenticated_packet_after_manual_endpoint")
            save_json(STATE, pairing)
        return ready
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
        # Do not disturb a pre-existing mobile viewer on a repeated start.
        cleanup_errors = []
        if prior_stopped or not previous.get("running"):
            current = viewer.status(viewer.OUTPUT)
            if current.get("running") and current.get("source") == "mobile":
                try:
                    run_viewer("stop")
                except (RuntimeError, OSError, subprocess.TimeoutExpired) as cleanup:
                    cleanup_errors.append(str(cleanup))
        if prior_stopped:
            try:
                restore_previous(previous, serial)
            except (RuntimeError, OSError, subprocess.TimeoutExpired) as cleanup:
                cleanup_errors.append("Previous source restore failed: " + str(cleanup))
        detail = "; ".join(cleanup_errors)
        raise RuntimeError(str(error) + ("; " + detail if detail else
                           "; previous caption source restored." if prior_stopped else "")) from error


def stop_service(serial, restore_usb=False):
    errors = []
    current = viewer.status(viewer.OUTPUT)
    if current.get("running") and current.get("source") == "mobile":
        try:
            run_viewer("stop")
        except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
            errors.append(str(error))
    try:
        stop_tunnel()
    except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
        errors.append(str(error))
    try:
        phone_action(serial, "stop")
    except (RuntimeError, OSError, subprocess.TimeoutExpired):
        print("Phone unavailable over USB. Tap Stop in phone app to stop its local queue.", file=sys.stderr)
    if restore_usb:
        current = viewer.status(viewer.OUTPUT)
        if current.get("running") and current.get("source") == "mobile":
            errors.append("Cannot restore USB while the mobile viewer is still running.")
        else:
            try:
                run_viewer("start", "--source", "android", "--resume-last", "--serial", resolve_serial(serial))
            except (RuntimeError, OSError, subprocess.TimeoutExpired) as error:
                errors.append(str(error))
    if errors:
        raise RuntimeError("; ".join(errors))


def status():
    state = viewer.read_json(STATE)
    return {"tunnel_running": tunnel_alive(state), "endpoint": state.get("endpoint"),
            "endpoint_kind": state.get("kind"),
            "phone_paired_to_current_endpoint": bool(state.get("endpoint") and state.get("paired_endpoint") == state.get("endpoint")),
            "viewer": viewer.status(viewer.OUTPUT), "audio_control": False, "automatic_send": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["tunnel-start", "tunnel-stop", "pair-usb", "phone-start",
                                          "phone-stop", "start", "stop", "status", "restore-usb",
                                          "install-phone", "pairing-code", "export-migration", "import-migration"])
    parser.add_argument("--serial", help="Auto-detect a single authorized USB Android phone if omitted")
    parser.add_argument("--file", type=Path, help="Migration ZIP or private pairing code destination")
    parser.add_argument("--phone-configured", action="store_true",
                        help="For start: phone endpoint was updated manually; verify via a fresh authenticated packet")
    args = parser.parse_args()
    try:
        if args.action in ("export-migration", "import-migration", "pairing-code"):
            if not args.file:
                parser.error("--file is required for " + args.action)
            operation = {"export-migration": export_migration, "import-migration": import_migration,
                         "pairing-code": pairing_code}[args.action]
            print(json.dumps(operation(args.file), ensure_ascii=False, indent=2))
            return 0
        elif args.action == "install-phone":
            install_phone(resolve_serial(args.serial))
        elif args.action == "tunnel-start":
            start_tunnel()
        elif args.action == "tunnel-stop":
            stop_tunnel()
        elif args.action == "pair-usb":
            pair_usb(args.serial)
        elif args.action.startswith("phone-"):
            phone_action(args.serial, args.action.split("-", 1)[1])
        elif args.action == "start":
            start_service(args.serial, phone_configured=args.phone_configured)
        elif args.action in ("stop", "restore-usb"):
            stop_service(args.serial, restore_usb=args.action == "restore-usb")
        print(json.dumps(status(), ensure_ascii=False, indent=2))
    except (RuntimeError, OSError, subprocess.TimeoutExpired, ValueError, ProtocolError,
            zipfile.BadZipFile, sqlite3.Error) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
