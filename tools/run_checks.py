#!/usr/bin/env python3
"""Run unique synthetic Python checks in canonical temporary paths, without live devices.

在规范的临时路径中运行不重复的 Python 合成检查，不接触真实设备。
"""
from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


def flatten(suite):
    """Unwrap suites so imported test classes cannot inflate the result count.

    展开测试套件，避免被导入的测试类让结果数量虚增。
    """
    for test in suite:
        if isinstance(test, unittest.TestSuite):
            yield from flatten(test)
        else:
            yield test


def guard(event, args):
    """Allow disposable loopback tests; refuse phone, tunnel and production ports.

    允许一次性回环测试，拒绝手机、隧道及固定运行端口。
    """
    if event == "subprocess.Popen":
        name = Path(str(args[0])).name.casefold()
        if any(item in name for item in ("adb", "cloudflared", "captionrelaycaptions")):
            raise RuntimeError("Synthetic checks refuse live device/tunnel/viewer subprocesses")
    if event == "socket.connect":
        address = args[1]
        if isinstance(address, tuple) and len(address) >= 2:
            if address[0] not in {"127.0.0.1", "::1", "localhost"} or address[1] in {18765, 18766}:
                raise RuntimeError("Synthetic checks refuse external/production connections")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--temp-parent", type=Path,
                        help="Existing private temporary directory; defaults to the system temp")
    parser.add_argument("--report", type=Path, help="Optional metadata-only JSON; keep outside the repo")
    args = parser.parse_args(argv)
    app = ROOT / "app"
    sys.path[:0] = [str(app), str(app / "mobile_caption/desktop"), str(ROOT / "tools")]
    modules = [p.stem for p in sorted(app.glob("test_*.py"))]
    modules.extend(["test_desktop", "test_source_bundle", "test_protocol_vectors"])
    # Resolve the long path before fixtures and production path checks compare it.
    # 先规范临时目录路径，避免 Windows 8.3 别名与路径断言不一致。
    parent = (args.temp_parent or Path(tempfile.gettempdir())).resolve(strict=True)
    previous = tempfile.tempdir
    previous_env = {name: os.environ.get(name) for name in ("TEMP", "TMP", "TMPDIR")}
    sys.dont_write_bytecode = True
    sys.addaudithook(guard)
    try:
        with tempfile.TemporaryDirectory(prefix="caption-relay-checks-", dir=parent) as temporary:
            tempfile.tempdir = str(Path(temporary).resolve())
            for name in previous_env:
                os.environ[name] = tempfile.tempdir
            syntax = 0
            for source in sorted(app.rglob("*.py")):
                if "__pycache__" not in source.parts:
                    ast.parse(source.read_text(encoding="utf-8-sig"), filename=source.name)
                    syntax += 1
            loaded = unittest.TestLoader().loadTestsFromNames(modules)
            unique = {test.id(): test for test in flatten(loaded)}
            result = unittest.TextTestRunner(verbosity=2).run(unittest.TestSuite(unique.values()))
            report = {"schema": 1, "scope": "synthetic_checks_only", "syntax_files": syntax,
                      "tests": result.testsRun, "failures": len(result.failures),
                      "errors": len(result.errors), "skips": len(result.skipped),
                      "ok": result.wasSuccessful() and not result.skipped,
                      "real_device_validation": False}
            print(json.dumps(report, sort_keys=True))
            if args.report:
                target = args.report.resolve()
                if target.is_relative_to(ROOT):
                    raise ValueError("Keep local verification reports outside the source repository")
                target.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
            return 0 if report["ok"] else 1
    finally:
        tempfile.tempdir = previous
        for name, value in previous_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


if __name__ == "__main__":
    raise SystemExit(main())
