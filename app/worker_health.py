"""ai-worker health HTTP surface (ADR-007 §9.2, §29-31).

ai-worker'in ana isi RabbitMQ command queue'larini tuketmek oldugu icin
(bloklayan pika consumer loop), health endpoint'leri ayri, hafif bir HTTP
server ile arka plan thread'inde sunulur. Yeni bir dependency eklenmez
(ADR-007 §15); stdlib `http.server` yeterlidir.

Liveness (ADR-007 §30) dis bagimliliga BAKMAZ; process ayaktaysa UP'tir.
Readiness (ADR-007 §31) RabbitMQ tuketim durumuna baglidir: worker aktif
olarak consume etmiyorsa 503/DOWN doner.
"""
from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class WorkerState:
    """Thread-safe: worker'in RabbitMQ tuketim durumunu tutar."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._consuming = False

    def mark_consuming(self, value: bool) -> None:
        with self._lock:
            self._consuming = value

    @property
    def is_consuming(self) -> bool:
        with self._lock:
            return self._consuming


def _make_handler(state: WorkerState) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler API
            if self.path == "/health/live":
                self._respond(200, "UP")
            elif self.path == "/health/ready":
                if state.is_consuming:
                    self._respond(200, "UP")
                else:
                    self._respond(503, "DOWN")
            else:
                self.send_response(404)
                self.end_headers()

        def _respond(self, status: int, health_status: str) -> None:
            body = json.dumps({"status": health_status}).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args) -> None:  # ADR-007 §32: kendi structured log akisimiz kullanilir
            return

    return Handler


def start_health_server(state: WorkerState, *, host: str = "0.0.0.0", port: int) -> ThreadingHTTPServer:
    """Arka plan thread'inde health server baslatir; server nesnesini dondurur."""
    server = ThreadingHTTPServer((host, port), _make_handler(state))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server
