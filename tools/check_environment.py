#!/usr/bin/env python3
"""Read-only toolchain diagnostics. Does not pair, install, start services or read captions.

只读工具链诊断，不配对、不安装、不启动服务、不读取字幕。
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def version(command):
    """Read only the tool's public version banner, with a bounded timeout.

    只读取工具的公开版本信息，并限制等待时间。
    """
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=10)
        return (result.stdout or result.stderr).splitlines()[0][:200]
    except (OSError, subprocess.TimeoutExpired, IndexError):
        return "unavailable"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--android-sdk", type=Path)
    parser.add_argument("--java-home", type=Path)
    parser.add_argument("--require-build", action="store_true", help="Fail unless APK build inputs are available")
    parser.add_argument("--require-portable", action="store_true", help="Also require Windows packaging inputs")
    args = parser.parse_args(argv)
    home = args.java_home or os.environ.get("JAVA_HOME")
    sdk = args.android_sdk or os.environ.get("ANDROID_SDK_ROOT") or os.environ.get("ANDROID_HOME")
    suffix = ".exe" if os.name == "nt" else ""
    tools = {name: str(Path(home) / "bin" / (name + suffix)) if home else shutil.which(name)
             for name in ("java", "javac", "keytool")}
    found = {name: bool(path and Path(path).is_file()) for name, path in tools.items()}
    dependencies = {name: importlib.util.find_spec(name) is not None for name in ("cryptography", "psutil", "tkinter")}
    build = {}
    if sdk:
        sdk = Path(sdk)
        files = {"platform_36": sdk / "platforms/android-36/android.jar",
                 "adb": sdk / ("platform-tools/adb" + suffix)}
        for name in ("aapt", "d8", "zipalign", "apksigner"):
            ending = ".bat" if os.name == "nt" and name in {"d8", "apksigner"} else suffix
            files[name] = sdk / "build-tools/36.0.0" / (name + ending)
        build = {name: path.is_file() for name, path in files.items()}
    portable = {"windows_x64": os.name == "nt" and platform.machine().lower() in {"amd64", "x86_64"},
                "cloudflared": (ROOT / "app/mobile_caption/tools/cloudflared.exe").is_file(),
                "usb_jar": (ROOT / "app/build/caption-bridge.jar").is_file(),
                "apk": (ROOT / "app/mobile_caption/android/build/CaptionRelayCaptionBridge.apk").is_file()}
    ok = sys.version_info >= (3, 11) and all(dependencies.values())
    if args.require_build:
        ok = ok and all(found.values()) and bool(build) and all(build.values())
    if args.require_portable:
        ok = ok and all(portable.values()) and bool(build) and build.get("adb", False)
    report = {"python": platform.python_version(), "python_64bit": sys.maxsize > 2**32,
              "dependencies": dependencies, "jdk_tools": found, "sdk_inputs": build,
              "portable_inputs": portable, "ok": bool(ok), "live_services_touched": False}
    if found.get("javac"):
        report["javac_version"] = version([tools["javac"], "-version"])
    print(json.dumps(report, indent=2, sort_keys=True))
    print("EN: False build fields are expected before SDK/APK/JAR/download setup; see docs/SETUP.md.")
    print("中文：配置 SDK、构建 APK/JAR 和下载工具前，构建字段为 false 属正常情况；请按配置手册处理。")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
