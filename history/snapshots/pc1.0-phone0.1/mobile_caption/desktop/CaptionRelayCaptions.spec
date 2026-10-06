# PyInstaller onedir recipe. Explicit data allowlist excludes runtime secrets.
import os
from pathlib import Path

desktop = Path(SPECPATH).resolve()
repo = desktop.parents[1]
adb_dir = Path(os.environ.get("CAPTIONRELAY_ADB_DIR", r"C:\Program Files (x86)\Android\android-sdk\platform-tools"))
apk = Path(os.environ.get("CAPTIONRELAY_APK", str(repo / "mobile_caption/android/build/CaptionRelayCaptionBridge.apk")))
stage = Path(os.environ["CAPTIONRELAY_DESKTOP_STAGE"])

scripts = ["atomic_io.py", "bridge.py", "captions.py", "caption_history.py", "caption_window.py",
           "caption_viewer_control.py", "mobile_bridge.py", "mobile_caption_control.py", "runtime_paths.py"]
data = [(str(repo / name), ".") for name in scripts]
data += [(str(repo / "build/caption-bridge.jar"), "build"),
         (str(repo / "mobile_caption/tools/cloudflared.exe"), "mobile_caption/tools"),
         (str(apk), "Android"),
         (str(apk), "mobile_caption/android/build"),
         (str(stage / "PORTABLE_README.txt"), "."),
         (str(stage / "THIRD_PARTY_NOTICES"), "THIRD_PARTY_NOTICES")]
for name in ("adb.exe", "AdbWinApi.dll", "AdbWinUsbApi.dll", "NOTICE.txt", "source.properties"):
    data.append((str(adb_dir / name), "tools"))

a = Analysis([str(desktop / "entrypoint.py")], pathex=[str(repo), str(desktop)],
             binaries=[], datas=data, hiddenimports=[], hookspath=[], hooksconfig={},
             runtime_hooks=[], excludes=[], noarchive=False)
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="CaptionRelayCaptions",
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False, contents_directory=".",
          version=str(desktop / "version_info.txt"))
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="CaptionRelayCaptions")
