"""RabbitMQ heartbeat pump testleri (bulgu, 22 Temmuz 2026 - Railway'de kanitlandi).

Uzun suren senkron isler (LLM cagrisi) sirasinda pika'nin heartbeat icin gereken
soket I/O'sunu isleyememesi (BrokenPipeError) sorununu cozen run_while_pumping_connection
icin. Gercek RabbitMQ gerektirmez -- connection sahte bir process_data_events ile taklit edilir.
"""
from __future__ import annotations

import threading
import time

import pytest

from app.messaging.consumer import run_while_pumping_connection


class _FakeConnection:
    def __init__(self) -> None:
        self.pump_calls: list[float | None] = []
        self.lock = threading.Lock()

    def process_data_events(self, time_limit: float | None = None) -> None:
        with self.lock:
            self.pump_calls.append(time_limit)
        time.sleep(0.01)


def test_operation_result_is_returned() -> None:
    connection = _FakeConnection()

    result = run_while_pumping_connection(lambda: "done", connection=connection, poll_interval=0.05)

    assert result == "done"


def test_connection_is_pumped_periodically_during_slow_operation() -> None:
    connection = _FakeConnection()

    def _slow_operation() -> str:
        time.sleep(0.2)
        return "done"

    result = run_while_pumping_connection(_slow_operation, connection=connection, poll_interval=0.02)

    assert result == "done"
    # ~0.2s / 0.02s araligi -> en az birkac pompalama olmali (heartbeat icin
    # soket I/O'nun aksamadigini kanitlar).
    assert len(connection.pump_calls) >= 3


def test_exception_from_operation_propagates_to_caller() -> None:
    connection = _FakeConnection()

    class _BoomError(RuntimeError):
        pass

    def _failing_operation() -> None:
        raise _BoomError("pipeline patladi")

    with pytest.raises(_BoomError, match="pipeline patladi"):
        run_while_pumping_connection(_failing_operation, connection=connection, poll_interval=0.05)


def test_fast_operation_does_not_hang() -> None:
    connection = _FakeConnection()

    started = time.monotonic()
    result = run_while_pumping_connection(lambda: "fast", connection=connection, poll_interval=5.0)
    elapsed = time.monotonic() - started

    assert result == "fast"
    assert elapsed < 1.0  # thread.join() sonsuza kadar beklemez
