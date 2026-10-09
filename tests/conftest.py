from contextlib import contextmanager
import threading

import pytest


@pytest.fixture
def server_cleanup():
    servers = []

    def register(server):
        servers.append(server)
        return server

    yield register
    for server in reversed(servers):
        server.stop()


@contextmanager
def running_server(server):
    """Start with explicit readiness and always collect server/worker resources."""
    errors = []

    def serve():
        try:
            server.start()
        except Exception as exc:
            errors.append(exc)
            server.ready.set()

    thread = threading.Thread(target=serve)
    thread.start()
    try:
        assert server.ready.wait(3), "Server did not become ready"
        assert not errors, errors
        yield server
    finally:
        server.stop()
        thread.join(3)
        assert not thread.is_alive(), "Listener thread leaked"
        assert not server._workers, "Worker threads leaked"
        assert not server._connections, "Connection sockets leaked"
        assert server.server_socket.fileno() == -1
        assert not errors, errors
