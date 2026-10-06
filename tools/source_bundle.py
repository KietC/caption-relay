#!/usr/bin/env python3
"""Adapted for Java/XML/PyInstaller source; strict source allowlist, no upload.
EN: Build a local source-only ZIP from an explicit hashed allowlist; no upload.
ZH: 按明确的哈希白名单制作本地源码 ZIP，不执行上传。
"""
import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import uuid
import zipfile

DENIED = {".git", "node_modules", ".venv", "runtime", "runtime_private", "cache",
          "cases", "training_runs", "profiles", "browser-profile", "__pycache__", "signing", "output", ".buildenv"}
EXTENSIONS = {".md", ".py", ".ps1", ".sh", ".ts", ".js", ".mjs", ".cjs", ".json",
              ".yaml", ".yml", ".toml", ".txt", ".css", ".html", ".svg", ".java", ".xml", ".spec"}
DOTFILES = {".gitignore", ".gitattributes", ".editorconfig"}
MAX_FILE = 16 * 1024 * 1024
MAX_TOTAL = 256 * 1024 * 1024
PATTERNS = [
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
    re.compile(rb"\bgithub_pat_[A-Za-z0-9_]{30,}\b"),
    re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(rb"\bsk-[A-Za-z0-9_-]{24,}\b"),
]


def no_reparse(path):
    # EN: Reject links and Windows junctions for the file and every ancestor.
    # ZH: 拒绝文件及所有父路径中的符号链接和 Windows junction。
    for item in (path, *path.parents):
        info = item.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("REPARSE_POINT_REFUSED")


def safe_relative(text):
    if not isinstance(text, str) or not text or "\\" in text or ":" in text:
        raise ValueError("ARCHIVE_PATH_INVALID")
    path = PurePosixPath(text)
    if path.is_absolute() or str(path) != text or any(p in {"", ".", ".."} for p in path.parts):
        raise ValueError("ARCHIVE_PATH_INVALID")
    if any(p.casefold() in DENIED or p.endswith((" ", ".")) for p in path.parts):
        raise ValueError("PRIVATE_DIRECTORY_REFUSED")
    name = path.name.casefold()
    if name.startswith(".env") or name in {"cookies.json", "storage-state.json", "password.txt", "config.json", "probe.json", "auth.json", "inbox.sqlite3"}:
        raise ValueError("PRIVATE_FILE_REFUSED")
    if path.suffix.casefold() not in EXTENSIONS and name not in DOTFILES and name != "license":
        raise ValueError("NON_SOURCE_FILE_REFUSED")
    return path


def load_one(root, row):
    if not isinstance(row, dict) or set(row) != {"path", "sha256"}:
        raise ValueError("ALLOWLIST_ROW_INVALID")
    relative = safe_relative(row["path"])
    expected = row["sha256"]
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("EXPECTED_SHA256_INVALID")
    path = root.joinpath(*relative.parts)
    no_reparse(path)
    resolved = path.resolve(strict=True)
    try:
        resolved.relative_to(root)
    except ValueError:
        raise ValueError("SOURCE_PATH_ESCAPED") from None
    before = path.stat()
    if not stat.S_ISREG(before.st_mode) or before.st_size > MAX_FILE:
        raise ValueError("SOURCE_SIZE_OR_TYPE_REFUSED")
    with path.open("rb") as stream:
        data = stream.read(MAX_FILE + 1)
        after = os.fstat(stream.fileno())
    if len(data) > MAX_FILE:
        raise ValueError("SOURCE_SIZE_LIMIT")
    identity = lambda s: (s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns)
    if identity(before) != identity(after) or identity(after) != identity(path.stat()):
        raise ValueError("SOURCE_CHANGED_DURING_READ")
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected:
        raise ValueError("SOURCE_BINDING_MISMATCH")
    if b"\x00" in data or any(rule.search(data) for rule in PATTERNS):
        raise ValueError("BINARY_OR_SECRET_CANDIDATE")
    data.decode("utf-8-sig")
    return {"path": str(relative), "sha256": actual, "bytes": len(data)}, data


def build(root, allowlist, output, workers):
    no_reparse(root)
    root = root.resolve(strict=True)
    with allowlist.open("rb") as stream:
        raw = stream.read(4 * 1024 * 1024 + 1)
    if len(raw) > 4 * 1024 * 1024:
        raise ValueError("ALLOWLIST_SIZE_LIMIT")
    packet = json.loads(raw.decode("utf-8-sig"))
    if not isinstance(packet, dict) or packet.get("schema") != 1:
        raise ValueError("ALLOWLIST_SCHEMA_INVALID")
    rows = packet.get("files")
    if not isinstance(rows, list) or not rows or len(rows) > 4096:
        raise ValueError("ALLOWLIST_COUNT_INVALID")
    names = [str(safe_relative(r["path"])) for r in rows]
    if len({n.casefold() for n in names}) != len(names):
        raise ValueError("DUPLICATE_ARCHIVE_PATH")
    if any(n.casefold() == "bundle_manifest.json" for n in names):
        raise ValueError("MANIFEST_PATH_RESERVED")
    output = output.absolute()
    receipt_path = output.with_suffix(output.suffix + ".receipt.json")
    if output.exists() or receipt_path.exists() or not output.parent.is_dir():
        raise ValueError("OUTPUT_ALREADY_EXISTS_OR_PARENT_MISSING")
    no_reparse(output.parent)
    try:
        output.resolve().relative_to(root)
    except ValueError:
        pass
    else:
        raise ValueError("OUTPUT_INSIDE_SOURCE_REFUSED")
    # EN: Bound outstanding reads and memory; archive writes have one owner.
    # ZH: 限制同时读取数量和内存，归档写入保持单 owner。
    temp = output.with_name(output.name + "." + uuid.uuid4().hex + ".partial")
    metadata = []
    total = 0
    try:
        with zipfile.ZipFile(temp, "x", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                for start in range(0, len(rows), workers):
                    futures = [pool.submit(load_one, root, row) for row in rows[start:start + workers]]
                    for future in futures:
                        info, data = future.result()
                        total += len(data)
                        if total > MAX_TOTAL:
                            raise ValueError("TOTAL_SIZE_LIMIT")
                        entry = zipfile.ZipInfo(info["path"], (1980, 1, 1, 0, 0, 0))
                        entry.compress_type = zipfile.ZIP_DEFLATED
                        entry.external_attr = 0o100644 << 16
                        archive.writestr(entry, data)
                        metadata.append(info)
            manifest = {"schema": 1, "scope": "SOURCE_ONLY_REQUIRES_HUMAN_REVIEW",
                        "files": sorted(metadata, key=lambda x: x["path"])}
            archive.writestr("BUNDLE_MANIFEST.json",
                             json.dumps(manifest, sort_keys=True, ensure_ascii=True, separators=(",", ":")))
        # EN: Linking a closed local temp file gives no-clobber publication, then unlink.
        # ZH: 用已关闭临时文件发布并拒绝覆盖，随后移除临时链接。
        os.link(temp, output)
        temp.unlink()
        digest = hashlib.sha256()
        with output.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
        report = {"stage": "PRIVATE_SOURCE_BUNDLE", "status": "PREPARED_NOT_UPLOADED",
                  "counts": {"files": len(metadata), "bytes": total, "uploads": 0},
                  "hashes": {"allowlist_sha256": hashlib.sha256(raw).hexdigest(),
                             "archive_sha256": digest.hexdigest()},
                  "error_codes": [],
                  "local_report_paths": [str(output), str(receipt_path)],
                  "limitations": ["HEURISTIC_SCAN_NOT_COMPREHENSIVE", "NOT_EXECUTION_VALIDATED"]}
        with receipt_path.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(report, stream, ensure_ascii=True, sort_keys=True, indent=2)
        return report
    finally:
        if temp.exists():
            temp.unlink()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--allowlist", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, choices=range(1, 17), default=8)
    args = parser.parse_args()
    try:
        result = build(args.root, args.allowlist, args.output, args.workers)
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
