import logging
import select
import socket
import time
from typing import override

from app.tcp.server import TCPServer

logger = logging.getLogger(__name__)


class ProxyServer(TCPServer):
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8000,
        target_host: str = "127.0.0.1",
        target_port: int = 8080,
        *,
        connect_timeout: float = 3.0,
        idle_timeout: float = 30.0,
        write_timeout: float = 5.0,
    ) -> None:
        if min(connect_timeout, idle_timeout, write_timeout) <= 0:
            raise ValueError("Proxy timeouts must be positive")
        super().__init__(host, port)
        self.target_host = target_host
        self.target_port = target_port
        self.connect_timeout = connect_timeout
        self.idle_timeout = idle_timeout
        self.write_timeout = write_timeout

    def _forward_data(self, source: socket.socket, destination: socket.socket) -> bool:
        """Forward one chunk, or propagate EOF without closing the reverse stream."""
        chunk = source.recv(self.BUFFER_SIZE)
        if not chunk:
            destination.shutdown(socket.SHUT_WR)
            return False
        destination.sendall(chunk)
        return True

    def _get_target_address(self) -> tuple[str, int]:
        return self.target_host, self.target_port

    @override
    def _process_request(
        self, client_socket: socket.socket, address: tuple[str, int]
    ) -> None:
        target_address = self._get_target_address()
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as target_socket:
            if not self._track_socket(target_socket):
                return
            try:
                target_socket.settimeout(self.connect_timeout)
                target_socket.connect(target_address)
                target_socket.settimeout(self.write_timeout)
                client_socket.settimeout(self.write_timeout)
                readers = [client_socket, target_socket]
                last_activity = time.monotonic()
                while readers and not self._stopping.is_set():
                    remaining = self.idle_timeout - (time.monotonic() - last_activity)
                    if remaining <= 0:
                        return
                    ready, _, _ = select.select(readers, [], [], min(remaining, 0.2))
                    for source in ready:
                        destination = (
                            target_socket if source is client_socket else client_socket
                        )
                        if not self._forward_data(source, destination):
                            readers.remove(source)
                        last_activity = time.monotonic()
            except OSError as exc:
                if not self._stopping.is_set():
                    logger.debug(
                        "Proxy connection to %s:%s ended: %s", *target_address, exc
                    )
            except ValueError:
                # select can observe a descriptor closed concurrently by stop().
                if not self._stopping.is_set():
                    raise
            finally:
                self._untrack_socket(target_socket)
