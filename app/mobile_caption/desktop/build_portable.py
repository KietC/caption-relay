"""Build an allowlisted portable Windows folder, validate it, then zip it.

根据白名单构建 Windows 便携目录，验证后再生成 ZIP。
"""
from __future__ import annotations

import argparse
from datetime import datetime
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import zipfile

DESKTOP = Path(__file__).resolve().parent
REPO = DESKTOP.parents[1]
ADB_FILES = ("adb.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll", "NOTICE.txt", "source.properties")


def default_adb_directory():
    """Prefer SDK environment settings or adb on PATH; never use a workstation-specific default.

    优先 SDK 环境设置或 PATH 上的 adb，不使用某台工作站的固定默认目录。
    """
    candidates = [Path(os.environ[name]) / "platform-tools" for name in ("ANDROID_SDK_ROOT", "ANDROID_HOME") if os.environ.get(name)]
    candidates.append(REPO / "tools")
    discovered = shutil.which("adb")
    if discovered:
        candidates.append(Path(discovered).parent)
    return next((path for path in candidates if (path / "adb.exe").is_file()), None)


def validate_build_inputs(apk, adb_dir, *, repo=REPO, desktop=DESKTOP):
    """Fail before creating build output or launching PyInstaller if any explicit input is missing.

    任一明确输入缺失时，在创建构建输出或启动 PyInstaller 之前报错。
    """
    if adb_dir is None:
        raise ValueError("ADB not found. Install Android platform-tools; set ANDROID_SDK_ROOT/ANDROID_HOME or pass --adb-dir.")
    sources = [Path(apk), repo / "build/caption-bridge.jar",
               repo / "mobile_caption/tools/cloudflared.exe",
               desktop / "CaptionRelayCaptions.spec", desktop / "version_info.txt",
               desktop / "PORTABLE_README.txt"]
    sources.extend(Path(adb_dir) / name for name in ADB_FILES)
    sources.extend(repo / name for name in (
        "atomic_io.py", "bridge.py", "captions.py", "caption_history.py", "caption_window.py",
        "caption_viewer_control.py", "mobile_bridge.py", "mobile_caption_control.py", "runtime_paths.py"))
    missing = [str(source) for source in sources if not source.is_file()]
    if missing:
        raise ValueError("Missing build inputs before PyInstaller: " + "; ".join(missing))
    return sources


def copy_licenses(stage):
    target = stage / "THIRD_PARTY_NOTICES"
    target.mkdir(parents=True)
    for source in (DESKTOP / "THIRD_PARTY_NOTICES").glob("*"):
        if source.is_file():
            shutil.copy2(source, target / source.name)
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.is_file():
        shutil.copy2(python_license, target / "Python-LICENSE.txt")
    versions = {}
    for package in ("cryptography", "cffi", "psutil", "pyinstaller", "altgraph", "pyinstaller-hooks-contrib"):
        distribution = importlib.metadata.distribution(package)
        versions[package] = distribution.version
        for relative in distribution.files or []:
            if not any(term in relative.name.lower() for term in ("license", "copying", "notice")):
                continue
            original = distribution.locate_file(relative)
            if original.is_file() and original.suffix.lower() not in (".py", ".pyc", ".pyd"):
                name = package + "-" + str(relative).replace("/", "_").replace("\\", "_")
                shutil.copy2(original, target / name)
    (target / "VERSIONS.json").write_text(json.dumps(versions, indent=2), encoding="utf-8")
    return versions


def clean_payload(folder):
    """Reject accidentally copied live state/signing secrets before delivery.

    交付前拒绝意外复制进包内的真实运行状态或私有签名资料。
    """
    bad = []
    files = []
    for path in folder.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(folder)
        lower = relative.as_posix().lower()
        if (lower.startswith(("mobile_caption/runtime/", "mobile_caption/android/signing/", "output/"))
                or path.name.lower() in {"config.json", "inbox.sqlite3", "history.json", "captions.md"}
                or path.suffix.lower() in {".jks", ".keystore", ".key", ".sqlite3", ".db"}):
            bad.append(relative.as_posix())
        files.append(path)
    if bad:
        raise RuntimeError("Build contains forbidden runtime/private files: " + ", ".join(bad))
    return files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk", type=Path, default=REPO / "mobile_caption/android/build/CaptionRelayCaptionBridge.apk")
    parser.add_argument("--adb-dir", type=Path, default=default_adb_directory())
    args = parser.parse_args()
    if os.name != "nt":
        parser.error("Build on Windows x64 with Python 3.11 x64.")
    try:
        validate_build_inputs(args.apk, args.adb_dir)
    except ValueError as error:
        parser.error(str(error))
    build = DESKTOP / "builds" / datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    stage = build / "stage"
    stage.mkdir(parents=True)
    versions = copy_licenses(stage)
    shutil.copy2(DESKTOP / "PORTABLE_README.txt", stage / "PORTABLE_README.txt")
    environment = dict(os.environ, CAPTIONRELAY_APK=str(args.apk.resolve()),
                       CAPTIONRELAY_ADB_DIR=str(args.adb_dir.resolve()), CAPTIONRELAY_DESKTOP_STAGE=str(stage))
    log_path = build / "pyinstaller.log"
    with log_path.open("w", encoding="utf-8") as log:
        subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm",
                        "--distpath", str(build / "dist"), "--workpath", str(build / "work"),
                        str(DESKTOP / "CaptionRelayCaptions.spec")], cwd=REPO, env=environment,
                       stdout=log, stderr=subprocess.STDOUT, check=True, timeout=600)
    folder = build / "dist" / "CaptionRelayCaptions"
    executable = folder / "CaptionRelayCaptions.exe"
    checks = {}
    for name, arguments in (("version", ["--version"]), ("runtime", ["--self-check"]),
                            ("manager", ["--gui-self-check"])):
        output = build / (name + "-check.json")
        result = subprocess.run([str(executable), *arguments, "--result", str(output)], cwd=folder,
                                capture_output=True, timeout=60, creationflags=subprocess.CREATE_NO_WINDOW)
        if result.returncode or not output.is_file():
            raise RuntimeError(f"Portable {name} check failed: {result.stderr.decode('utf-8', errors='replace')[-2000:]}")
        checks[name] = json.loads(output.read_text(encoding="utf-8"))
        if not checks[name].get("ok"):
            raise RuntimeError(f"Portable {name} check returned failure; see {output}")
    dispatch = subprocess.run([str(executable), "mobile_caption_control.py", "--help"], cwd=folder,
                              capture_output=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW)
    if dispatch.returncode or b"--phone-configured" not in dispatch.stdout:
        raise RuntimeError("Frozen script dispatch/stdout verification failed: " + repr(dispatch.stdout[-200:]))
    checks["dispatch_stdout"] = True
    from fixture_smoke import run as run_fixture_smoke
    checks["frozen_pipeline"] = run_fixture_smoke(executable)
    files = clean_payload(folder)
    manifest = {path.relative_to(folder).as_posix(): {"bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest()} for path in files}
    (folder / "FILES.sha256.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    archive = build / "CaptionRelayCaptions-Windows-x64-portable.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zipped:
        for path in folder.rglob("*"):
            if path.is_file():
                zipped.write(path, path.relative_to(folder.parent))
    report = {"ok": True, "directory": str(folder), "archive": str(archive), "files": len(files) + 1,
              "archive_bytes": archive.stat().st_size, "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
              "apk_sha256": hashlib.sha256(args.apk.read_bytes()).hexdigest(), "versions": versions,
              "checks": checks, "live_services_touched": False}
    (build / "BUILD_REPORT.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (DESKTOP / "builds" / "latest-build.json").write_text(json.dumps({"report": str(build / "BUILD_REPORT.json"),
                                                                    "archive": str(archive), "directory": str(folder)}, indent=2), encoding="utf-8")
    print(json.dumps({"ok": True, "report": str(build / "BUILD_REPORT.json"), "archive": str(archive)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
