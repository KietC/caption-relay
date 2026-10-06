#!/usr/bin/env python3
"""Audit tracked/explicit source files without traversing local runtime data.

检查 Git 已跟踪或明确列出的源码文件，不遍历本机运行数据。
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

import source_bundle

ROOT = Path(__file__).resolve().parents[1]
RULES = {
    "PRIVATE_KEY": re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    "GITHUB_TOKEN": re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{30,})\b"),
    "API_KEY": re.compile(r"\bsk-[A-Za-z0-9_-]{24,}\b"),
    "AWS_ACCESS_KEY": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
    "PERSONAL_WINDOWS_PATH": re.compile(r"[A-Za-z]:[/\\]Users[/\\](?!Public\b|Default\b|<)[A-Za-z0-9_.-]+", re.I),
    "PRIVATE_IPV4": re.compile(r"\b(?:192\.168\.\d{1,3}\.\d{1,3}|10\.\d{1,3}\.\d{1,3}\.\d{1,3})\b"),
    "LIVE_TUNNEL": re.compile(r"https://(?!test\.|previous\.|recovered\.)[a-z0-9-]+\.trycloudflare\.com", re.I),
    "PAIRING_PACKET": re.compile(r"CRCP1:[A-Za-z0-9_+/=-]{80,}"),
}


def candidates(fileset):
    """Prefer explicit files or Git's index; before git init only walk source roots.

    优先明确清单或 Git 索引；初始化 Git 前只遍历源码目录并剪枝私密目录。
    """
    if fileset:
        packet = json.loads(fileset.read_text(encoding="utf-8-sig"))
        if packet.get("schema") != 1 or not isinstance(packet.get("files"), list):
            raise ValueError("Expected a schema-1 fileset")
        return [row if isinstance(row, str) else row["path"] for row in packet["files"]]
    if (ROOT / ".git").exists():
        result = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, capture_output=True, check=True)
        return [name for name in result.stdout.decode("utf-8").split("\0") if name]
    names = []
    prune = source_bundle.DENIED | {"build", "builds", "dist"}
    for current, dirs, files in os.walk(ROOT, followlinks=False):
        dirs[:] = sorted(name for name in dirs if name.casefold() not in prune
                         and not name.startswith(".venv") and not (Path(current) / name).is_symlink())
        for name in sorted(files):
            names.append((Path(current) / name).relative_to(ROOT).as_posix())
    return names


def main(argv=None):
    # Use predictable UTF-8 for bilingual output even when redirected on Windows.
    # 即使 Windows 重定向输出，也统一使用 UTF-8 显示双语信息。
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fileset", type=Path, help="Explicit schema-1 path list or hashed allowlist")
    args = parser.parse_args(argv)
    findings = []
    names = candidates(args.fileset)
    for name in names:
        try:
            relative = source_bundle.safe_relative(name)
            path = ROOT.joinpath(*relative.parts)
            source_bundle.no_reparse(path)
            with path.open("rb") as stream:
                raw = stream.read(source_bundle.MAX_FILE + 1)
            if len(raw) > source_bundle.MAX_FILE or b"\0" in raw:
                raise ValueError("NON_TEXT_OR_TOO_LARGE")
            text = raw.decode("utf-8-sig")
        except (OSError, ValueError, UnicodeError) as exc:
            findings.append({"path": name, "rule": str(exc) if isinstance(exc, ValueError) else "UNREADABLE_SOURCE"})
            continue
        for rule, pattern in RULES.items():
            for match in pattern.finditer(text):
                # Report location and category only; never print the suspected secret.
                # 只报告位置和类别，不打印可能的密钥内容。
                findings.append({"path": name, "line": text.count("\n", 0, match.start()) + 1, "rule": rule})
    print(json.dumps({"schema": 1, "files": len(names), "ok": not findings,
                      "scope": "source_candidates_not_runtime", "findings": findings}, indent=2))
    print("EN: A clean heuristic scan is not proof of privacy; review the exact staged diff before publication.")
    print("中文：规则扫描通过并不等于隐私得到完全证明；发布前必须审阅确切暂存差异。")
    return 1 if findings or not names else 0


if __name__ == "__main__":
    raise SystemExit(main())
