"""Tiny localhost HTTP endpoint used by the browser extension.

Security: binds to 127.0.0.1 only, every endpoint requires the pairing token (compared in
constant time), CORS is granted only to browser-extension origins, and bodies are size-capped.
"""

from __future__ import annotations

import hmac
import json
import logging

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QHostAddress, QTcpServer, QTcpSocket

from downloader import __version__
from downloader.config.settings import SettingsStore

log = logging.getLogger(__name__)

MAX_REQUEST = 64 * 1024
EXTENSION_ORIGINS = ("chrome-extension://", "moz-extension://", "safari-web-extension://")


def parse_request(raw: bytes) -> tuple[str, str, dict[str, str], bytes] | None:
    """Return (method, path, headers, body) once a full request is buffered, else None."""
    head, sep, body = raw.partition(b"\r\n\r\n")
    if not sep:
        return None
    lines = head.decode("latin-1").split("\r\n")
    try:
        method, path, _version = lines[0].split(" ", 2)
    except ValueError:
        return ("BAD", "", {}, b"")
    headers = {}
    for line in lines[1:]:
        key, _, value = line.partition(":")
        headers[key.strip().lower()] = value.strip()
    length = int(headers.get("content-length", "0") or 0)
    if len(body) < length:
        return None
    return method.upper(), path, headers, body[:length]


class LocalServer(QObject):
    add_requested = Signal(str, str, bool)  # url, preset ("" = default), quick (skip dialog)

    def __init__(self, settings: SettingsStore, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.settings = settings
        self.server = QTcpServer(self)
        self.server.newConnection.connect(self._on_connection)
        self._buffers: dict[QTcpSocket, bytes] = {}

    def start(self) -> bool:
        self.stop()
        s = self.settings.data
        if not s.local_server_enabled:
            return False
        ok = self.server.listen(QHostAddress(QHostAddress.SpecialAddress.LocalHost),
                                s.local_server_port)
        if ok:
            log.info("Browser integration listening on 127.0.0.1:%d", s.local_server_port)
        else:
            log.warning("Could not listen on port %d: %s", s.local_server_port,
                        self.server.errorString())
        return ok

    def stop(self) -> None:
        if self.server.isListening():
            self.server.close()

    def restart(self) -> None:
        self.start()

    def _on_connection(self) -> None:
        while self.server.hasPendingConnections():
            sock = self.server.nextPendingConnection()
            self._buffers[sock] = b""
            sock.readyRead.connect(lambda s=sock: self._on_ready(s))
            sock.disconnected.connect(lambda s=sock: self._cleanup(s))

    def _cleanup(self, sock: QTcpSocket) -> None:
        self._buffers.pop(sock, None)
        sock.deleteLater()

    def _on_ready(self, sock: QTcpSocket) -> None:
        self._buffers[sock] = self._buffers.get(sock, b"") + bytes(sock.readAll().data())
        if len(self._buffers[sock]) > MAX_REQUEST:
            self._respond(sock, 413, {"error": "request too large"}, "")
            return
        parsed = parse_request(self._buffers[sock])
        if parsed is None:
            return
        status, payload, origin = self.handle(*parsed)
        self._respond(sock, status, payload, origin)

    def handle(self, method: str, path: str, headers: dict[str, str],
               body: bytes) -> tuple[int, dict | None, str]:
        """Pure request handler (unit-tested). Returns (status, json payload, allowed origin)."""
        origin = headers.get("origin", "")
        allowed_origin = origin if origin.startswith(EXTENSION_ORIGINS) else ""
        if origin and not allowed_origin:
            return 403, {"error": "forbidden origin"}, ""
        if method == "OPTIONS":
            return 204, None, allowed_origin
        token = headers.get("x-downloader-token", "")
        if not hmac.compare_digest(token.encode(), self.settings.data.local_server_token.encode()):
            return 401, {"error": "invalid token"}, allowed_origin
        route = path.split("?", 1)[0]
        if method == "GET" and route == "/ping":
            return 200, {"app": "Downloader", "version": __version__}, allowed_origin
        if method == "POST" and route == "/add":
            try:
                data = json.loads(body.decode("utf-8") or "{}")
            except (UnicodeDecodeError, json.JSONDecodeError):
                return 400, {"error": "invalid json"}, allowed_origin
            url = str(data.get("url") or "")
            if not url.startswith(("http://", "https://")) or len(url) > 4096:
                return 400, {"error": "invalid url"}, allowed_origin
            self.add_requested.emit(url, str(data.get("preset") or ""), bool(data.get("quick")))
            return 200, {"ok": True}, allowed_origin
        return 404, {"error": "not found"}, allowed_origin

    def _respond(self, sock: QTcpSocket, status: int, payload: dict | None, origin: str) -> None:
        reasons = {200: "OK", 204: "No Content", 400: "Bad Request", 401: "Unauthorized",
                   403: "Forbidden", 404: "Not Found", 413: "Payload Too Large"}
        body = json.dumps(payload).encode() if payload is not None else b""
        headers = [
            f"HTTP/1.1 {status} {reasons.get(status, 'Error')}",
            "Content-Type: application/json",
            f"Content-Length: {len(body)}",
            "Connection: close",
        ]
        if origin:
            headers += [
                f"Access-Control-Allow-Origin: {origin}",
                "Access-Control-Allow-Methods: GET, POST, OPTIONS",
                "Access-Control-Allow-Headers: Content-Type, X-Downloader-Token",
                "Vary: Origin",
            ]
        sock.write(("\r\n".join(headers) + "\r\n\r\n").encode() + body)
        sock.flush()
        sock.disconnectFromHost()
