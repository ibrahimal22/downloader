"""Single-instance guard: a second launch forwards its arguments and exits."""

from __future__ import annotations

import getpass
import json
import logging

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

log = logging.getLogger(__name__)


def _server_name() -> str:
    try:
        user = getpass.getuser()
    except (KeyError, OSError):
        user = "user"
    return f"Downloader-{user}"


def forward_to_running(args: list[str], timeout_ms: int = 500) -> bool:
    """If another instance is running, send it `args` and return True."""
    sock = QLocalSocket()
    sock.connectToServer(_server_name())
    if not sock.waitForConnected(timeout_ms):
        return False
    sock.write(json.dumps(args).encode("utf-8"))
    sock.flush()
    sock.waitForBytesWritten(timeout_ms)
    sock.disconnectFromServer()
    return True


class InstanceServer(QObject):
    message = Signal(list)  # argv of the second launch

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self.server = QLocalServer(self)
        self.server.newConnection.connect(self._on_connection)

    def listen(self) -> bool:
        name = _server_name()
        if not self.server.listen(name):
            # A crashed instance can leave a stale socket behind.
            QLocalServer.removeServer(name)
            if not self.server.listen(name):
                log.warning("Single-instance server failed: %s", self.server.errorString())
                return False
        return True

    def _on_connection(self) -> None:
        sock = self.server.nextPendingConnection()
        buffer = bytearray()

        def read() -> None:
            buffer.extend(bytes(sock.readAll().data()))

        def done() -> None:
            read()
            try:
                args = json.loads(buffer.decode("utf-8") or "[]")
            except (UnicodeDecodeError, json.JSONDecodeError):
                args = []
            self.message.emit(args if isinstance(args, list) else [])
            sock.deleteLater()

        sock.readyRead.connect(read)
        sock.disconnected.connect(done)
