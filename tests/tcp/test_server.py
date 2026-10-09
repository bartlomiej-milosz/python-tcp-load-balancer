import socket
from unittest.mock import MagicMock

import pytest

from app.tcp.server import TCPServer


class ConcreteTCPServer(TCPServer):
    def _process_request(self, client_socket, address):
        pass


@pytest.fixture
def get_tcp_server():
    server = ConcreteTCPServer()
    try:
        yield server
    finally:
        server.stop()


@pytest.mark.parametrize(
    "error", [None, ConnectionResetError(), BrokenPipeError(), TimeoutError()]
)
def test_handle_client_always_closes_socket(get_tcp_server, mocker, error):
    client = MagicMock(spec=socket.socket)
    mocker.patch.object(get_tcp_server, "_process_request", side_effect=error)
    get_tcp_server._handle_client(client, ("127.0.0.1", 54321))
    client.close.assert_called_once()


def test_unexpected_programming_error_is_not_hidden(get_tcp_server, mocker):
    client = MagicMock(spec=socket.socket)
    mocker.patch.object(
        get_tcp_server, "_process_request", side_effect=ValueError("bug")
    )
    with pytest.raises(ValueError, match="bug"):
        get_tcp_server._handle_client(client, ("127.0.0.1", 1))
    client.close.assert_called_once()


def test_start_binds_listens_accepts_and_cleans_up(get_tcp_server, mocker):
    get_tcp_server.server_socket.close()
    listener = MagicMock(spec=socket.socket)
    listener.getsockname.return_value = ("127.0.0.1", 8080)
    client = MagicMock(spec=socket.socket)
    listener.accept.side_effect = [(client, ("127.0.0.1", 54321)), KeyboardInterrupt()]
    get_tcp_server.server_socket = listener
    process = mocker.patch.object(get_tcp_server, "_process_request")
    get_tcp_server.start()
    listener.bind.assert_called_once_with(("127.0.0.1", 8080))
    listener.listen.assert_called_once_with(TCPServer.BACKLOG)
    assert get_tcp_server.ready.is_set()
    process.assert_called_once_with(client, ("127.0.0.1", 54321))
    assert not get_tcp_server._workers
    assert not get_tcp_server._connections
    listener.close.assert_called_once()


@pytest.mark.parametrize("operation", ["bind", "listen"])
def test_start_failure_closes_listener(get_tcp_server, operation):
    get_tcp_server.server_socket.close()
    listener = MagicMock(spec=socket.socket)
    getattr(listener, operation).side_effect = OSError("startup failure")
    get_tcp_server.server_socket = listener
    with pytest.raises(OSError, match="startup failure"):
        get_tcp_server.start()
    listener.close.assert_called_once()
    assert not get_tcp_server.ready.is_set()


def test_stop_is_safe_before_start_and_on_repeated_calls(get_tcp_server):
    get_tcp_server.stop()
    get_tcp_server.stop()
    get_tcp_server.start()
    assert get_tcp_server.server_socket.fileno() == -1
    assert not get_tcp_server.ready.is_set()
