import logging
import socket
import time
from typing import Callable, override

from app.http.models import HTTPRequest, HTTPResponse
from app.http.protocol import (
    HTTPError,
    build_response,
    content_length,
    parse_headers,
    parse_request,
)
from app.tcp.server import TCPServer

logger = logging.getLogger(__name__)


class HTTPServer(TCPServer):
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8080,
        *,
        max_header_bytes: int = 16 * 1024,
        max_body_bytes: int = 1024 * 1024,
        request_timeout: float = 10.0,
        write_timeout: float = 5.0,
    ) -> None:
        if max_header_bytes < 4 or max_body_bytes < 0:
            raise ValueError("Invalid HTTP size limits")
        if request_timeout <= 0 or write_timeout <= 0:
            raise ValueError("HTTP timeouts must be positive")
        super().__init__(host, port)
        self.max_header_bytes = max_header_bytes
        self.max_body_bytes = max_body_bytes
        self.request_timeout = request_timeout
        self.write_timeout = write_timeout
        self.routes: dict[tuple[str, str], Callable[[HTTPRequest], HTTPResponse]] = {}

    def add_route(
        self, method: str, path: str, handler: Callable[[HTTPRequest], HTTPResponse]
    ) -> None:
        """Register routes before starting the server."""
        self.routes[(method, path)] = handler

    def _receive(self, client_socket: socket.socket) -> bytes:
        """Read bounded framing under one deadline, including fragmented bodies."""
        deadline = time.monotonic() + self.request_timeout

        def receive(size: int) -> bytes:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Request deadline exceeded")
            client_socket.settimeout(remaining)
            chunk = client_socket.recv(size)
            if not chunk:
                raise HTTPError()
            return chunk

        data = bytearray()
        while b"\r\n\r\n" not in data:
            if len(data) >= self.max_header_bytes:
                raise HTTPError(431)
            data.extend(
                receive(min(self.BUFFER_SIZE, self.max_header_bytes - len(data)))
            )
        head, _, initial_body = bytes(data).partition(b"\r\n\r\n")
        length = content_length(parse_headers(head).headers)
        if length > self.max_body_bytes:
            raise HTTPError(413)
        body = bytearray(initial_body[:length])
        while len(body) < length:
            body.extend(receive(min(self.BUFFER_SIZE, length - len(body))))
        return head + b"\r\n\r\n" + bytes(body)

    def _send_response(
        self, client_socket: socket.socket, response: HTTPResponse
    ) -> None:
        client_socket.settimeout(self.write_timeout)
        client_socket.sendall(build_response(response))

    def _dispatch_request(self, request: HTTPRequest) -> HTTPResponse:
        handler = self.routes.get((request.method, request.path))
        if handler is None:
            return HTTPResponse(404, "Not Found", "404 Page Not Found")
        try:
            return handler(request)
        except Exception:
            # Application handlers are the deliberate exception boundary.
            logger.exception("Handler error on %s", request.path)
            return HTTPResponse(
                500, "Internal Server Error", "500 Internal Server Error"
            )

    @override
    def _process_request(
        self, client_socket: socket.socket, address: tuple[str, int]
    ) -> None:
        try:
            request = parse_request(self._receive(client_socket))
        except HTTPError as exc:
            response = HTTPResponse(
                exc.status_code, exc.status_text, f"{exc.status_code} {exc.status_text}"
            )
        except TimeoutError:
            response = HTTPResponse(408, "Request Timeout", "408 Request Timeout")
        else:
            logger.info(
                "Received %s %s from %s:%s", request.method, request.path, *address
            )
            response = self._dispatch_request(request)
        try:
            self._send_response(client_socket, response)
        except OSError:
            logger.debug("Client %s:%s closed before response completed", *address)
