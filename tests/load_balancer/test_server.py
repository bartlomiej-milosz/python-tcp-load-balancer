import threading
import pytest

from app.load_balancer.server import LoadBalancerServer


def test_load_balancer_round_robin(server_cleanup):
    backends = [("127.0.0.1", 8081), ("127.0.0.1", 8082), ("127.0.0.1", 8083)]
    lb = server_cleanup(LoadBalancerServer(backend_servers=backends))
    assert lb._get_target_address() == ("127.0.0.1", 8081)
    assert lb._get_target_address() == ("127.0.0.1", 8082)
    assert lb._get_target_address() == ("127.0.0.1", 8083)
    assert lb._get_target_address() == ("127.0.0.1", 8081)
    assert lb._get_target_address() == ("127.0.0.1", 8082)


def test_load_balancer_empty_backends():
    with pytest.raises(ValueError, match="No backend servers available in the pool."):
        LoadBalancerServer(backend_servers=[])


def test_load_balancer_thread_safety(server_cleanup):
    backends = [("127.0.0.1", 8081), ("127.0.0.1", 8082), ("127.0.0.1", 8083)]
    lb = server_cleanup(LoadBalancerServer(backend_servers=backends))
    results = []

    def worker():
        # Each worker makes 1 request
        target = lb._get_target_address()
        results.append(target)

    threads = []
    for _ in range(300):
        t = threading.Thread(target=worker)
        threads.append(t)
        t.start()
    for t in threads:
        t.join(timeout=2)
        assert not t.is_alive()

    assert len(results) == 300
    assert results.count(("127.0.0.1", 8081)) == 100
    assert results.count(("127.0.0.1", 8082)) == 100
    assert results.count(("127.0.0.1", 8083)) == 100


@pytest.mark.parametrize(
    "backends",
    [
        [("", 80)],
        [("host", 0)],
        [("host", 65536)],
        [("host", True)],
        [(" host", 80)],
        [("host", "80")],
        [("host",)],
        ["host:80"],
    ],
)
def test_invalid_backend_configuration_is_rejected(backends):
    with pytest.raises(ValueError):
        LoadBalancerServer(backend_servers=backends)


def test_backend_configuration_is_copied(server_cleanup):
    backends = [("127.0.0.1", 8081)]
    lb = server_cleanup(LoadBalancerServer(backend_servers=backends))
    backends.clear()
    assert lb._get_target_address() == ("127.0.0.1", 8081)
