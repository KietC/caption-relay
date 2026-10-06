"""Exercise only a fresh portable EXE and disposable loopback test capture.

只检查新建便携 EXE 和一次性的回环测试采集。
"""
import base64
from datetime import datetime, timezone
import hashlib
import hmac
from http.client import HTTPConnection
import json
from pathlib import Path
import secrets
import socket
import subprocess
import tempfile
import time

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def run(executable):
    executable = Path(executable).resolve()
    with tempfile.TemporaryDirectory(prefix="captionrelay-frozen-fixture-") as temporary:
        directory = Path(temporary)
        output = directory / "capture"
        key = secrets.token_bytes(32)
        token = secrets.token_urlsafe(32)
        config = directory / "config.json"
        config.write_text(json.dumps({"device_id": "portable-fixture", "token": token,
                                     "key_b64": base64.b64encode(key).decode()}), encoding="utf-8")
        with socket.socket() as reserve:
            reserve.bind(("127.0.0.1", 0))
            port = reserve.getsockname()[1]
        process = subprocess.Popen([str(executable), "mobile_bridge.py", "--config", str(config),
                                    "--output", str(output), "--port", str(port), "--duration", "60"],
                                   cwd=executable.parent, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            deadline = time.monotonic() + 15
            while not (output / "status.json").exists():
                if process.poll() is not None or time.monotonic() >= deadline:
                    raise RuntimeError("Frozen receiver did not initialize")
                time.sleep(0.05)
            timestamp = datetime.now(timezone.utc).isoformat()
            package = "com.xiaomi.aiasst.vision"
            nodes = [{"id": package + ":id/sentence_id", "package": package,
                      "container_id": package + ":id/recyclerView_source",
                      "text": "PORTABLE TEST: sample caption", "bounds": [0, 0, 100, 20]},
                     {"id": package + ":id/sentence_id", "package": package,
                      "container_id": package + ":id/recyclerView_dest",
                      "text": "便携版测试：示例字幕", "bounds": [0, 21, 100, 40]}]
            event = {"type": "snapshot", "timestamp": timestamp,
                     "windows": [{"id": 1, "package": package, "nodes": nodes}]}
            nonce = secrets.token_bytes(12)
            cipher = AESGCM(key).encrypt(nonce, json.dumps(event, ensure_ascii=False).encode(),
                                         b"caption-relay-v1|portable-fixture|fixture-stream|1")
            envelope = {"v": 1, "device_id": "portable-fixture", "stream_id": "fixture-stream", "seq": 1,
                        "nonce": base64.b64encode(nonce).decode(), "ciphertext": base64.b64encode(cipher).decode()}
            responses = []
            for retry in range(2):
                conn = HTTPConnection("127.0.0.1", port, timeout=5)
                try:
                    conn.request("POST", "/v1/captions", json.dumps(envelope).encode(),
                                 {"Authorization": "Bearer " + token, "Content-Type": "application/json"})
                    response = conn.getresponse()
                    value = json.loads(response.read())
                    assert response.status == 200, value
                    responses.append(value)
                finally:
                    conn.close()
            expected = hmac.new(key, b"caption-relay-ack-v1|portable-fixture|fixture-stream|1", hashlib.sha256).hexdigest()
            assert responses[0]["mac"] == expected and responses[0] == responses[1]
            while time.monotonic() < deadline:
                try:
                    latest = json.loads((output / "caption_latest.json").read_text(encoding="utf-8"))
                    if latest.get("has_captions"):
                        break
                except (OSError, ValueError):
                    pass
                time.sleep(0.05)
            else:
                raise RuntimeError("Frozen receiver did not persist the bilingual fixture")
            history = json.loads((output / "history.json").read_text(encoding="utf-8"))
            assert len(history["entries"]) == 1
            assert "便携版测试：示例字幕" in (output / "captions.md").read_text(encoding="utf-8")
            result_path = directory / "viewer-check.json"
            child = subprocess.run([str(executable), "--caption-fixture", str(output), "--result", str(result_path)],
                                   cwd=executable.parent, capture_output=True, timeout=30,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
            if child.returncode:
                raise RuntimeError("Frozen viewer fixture failed: " + child.stderr.decode("utf-8", errors="replace")[-1500:])
            viewer = json.loads(result_path.read_text(encoding="utf-8"))
            assert viewer["ok"]
            (output / "stop.request").write_text("stop\n", encoding="utf-8")
            stdout, stderr = process.communicate(timeout=15)
            assert process.returncode == 0, stderr.decode("utf-8", errors="replace")[-1500:]
            state = json.loads((output / "status.json").read_text(encoding="utf-8"))
            assert state["state"] == "stopped_by_user"
            return {"ok": True, "frozen_receiver": True, "aes_gcm_ack": True,
                    "exact_retry_deduplicated": True, "history_and_markdown": True,
                    "frozen_caption_viewer": viewer["caption_fixture"], "graceful_stop": True,
                    "alternate_loopback_port": True, "production_services_touched": False}
        finally:
            if process.poll() is None:
                output.mkdir(exist_ok=True)
                (output / "stop.request").write_text("stop\n", encoding="utf-8")
                try:
                    process.communicate(timeout=15)
                except subprocess.TimeoutExpired:
                    process.terminate()
                    process.communicate(timeout=5)
