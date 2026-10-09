import logging
import os

from app.proxy.server import ProxyServer


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s"
    )
    server = ProxyServer(
        host=os.environ.get("HOST", "127.0.0.1"),
        port=8000,
        target_host=os.environ.get("TARGET_HOST", "127.0.0.1"),
        target_port=int(os.environ.get("TARGET_PORT", "8001")),
    )
    server.run()


if __name__ == "__main__":
    main()
