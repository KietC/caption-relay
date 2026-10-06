"""Mutable paths and executables for source and one-folder Windows releases."""
from pathlib import Path
import shutil
import sys

FROZEN = bool(getattr(sys, "frozen", False))
BASE = Path(sys.executable).resolve().parent if FROZEN else Path(__file__).resolve().parent
PYTHON = Path(sys.executable) if FROZEN else Path(sys.executable).with_name("python.exe")
PYTHONW = PYTHON if FROZEN else PYTHON.with_name("pythonw.exe")
_ADB_CANDIDATES = [BASE / "platform-tools" / "adb.exe", BASE / "tools" / "adb.exe",
                   Path(r"C:\Program Files (x86)\Android\android-sdk\platform-tools\adb.exe")]
ADB = next((str(path) for path in _ADB_CANDIDATES if path.is_file()), shutil.which("adb") or "adb")
