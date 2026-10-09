import logging
import os

from app.load_balancer.server import LoadBalancerServer


def parse_backends(value: str) -> list[tuple[str, int]]:
    """Parse the demo's IPv4/hostname host:port configuration."""
    try:
        backends = []
        for entry in value.split(","):
            host, port = entry.strip().split(":")
            if not port.isascii() or not port.isdecimal():
                raise ValueError("Port must be decimal")
            backends.append((host, int(port)))
        return backends
    except ValueError as exc:
        raise ValueError(
            "BACKEND_SERVERS must contain comma-separated host:port entries"
        ) from exc


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )
    try:
        backends = parse_backends(
            os.environ.get(
                "BACKEND_SERVERS", "127.0.0.1:8081,127.0.0.1:8082,127.0.0.1:8083"
            )
        )
        server = LoadBalancerServer(
            host=os.environ.get("HOST", "127.0.0.1"),
            port=8001,
            backend_servers=backends,
        )
    except ValueError as exc:
        raise SystemExit(f"Invalid backend configuration: {exc}") from exc
    server.run()


if __name__ == "__main__":
    main()
