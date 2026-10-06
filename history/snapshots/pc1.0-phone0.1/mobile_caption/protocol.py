"""Version 1 wire contract. Only the two Xiaomi caption fields are accepted."""
import base64
from dataclasses import dataclass
from datetime import datetime
import hashlib
import hmac
import json
from pathlib import Path
import re

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from captions import PACKAGE, ROLES, SENTENCE_ID, caption_role

MAX_ENVELOPE = 128 * 1024
MAX_PLAINTEXT = 32 * 1024
NODE_IDS = set(ROLES) | {SENTENCE_ID}
IDENTIFIER = re.compile(r"[A-Za-z0-9._:-]{1,128}\Z")
MAX_SEQ = 2**63 - 1


class ProtocolError(Exception):
    def __init__(self, code, status=400):
        super().__init__(code)
        self.code = code
        self.status = status


def _object_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ProtocolError("duplicate_json_key")
        result[key] = value
    return result


def json_object(data):
    try:
        result = json.loads(data, object_pairs_hook=_object_pairs,
                            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise ProtocolError("invalid_json") from None
    if not isinstance(result, dict):
        raise ProtocolError("expected_json_object")
    return result


def _b64(value, expected=None):
    if not isinstance(value, str):
        raise ProtocolError("invalid_base64")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (ValueError, TypeError):
        raise ProtocolError("invalid_base64") from None
    if expected is not None and len(decoded) != expected:
        raise ProtocolError("invalid_key_or_nonce_length")
    return decoded


@dataclass(frozen=True)
class Config:
    device_id: str
    token: str
    key: bytes
    inbox_path: Path | None = None

    @classmethod
    def load(cls, path):
        data = json_object(Path(path).read_bytes())
        device = data.get("device_id")
        token = data.get("token")
        if not isinstance(device, str) or not IDENTIFIER.fullmatch(device):
            raise ProtocolError("invalid_config_device")
        if (not isinstance(token, str) or not 32 <= len(token) <= 256
                or any(ord(char) < 33 or ord(char) > 126 for char in token)):
            raise ProtocolError("invalid_config_token")
        inbox_path = data.get("inbox_path")
        if inbox_path is not None:
            if not isinstance(inbox_path, str) or not inbox_path:
                raise ProtocolError("invalid_config_inbox_path")
            inbox_path = Path(inbox_path)
            if not inbox_path.is_absolute():
                inbox_path = Path(path).parent / inbox_path
        return cls(device, token, _b64(data.get("key_b64"), 32), inbox_path)


def _keys(value, allowed, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        raise ProtocolError("invalid_event_schema")


def _text(value, maximum=16384, nullable=False):
    if nullable and value is None:
        return
    if not isinstance(value, str) or len(value) > maximum:
        raise ProtocolError("invalid_text")
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        raise ProtocolError("invalid_text_encoding") from None


def _integer(value, minimum=0, maximum=MAX_SEQ):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ProtocolError("invalid_integer")


def _timestamp(value):
    _text(value, 80)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("missing timezone")
    except ValueError:
        raise ProtocolError("invalid_timestamp") from None


def _bounds(value):
    if (not isinstance(value, list) or len(value) != 4
            or any(type(x) is not int or abs(x) > 1000000 for x in value)):
        raise ProtocolError("invalid_bounds")


def validate_event(event):
    kind = event.get("type")
    _timestamp(event.get("timestamp"))
    if kind == "snapshot":
        _keys(event, {"type", "timestamp", "windows"}, {"windows"})
        windows = event["windows"]
        if not isinstance(windows, list) or len(windows) > 16:
            raise ProtocolError("too_many_windows")
        count = 0
        for window in windows:
            _keys(window, {"id", "package", "bounds", "nodes"}, {"package", "nodes"})
            if window["package"] != PACKAGE:
                raise ProtocolError("unapproved_caption_package")
            if "id" in window:
                _integer(window["id"], -1, 2**31 - 1)
            if "bounds" in window:
                _bounds(window["bounds"])
            nodes = window["nodes"]
            if not isinstance(nodes, list):
                raise ProtocolError("invalid_nodes")
            count += len(nodes)
            if count > 128:
                raise ProtocolError("too_many_nodes")
            for node in nodes:
                _keys(node, {"package", "id", "text", "description", "bounds", "container_id"}, {"package", "id", "text"})
                if (node["package"] != PACKAGE or not isinstance(node["id"], str)
                        or node["id"] not in NODE_IDS):
                    raise ProtocolError("unapproved_caption_node")
                if caption_role(node) is None:
                    raise ProtocolError("unapproved_caption_container")
                _text(node["text"], nullable=True)
                if "description" in node:
                    _text(node["description"], nullable=True)
                if "bounds" in node:
                    _bounds(node["bounds"])
    elif kind == "heartbeat":
        _keys(event, {"type", "timestamp", "counts", "queue_depth", "captured_snapshots", "dropped_snapshots", "captions_visible"})
        if "captions_visible" in event and type(event["captions_visible"]) is not bool:
            raise ProtocolError("invalid_captions_visible")
        counts = event.get("counts", {})
        if not isinstance(counts, dict) or len(counts) > 16:
            raise ProtocolError("invalid_counts")
        for name, value in counts.items():
            if not IDENTIFIER.fullmatch(name):
                raise ProtocolError("invalid_counter_name")
            _integer(value)
        for name in ("queue_depth", "captured_snapshots", "dropped_snapshots"):
            if name in event:
                _integer(event[name])
    elif kind == "gap":
        _keys(event, {"type", "timestamp", "dropped_snapshots", "dropped", "first_dropped_at", "last_dropped_at", "reason"},
              {"dropped_snapshots", "first_dropped_at", "last_dropped_at", "reason"})
        _integer(event["dropped_snapshots"], 1)
        if "dropped" in event:
            _integer(event["dropped"], 1)
            if event["dropped"] != event["dropped_snapshots"]:
                raise ProtocolError("inconsistent_gap_count")
        _timestamp(event["first_dropped_at"])
        _timestamp(event["last_dropped_at"])
        if event["reason"] not in ("outbox_full", "payload_too_large"):
            raise ProtocolError("invalid_gap_reason")
    else:
        raise ProtocolError("invalid_event_type")
    return event


def aad(device, stream, seq):
    return f"caption-relay-v1|{device}|{stream}|{seq}".encode("ascii")


def ack(config, stream, seq):
    body = f"caption-relay-ack-v1|{config.device_id}|{stream}|{seq}".encode("ascii")
    return {"v": 1, "device_id": config.device_id, "stream_id": stream, "seq": seq,
            "mac": hmac.new(config.key, body, hashlib.sha256).hexdigest()}


def authorize(config, authorization):
    if not isinstance(authorization, str) or not hmac.compare_digest(
            authorization.encode("utf-8"), ("Bearer " + config.token).encode("ascii")):
        raise ProtocolError("unauthorized", 401)


def decode(config, authorization, raw):
    authorize(config, authorization)
    if len(raw) > MAX_ENVELOPE:
        raise ProtocolError("payload_too_large", 413)
    envelope = json_object(raw)
    _keys(envelope, {"v", "device_id", "stream_id", "seq", "nonce", "ciphertext"},
          {"v", "device_id", "stream_id", "seq", "nonce", "ciphertext"})
    if type(envelope["v"]) is not int or envelope["v"] != 1:
        raise ProtocolError("unsupported_version")
    if envelope["device_id"] != config.device_id:
        raise ProtocolError("wrong_device", 403)
    stream = envelope["stream_id"]
    if not isinstance(stream, str) or not IDENTIFIER.fullmatch(stream):
        raise ProtocolError("invalid_stream")
    _integer(envelope["seq"], 1)
    nonce = _b64(envelope["nonce"], 12)
    envelope["nonce"] = base64.b64encode(nonce).decode("ascii")
    ciphertext = _b64(envelope["ciphertext"])
    if not 16 <= len(ciphertext) <= MAX_PLAINTEXT + 16:
        raise ProtocolError("payload_too_large", 413)
    try:
        plaintext = AESGCM(config.key).decrypt(nonce, ciphertext,
                                             aad(config.device_id, stream, envelope["seq"]))
    except InvalidTag:
        raise ProtocolError("authentication_failed", 403) from None
    event = validate_event(json_object(plaintext))
    fingerprint = hashlib.sha256(nonce + ciphertext).hexdigest()
    return envelope, event, fingerprint
