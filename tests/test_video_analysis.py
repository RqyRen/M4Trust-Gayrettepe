"""VIDEO_ANALYSIS pipeline testleri (ADR-002 §3.2, §10.1, §11; ADR-003 §22).

Indirme, hash, format tespiti, GERCEK frame ornekleme (OpenCV) ve canonical
mapping gercek calisir. Roboflow HTTP cagrisi (ADR-004 §17 "mock-first")
monkeypatch ile sahtelenir; gercek Roboflow entegrasyonu ayri bir canli
script ile (pytest disinda) dogrulanir.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

import cv2
import numpy as np
import pytest

from app.contracts.errors import ErrorCode, PipelineFailure
from app.contracts.validation import validate_outgoing
from app.pipeline.registry import pipeline_for
from app.pipeline.video_analysis import roboflow_client
from app.pipeline.video_analysis.frames import sample_frames, sample_image
from app.pipeline.video_analysis.media import IMAGE_JPEG, IMAGE_PNG, MP4, WEBM, detect_media_type
from app.pipeline.video_analysis.pipeline import run

_EXAMPLES = Path(__file__).resolve().parents[1] / "contracts" / "examples"


def _real_mp4_bytes(*, seconds: float = 3.0, fps: float = 10.0) -> bytes:
    """OpenCV ile gercek, decode edilebilir bir MP4 uretir (bir kutu ceviriyor)."""
    fd, tmp_path = tempfile.mkstemp(suffix=".mp4")
    os.close(fd)
    try:
        fourcc = cv2.VideoWriter_fourcc(*"mp4v")
        writer = cv2.VideoWriter(tmp_path, fourcc, fps, (320, 240))
        for _ in range(int(seconds * fps)):
            frame = np.full((240, 320, 3), 255, dtype=np.uint8)
            cv2.rectangle(frame, (50, 50), (150, 150), (100, 50, 20), -1)
            writer.write(frame)
        writer.release()
        return Path(tmp_path).read_bytes()
    finally:
        Path(tmp_path).unlink(missing_ok=True)


def _real_jpeg_bytes() -> bytes:
    """OpenCV ile gercek, decode edilebilir bir JPEG uretir (bir kutu iceriyor)."""
    frame = np.full((240, 320, 3), 255, dtype=np.uint8)
    cv2.rectangle(frame, (50, 50), (150, 150), (100, 50, 20), -1)
    success, buffer = cv2.imencode(".jpg", frame)
    assert success
    return buffer.tobytes()


def _real_png_bytes() -> bytes:
    """OpenCV ile gercek, decode edilebilir bir PNG uretir (bir kutu iceriyor)."""
    frame = np.full((240, 320, 3), 255, dtype=np.uint8)
    cv2.rectangle(frame, (50, 50), (150, 150), (100, 50, 20), -1)
    success, buffer = cv2.imencode(".png", frame)
    assert success
    return buffer.tobytes()


_MP4_BYTES = _real_mp4_bytes()
_JPEG_BYTES = _real_jpeg_bytes()
_PNG_BYTES = _real_png_bytes()
# Dogru magic byte (ftyp) ama gercek video verisi degil - decode basarisiz olmali.
_CORRUPTED_MP4 = b"\x00\x00\x00\x18ftypisom" + b"not real video data " * 200
_CORRUPTED_JPEG = b"\xff\xd8\xff" + b"not real image data " * 200
_JUNK = b"just plain bytes, not a video container"

_BODIES = {
    "/mp4": _MP4_BYTES,
    "/corrupted": _CORRUPTED_MP4,
    "/junk": _JUNK,
    "/jpeg": _JPEG_BYTES,
    "/png": _PNG_BYTES,
    "/corrupted-jpeg": _CORRUPTED_JPEG,
}


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
    # Fixture'daki sabit deadlineAt zamanla gecmise duser (Berke review #5:
    # deadline artik semantik kontrol ediliyor) -- testler her zaman calisan
    # zamana gore GELECEK bir deadline kullanmali.
    req["payload"]["deadlineAt"] = (datetime.now(timezone.utc) + timedelta(minutes=30)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return req


def _expected_objects(request: dict) -> list[dict]:
    return request["payload"]["processing"]["expectedObjects"]


def _mock_roboflow(
    monkeypatch: pytest.MonkeyPatch,
    *,
    logistics: list[dict] | None = None,
    damage: list[dict] | None = None,
) -> None:
    monkeypatch.setattr(roboflow_client, "detect_objects", lambda image, settings: logistics or [])
    monkeypatch.setattr(roboflow_client, "detect_damage", lambda image, settings: damage or [])


# --- Pipeline uctan uca (gercek indirme + hash + format + GERCEK frame ornekleme) ---

def test_video_pipeline_produces_schema_valid_completed_event(base_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_roboflow(monkeypatch, logistics=[{"class": "sealed_box", "confidence": 0.9}])
    request = _request(base_url, "/mp4", _MP4_BYTES)
    event = run(request)

    assert event["eventType"] == "ai.job.completed.v1"
    assert event["jobType"] == "VIDEO_ANALYSIS"
    assert event["causationId"] == request["eventId"]
    validate_outgoing(event)


def test_check_cancelled_before_frame_analysis_stops_pipeline(base_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Berke review #1: pahali Roboflow cagrilarindan ONCE cancellation
    kontrol edilmeli. Ilk checkpoint (indirmeden once) gecerli sayilir, ikinci
    checkpoint'te (ilk frame'in Roboflow cagrisindan once) iptal simule
    edilir -- indirme/frame ornekleme GERCEK calisir ama Roboflow'a hic
    ulasilmamalidir.
    """
    from app.common.cancellation import JobCancelled

    calls = {"n": 0}

    def _check_cancelled() -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            raise JobCancelled("job-under-test")

    detect_calls: list[int] = []
    monkeypatch.setattr(roboflow_client, "detect_objects", lambda image, settings: detect_calls.append(1) or [])
    monkeypatch.setattr(roboflow_client, "detect_damage", lambda image, settings: [])

    request = _request(base_url, "/mp4", _MP4_BYTES)
    with pytest.raises(JobCancelled):
        run(request, check_cancelled=_check_cancelled)

    assert detect_calls == []  # Roboflow cagrisi hic yapilmadi -- maliyet onlendi
    assert calls["n"] == 2


def test_advisory_outcome_is_not_a_business_decision(base_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # ADR-002 §10.1 / ADR-003 §22: sonuc advisory'dir, business karar alanlari yok.
    _mock_roboflow(monkeypatch, logistics=[{"class": "sealed_box", "confidence": 0.9}])
    request = _request(base_url, "/mp4", _MP4_BYTES)
    event = run(request)
    summary = event["payload"]["result"]["summary"]
    assert summary["advisoryOutcome"] in {"NO_ISSUE_DETECTED", "REVIEW_SUGGESTED", "INSUFFICIENT_EVIDENCE", "UNKNOWN"}
    assert "paymentRelease" not in summary
    assert "disputeResolved" not in summary
    assert "deliveryApproved" not in summary


def test_object_count_matches_expected_gives_no_issue(base_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    request = _request(base_url, "/mp4", _MP4_BYTES)
    expected = _expected_objects(request)  # full-request: sealed_box x4, delivery_note x1
    logistics = []
    for item in expected:
        logistics.extend({"class": item["label"], "confidence": 0.95} for _ in range(item["expectedCount"]))
    _mock_roboflow(monkeypatch, logistics=logistics)

    event = run(request)
    result = event["payload"]["result"]
    assert result["summary"]["advisoryOutcome"] == "NO_ISSUE_DETECTED"
    assert result["summary"]["reviewReasons"] == []

    counts = {o["label"]: o["observedValue"] for o in result["observations"] if o["type"] == "OBJECT_COUNT"}
    for item in expected:
        assert counts[item["label"]] == item["expectedCount"]


def test_object_count_below_expected_triggers_review(base_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    request = _request(base_url, "/mp4", _MP4_BYTES)
    expected = _expected_objects(request)
    # Beklenenden AZ nesne tespit edildi (ornegin 4 yerine 1 sealed_box).
    _mock_roboflow(monkeypatch, logistics=[{"class": expected[0]["label"], "confidence": 0.9}])

    event = run(request)
    result = event["payload"]["result"]
    assert result["summary"]["advisoryOutcome"] == "REVIEW_SUGGESTED"
    assert f"{expected[0]['label']}_COUNT_BELOW_EXPECTED" in result["summary"]["reviewReasons"]


def test_damage_detection_produces_anomaly_and_review(base_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_roboflow(monkeypatch, damage=[{"class": "damaged", "confidence": 0.92}])
    request = _request(base_url, "/mp4", _MP4_BYTES)
    event = run(request)

    result = event["payload"]["result"]
    assert len(result["anomalies"]) == 1
    anomaly = result["anomalies"][0]
    assert anomaly["type"] == "DAMAGED_PARCEL"
    assert anomaly["severity"] == "HIGH"  # confidence 0.92 >= 0.85
    assert result["summary"]["advisoryOutcome"] == "REVIEW_SUGGESTED"
    assert "DAMAGED_PARCEL_DETECTED" in result["summary"]["reviewReasons"]


def test_low_confidence_damage_is_filtered_out(base_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    # min_confidence esiginin altindaki tespitler yok sayilir.
    _mock_roboflow(monkeypatch, damage=[{"class": "damaged", "confidence": 0.1}])
    request = _request(base_url, "/mp4", _MP4_BYTES)
    event = run(request)
    assert event["payload"]["result"]["anomalies"] == []


# --- Foto pipeline (JPEG/PNG tek-kare, box counting) ---

def test_photo_pipeline_produces_schema_valid_completed_event(base_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_roboflow(monkeypatch, logistics=[{"class": "sealed_box", "confidence": 0.9}])
    request = _request(base_url, "/jpeg", _JPEG_BYTES, media_type=IMAGE_JPEG)
    event = run(request)

    assert event["eventType"] == "ai.job.completed.v1"
    assert event["jobType"] == "VIDEO_ANALYSIS"
    validate_outgoing(event)


def test_photo_object_count_matches_expected(base_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    request = _request(base_url, "/jpeg", _JPEG_BYTES, media_type=IMAGE_JPEG)
    expected = _expected_objects(request)  # full-request: sealed_box x4, delivery_note x1
    logistics = []
    for item in expected:
        logistics.extend({"class": item["label"], "confidence": 0.95} for _ in range(item["expectedCount"]))
    _mock_roboflow(monkeypatch, logistics=logistics)

    event = run(request)
    result = event["payload"]["result"]
    assert result["summary"]["advisoryOutcome"] == "NO_ISSUE_DETECTED"

    counts = {o["label"]: o["observedValue"] for o in result["observations"] if o["type"] == "OBJECT_COUNT"}
    for item in expected:
        assert counts[item["label"]] == item["expectedCount"]


def test_png_photo_is_accepted(base_url: str, monkeypatch: pytest.MonkeyPatch) -> None:
    _mock_roboflow(monkeypatch, logistics=[{"class": "sealed_box", "confidence": 0.9}])
    request = _request(base_url, "/png", _PNG_BYTES, media_type=IMAGE_PNG)
    event = run(request)
    validate_outgoing(event)


def test_corrupted_photo_rejected(base_url: str) -> None:
    request = _request(base_url, "/corrupted-jpeg", _CORRUPTED_JPEG, media_type=IMAGE_JPEG)
    with pytest.raises(PipelineFailure) as exc:
        run(request)
    assert exc.value.code is ErrorCode.CORRUPTED_FILE


def test_detect_media_type_reads_jpeg_and_png_magic_bytes(tmp_path: Path) -> None:
    jpeg_path = tmp_path / "a.jpg"
    jpeg_path.write_bytes(_JPEG_BYTES)
    assert detect_media_type(jpeg_path, declared=IMAGE_JPEG) == IMAGE_JPEG

    png_path = tmp_path / "a.png"
    png_path.write_bytes(_PNG_BYTES)
    assert detect_media_type(png_path, declared=IMAGE_PNG) == IMAGE_PNG


def test_sample_image_returns_single_frame_at_time_zero(tmp_path: Path) -> None:
    jpeg_path = tmp_path / "a.jpg"
    jpeg_path.write_bytes(_JPEG_BYTES)
    frames = sample_image(jpeg_path)
    assert len(frames) == 1
    assert frames[0].index == 0
    assert frames[0].time_ms == 0
    assert frames[0].jpeg.startswith(b"\xff\xd8")


# --- Hata yollari ---

def test_past_deadline_is_rejected_before_download(base_url: str) -> None:
    """Berke review #5: suresi gecmis deadline pahali indirme/Roboflow
    cagrisindan ONCE reddedilmeli. `/does-not-exist` gercekten fetch edilseydi
    farkli bir (deadline-disi) hata koduyla basarisiz olurdu -- INVALID_DEADLINE
    almamiz checkpoint'in indirmeden ONCE tetiklendigini kanitlar.
    """
    request = _request(base_url, "/does-not-exist", b"unused")
    request["payload"]["deadlineAt"] = "2000-01-01T00:00:00Z"
    with pytest.raises(PipelineFailure) as exc:
        run(request)
    assert exc.value.code is ErrorCode.INVALID_DEADLINE


def test_hash_mismatch_fails_before_analysis(base_url: str) -> None:
    request = _request(base_url, "/mp4", _MP4_BYTES)
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
    request = _request(base_url, "/mp4", _MP4_BYTES, media_type=WEBM)
    with pytest.raises(PipelineFailure) as exc:
        run(request)
    assert exc.value.code is ErrorCode.UNSUPPORTED_MEDIA_TYPE


def test_corrupted_video_container_rejected(base_url: str) -> None:
    # Dogru magic byte ama decode edilemeyen icerik -> CORRUPTED_FILE.
    request = _request(base_url, "/corrupted", _CORRUPTED_MP4)
    with pytest.raises(PipelineFailure) as exc:
        run(request)
    assert exc.value.code is ErrorCode.CORRUPTED_FILE


# --- Frame ornekleme birim testleri ---

def test_frame_sampling_bounded_by_max_frames(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from app.config import get_settings

    video_path = tmp_path / "long.mp4"
    video_path.write_bytes(_real_mp4_bytes(seconds=5.0, fps=10.0))  # 50 frame, 1sn=10 frame araligi -> ~5 ornek

    settings = get_settings()
    monkeypatch.setattr(settings, "video_max_sampled_frames", 3)
    frames = sample_frames(video_path, settings)
    assert len(frames) <= 3
    assert all(f.jpeg.startswith(b"\xff\xd8") for f in frames)  # gecerli JPEG magic byte


def test_detect_media_type_reads_magic_bytes(tmp_path: Path) -> None:
    mp4 = tmp_path / "a.mp4"
    mp4.write_bytes(_MP4_BYTES)
    assert detect_media_type(mp4, declared=MP4) == MP4


# --- Registry ---

def test_registry_routes_video_analysis_to_real_pipeline() -> None:
    assert pipeline_for("VIDEO_ANALYSIS") is run
