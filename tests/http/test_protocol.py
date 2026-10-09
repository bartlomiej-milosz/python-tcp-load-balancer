import pytest
from app.http.models import HTTPRequest, HTTPResponse
from app.http.protocol import build_response, parse_request


@pytest.mark.parametrize(
    "raw_data, expected_method, expected_path, expected_headers, expected_body",
    [
        (
            b"GET /home HTTP/1.1\r\nHost: localhost\r\n\r\n",
            "GET",
            "/home",
            {"host": "localhost"},
            "",
        ),
        (
            b'POST /api HTTP/1.1\r\nHost: example.com\r\nAccept: */*\r\nContent-Length: 15\r\n\r\n{"key":"value"}',
            "POST",
            "/api",
            {"host": "example.com", "accept": "*/*", "content-length": "15"},
            '{"key":"value"}',
        ),
        (b"DELETE /old-file HTTP/1.0\r\n\r\n", "DELETE", "/old-file", {}, ""),
    ],
)
def test_parse_request_returns_valid_request_object(
    raw_data: bytes,
    expected_method: str,
    expected_path: str,
    expected_headers: dict[str, str],
    expected_body: str,
):
    request: HTTPRequest = parse_request(raw_data)
    assert request.method == expected_method
    assert request.path == expected_path
    assert request.headers == expected_headers
    assert request.body == expected_body


@pytest.mark.parametrize(
    "response_obj, expected_bytes",
    [
        (
            HTTPResponse(status_code=200, status_text="OK"),
            b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
        ),
        (
            HTTPResponse(status_code=404, status_text="Not Found", body="Error!"),
            b"HTTP/1.1 404 Not Found\r\nContent-Type: text/plain\r\nContent-Length: 6\r\nConnection: close\r\n\r\nError!",
        ),
        (
            HTTPResponse(
                status_code=200,
                status_text="OK",
                body='{"user": "kate"}',
                headers={"Content-Type": "application/json"},
            ),
            b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 16\r\nConnection: close\r\n\r\n{"user": "kate"}',
        ),
    ],
)
def test_build_response_serializes_correctly(
    response_obj: HTTPResponse, expected_bytes: bytes
):
    result: bytes = build_response(response_obj)
    assert result == expected_bytes


def test_utf8_response_framing_is_authoritative_and_does_not_mutate():
    response = HTTPResponse(
        200,
        "OK",
        "Zażółć 🌍",
        {
            "content-length": "1",
            "CONTENT-LENGTH": "2",
            "connection": "keep-alive",
            "Transfer-Encoding": "chunked",
        },
    )
    original = response.headers.copy()
    head, body = build_response(response).split(b"\r\n\r\n", 1)
    assert body == response.body.encode("utf-8")
    assert head.count(b"Content-Length:") == 1
    assert f"Content-Length: {len(body)}".encode() in head
    assert b"Connection: close" in head
    assert b"chunked" not in head
    assert response.headers == original


@pytest.mark.parametrize(
    "head",
    [
        b"GET / HTTP/1.1",
        b"GET / HTTP/1.1\r\nMissing-Colon",
        b"GET / HTTP/1.1\r\nBad Name: x",
        b"GET / HTTP/1.1\r\nHost : x",
        b"GET / HTTP/1.1\r\n Host: x",
        b"GET / HTTP/1.1\r\nHost: x\x00",
        b"GET / HTTP/2",
        b"GET  / HTTP/1.1",
        b"GET / HTTP/1.1 extra",
        b"GET / HTTP/1.1\r\nContent-Length: -1",
        b"GET / HTTP/1.1\r\nContent-Length: +1",
        b"GET / HTTP/1.1\r\nContent-Length: 1.0",
        b"GET / HTTP/1.1\r\nContent-Length: ",
        b"GET / HTTP/1.1\r\nContent-Length: 0\r\ncontent-length: 0",
        b"GET / HTTP/1.1\r\nTransfer-Encoding: chunked",
        b"GET / HTTP/1.1\r\nTransfer-Encoding: gzip\r\nContent-Length: 0",
    ],
)
def test_rejects_malformed_or_unsupported_requests(head):
    from app.http.protocol import HTTPError

    # The first case specifically checks a missing separator.
    raw = head if head == b"GET / HTTP/1.1" else head + b"\r\n\r\n"
    with pytest.raises(HTTPError):
        parse_request(raw)


@pytest.mark.parametrize("body", [b"ab", b"\xffab"])
def test_rejects_truncated_or_invalid_utf8_body(body):
    from app.http.protocol import HTTPError

    with pytest.raises(HTTPError):
        parse_request(b"POST / HTTP/1.1\r\nContent-Length: 3\r\n\r\n" + body)


@pytest.mark.parametrize(
    "length, expected", [(b"", ""), (b"content-LENGTH: 2\r\n", "ab")]
)
def test_body_uses_only_declared_bytes(length, expected):
    request = parse_request(b"POST / HTTP/1.1\r\n" + length + b"\r\nabcdef")
    assert request.body == expected
