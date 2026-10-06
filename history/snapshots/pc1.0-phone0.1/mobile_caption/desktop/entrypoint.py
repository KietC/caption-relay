"""Portable desktop entry. Only explicit known modules may be dispatched."""
from __future__ import annotations

import argparse
import ctypes
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import traceback

VERSION = "1.0.0"
PROGRAM = "CaptionRelay Captions"
SCRIPTS = {"caption_viewer_control.py", "mobile_bridge.py", "caption_window.py", "mobile_caption_control.py", "bridge.py"}
if not getattr(sys, "frozen", False):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))


def package_root():
    return Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]


def restore_pipe_streams():
    """Windowed EXEs still need JSON stdout when launched with redirected pipes."""
    for name, standard_handle in (("stdout", -11), ("stderr", -12)):
        if getattr(sys, name) is not None:
            if hasattr(getattr(sys, name), "reconfigure"):
                getattr(sys, name).reconfigure(encoding="utf-8", errors="replace")
            continue
        stream = None
        if os.name == "nt":
            try:
                import msvcrt
                kernel = ctypes.WinDLL("kernel32", use_last_error=True)
                kernel.GetStdHandle.argtypes = [ctypes.c_ulong]
                kernel.GetStdHandle.restype = ctypes.c_void_p
                handle = kernel.GetStdHandle(standard_handle & 0xFFFFFFFF)
                if handle and handle != ctypes.c_void_p(-1).value:
                    descriptor = msvcrt.open_osfhandle(handle, os.O_WRONLY)
                    stream = os.fdopen(descriptor, "w", encoding="utf-8", errors="replace", buffering=1)
            except (OSError, ValueError):
                pass
        setattr(sys, name, stream or open(os.devnull, "w", encoding="utf-8"))


def dispatch(script, arguments):
    sys.argv = [str(package_root() / script), *arguments]
    if script == "caption_viewer_control.py":
        import caption_viewer_control
        return caption_viewer_control.main()
    if script == "mobile_caption_control.py":
        import mobile_caption_control
        return mobile_caption_control.main()
    if script == "mobile_bridge.py":
        import mobile_bridge
        return mobile_bridge.main()
    if script == "caption_window.py":
        import caption_window
        return caption_window.main()
    if script == "bridge.py":
        import bridge
        return bridge.main()
    raise ValueError("Unknown command module")


def self_check():
    """Pure local packaging checks; never runs ADB, tunnel, or active services."""
    import sqlite3
    import tkinter as tk
    import psutil
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    import caption_viewer_control
    import caption_window
    import mobile_caption_control
    import mobile_bridge
    from mobile_caption.store import Inbox
    from mobile_caption.protocol import Config

    root = package_root()
    nonce = bytes(range(12))
    key = AESGCM.generate_key(bit_length=256)
    cipher = AESGCM(key)
    message = "CaptionRelay 字幕 package self-check".encode("utf-8")
    assert cipher.decrypt(nonce, cipher.encrypt(nonce, message, b"local-check"), b"local-check") == message
    with tempfile.TemporaryDirectory(prefix="captionrelay-package-check-") as temporary:
        inbox = Inbox(Path(temporary) / "inbox.sqlite3")
        assert inbox.pending("isolated-check") == []
        inbox.close()
    interpreter = tk.Tcl()
    result = {"program": PROGRAM, "version": VERSION, "ok": True,
              "frozen": bool(getattr(sys, "frozen", False)), "root": str(root),
              "python": sys.version.split()[0], "sqlite": sqlite3.sqlite_version,
              "tcl": interpreter.eval("info patchlevel"), "psutil": psutil.__version__,
              "aes_gcm_roundtrip": True, "durable_inbox": True,
              "controller_root": str(mobile_caption_control.BASE),
              "collector_root": str(mobile_bridge.BASE),
              "viewer_root": str(caption_viewer_control.BASE),
              "audio_touched": False, "network_started": False}
    if result["frozen"]:
        expected = ["mobile_caption/tools/cloudflared.exe", "tools/adb.exe",
                    "tools/AdbWinApi.dll", "tools/AdbWinUsbApi.dll", "build/caption-bridge.jar",
                    "Android/CaptionRelayCaptionBridge.apk", "mobile_caption/android/build/CaptionRelayCaptionBridge.apk"]
        result["assets"] = {name: (root / name).is_file() for name in expected}
        result["ok"] = (all(result["assets"].values()) and all(Path(result[key]) == root for key in
                         ("controller_root", "collector_root", "viewer_root")))
    return result


def caption_fixture_check(output):
    """Read only the explicitly supplied synthetic capture in a hidden window."""
    import tkinter as tk
    from caption_window import CaptionWindow
    root = tk.Tk()
    root.withdraw()
    try:
        app = CaptionWindow(root, Path(output), topmost=False, schedule=False)
        root.update_idletasks()
        assert app.original.get("1.0", "end-1c") == "PORTABLE TEST: sample caption"
        assert app.translation.get("1.0", "end-1c") == "便携版测试：示例字幕"
        assert app.latest.transport == "mobile_https"
        assert app.latest.state == "live"
        return {"ok": True, "bilingual_text_rendered": True, "network_source": True,
                "live_fixture": True, "no_clipboard_change": True, "window_hidden": True}
    finally:
        root.destroy()


def main(arguments=None):
    restore_pipe_streams()
    arguments = list(sys.argv[1:] if arguments is None else arguments)
    if arguments and Path(arguments[0]).name in SCRIPTS:
        return dispatch(Path(arguments[0]).name, arguments[1:]) or 0
    parser = argparse.ArgumentParser(description="CaptionRelay phone captions portable desktop")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--self-check", action="store_true")
    parser.add_argument("--gui-self-check", action="store_true")
    parser.add_argument("--caption-fixture", type=Path, help="Verify an isolated synthetic capture in a hidden caption window")
    parser.add_argument("--result", type=Path, help="Write packaging-check JSON to this path")
    options = parser.parse_args(arguments)
    if options.version or options.self_check or options.gui_self_check or options.caption_fixture:
        result = {"program": PROGRAM, "version": VERSION, "ok": True}
        if options.self_check or options.gui_self_check:
            result = self_check()
        if options.gui_self_check:
            from manager import gui_self_check
            result["manager"] = gui_self_check()
            result["ok"] = result["ok"] and result["manager"]["ok"]
        if options.caption_fixture:
            result["caption_fixture"] = caption_fixture_check(options.caption_fixture)
            result["ok"] = result["ok"] and result["caption_fixture"]["ok"]
        if options.result:
            options.result.parent.mkdir(parents=True, exist_ok=True)
            options.result.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False))
        return 0 if result["ok"] else 1
    from manager import run
    return run()


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception:
        restore_pipe_streams()
        traceback.print_exc()
        raise SystemExit(1)
