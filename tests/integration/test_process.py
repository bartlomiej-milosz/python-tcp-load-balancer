import os
from pathlib import Path
import queue
import signal
import socket
import subprocess
import sys
import threading

import pytest

pytestmark = pytest.mark.integration


@pytest.mark.parametrize("termination_signal", [signal.SIGINT, signal.SIGTERM])
def test_demo_process_handles_signal_with_an_active_reader(termination_signal):
    root = Path(__file__).resolve().parents[2]
    environment = {**os.environ, "PYTHONPATH": str(root / "src"), "HOST": "127.0.0.1"}
    process = subprocess.Popen(
        [sys.executable, "-m", "app.run_http_server", "0"],
        cwd=root,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )
    ready = queue.Queue()

    def read_ready_line():
        assert process.stderr is not None
        ready.put(process.stderr.readline().decode())

    reader = threading.Thread(target=read_ready_line)
    reader.start()
    try:
        line = ready.get(timeout=3)
        assert "The server is listening on: 127.0.0.1:" in line, line
        port = int(line.rsplit(":", 1)[1])
        with socket.create_connection(("127.0.0.1", port), timeout=2) as client:
            client.sendall(b"GET /")
            process.send_signal(termination_signal)
            assert process.wait(timeout=3) == 0
            assert client.recv(1024) == b""
        _, logs = process.communicate(timeout=2)
        assert b"Traceback" not in logs
    finally:
        if process.poll() is None:
            process.kill()
        process.wait(timeout=3)
        reader.join(timeout=3)
        assert not reader.is_alive()
        if process.stderr is not None:
            process.stderr.close()
