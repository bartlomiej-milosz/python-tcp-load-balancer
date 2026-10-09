import socket
import pytest
from unittest.mock import MagicMock
from pytest_mock import MockerFixture
from app.http.models import HTTPRequest, HTTPResponse
from app.http.server import HTTPServer


@pytest.fixture
def get_http_server():
    server = HTTPServer()
    try:
        yield server
    finally:
        server.server_socket.close()


@pytest.fixture
def mock_client_socket(mocker: MockerFixture) -> MagicMock:
    return mocker.MagicMock(spec=socket.socket)


class TestHTTPServer:
    def test_receive_stops_at_double_crlf(
        self, get_http_server: HTTPServer, mock_client_socket: MagicMock
    ):
        http_server: HTTPServer = get_http_server
        mock_client_socket.recv.side_effect = [
            b"GET / HTTP/1.1\r\n",
            b"Host: localhost",
            b"\r\n\r\n",
        ]
        result: bytes = http_server._receive(mock_client_socket)
        expected_bytes: bytes = b"GET / HTTP/1.1\r\nHost: localhost\r\n\r\n"
        assert result == expected_bytes
        assert mock_client_socket.recv.call_count == 3

    def test_receive_fetches_full_body_based_on_content_length(
        self, get_http_server: HTTPServer, mock_client_socket: MagicMock
    ):
        http_server: HTTPServer = get_http_server
        mock_client_socket.recv.side_effect = [
            b"POST /api HTTP/1.1\r\n",
            b"Content-Length: 10\r\n\r\n",
            b"1234",
            b"5678",
            b"90",
        ]
        result: bytes = http_server._receive(mock_client_socket)
        expected_bytes: bytes = (
            b"POST /api HTTP/1.1\r\nContent-Length: 10\r\n\r\n1234567890"
        )
        assert result == expected_bytes
        assert mock_client_socket.recv.call_count == 5

    def test_add_route_adds_handler_to_routes_dict(self, get_http_server: HTTPServer):
        def dummy_handler(request: HTTPRequest) -> HTTPResponse:
            return HTTPResponse(200, "OK")

        get_http_server.add_route("GET", "/", dummy_handler)

        assert ("GET", "/") in get_http_server.routes
        assert get_http_server.routes[("GET", "/")] == dummy_handler

    def test_process_request_calls_handler_when_route_exists(
        self,
        get_http_server: HTTPServer,
        mock_client_socket: MagicMock,
        mocker: MockerFixture,
    ):
        mocker.patch.object(
            get_http_server, "_receive", return_value=b"GET / HTTP/1.1\r\n\r\n"
        )
        mock_handler = MagicMock()
        mock_handler.return_value = HTTPResponse(200, "OK", "Test body")

        get_http_server.add_route("GET", "/", mock_handler)
        get_http_server._process_request(mock_client_socket, ("127.0.0.1", 54321))

        mock_handler.assert_called_once()
        expected_response = b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 9\r\nConnection: close\r\n\r\nTest body"
        mock_client_socket.sendall.assert_called_once_with(expected_response)

    def test_process_request_returns_404_when_route_missing(
        self,
        get_http_server: HTTPServer,
        mock_client_socket: MagicMock,
        mocker: MockerFixture,
    ):
        mocker.patch.object(
            get_http_server, "_receive", return_value=b"GET /unknown HTTP/1.1\r\n\r\n"
        )
        get_http_server._process_request(mock_client_socket, ("127.0.0.1", 54321))

        expected_response = b"HTTP/1.1 404 Not Found\r\nContent-Type: text/plain\r\nContent-Length: 18\r\nConnection: close\r\n\r\n404 Page Not Found"
        mock_client_socket.sendall.assert_called_once_with(expected_response)

    def test_process_request_returns_400_on_parse_error(
        self,
        get_http_server: HTTPServer,
        mock_client_socket: MagicMock,
        mocker: MockerFixture,
    ):
        mocker.patch.object(
            get_http_server, "_receive", return_value=b"MALFORMED_REQUEST_DATA\r\n\r\n"
        )
        get_http_server._process_request(mock_client_socket, ("127.0.0.1", 54321))

        expected_response = b"HTTP/1.1 400 Bad Request\r\nContent-Type: text/plain\r\nContent-Length: 15\r\nConnection: close\r\n\r\n400 Bad Request"
        mock_client_socket.sendall.assert_called_once_with(expected_response)

    def test_process_request_returns_500_on_handler_error(
        self,
        get_http_server: HTTPServer,
        mock_client_socket: MagicMock,
        mocker: MockerFixture,
    ):
        mocker.patch.object(
            get_http_server, "_receive", return_value=b"GET / HTTP/1.1\r\n\r\n"
        )

        def broken_handler(request: HTTPRequest) -> HTTPResponse:
            raise ValueError("Something went wrong!")

        get_http_server.add_route("GET", "/", broken_handler)
        get_http_server._process_request(mock_client_socket, ("127.0.0.1", 54321))

        expected_response = b"HTTP/1.1 500 Internal Server Error\r\nContent-Type: text/plain\r\nContent-Length: 25\r\nConnection: close\r\n\r\n500 Internal Server Error"
        mock_client_socket.sendall.assert_called_once_with(expected_response)


@pytest.mark.parametrize(
    "chunks, status",
    [
        ([b"GET / HTTP/1.1\r\n", b""], 400),
        ([b"POST / HTTP/1.1\r\nContent-Length: 3\r\n\r\na", b""], 400),
        ([b"GET / HTTP/1.1\r\nX: " + b"a" * 100], 431),
        ([b"POST / HTTP/1.1\r\nContent-Length: 11\r\n\r\n"], 413),
        ([TimeoutError()], 408),
    ],
)
def test_read_failures_produce_distinct_responses(chunks, status, mock_client_socket):
    server = HTTPServer(max_header_bytes=64, max_body_bytes=10)
    try:
        mock_client_socket.recv.side_effect = chunks
        server._process_request(mock_client_socket, ("127.0.0.1", 1))
        assert mock_client_socket.sendall.call_args.args[0].startswith(
            f"HTTP/1.1 {status} ".encode()
        )
    finally:
        server.server_socket.close()


def test_receive_discards_excess_body(get_http_server, mock_client_socket):
    mock_client_socket.recv.return_value = (
        b"POST / HTTP/1.1\r\nContent-Length: 2\r\n\r\nabc"
    )
    assert get_http_server._receive(mock_client_socket).endswith(b"\r\n\r\nab")


def test_request_deadline_is_not_reset_by_progress(
    get_http_server, mock_client_socket, mocker
):
    mocker.patch("app.http.server.time.monotonic", side_effect=[0, 1, 9, 10])
    mock_client_socket.recv.side_effect = [b"G", b"E"]
    with pytest.raises(TimeoutError):
        get_http_server._receive(mock_client_socket)
    assert [call.args[0] for call in mock_client_socket.settimeout.call_args_list] == [
        9,
        1,
    ]


def test_closed_client_during_response_is_controlled(
    get_http_server, mock_client_socket
):
    mock_client_socket.recv.return_value = b"GET / HTTP/1.1\r\n\r\n"
    mock_client_socket.sendall.side_effect = BrokenPipeError()
    get_http_server._process_request(mock_client_socket, ("127.0.0.1", 1))
