import socket
import pytest
from unittest.mock import MagicMock
from pytest_mock import MockerFixture
from app.proxy.server import ProxyServer


@pytest.fixture
def get_proxy_server():
    server = ProxyServer(target_host="127.0.0.1", target_port=8080)
    try:
        yield server
    finally:
        server.stop()


@pytest.fixture
def mock_client_socket(mocker: MockerFixture) -> MagicMock:
    return mocker.MagicMock(spec=socket.socket)


class TestProxyServer:
    def test_forward_data_returns_true_when_chunk_received(
        self,
        get_proxy_server: ProxyServer,
        mock_client_socket: MagicMock,
        mocker: MockerFixture,
    ):
        mock_dest = mocker.MagicMock(spec=socket.socket)
        mock_client_socket.recv.return_value = b"Hello"

        result = get_proxy_server._forward_data(mock_client_socket, mock_dest)

        assert result is True
        mock_dest.sendall.assert_called_once_with(b"Hello")

    def test_forward_data_returns_false_when_connection_closed(
        self,
        get_proxy_server: ProxyServer,
        mock_client_socket: MagicMock,
        mocker: MockerFixture,
    ):
        mock_dest = mocker.MagicMock(spec=socket.socket)
        mock_client_socket.recv.return_value = b""

        result = get_proxy_server._forward_data(mock_client_socket, mock_dest)

        assert result is False
        mock_dest.sendall.assert_not_called()
        mock_dest.shutdown.assert_called_once_with(socket.SHUT_WR)

    def test_process_request_handles_connection_refused_gracefully(
        self,
        get_proxy_server: ProxyServer,
        mock_client_socket: MagicMock,
        mocker: MockerFixture,
    ):
        mock_socket_class = mocker.patch("app.proxy.server.socket.socket")
        mock_target_socket = mock_socket_class.return_value.__enter__.return_value
        mock_target_socket.connect.side_effect = ConnectionRefusedError()

        mock_logger = mocker.patch("app.proxy.server.logger")
        mock_select = mocker.patch("app.proxy.server.select.select")

        get_proxy_server._process_request(mock_client_socket, ("127.0.0.1", 54321))

        mock_logger.debug.assert_called_once()
        mock_client_socket.sendall.assert_not_called()
        mock_select.assert_not_called()


@pytest.mark.parametrize(
    "error", [TimeoutError(), ConnectionResetError(), BrokenPipeError()]
)
def test_forwarding_network_errors_end_connection(
    get_proxy_server, mock_client_socket, mocker, error
):
    socket_class = mocker.patch("app.proxy.server.socket.socket")
    target = socket_class.return_value.__enter__.return_value
    mocker.patch(
        "app.proxy.server.select.select", return_value=([mock_client_socket], [], [])
    )
    mock_client_socket.recv.return_value = b"bytes"
    target.sendall.side_effect = error
    get_proxy_server._process_request(mock_client_socket, ("127.0.0.1", 1))
    target.settimeout.assert_any_call(get_proxy_server.connect_timeout)
    target.settimeout.assert_any_call(get_proxy_server.write_timeout)
    assert not get_proxy_server._connections


def test_backend_connect_timeout_is_controlled(
    get_proxy_server, mock_client_socket, mocker
):
    socket_class = mocker.patch("app.proxy.server.socket.socket")
    target = socket_class.return_value.__enter__.return_value
    target.connect.side_effect = TimeoutError()
    get_proxy_server._process_request(mock_client_socket, ("127.0.0.1", 1))
    mock_client_socket.sendall.assert_not_called()
    assert not get_proxy_server._connections
