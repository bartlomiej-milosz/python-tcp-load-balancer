import logging
import threading
from typing import override

from app.proxy.server import ProxyServer

logger = logging.getLogger(__name__)


class LoadBalancerServer(ProxyServer):
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8001,
        backend_servers: list[tuple[str, int]] | None = None,
        *,
        connect_timeout: float = 3.0,
        idle_timeout: float = 30.0,
        write_timeout: float = 5.0,
    ) -> None:
        backends = (
            backend_servers
            if backend_servers is not None
            else [("127.0.0.1", 8081), ("127.0.0.1", 8082)]
        )
        if not backends:
            raise ValueError("No backend servers available in the pool.")
        for backend in backends:
            if (
                not isinstance(backend, tuple)
                or len(backend) != 2
                or not isinstance(backend[0], str)
                or not backend[0].strip()
                or any(
                    char.isspace() or ord(char) < 32 or ord(char) == 127
                    for char in backend[0]
                )
                or not isinstance(backend[1], int)
                or isinstance(backend[1], bool)
                or not 1 <= backend[1] <= 65535
            ):
                raise ValueError(
                    "Each backend must be a (host, port) tuple with port 1..65535"
                )
        super().__init__(
            host,
            port,
            target_host="",
            target_port=0,
            connect_timeout=connect_timeout,
            idle_timeout=idle_timeout,
            write_timeout=write_timeout,
        )
        self.backend_servers = tuple(backends)
        self.current_backend_index = 0
        self._lock = threading.Lock()

    @override
    def _get_target_address(self) -> tuple[str, int]:
        """Advance the cursor once per connection; network work stays outside the lock."""
        with self._lock:
            backend = self.backend_servers[self.current_backend_index]
            self.current_backend_index = (self.current_backend_index + 1) % len(
                self.backend_servers
            )
        logger.info("Routing connection to backend: %s:%s", *backend)
        return backend
