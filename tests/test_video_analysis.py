"""VIDEO_ANALYSIS pipeline testleri (ADR-002 §3.2, §10.1, §11).

Gercek HTTP kaynagi uzerinden: indirme -> hash -> format kontrolu ->
canonical sonuc -> teknik schema validation.
"""
from __future__ import annotations

import hashlib
import json
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import pytest

from app.contracts.errors import ErrorCode, PipelineFailure
from app.contracts.validation import validate_outgoing
from app.pipeline.registry import pipeline_for
from app.pipeline.video_analysis.media import MP4, WEBM, detect_media_type
from app.pipeline.video_analysis.pipeline import run

_EXAMPLES = Path(__file__).resolve().parents[1] / "contracts" / "examples"

# ISO BMFF/MP4: [4 byte box size][ftyp][...]
_MP4 = b"\x00\x00\x00\x18ftypisom" + b"video frame data " * 100
# WebM/Matroska EBML magic
_WEBM = b"\x1a\x45\xdf\xa3" + b"webm cluster data " * 100
_JUNK = b"just plain bytes, not a video container"

_BODIES = {"/mp4": _MP4, "/webm": _WEBM, "/junk": _JUNK}


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        body = _BODIES.get(self.path)
        if body is None:
            self.send_response(404)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args) -> None:
        return


@pytest.fixture(scope="module")
def base_url() -> str:
    server = HTTPServer(("127.0.0.1", 0), _Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()


def _request(base_url: str, path: str, body: bytes, media_type: str = MP4) -> dict:
    req = json.loads((_EXAMPLES / "video-analysis" / "full-request.json").read_text(encoding="utf-8"))
    expires = (datetime.now(timezone.utc) + timedelta(minutes=10)).strftime("%Y-%m-%dT%H:%M:%SZ")
    req["payload"]["input"].update(
        {
            "mediaType": media_type,
            "sizeBytes": len(body),
            "sha256": hashlib.sha256(body).hexdigest(),
            "download": {"url": f"{base_url}{path}", "expiresAt": expires},
        }
    )
    return req


# --- Pipeline uctan uca (gercek indirme + hash + format) ---

def test_mp4_produces_schema_valid_completed_event(base_url: str) -> None:
    request = _request(base_url, "/mp4", _MP4)
    event = run(request)

    assert event["eventType"] == "ai.job.completed.v1"
    assert event["jobType"] == "VIDEO_ANALYSIS"
    assert event["causationId"] == request["eventId"]
    validate_outgoing(event)


def test_advisory_outcome_is_not_a_business_decision(base_url: str) -> None:
    # ADR-002 §10.1 / ADR-003 §22: sonuc advisory'dir, business karar alanlari yok.
    request = _request(base_url, "/mp4", _MP4)
    event = run(request)
    summary = event["payload"]["result"]["summary"]
    assert summary["advisoryOutcome"] in {"NO_ISSUE_DETECTED", "REVIEW_SUGGESTED", "INSUFFICIENT_EVIDENCE", "UNKNOWN"}
    assert "paymentRelease" not in summary
    assert "disputeResolved" not in summary
    assert "deliveryApproved" not in summary


def test_webm_is_supported(base_url: str) -> None:
    request = _request(base_url, "/webm", _WEBM, media_type=WEBM)
    event = run(request)
    assert event["eventType"] == "ai.job.completed.v1"


def test_hash_mismatch_fails_before_analysis(base_url: str) -> None:
    request = _request(base_url, "/mp4", _MP4)
    request["payload"]["input"]["sha256"] = "a" * 64
    with pytest.raises(PipelineFailure) as exc:
        run(request)
    assert exc.value.code is ErrorCode.CONTENT_HASH_MISMATCH


def test_unsupported_content_rejected(base_url: str) -> None:
    request = _request(base_url, "/junk", _JUNK)
    with pytest.raises(PipelineFailure) as exc:
        run(request)
    assert exc.value.code is ErrorCode.UNSUPPORTED_MEDIA_TYPE


def test_declared_media_type_mismatch_rejected(base_url: str) -> None:
    # Icerik MP4 ama WebM beyan edilmis.
    request = _request(base_url, "/mp4", _MP4, media_type=WEBM)
    with pytest.raises(PipelineFailure) as exc:
        run(request)
    assert exc.value.code is ErrorCode.UNSUPPORTED_MEDIA_TYPE


# --- Format tespiti birim davranisi ---

def test_detect_media_type_reads_magic_bytes(tmp_path: Path) -> None:
    mp4 = tmp_path / "a.mp4"
    mp4.write_bytes(_MP4)
    assert detect_media_type(mp4, declared=MP4) == MP4

    webm = tmp_path / "a.webm"
    webm.write_bytes(_WEBM)
    assert detect_media_type(webm, declared=WEBM) == WEBM


# --- Registry ---

def test_registry_routes_video_analysis_to_real_pipeline() -> None:
    assert pipeline_for("VIDEO_ANALYSIS") is run
