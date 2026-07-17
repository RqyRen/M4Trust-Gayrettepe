"""ai-api operasyonel endpoint testleri (ADR-002 §21, ADR-007 §29-31).

Bulgu (16 Temmuz 2026): Berke'nin authoritative `contracts/openapi/ai-internal-v1.yaml`
dosyasindan diff alindiginda iki gercek sapma bulundu — hicbir test bunlari
yakalamiyordu. Bu dosya dort operasyonel endpoint'i committed contract'a karsi
dogrular.
"""
from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_health_live_returns_up() -> None:
    response = client.get("/health/live")
    assert response.status_code == 200
    assert response.json() == {"status": "UP"}


def test_health_ready_returns_up_not_ready_string() -> None:
    # Bulgu: eskiden {"status": "READY"} donuyordu; contract yalniz UP/DOWN kabul eder.
    response = client.get("/health/ready")
    assert response.status_code == 200
    assert response.json() == {"status": "UP"}


def test_capabilities_has_service_identity_and_job_types() -> None:
    response = client.get("/internal/v1/capabilities")
    assert response.status_code == 200
    body = response.json()
    # Kanonik ad (ADR-007 §9, contracts CHANGELOG) — eskiden "ai-service" idi.
    assert body["service"] == "m4trust-ai-service"
    assert body["serviceVersion"]
    job_types = {c["jobType"] for c in body["capabilities"]}
    assert job_types == {"DOCUMENT_EXTRACTION", "VIDEO_ANALYSIS"}
    for c in body["capabilities"]:
        assert c["requestSchemaVersions"] == ["1.0.0"]
        assert c["resultSchemaVersions"] == ["1.0.0"]


def test_contracts_returns_bare_array_not_wrapped_object() -> None:
    # Bulgu: eskiden {"contracts": [...]} donuyordu; contract duz array ister.
    response = client.get("/internal/v1/contracts")
    assert response.status_code == 200
    body = response.json()
    assert isinstance(body, list)
    assert len(body) > 0
    for entry in body:
        assert set(entry) >= {"name", "version", "sha256"}
        assert entry["version"] == "1.0.0"
