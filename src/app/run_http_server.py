import json
import logging
import os
import sys
from typing import Callable

from app.http.models import HTTPRequest, HTTPResponse
from app.http.server import HTTPServer


def get_api_status(
    port: int, backend_id: str | None = None
) -> Callable[[HTTPRequest], HTTPResponse]:
    def api_status(request: HTTPRequest) -> HTTPResponse:
        data = {
            "status": "ok",
            "version": "1.0",
            "backend_id": backend_id if backend_id is not None else f"backend-{port}",
            "message": f"Backend API is running on port {port}",
        }
        return HTTPResponse(
            200, "OK", json.dumps(data), {"Content-Type": "application/json"}
        )

    return api_status


def api_users(request: HTTPRequest) -> HTTPResponse:
    """Return the original demonstration users."""
    return HTTPResponse(
        200,
        "OK",
        json.dumps({"users": ["kate", "admin", "guest"]}),
        {"Content-Type": "application/json"},
    )


def api_echo(request: HTTPRequest) -> HTTPResponse:
    """Echo a UTF-8 text body with the normalized request content type."""
    return HTTPResponse(
        200,
        "OK",
        request.body,
        {
            "Content-Type": request.headers.get(
                "content-type", "text/plain; charset=utf-8"
            )
        },
    )


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8080
    server = HTTPServer(host=os.environ.get("HOST", "127.0.0.1"), port=port)
    server.add_route(
        "GET", "/api/status", get_api_status(port, os.environ.get("BACKEND_ID"))
    )
    server.add_route("GET", "/api/users", api_users)
    server.add_route("POST", "/api/echo", api_echo)
    server.run()


if __name__ == "__main__":
    main()
