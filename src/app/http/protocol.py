import re
from http import HTTPStatus

from app.http.models import HTTPRequest, HTTPResponse

TOKEN = re.compile(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+")


class HTTPError(ValueError):
    """A controlled failure in the supported HTTP request contract."""

    def __init__(self, status_code: int = 400) -> None:
        self.status_code = status_code
        self.status_text = HTTPStatus(status_code).phrase
        super().__init__(self.status_text)


def parse_headers(data: bytes) -> HTTPRequest:
    """Validate an ASCII request head before collapsing headers into a mapping."""
    try:
        lines = data.decode("ascii").split("\r\n")
    except UnicodeDecodeError as exc:
        raise HTTPError() from exc
    parts = lines[0].split(" ")
    if len(parts) != 3:
        raise HTTPError()
    method, path, version = parts
    if (
        not TOKEN.fullmatch(method)
        or not path.startswith("/")
        or any(ord(char) <= 32 or ord(char) == 127 for char in path)
        or version not in {"HTTP/1.0", "HTTP/1.1"}
    ):
        raise HTTPError()
    headers: dict[str, str] = {}
    for line in lines[1:]:
        key, separator, value = line.partition(":")
        if not separator or not TOKEN.fullmatch(key):
            raise HTTPError()
        if any(ord(char) < 32 and char != "\t" or ord(char) == 127 for char in value):
            raise HTTPError()
        key = key.lower()
        if key == "content-length" and key in headers:
            raise HTTPError()
        headers[key] = value.strip(" \t")
    if "transfer-encoding" in headers:
        # Reject both unsupported encoding and ambiguous TE/CL framing.
        raise HTTPError()
    content_length(headers)
    return HTTPRequest(method, path, version, headers)


def content_length(headers: dict[str, str]) -> int:
    value = headers.get("content-length", "0")
    if not re.fullmatch(r"[0-9]+", value):
        raise HTTPError()
    significant = value.lstrip("0") or "0"
    if len(significant) > 20:
        raise HTTPError(413)
    return int(significant)


def parse_request(data: bytes) -> HTTPRequest:
    """Parse one complete request; discard bytes beyond its declared body."""
    head, separator, body = data.partition(b"\r\n\r\n")
    if not separator:
        raise HTTPError()
    request = parse_headers(head)
    length = content_length(request.headers)
    if len(body) < length:
        raise HTTPError()
    try:
        request.body = body[:length].decode("utf-8")
    except UnicodeDecodeError as exc:
        raise HTTPError() from exc
    return request


def build_response(response: HTTPResponse) -> bytes:
    """Serialize without mutating the response; framing headers are authoritative."""
    body = response.body.encode("utf-8")
    headers = {
        key: value
        for key, value in response.headers.items()
        if key.lower() not in {"content-length", "connection", "transfer-encoding"}
    }
    headers["Content-Length"] = str(len(body))
    headers["Connection"] = "close"
    status = f"{response.version} {response.status_code} {response.status_text}\r\n"
    head = status + "".join(f"{key}: {value}\r\n" for key, value in headers.items())
    return (head + "\r\n").encode("utf-8") + body
