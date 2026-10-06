"""Private caption-state migration. The caller must stop receiver and tunnel first.

Bundles contain pairing credentials and transcripts in an unencrypted ZIP.
They deliberately exclude executables, audio, tunnel logs and phone UI dumps.
"""
from __future__ import annotations

import base64
from contextlib import closing
import csv
import ctypes
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import re
import shutil
import sqlite3
import stat
import subprocess
import tempfile
import time
import uuid
import zipfile

from .protocol import Config, IDENTIFIER, MAX_PLAINTEXT, ProtocolError, json_object, validate_event


FORMAT = "captionrelay-mobile-caption-state"
VERSION = 1
CONFIG = "mobile_caption/runtime/config.json"
INBOX = "mobile_caption/runtime/inbox.sqlite3"
VIEWER = "output/caption_viewer"
MANIFEST = "manifest.json"
MAX_FILES = 10000
MAX_FILE_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_MANIFEST_BYTES = 2 * 1024 * 1024
VIEWER_FILES = {"viewer_status.json", "status.json", "window_status.json", "window_only.json",
                "history.json", "caption_latest.json", "caption_latest.txt", "captions.md",
                "transmission_gaps.md"}
STATE_FILES = {"viewer_status.json", "status.json", "window_status.json", "window_only.json"}
PROCESS_KEYS = {"pid", "created", "owner_created", "run_id", "last_capture_exit",
                "capture_starts", "cleanup_pending", "endpoint", "paired_endpoint",
                "log_path", "tunnel", "tunnel_running"}


class MigrationError(ValueError):
    pass


def _stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "_" + uuid.uuid4().hex[:10]


def _json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def _object(data):
    try:
        return json_object(data)
    except ProtocolError as error:
        raise MigrationError("Invalid migration JSON: " + error.code) from error


def _config(path):
    try:
        return Config.load(path)
    except ProtocolError as error:
        raise MigrationError("Invalid migration config: " + error.code) from error


def _linked(path):
    if not path.exists() and not path.is_symlink():
        return False
    info = path.lstat()
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, "st_file_attributes", 0) & 0x400)


def _no_links(path):
    for item in (path, *path.parents):
        if _linked(item):
            raise MigrationError("Symbolic links or reparse points are not accepted: " + str(item))


def _tree_files(root):
    _no_links(root)
    if not root.exists():
        return
    for directory, directories, files in os.walk(root, followlinks=False):
        for name in directories + files:
            item = Path(directory) / name
            if _linked(item):
                raise MigrationError("Symbolic links or reparse points are not accepted: " + str(item))
        for name in files:
            yield Path(directory) / name


@lru_cache(maxsize=1)
def _user_sid():
    result = subprocess.run(["whoami", "/user", "/fo", "csv", "/nh"], capture_output=True,
                            text=True, check=True, creationflags=subprocess.CREATE_NO_WINDOW)
    sid = next(csv.reader(io.StringIO(result.stdout.strip())))[-1]
    if not re.fullmatch(r"S-1-[0-9-]+", sid):
        raise MigrationError("Cannot determine Windows account for private migration ACL")
    return sid


def _private(path):
    """Replace the DACL, not merely add grants beside existing broad permissions."""
    if os.name != "nt":
        path.chmod(0o700 if path.is_dir() else 0o600)
        return
    from ctypes import wintypes
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    convert = advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p),
                        ctypes.POINTER(wintypes.DWORD)]
    convert.restype = wintypes.BOOL
    get_dacl = advapi.GetSecurityDescriptorDacl
    get_dacl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL),
                         ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL)]
    get_dacl.restype = wintypes.BOOL
    set_info = advapi.SetNamedSecurityInfoW
    set_info.argtypes = [wintypes.LPWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                         ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    set_info.restype = wintypes.DWORD
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    descriptor = ctypes.c_void_p()
    inheritance = "OICI" if path.is_dir() else ""
    sddl = "D:P" + "".join(f"(A;{inheritance};FA;;;{sid})" for sid in (_user_sid(), "SY", "BA"))
    if not convert(sddl, 1, ctypes.byref(descriptor), None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        present, defaulted, dacl = wintypes.BOOL(), wintypes.BOOL(), ctypes.c_void_p()
        if not get_dacl(descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)):
            raise ctypes.WinError(ctypes.get_last_error())
        result = set_info(str(path), 1, 0x80000004, None, None, dacl, None)
        if result:
            raise ctypes.WinError(result)
    finally:
        kernel.LocalFree(descriptor)


def _allowed(name):
    if name in {CONFIG, INBOX}:
        return True
    parts = PurePosixPath(name).parts
    return (parts[:2] == ("output", "caption_viewer") and len(parts) in (3, 4)
            and parts[-1] in VIEWER_FILES
            and (len(parts) == 3 or parts[2].startswith("session_")))


def _archive_name(name):
    if (not isinstance(name, str) or not name or "\\" in name or ":" in name
            or any(ord(char) < 32 for char in name)):
        raise MigrationError("Invalid archive path")
    parts = name.split("/")
    if any(part in {"", ".", ".."} or part.endswith((".", " "))
           or re.fullmatch(r"(?i)(CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?", part)
           for part in parts):
        raise MigrationError("Unsafe archive path")
    if name != MANIFEST and not _allowed(name):
        raise MigrationError("Unapproved archive file: " + name)
    return name


def _source_base(value):
    if not isinstance(value, str) or not value or len(value) > 32768 or "\0" in value:
        raise MigrationError("Invalid manifest source_base")
    path = PureWindowsPath(value) if "\\" in value or PureWindowsPath(value).drive else PurePosixPath(value)
    if not path.is_absolute() or ".." in path.parts:
        raise MigrationError("Manifest source_base must be an absolute normalized path")
    return value


def _digest(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _open_readonly(path):
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=10)


def _validate_database(path, device_id):
    with closing(_open_readonly(path)) as db:
        db.execute("PRAGMA trusted_schema=OFF")
        deadline = time.monotonic() + 30
        db.set_progress_handler(lambda: int(time.monotonic() > deadline), 10000)
        if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
            raise MigrationError("Inbox SQLite integrity check failed")
        schema = db.execute("SELECT type,name,tbl_name,sql FROM sqlite_master").fetchall()
        if any(kind not in {"table", "index"} or table not in {"events", "consumers"}
               or (kind == "index" and sql is not None) for kind, name, table, sql in schema):
            raise MigrationError("Unexpected inbox SQLite schema")
        for table, columns in (("events", ["id", "device", "stream", "seq", "nonce", "fingerprint", "event", "received_at"]),
                               ("consumers", ["consumer", "cursor"])):
            if [row[1] for row in db.execute(f"PRAGMA table_info({table})")] != columns:
                raise MigrationError("Unexpected inbox SQLite columns")
        maximum = db.execute("SELECT COALESCE(MAX(id),0) FROM events").fetchone()[0]
        count = 0
        for row in db.execute("SELECT id,device,stream,seq,nonce,fingerprint,event,received_at FROM events"):
            row_id, device, stream, seq, nonce, fingerprint, event, received_at = row
            if (type(row_id) is not int or row_id < 1 or device != device_id
                    or not isinstance(stream, str) or not IDENTIFIER.fullmatch(stream)
                    or type(seq) is not int or seq < 1 or not isinstance(nonce, str)
                    or not isinstance(fingerprint, str) or not re.fullmatch(r"[0-9a-f]{64}", fingerprint)
                    or not isinstance(event, str) or len(event.encode("utf-8")) > MAX_PLAINTEXT):
                raise MigrationError("Invalid stored inbox event")
            try:
                if len(base64.b64decode(nonce, validate=True)) != 12:
                    raise ValueError("nonce")
                validate_event(json_object(event))
                if datetime.fromisoformat(received_at.replace("Z", "+00:00")).tzinfo is None:
                    raise ValueError("timestamp")
            except Exception as error:
                raise MigrationError("Invalid stored inbox event data") from error
            count += 1
        if db.execute("SELECT 1 FROM events GROUP BY device,stream HAVING MIN(seq)<>1 OR MAX(seq)<>COUNT(*) OR COUNT(DISTINCT seq)<>COUNT(*) LIMIT 1").fetchone():
            raise MigrationError("Stored inbox sequence is not continuous")
        if db.execute("SELECT 1 FROM events GROUP BY nonce HAVING COUNT(*)>1 LIMIT 1").fetchone():
            raise MigrationError("Stored inbox contains duplicate nonce")
        for consumer, cursor in db.execute("SELECT consumer,cursor FROM consumers"):
            if (not isinstance(consumer, str) or not consumer or "\0" in consumer
                    or type(cursor) is not int or not 0 <= cursor <= maximum):
                raise MigrationError("Invalid inbox consumer cursor")
        return count


def _validate_json_files(stage, names):
    for name in names:
        if name.endswith(".json"):
            value = _object((stage / name).read_bytes())
            if name.endswith("/history.json"):
                entries = value.get("entries", [])
                if not isinstance(entries, list) or any(not isinstance(row, dict) or type(row.get("id")) is not int for row in entries):
                    raise MigrationError("Invalid caption history")


def export_bundle(base: Path, destination: Path):
    base, destination = Path(base).absolute(), Path(destination).absolute()
    _no_links(base)
    _no_links(destination)
    base = base.resolve()
    if destination.exists():
        raise FileExistsError("Migration archive already exists: " + str(destination))
    config_path = base / CONFIG
    _no_links(config_path)
    config = _config(config_path)
    inbox_path = (config.inbox_path or base / INBOX).absolute()
    _no_links(inbox_path)
    if inbox_path.resolve() != (base / INBOX).resolve():
        raise MigrationError("Export requires the canonical mobile_caption/runtime/inbox.sqlite3")
    if not inbox_path.is_file():
        raise MigrationError("Caption inbox does not exist")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=".caption_export_", dir=base) as temporary:
        stage = Path(temporary)
        _private(stage)
        (stage / CONFIG).parent.mkdir(parents=True)
        shutil.copyfile(config_path, stage / CONFIG)
        packaged_config = _object((stage / CONFIG).read_bytes())
        packaged_config["inbox_path"] = str(base / INBOX)
        _json(stage / CONFIG, packaged_config)
        with closing(_open_readonly(inbox_path)) as source_db, closing(sqlite3.connect(stage / INBOX)) as target_db:
            source_db.backup(target_db)
            target_db.execute("PRAGMA journal_mode=DELETE")
        names = [CONFIG, INBOX]
        for path in _tree_files(base / VIEWER):
            name = path.relative_to(base).as_posix()
            if _allowed(name):
                target = stage / name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
                names.append(name)
        names.sort()
        if len(names) > MAX_FILES:
            raise MigrationError("Too many migration files")
        entries = []
        total = 0
        for name in names:
            size = (stage / name).stat().st_size
            total += size
            if size > MAX_FILE_BYTES or total > MAX_TOTAL_BYTES:
                raise MigrationError("Migration size limit exceeded")
            entries.append({"path": name, "size": size, "sha256": _digest(stage / name)})
        _validate_json_files(stage, names)
        event_count = _validate_database(stage / INBOX, config.device_id)
        manifest = {"format": FORMAT, "version": VERSION, "source_base": str(base),
                    "created_at": datetime.now(timezone.utc).isoformat(), "files": entries}
        _json(stage / MANIFEST, manifest)
        # Stage on the destination volume so publication is a single rename.
        handle, pending_name = tempfile.mkstemp(prefix=".caption_bundle_", suffix=".zip", dir=destination.parent)
        os.close(handle)
        pending = Path(pending_name)
        try:
            _private(pending)
            with zipfile.ZipFile(pending, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for name in [MANIFEST, *names]:
                    archive.write(stage / name, name)
            # Windows rename refuses to replace; hard-link publication has the
            # same no-clobber property on POSIX and keeps the private file ACL.
            if os.name == "nt":
                pending.rename(destination)
            else:
                os.link(pending, destination)
                pending.unlink()
        finally:
            pending.unlink(missing_ok=True)
    return {"archive": str(destination), "file_count": len(names), "event_count": event_count,
            "bytes": destination.stat().st_size, "sha256": _digest(destination),
            "private": True, "encrypted": False, "contains_credentials": True}


def _unpack(source, stage):
    with zipfile.ZipFile(source, "r") as archive:
        infos = archive.infolist()
        if len(infos) > MAX_FILES + 1:
            raise MigrationError("Too many archive members")
        members, seen, total = {}, set(), 0
        for info in infos:
            name = _archive_name(info.filename)
            mode = info.external_attr >> 16
            if (info.is_dir() or stat.S_IFMT(mode) not in (0, stat.S_IFREG)
                    or info.external_attr & 0x400 or info.flag_bits & 1):
                raise MigrationError("Links, special files and encrypted members are not accepted")
            if name.casefold() in seen:
                raise MigrationError("Duplicate archive path")
            seen.add(name.casefold())
            total += info.file_size
            limit = MAX_MANIFEST_BYTES if name == MANIFEST else MAX_FILE_BYTES
            if info.file_size > limit or total > MAX_TOTAL_BYTES:
                raise MigrationError("Archive size limit exceeded")
            members[name] = info
        if MANIFEST not in members:
            raise MigrationError("Migration manifest is missing")
        manifest = _object(archive.read(MANIFEST))
        if manifest.get("format") != FORMAT or type(manifest.get("version")) is not int or manifest["version"] != VERSION:
            raise MigrationError("Unsupported migration format/version")
        _source_base(manifest.get("source_base"))
        files = manifest.get("files")
        if not isinstance(files, list) or not files or len(files) > MAX_FILES:
            raise MigrationError("Invalid migration file manifest")
        expected = {}
        for item in files:
            if not isinstance(item, dict):
                raise MigrationError("Invalid migration file entry")
            name = _archive_name(item.get("path"))
            if (name == MANIFEST or name in expected or type(item.get("size")) is not int
                    or not 0 <= item["size"] <= MAX_FILE_BYTES
                    or not isinstance(item.get("sha256"), str)
                    or not re.fullmatch(r"[0-9a-f]{64}", item["sha256"])):
                raise MigrationError("Invalid migration file metadata")
            expected[name] = item
        if set(expected) != set(members) - {MANIFEST} or not {CONFIG, INBOX} <= set(expected):
            raise MigrationError("Archive and manifest files do not match")
        for name, metadata in expected.items():
            if members[name].file_size != metadata["size"]:
                raise MigrationError("Archive file size does not match manifest")
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            digest, size = hashlib.sha256(), 0
            with archive.open(members[name]) as reader, target.open("xb") as writer:
                while chunk := reader.read(1024 * 1024):
                    size += len(chunk)
                    if size > metadata["size"]:
                        raise MigrationError("Archive data exceeds declared size")
                    writer.write(chunk)
                    digest.update(chunk)
            if size != metadata["size"] or digest.hexdigest() != metadata["sha256"]:
                raise MigrationError("Archive file hash verification failed")
        return manifest, list(expected)


def _remap_path(value, old_base, new_base, *, viewer_only=False):
    if not isinstance(value, str):
        raise MigrationError("Invalid saved path")
    old = old_base.replace("\\", "/").rstrip("/")
    raw = value.replace("\\", "/").rstrip("/")
    prefix = old + ("/" + VIEWER if viewer_only else "")
    if raw.casefold() == prefix.casefold():
        suffix = ""
    elif raw.casefold().startswith(prefix.casefold() + "/"):
        suffix = raw[len(prefix) + 1:]
    else:
        return None
    parts = suffix.split("/") if suffix else []
    if any(part in {"", ".", ".."} or ":" in part for part in parts):
        raise MigrationError("Unsafe saved path")
    root = new_base / VIEWER if viewer_only else new_base
    return str(root.joinpath(*parts))


def _clear_process_state(value):
    if isinstance(value, dict):
        return {key: _clear_process_state(item) for key, item in value.items()
                if key not in PROCESS_KEYS and not key.endswith("_pid")}
    if isinstance(value, list):
        return [_clear_process_state(item) for item in value]
    return value


def _prepare_import(stage, names, old_base, base):
    config = _object((stage / CONFIG).read_bytes())
    expected_inbox = old_base.replace("\\", "/").rstrip("/") + "/" + INBOX
    if config.get("inbox_path", "").replace("\\", "/").casefold() != expected_inbox.casefold():
        raise MigrationError("Imported inbox path does not match the manifest source base")
    config["inbox_path"] = str(base / INBOX)
    config["endpoint"] = ""
    _json(stage / CONFIG, config)
    remapped_count = 0
    with closing(sqlite3.connect(stage / INBOX)) as db, db:
        db.execute("PRAGMA trusted_schema=OFF")
        consumers = {}
        for consumer, cursor in db.execute("SELECT consumer,cursor FROM consumers"):
            mapped = _remap_path(consumer, old_base, base, viewer_only=True)
            key = mapped.casefold() if mapped is not None else consumer
            if key in consumers and consumers[key] != cursor:
                raise MigrationError("Consumer paths collide with different cursors")
            consumers[key] = cursor
            remapped_count += int(mapped is not None)
        db.execute("DELETE FROM consumers")
        db.executemany("INSERT INTO consumers(consumer,cursor) VALUES (?,?)", consumers.items())
    for name in names:
        if not name.startswith(VIEWER + "/") or not name.endswith(".json"):
            continue
        path = stage / name
        data = _object(path.read_bytes())
        if path.name in STATE_FILES:
            data = _clear_process_state(data)
            data.update(state="stopped", running=False)
        for key in ("output", "capture_output", "markdown_path", "mobile_config", "ios_config"):
            if key in data:
                mapped = _remap_path(data[key], old_base, base)
                if mapped is None:
                    raise MigrationError("Saved caption path is outside the source base: " + key)
                data[key] = mapped
        if path.name == "viewer_status.json":
            data.update(output=str(base / VIEWER), mobile_config=str(base / CONFIG),
                        audio_control=False, automatic_send=False)
        if path.name == "history.json":
            data["markdown_path"] = str(base / PurePosixPath(name).parent / "captions.md")
        _json(path, data)
    (stage / VIEWER).mkdir(parents=True, exist_ok=True)
    return remapped_count


def _replace(source, destination):
    os.replace(source, destination)


def _install(base, stage):
    backup = base / "output" / "migration_backups" / ("import_" + _stamp())
    _no_links(backup)
    backup.mkdir(parents=True)
    _private(backup)
    paths = ("mobile_caption/runtime", VIEWER)
    journal = {"state": "prepared", "paths": list(paths), "completed": []}
    _json(backup / "transaction.json", journal)
    moved, installed = [], []
    try:
        for name in paths:
            target, previous = base / name, backup / name
            _no_links(target)
            if target.exists():
                list(_tree_files(target))
                previous.parent.mkdir(parents=True, exist_ok=True)
                _replace(target, previous)
                moved.append(name)
            target.parent.mkdir(parents=True, exist_ok=True)
            _replace(stage / name, target)
            installed.append(name)
            journal["completed"].append(name)
            _json(backup / "transaction.json", journal)
        # Moved existing directories retain their old ACL; protect the backup
        # explicitly instead of assuming they inherited the private parent.
        for directory, directories, files in os.walk(backup):
            _private(Path(directory))
            for filename in files:
                _private(Path(directory) / filename)
        journal["state"] = "committed"
        _json(backup / "transaction.json", journal)
    except Exception:
        rollback_errors = []
        for name in reversed(paths):
            try:
                if name in installed:
                    displaced = stage / "rolled_back" / name
                    displaced.parent.mkdir(parents=True, exist_ok=True)
                    _replace(base / name, displaced)
                if name in moved:
                    _replace(backup / name, base / name)
            except Exception as error:
                rollback_errors.append(type(error).__name__)
        journal.update(state="rollback_failed" if rollback_errors else "rolled_back")
        _json(backup / "transaction.json", journal)
        if rollback_errors:
            raise MigrationError("Import rollback needs recovery; preserved backup: " + str(backup)) from None
        raise
    return backup


def import_bundle(base: Path, source: Path):
    base, source = Path(base).absolute(), Path(source).absolute()
    _no_links(base)
    _no_links(source)
    base.mkdir(parents=True, exist_ok=True)
    base = base.resolve()
    with tempfile.TemporaryDirectory(prefix=".caption_import_", dir=base) as temporary:
        stage = Path(temporary)
        _private(stage)
        manifest, names = _unpack(source, stage)
        _validate_json_files(stage, names)
        config = _config(stage / CONFIG)
        event_count = _validate_database(stage / INBOX, config.device_id)
        remapped_count = _prepare_import(stage, names, manifest["source_base"], base)
        _config(stage / CONFIG)
        _validate_database(stage / INBOX, config.device_id)
        backup = _install(base, stage)
    return {"archive": str(source), "base": str(base), "backup_path": str(backup),
            "file_count": len(names), "event_count": event_count,
            "remapped_consumers": remapped_count, "state": "imported_stopped",
            "requires_endpoint_update": True}
