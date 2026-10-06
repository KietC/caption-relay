#!/usr/bin/env python3
"""Package source only using an explicit hashed allowlist; never upload.

The previous implicit packer is preserved in the historical snapshots.
This wrapper requires --file and --allowlist; private runtime/signing data
and APK/JAR/EXE binaries are never discovered or added automatically.

只根据明确的文件哈希白名单打包源码，不执行上传。
旧的隐式打包器保留在历史快照中。
本封装要求 --file 和 --allowlist，不自动发现或加入私有运行状态、签名或 APK/JAR/EXE 二进制。
"""
import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from tools import source_bundle


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, required=True)
    parser.add_argument("--allowlist", type=Path, required=True)
    parser.add_argument("--workers", type=int, choices=range(1, 17), default=8)
    args = parser.parse_args(argv)
    try:
        result = source_bundle.build(ROOT, args.allowlist, args.file, args.workers)
    except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
        code = str(exc)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,80}", code):
            code = "BUNDLE_IO_OR_SCHEMA_ERROR"
        result = {"stage": "PRIVATE_SOURCE_BUNDLE", "status": "FAIL_CLOSED",
                  "counts": {"uploads": 0}, "hashes": {}, "error_codes": [code],
                  "local_report_paths": []}
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 1 if result["status"] == "FAIL_CLOSED" else 0


if __name__ == "__main__":
    sys.exit(main())