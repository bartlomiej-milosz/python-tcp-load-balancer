import json

import pytest

from app.http.protocol import parse_request
from app.run_http_server import api_echo, api_users, get_api_status
from app.run_load_balancer import parse_backends


def test_status_reports_distinct_identity_even_on_the_same_port():
    request = parse_request(b"GET /api/status HTTP/1.1\r\n\r\n")
    first = json.loads(get_api_status(8080, "backend-1")(request).body)
    second = json.loads(get_api_status(8080, "backend-2")(request).body)
    assert first["backend_id"] == "backend-1"
    assert second["backend_id"] == "backend-2"
    assert first["status"] == "ok"
    assert json.loads(api_users(request).body)["users"] == ["kate", "admin", "guest"]


@pytest.mark.parametrize("header", [b"Content-Type", b"content-type", b"CONTENT-TYPE"])
def test_demo_echo_preserves_utf8_and_content_type(header):
    body = "Hello 🌍".encode()
    request = parse_request(
        b"POST /api/echo HTTP/1.1\r\n"
        + header
        + b": text/plain; charset=utf-8\r\nContent-Length: 10\r\n\r\n"
        + body
    )
    response = api_echo(request)
    assert response.body.encode() == body
    assert response.headers["Content-Type"] == "text/plain; charset=utf-8"


@pytest.mark.parametrize(
    "value", ["", "host", "host:bad", "host:-1", "host:+80", "host:80,", "::1:80"]
)
def test_backend_environment_syntax_is_validated(value):
    with pytest.raises(ValueError, match="BACKEND_SERVERS"):
        parse_backends(value)


def test_backend_environment_accepts_comma_separated_addresses():
    assert parse_backends("first:8080, second:8081") == [
        ("first", 8080),
        ("second", 8081),
    ]
