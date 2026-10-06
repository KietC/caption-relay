#!/usr/bin/env python3
"""Validate an explicit schema-1 fileset and write a hashed source allowlist.

Fileset: {"schema": 1, "files": ["README.md", "app/captions.py"]}.
No filesystem discovery, runtime traversal, upload, or file overwrite is performed.

校验 schema-1 明确文件清单并写入哈希白名单，不自动发现文件、遍历运行数据、上传或覆盖文件。
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import sys
import uuid

try:
    from . import source_bundle
except ImportError:
    import source_bundle


def generate(root, fileset, output):
    source_bundle.no_reparse(root)
    root = root.resolve(strict=True)
    with fileset.open("rb") as stream:
        raw = stream.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError("FILESET_SIZE_LIMIT")
    packet = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(packet, dict) or set(packet) != {"schema", "files"} or packet["schema"] != 1:
        raise ValueError("FILESET_SCHEMA_INVALID")
    names = packet["files"]
    if not isinstance(names, list) or not names or len(names) > 4096:
        raise ValueError("FILESET_COUNT_INVALID")
    paths = [source_bundle.safe_relative(name) for name in names]
    if len({str(path).casefold() for path in paths}) != len(paths):
        raise ValueError("DUPLICATE_ARCHIVE_PATH")
    if any(str(path).casefold() == "bundle_manifest.json" for path in paths):
        raise ValueError("MANIFEST_PATH_RESERVED")
    output = output.absolute()
    if output.exists() or not output.parent.is_dir():
        raise ValueError("OUTPUT_ALREADY_EXISTS_OR_PARENT_MISSING")
    source_bundle.no_reparse(output.parent)
    try:
        output.resolve().relative_to(root)
    except ValueError:
        pass
    else:
        raise ValueError("OUTPUT_INSIDE_SOURCE_REFUSED")
    rows = []
    total = 0
    for relative in paths:
        path = root.joinpath(*relative.parts)
        source_bundle.no_reparse(path)
        # Bind the validation read to a bounded first hash. load_one rechecks
        # identity, size, UTF-8, secret patterns and the expected content hash.
        # 首次有限读取绑定哈希；load_one 再核对身份、大小、UTF-8、密钥规则及预期内容哈希。
        with path.open("rb") as stream:
            initial = stream.read(source_bundle.MAX_FILE + 1)
        if len(initial) > source_bundle.MAX_FILE:
            raise ValueError("SOURCE_SIZE_LIMIT")
        row = {"path": str(relative), "sha256": hashlib.sha256(initial).hexdigest()}
        info, _ = source_bundle.load_one(root, row)
        total += info["bytes"]
        if total > source_bundle.MAX_TOTAL:
            raise ValueError("TOTAL_SIZE_LIMIT")
        rows.append(row)
    encoded = (json.dumps({"schema": 1, "files": sorted(rows, key=lambda row: row["path"])},
                          ensure_ascii=True, sort_keys=True, indent=2) + "\n").encode("utf-8")
    temporary = output.with_name(output.name + "." + uuid.uuid4().hex + ".partial")
    try:
        with temporary.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        # Atomic publication with no overwrite, matching the source ZIP helper.
        # 与源码 ZIP 工具一致，采用原子发布且拒绝覆盖。
        os.link(temporary, output)
        return {"status": "ALLOWLIST_READY_NOT_UPLOADED", "files": len(rows),
                "bytes": total, "sha256": hashlib.sha256(encoded).hexdigest(), "uploads": 0}
    finally:
        if temporary.exists():
            temporary.unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--fileset", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        result = generate(args.root, args.fileset, args.output)
    except (OSError, ValueError, TypeError, KeyError, UnicodeError) as exc:
        code = str(exc)
        if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,80}", code):
            code = "ALLOWLIST_IO_OR_SCHEMA_ERROR"
        result = {"status": "FAIL_CLOSED", "error_codes": [code], "uploads": 0}
    print(json.dumps(result, ensure_ascii=True, sort_keys=True))
    return 1 if result["status"] == "FAIL_CLOSED" else 0


if __name__ == "__main__":
    sys.exit(main())
