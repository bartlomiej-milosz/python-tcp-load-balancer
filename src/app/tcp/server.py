import logging
import signal
import socket
import threading
import time
from abc import ABC, abstractmethod
from typing import Final

logger = logging.getLogger(__name__)


class TCPServer(ABC):
    BACKLOG: Final = 5
    BUFFER_SIZE: Final = 1024

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8080,
        *,
        shutdown_timeout: float = 5.0,
    ) -> None:
        if shutdown_timeout <= 0:
            raise ValueError("Shutdown timeout must be positive")
        self.host = host
        self.port = port
        self.shutdown_timeout = shutdown_timeout
        self.ready = threading.Event()
        self._stopping = threading.Event()
        self._lifecycle_lock = threading.Lock()
        self._connections: set[socket.socket] = set()
        self._workers: set[threading.Thread] = set()
        self._serve_thread: threading.Thread | None = None
        self.server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            self.server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        except OSError:
            self.server_socket.close()
            raise

    @abstractmethod
    def _process_request(
        self, client_socket: socket.socket, address: tuple[str, int]
    ) -> None:
        """Handle one connection in a worker thread."""
        ...

    @staticmethod
    def _close_socket(connection: socket.socket) -> None:
        try:
            connection.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        connection.close()

    def _track_socket(self, connection: socket.socket) -> bool:
        with self._lifecycle_lock:
            if self._stopping.is_set():
                self._close_socket(connection)
                return False
            self._connections.add(connection)
            return True

    def _untrack_socket(self, connection: socket.socket) -> None:
        with self._lifecycle_lock:
            self._connections.discard(connection)

    def _handle_client(
        self, client_socket: socket.socket, address: tuple[str, int]
    ) -> None:
        try:
            self._process_request(client_socket, address)
        except OSError as exc:
            if not self._stopping.is_set():
                logger.debug("Connection %s:%s ended: %s", *address, exc)
        finally:
            self._close_socket(client_socket)
            with self._lifecycle_lock:
                self._connections.discard(client_socket)
                self._workers.discard(threading.current_thread())

    def start(self) -> None:
        """Serve until stop; a stopped instance cannot be restarted."""
        self._serve_thread = threading.current_thread()
        try:
            if self._stopping.is_set():
                return
            self.server_socket.bind((self.host, self.port))
            self.server_socket.listen(self.BACKLOG)
            self.port = self.server_socket.getsockname()[1]
            self.server_socket.settimeout(0.2)
            self.ready.set()
            logger.info("The server is listening on: %s:%s", self.host, self.port)
            while not self._stopping.is_set():
                try:
                    client_socket, address = self.server_socket.accept()
                except TimeoutError:
                    continue
                except OSError:
                    if self._stopping.is_set():
                        break
                    raise
                with self._lifecycle_lock:
                    if self._stopping.is_set():
                        self._close_socket(client_socket)
                        break
                    self._connections.add(client_socket)
                    worker = threading.Thread(
                        target=self._handle_client, args=(client_socket, address)
                    )
                    self._workers.add(worker)
                    try:
                        worker.start()
                    except RuntimeError:
                        self._workers.discard(worker)
                        self._connections.discard(client_socket)
                        self._close_socket(client_socket)
                        raise
        except KeyboardInterrupt:
            logger.info("Server shutting down")
        finally:
            self.stop()

    def stop(self) -> None:
        """Idempotently close listener and streams, then join within one budget."""
        deadline = time.monotonic() + self.shutdown_timeout
        self._stopping.set()
        self._close_socket(self.server_socket)
        with self._lifecycle_lock:
            connections = tuple(self._connections)
            workers = tuple(self._workers)
        for connection in connections:
            self._close_socket(connection)
        current = threading.current_thread()
        for worker in workers:
            if worker is not current:
                worker.join(max(0, deadline - time.monotonic()))
        if self._serve_thread is not None and self._serve_thread is not current:
            self._serve_thread.join(max(0, deadline - time.monotonic()))
        if any(worker.is_alive() and worker is not current for worker in workers):
            logger.warning("Shutdown deadline exceeded; a handler is still running")

    def run(self) -> None:
        """Run from the main thread, with orderly SIGINT/SIGTERM shutdown."""

        def request_stop(signum, frame):
            # Signal handlers must not acquire a lock held by interrupted code.
            self._stopping.set()

        previous = {
            sig: signal.signal(sig, request_stop)
            for sig in (signal.SIGINT, signal.SIGTERM)
        }
        try:
            self.start()
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
