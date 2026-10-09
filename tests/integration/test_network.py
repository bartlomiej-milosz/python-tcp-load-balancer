import socket
import threading
from contextlib import ExitStack

import pytest

from app.http.models import HTTPResponse
from app.http.server import HTTPServer
from app.load_balancer.server import LoadBalancerServer
from app.proxy.server import ProxyServer
from app.tcp.server import TCPServer
from tests.conftest import running_server

pytestmark = pytest.mark.integration


def connect(server):
    return socket.create_connection(("127.0.0.1", server.port), timeout=2)


def read_all(connection):
    chunks = []
    while chunk := connection.recv(4096):
        chunks.append(chunk)
    return b"".join(chunks)


class EchoAfterEOF(TCPServer):
    def __init__(self):
        super().__init__(port=0)
        self.accepted = threading.Event()

    def _process_request(self, client_socket, address):
        self.accepted.set()
        client_socket.settimeout(2)
        body = read_all(client_socket)
        client_socket.sendall(body)


@pytest.mark.parametrize(
    "payload",
    [
        b"request and response",
        b"x" * (TCPServer.BUFFER_SIZE * 33 + 17),
        bytes(range(256)) * 129,
    ],
    ids=["text", "larger-than-buffer", "binary"],
)
def test_proxy_preserves_bytes_and_response_after_client_half_close(payload):
    with running_server(EchoAfterEOF()) as backend:
        proxy = ProxyServer(port=0, target_port=backend.port)
        with running_server(proxy), connect(proxy) as client:
            client.sendall(payload)
            client.shutdown(socket.SHUT_WR)
            assert read_all(client) == payload


def test_http_request_and_utf8_response_through_proxy():
    http = HTTPServer(port=0)
    http.add_route(
        "POST", "/echo", lambda request: HTTPResponse(200, "OK", request.body)
    )
    with running_server(http):
        proxy = ProxyServer(port=0, target_port=http.port)
        with running_server(proxy), connect(proxy) as client:
            body = "Hello 🌍".encode()
            client.sendall(
                f"POST /echo HTTP/1.1\r\ncontent-length: {len(body)}\r\n\r\n".encode()
                + body
            )
            head, returned = read_all(client).split(b"\r\n\r\n", 1)
            assert b"200 OK" in head
            assert f"Content-Length: {len(body)}".encode() in head
            assert returned == body


def test_round_robin_routes_each_connection_to_a_distinct_backend():
    with ExitStack() as stack:
        backends = []
        for identity in ("one", "two", "three"):
            server = HTTPServer(port=0)
            server.add_route(
                "GET", "/", lambda request, name=identity: HTTPResponse(200, "OK", name)
            )
            stack.enter_context(running_server(server))
            backends.append(("127.0.0.1", server.port))
        lb = stack.enter_context(
            running_server(LoadBalancerServer(port=0, backend_servers=backends))
        )
        received = []
        for _ in range(6):
            with connect(lb) as client:
                client.sendall(b"GET / HTTP/1.1\r\n\r\n")
                received.append(read_all(client).split(b"\r\n\r\n", 1)[1])
        assert received == [b"one", b"two", b"three"] * 2


def test_unavailable_backend_closes_connection_without_http_response():
    # Reserve an ephemeral port without listening, avoiding a port allocation race.
    with socket.socket() as reserved:
        reserved.bind(("127.0.0.1", 0))
        proxy = ProxyServer(
            port=0, target_port=reserved.getsockname()[1], connect_timeout=0.1
        )
        with running_server(proxy), connect(proxy) as client:
            assert client.recv(1024) == b""


def test_idle_proxy_connection_expires():
    with running_server(EchoAfterEOF()) as backend:
        proxy = ProxyServer(port=0, target_port=backend.port, idle_timeout=0.1)
        with running_server(proxy), connect(proxy) as client:
            assert backend.accepted.wait(2)
            assert client.recv(1024) == b""


@pytest.mark.parametrize("payload", [b"", b"partial request"])
def test_stop_closes_active_proxy_streams_and_workers(payload):
    with running_server(EchoAfterEOF()) as backend:
        proxy = ProxyServer(port=0, target_port=backend.port)
        with running_server(proxy), connect(proxy) as client:
            if payload:
                client.sendall(payload)
            assert backend.accepted.wait(2)
            proxy.stop()
            proxy.stop()
            assert client.recv(1024) == b""
            assert not proxy._workers


def test_stop_interrupts_http_reader(mocker):
    http = HTTPServer(port=0, request_timeout=60)
    entered = threading.Event()
    original = http._receive

    def receive(client):
        entered.set()
        return original(client)

    mocker.patch.object(http, "_receive", side_effect=receive)
    with running_server(http), connect(http) as client:
        client.sendall(b"GET /")
        assert entered.wait(2)
        http.stop()
        assert client.recv(1024) == b""


def test_backend_half_close_keeps_client_to_backend_stream_open():
    received = []
    complete = threading.Event()

    class HalfClosedBackend(TCPServer):
        def _process_request(self, client_socket, address):
            client_socket.settimeout(2)
            client_socket.sendall(b"greeting")
            client_socket.shutdown(socket.SHUT_WR)
            received.append(read_all(client_socket))
            complete.set()

    with running_server(HalfClosedBackend(port=0)) as backend:
        proxy = ProxyServer(port=0, target_port=backend.port)
        with running_server(proxy), connect(proxy) as client:
            assert read_all(client) == b"greeting"
            client.sendall(b"after backend EOF")
            client.shutdown(socket.SHUT_WR)
            assert complete.wait(2)
            assert received == [b"after backend EOF"]


def test_fragmented_http_body_and_single_response_contract():
    http = HTTPServer(port=0)
    http.add_route("POST", "/", lambda request: HTTPResponse(200, "OK", request.body))
    with running_server(http), connect(http) as client:
        for fragment in [
            b"POST / HTTP/1.1\r",
            b"\nContent-Length: 4\r\n\r",
            b"\n\xf0\x9f",
            b"\x8c\x8dGET / HTTP/1.1\r\n\r\n",
        ]:
            client.sendall(fragment)
        response = read_all(client)
        assert response.count(b"HTTP/1.1") == 1
        assert response.endswith("🌍".encode())


def test_http_request_timeout_returns_408():
    with running_server(HTTPServer(port=0, request_timeout=0.1)) as server:
        with connect(server) as client:
            client.sendall(b"G")
            assert read_all(client).startswith(b"HTTP/1.1 408 Request Timeout")
