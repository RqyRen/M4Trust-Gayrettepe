"""Roboflow ham tespitlerini canonical result objesine cevirir
(ADR-002 SS10 sonuc contract'i, SS10.1 "advisoryOutcome odeme karari degildir").

Model-native cevap (Roboflow'un x/y/width/height/class formati) burada
tuketilip kaybolur; Spring yalnizca canonical observations/anomalies/summary
yapisini gorur (ADR-002 SS8.1, SS10, ADR-003 SS22).

Basitlestirme notu: hasar tespitleri video basina TEK bir anomaly'e
toplanir (en yuksek guvenli frame'den) - ardisik frame'lerde ayni fiziksel
hasarin tekrar tekrar raporlanmasini onlemek icin. Bu implementasyon
detayidir, contract'in parcasi degildir (ADR-002 SS26).
"""
from __future__ import annotations

from app.pipeline.video_analysis.frames import SampledFrame

_INTERVAL_PADDING_MS = 500  # timeRange.endMs > startMs garantisi icin


def _clamp_confidence(value) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, v))


def _severity_for_confidence(confidence: float) -> str:
    if confidence >= 0.85:
        return "HIGH"
    if confidence >= 0.6:
        return "MEDIUM"
    return "LOW"


def _time_range(time_ms: int) -> dict:
    return {"startMs": time_ms, "endMs": time_ms + _INTERVAL_PADDING_MS}


def _class_counts_per_frame(predictions: list[dict], min_confidence: float) -> dict[str, int]:
    counts: dict[str, int] = {}
    for pred in predictions:
        if _clamp_confidence(pred.get("confidence")) < min_confidence:
            continue
        label = str(pred.get("class", "UNKNOWN"))
        counts[label] = counts.get(label, 0) + 1
    return counts


def aggregate_results(
    frames: list[SampledFrame],
    logistics_predictions_per_frame: list[list[dict]],
    damage_predictions_per_frame: list[list[dict]],
    expected_objects: list[dict],
    min_confidence: float,
) -> dict:
    """Frame basina ham Roboflow tespitlerinden canonical `result` uretir."""
    per_frame_counts = [_class_counts_per_frame(preds, min_confidence) for preds in logistics_predictions_per_frame]

    observations: list[dict] = []
    review_reasons: list[str] = []
    seen_labels: set[str] = set()

    # 1) Beklenen nesneler (request.processing.expectedObjects) -> OBJECT_COUNT.
    for item in expected_objects:
        label = str(item["label"])
        seen_labels.add(label)
        best_frame_idx = max(range(len(frames)), key=lambda i: per_frame_counts[i].get(label, 0), default=None)
        max_count = per_frame_counts[best_frame_idx].get(label, 0) if best_frame_idx is not None else 0
        time_ms = frames[best_frame_idx].time_ms if best_frame_idx is not None and max_count > 0 else (
            frames[0].time_ms if frames else 0
        )
        confidences = [
            _clamp_confidence(p.get("confidence"))
            for p in (logistics_predictions_per_frame[best_frame_idx] if best_frame_idx is not None else [])
            if str(p.get("class")) == label
        ]
        confidence = sum(confidences) / len(confidences) if confidences else 0.0

        observations.append(
            {
                "observationReference": f"observation-{len(observations) + 1}",
                "type": "OBJECT_COUNT",
                "label": label,
                "observedValue": max_count,
                "confidence": confidence,
                "timeRange": _time_range(time_ms),
            }
        )

        expected_count = int(item.get("expectedCount", 0))
        if max_count < expected_count:
            review_reasons.append(f"{label}_COUNT_BELOW_EXPECTED")

    # 2) Beklenmeyen ama tespit edilen siniflar -> OBJECT_PRESENCE.
    for i, frame in enumerate(frames):
        for label, count in per_frame_counts[i].items():
            if label in seen_labels or count == 0:
                continue
            seen_labels.add(label)
            confidences = [
                _clamp_confidence(p.get("confidence"))
                for p in logistics_predictions_per_frame[i]
                if str(p.get("class")) == label
            ]
            observations.append(
                {
                    "observationReference": f"observation-{len(observations) + 1}",
                    "type": "OBJECT_PRESENCE",
                    "label": label,
                    "observedValue": True,
                    "confidence": max(confidences) if confidences else 0.0,
                    "timeRange": _time_range(frame.time_ms),
                }
            )

    # 3) Hasar tespitleri -> tek (en yuksek guvenli) anomaly (bkz. modul docstring'i).
    anomalies: list[dict] = []
    best_damage: tuple[float, int] | None = None  # (confidence, frame_index)
    for i, preds in enumerate(damage_predictions_per_frame):
        for pred in preds:
            confidence = _clamp_confidence(pred.get("confidence"))
            if confidence < min_confidence:
                continue
            if best_damage is None or confidence > best_damage[0]:
                best_damage = (confidence, i)

    if best_damage is not None:
        confidence, frame_idx = best_damage
        anomalies.append(
            {
                "anomalyReference": "anomaly-1",
                "type": "DAMAGED_PARCEL",
                "severity": _severity_for_confidence(confidence),
                "description": "Video model detected a possibly damaged parcel.",
                "confidence": confidence,
                "timeRange": _time_range(frames[frame_idx].time_ms),
            }
        )
        review_reasons.append("DAMAGED_PARCEL_DETECTED")

    advisory_outcome = "REVIEW_SUGGESTED" if review_reasons else "NO_ISSUE_DETECTED"

    return {
        "durationMs": frames[-1].time_ms if frames else 0,
        "observations": observations,
        "anomalies": anomalies,
        "summary": {"advisoryOutcome": advisory_outcome, "reviewReasons": review_reasons},
    }
