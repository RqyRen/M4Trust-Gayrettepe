"""Video frame ornekleme (ADR-002 SS3.2 "Frame veya segment analizi").

Butun frame'ler islenmez: maliyet/sure sinirlamasi icin sabit araliklarla
ornekleme yapilir, ustten sinirlanir (video_max_sampled_frames).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2

from app.config import Settings
from app.contracts.errors import ErrorCode, PipelineFailure


@dataclass(frozen=True)
class SampledFrame:
    index: int
    time_ms: int
    jpeg: bytes


def sample_frames(path: Path, settings: Settings) -> list[SampledFrame]:
    """Videodan sabit araliklarla ornek frame'ler cikarir (JPEG kodlanmis).

    Firlatir: PipelineFailure(CORRUPTED_FILE) — video acilamiyorsa veya hic
    okunabilir frame yoksa.
    """
    capture = cv2.VideoCapture(str(path))
    if not capture.isOpened():
        capture.release()
        raise PipelineFailure(
            ErrorCode.CORRUPTED_FILE,
            "video content could not be opened",
            details={"reason": "unreadable container"},
        )

    fps = capture.get(cv2.CAP_PROP_FPS) or 0.0
    if fps <= 0:
        fps = 25.0  # guvenli varsayilan; bazi container'lar FPS raporlamaz

    interval_frames = max(1, round(fps * settings.video_frame_sample_interval_seconds))

    sampled: list[SampledFrame] = []
    frame_index = 0
    try:
        while len(sampled) < settings.video_max_sampled_frames:
            ok, frame = capture.read()
            if not ok:
                break
            if frame_index % interval_frames == 0:
                success, buffer = cv2.imencode(".jpg", frame)
                if success:
                    time_ms = int((frame_index / fps) * 1000)
                    sampled.append(SampledFrame(index=frame_index, time_ms=time_ms, jpeg=buffer.tobytes()))
            frame_index += 1
    finally:
        capture.release()

    if not sampled:
        raise PipelineFailure(
            ErrorCode.CORRUPTED_FILE,
            "no readable frames were found in the video",
            details={"reason": "empty or unreadable stream"},
        )

    return sampled
