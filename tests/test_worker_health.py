"""ai-worker health endpoint testleri (ADR-007 §9.2, §29-31).

Gercek HTTP server uzerinden calisir (RabbitMQ gerektirmez); WorkerState
elle set/unset edilerek readiness gecisleri dogrulanir.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

from app.common import metrics
from app.worker_health import WorkerState, start_health_server


@pytest.fixture
def server():
    state = WorkerState()
    srv = start_health_server(state, host="127.0.0.1", port=0)
    yield srv, state
    srv.shutdown()


def _get(port: int, path: str) -> tuple[int, dict | None]:
    url = f"http://127.0.0.1:{port}{path}"
    try:
        with urllib.request.urlopen(url) as response:
            status, body = response.status, response.read()
    except urllib.error.HTTPError as exc:
        status, body = exc.code, exc.read()
    return status, json.loads(body) if body else None


def test_liveness_is_always_up_regardless_of_consumer_state(server) -> None:
    srv, state = server
    status, body = _get(srv.server_port, "/health/live")
    assert (status, body) == (200, {"status": "UP"})

    state.mark_consuming(False)
    status, body = _get(srv.server_port, "/health/live")
    assert (status, body) == (200, {"status": "UP"})


def test_readiness_reflects_consuming_state(server) -> None:
    srv, state = server

    state.mark_consuming(False)
    status, body = _get(srv.server_port, "/health/ready")
    assert (status, body) == (503, {"status": "DOWN"})

    state.mark_consuming(True)
    status, body = _get(srv.server_port, "/health/ready")
    assert (status, body) == (200, {"status": "UP"})


def test_unknown_path_returns_404(server) -> None:
    srv, _state = server
    status, _ = _get(srv.server_port, "/nope")
    assert status == 404


def test_metrics_endpoint_reflects_live_counters(server) -> None:
    # Berke review #12: /internal/v1/metrics gercek sayac degerlerini dondurmeli.
    srv, _state = server
    before = metrics.snapshot()["dead_letter_count"]
    metrics.increment("dead_letter_count")

    status, body = _get(srv.server_port, "/internal/v1/metrics")

    assert status == 200
    assert body["dead_letter_count"] == before + 1
    assert set(body) >= set(metrics.snapshot())
