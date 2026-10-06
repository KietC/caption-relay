#!/usr/bin/env python3
"""Compile and run Android-independent JVM tests; optionally compile the Android adapter.

编译并运行不依赖 Android 的 JVM 测试，也可指定 Android SDK 编译适配器。
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import shutil
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]
ANDROID = ROOT / "app/mobile_caption/android"
NAMES = ("RetryGate", "PairingPolicy", "PendingBatch", "AckVerifier", "LatestSlot", "LiveRetryGate", "LiveAck")


def java_tool(name, java_home):
    """Prefer an explicit JDK, then JAVA_HOME, then PATH; no machine-specific default.

    优先显式 JDK，其次 JAVA_HOME 和 PATH，不采用某台机器的固定路径。
    """
    home = java_home or os.environ.get("JAVA_HOME")
    suffix = ".exe" if os.name == "nt" else ""
    candidate = str(Path(home) / "bin" / (name + suffix)) if home else shutil.which(name)
    if not candidate or not Path(candidate).is_file():
        raise RuntimeError(f"Missing {name}; install JDK 21 and set JAVA_HOME or pass --java-home")
    return candidate


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--java-home", type=Path)
    parser.add_argument("--android-jar", type=Path, help="Optional SDK platform android.jar (API 36 recommended)")
    args = parser.parse_args(argv)
    javac, java = (java_tool(name, args.java_home) for name in ("javac", "java"))
    package = Path("org/captionrelay/bridge")
    sources = [ANDROID / "src" / package / f"{name}.java" for name in NAMES]
    sources = [path for path in sources if path.is_file()]
    sources.extend(ANDROID / "tests" / package / f"{name}Test.java" for name in NAMES)
    with tempfile.TemporaryDirectory(prefix="caption-relay-java-") as temporary:
        target = Path(temporary)
        subprocess.run([javac, "-encoding", "UTF-8", "--release", "8", "-d", str(target),
                        *map(str, sources)], check=True, timeout=120)
        for name in NAMES:
            subprocess.run([java, "-cp", str(target), f"org.captionrelay.bridge.{name}Test"],
                           check=True, timeout=30)
        if args.android_jar:
            jar = args.android_jar.resolve(strict=True)
            adapter = sorted((ANDROID / "src").rglob("*.java"))
            subprocess.run([javac, "-encoding", "UTF-8", "--release", "8", "-cp", str(jar),
                            "-d", str(target / "adapter"), *map(str, adapter)], check=True, timeout=120)
            print(f"Android adapter compilation PASS: {len(adapter)} Java files (not device validation)")
    print("JVM core groups PASS: 7 (no phone, SDK signing key or live endpoint used)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
