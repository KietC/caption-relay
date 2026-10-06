"""Loopback HTTP endpoint, intended only behind a TLS tunnel.

仅监听本机回环的 HTTP 端点，预期放在 TLS 隧道之后使用。
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import socket
import threading
import time
from pathlib import Path

from .protocol import (MAX_BATCH_BYTES, MAX_ENVELOPE, ProtocolError, ack, authorize,
                       decode, decode_batch, decode_live, live_ack)


class CaptionServer(ThreadingHTTPServer):
    daemon_threads = True
    request_queue_size = 16

    def __init__(self, address, config, inbox, update_apk=None, live=None, download_apks=None):
        if address[0] not in ("127.0.0.1", "::1"):
            raise ValueError("Receiver must bind loopback; expose it through HTTPS only.")
        self.config = config
        self.inbox = inbox
        self.live = live
        self.update_apk = Path(update_apk) if update_apk is not None else None
        # New callers provide explicit version-to-artifact mappings; legacy callers retain their fixed routes.
        # 新调用者明确映射版本和公开文件；旧调用者仍沿用固定路由。
        self.download_apks = None if download_apks is None else {
            name: Path(path) for name, path in download_apks.items()}
        if self.download_apks is not None and set(self.download_apks) - {
                "CaptionRelayCaptionBridge-0.2.0.apk", "CaptionRelayCaptionBridge-0.3.0.apk"}:
            raise ValueError("Only fixed public APK filenames may be downloaded")
        self.slots = threading.BoundedSemaphore(16)
        super().__init__(address, Handler)

    def process_request(self, request, client_address):
        if not self.slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.slots.release()


class Handler(BaseHTTPRequestHandler):
    # Each response closes its local connection. Cloudflare owns/reuses TLS.
    # 每次响应关闭本地连接；TLS 连接由 Cloudflare 管理和复用。
    protocol_version = "HTTP/1.0"
    server_version = "CaptionRelay"
    sys_version = ""

    def setup(self):
        super().setup()
        self.connection.settimeout(10)

    def log_message(self, *args):
        pass  # Never log bearer tokens, caption bodies, or URL query strings.

    def reply(self, status, data):
        raw = json.dumps(data, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        # Fixed public application artifacts only; no directory browsing,
        # configuration, pairing material, or caption history is exposed.
        # 只暴露固定的公开应用文件，不提供目录浏览、配置、配对资料或字幕历史。
        downloads = {"/downloads/CaptionRelayCaptionBridge-0.2.0.apk": "CaptionRelayCaptionBridge-0.2.0.apk",
                     "/downloads/CaptionRelayCaptionBridge-0.3.0.apk": "CaptionRelayCaptionBridge-0.3.0.apk"}
        if self.path in downloads and (self.server.update_apk is not None or self.server.download_apks is not None):
            filename = downloads[self.path]
            if self.server.download_apks is not None:
                path = self.server.download_apks.get(filename)
                if path is None:
                    return self.reply(404, {"error": "update_unavailable"})
            else:
                path = (self.server.update_apk if filename == "CaptionRelayCaptionBridge-0.2.0.apk"
                        else self.server.update_apk.parent / "CaptionRelayCaptionBridge-0.3.0.apk")
            try:
                size = path.stat().st_size
                if not 1 <= size <= 16 * 1024 * 1024:
                    return self.reply(503, {"error": "update_unavailable"})
                raw = path.read_bytes()
                if not 1 <= len(raw) <= 16 * 1024 * 1024:
                    return self.reply(503, {"error": "update_unavailable"})
            except OSError:
                return self.reply(404, {"error": "update_unavailable"})
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/vnd.android.package-archive")
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Content-Disposition", f'attachment; filename="{filename}"')
                self.send_header("Cache-Control", "no-store")
                self.send_header("Connection", "close")
                self.end_headers()
                self.wfile.write(raw)
            except (OSError, ConnectionError):
                return
            return
        self.reply(404, {"error": "not_found"})

    def do_POST(self):
        try:
            batch = self.path == "/v1/captions/batch"
            live = self.path == "/v1/captions/live"
            if self.path != "/v1/captions" and not batch and not live:
                raise ProtocolError("not_found", 404)
            if batch or live:
                # Authenticate new routes before inspecting/reading bodies.
                # 新路由先认证，再检查或读取请求体。
                authorizations = self.headers.get_all("Authorization", [])
                if len(authorizations) != 1:
                    raise ProtocolError("unauthorized", 401)
                authorize(self.server.config, authorizations[0])
                if live and self.server.live is None:
                    raise ProtocolError("live_unavailable", 503)
            if self.headers.get("Transfer-Encoding") is not None:
                raise ProtocolError("chunked_not_supported")
            lengths = self.headers.get_all("Content-Length", [])
            if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
                raise ProtocolError("content_length_required", 411)
            if len(lengths[0]) > 8:
                raise ProtocolError("payload_too_large", 413)
            length = int(lengths[0])
            if not 1 <= length <= (MAX_BATCH_BYTES if batch else MAX_ENVELOPE):
                raise ProtocolError("payload_too_large", 413)
            if self.headers.get_content_type() != "application/json":
                raise ProtocolError("json_required", 415)
            authorizations = self.headers.get_all("Authorization", [])
            if len(authorizations) != 1:
                raise ProtocolError("unauthorized", 401)
            # Refuse unauthenticated slow bodies before occupying a read slot.
            # 占用读取槽位前，先拒绝未认证的慢速请求体。
            authorize(self.server.config, authorizations[0])
            raw = self.rfile.read(length)
            received_monotonic = time.monotonic()
            if len(raw) != length:
                raise ProtocolError("incomplete_request")
            if live:
                envelope, event, fingerprint = decode_live(self.server.config, authorizations[0], raw)
                self.server.live.offer(envelope, event, fingerprint, received_monotonic=received_monotonic)
                self.reply(200, live_ack(self.server.config, envelope["stream_id"], envelope["seq"]))
                return
            if batch:
                decoded = decode_batch(self.server.config, authorizations[0], raw)
                self.server.inbox.accept_batch(decoded)
                envelope = decoded[-1][0]
            else:
                envelope, event, fingerprint = decode(self.server.config, authorizations[0], raw)
                self.server.inbox.accept(envelope, event, fingerprint)
            self.reply(200, ack(self.server.config, envelope["stream_id"], envelope["seq"]))
        except ProtocolError as error:
            self.reply(error.status, {"error": error.code})
        except (socket.timeout, ConnectionError, BrokenPipeError):
            return
        except Exception:
            # Persistence failures must NEVER return ACK, and never leak paths.
            # 持久写入失败时绝不能返回确认，也不能泄漏路径。
            self.reply(503, {"error": "receiver_unavailable"})
