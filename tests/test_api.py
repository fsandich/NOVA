from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from dynatrace_triage.api.app import create_app
from dynatrace_triage.config import Settings
from dynatrace_triage.escalation import IncidentResult
from dynatrace_triage.service import TriageService

KEY = {"x-api-key": "clave-de-prueba"}


class FakeSnow:
    """Marcador de ServiceNow configurado; open_incident se reemplaza con monkeypatch."""


@pytest.fixture
def make_client(scenario, monkeypatch):
    def _make(name="workload_continuo", *, snow=False, keys="clave-de-prueba", **overrides):
        engine, fake = scenario(name, **overrides)
        service = TriageService(
            engine, high_priority_envs={"PROD"}, snow=FakeSnow() if snow else None
        )
        if snow:
            monkeypatch.setattr(
                "dynatrace_triage.service.open_incident",
                lambda report, snow, assignment_group="": IncidentResult(
                    "INC100", "s1", "https://x/inc", True
                ),
            )
        app = create_app(
            service_factory=lambda: service, settings=Settings(api_keys=keys, _env_file=None)
        )
        return TestClient(app), fake

    return _make


def test_health_sin_clave(make_client):
    client, _ = make_client()
    assert client.get("/health").json() == {"status": "ok"}


@pytest.mark.parametrize("headers", [{}, {"x-api-key": "otra"}])
def test_rechaza_sin_clave_valida(make_client, headers):
    client, fake = make_client()
    r = client.post("/v1/triage", json={"problem_id": "P-2610334"}, headers=headers)
    assert r.status_code == 401
    assert fake.calls == []


def test_sin_claves_configuradas_falla_cerrado(make_client):
    client, _ = make_client(keys="")
    r = client.post("/v1/triage", json={"problem_id": "P-2610334"}, headers=KEY)
    assert r.status_code == 503


def test_triage(make_client):
    client, _ = make_client()
    r = client.post("/v1/triage", json={"problem_id": "P-2610334"}, headers=KEY)
    assert r.status_code == 200
    body = r.json()
    assert body["report"]["confianza"] == "Alta"
    assert body["report"]["escalamiento"]["decision"] == "PREGUNTAR"
    assert "**Duración:** Incidente continuo desde 30/09 20:54 UTC" in body["markdown"]


def test_problem_id_invalido(make_client):
    client, fake = make_client()
    r = client.post("/v1/triage", json={"problem_id": "P-1 or true"}, headers=KEY)
    assert r.status_code == 422
    assert fake.calls == []


def test_problem_no_encontrado(make_client):
    client, _ = make_client(detalle={"records": []})
    r = client.post("/v1/triage", json={"problem_id": "P-9"}, headers=KEY)
    assert r.status_code == 404
    assert "7 días" in r.json()["detail"]


def test_error_de_dynatrace(make_client):
    client, _ = make_client(detalle={"error": "HTTP 401: token inválido"})
    r = client.post("/v1/triage", json={"problem_id": "P-2610334"}, headers=KEY)
    assert r.status_code == 502


def test_incidente_sin_servicenow(make_client):
    client, _ = make_client()
    r = client.post("/v1/incidents", json={"problem_id": "P-2610334"}, headers=KEY)
    assert r.status_code == 503


def test_incidente(make_client):
    client, _ = make_client(snow=True)
    r = client.post("/v1/incidents", json={"problem_id": "P-2610334"}, headers=KEY)
    assert r.json() == {
        "number": "INC100",
        "url": "https://x/inc",
        "created": True,
        "problem_id": "P-2610334",
    }


def test_webhook_escala_solo_si_la_politica_lo_dice(make_client):
    # PROD → ESCALAR → crea incidente
    client, _ = make_client("servicio_dependencia", snow=True)
    r = client.post("/v1/webhooks/dynatrace", json={"problem_id": "P-2610405"}, headers=KEY)
    assert r.json()["decision"] == "ESCALAR" and r.json()["incident_number"] == "INC100"

    # QA con confianza Alta → PREGUNTAR → no crea incidente (no hay humano en el camino automático)
    client, _ = make_client("workload_continuo", snow=True)
    r = client.post("/v1/webhooks/dynatrace", json={"problem_id": "P-2610334"}, headers=KEY)
    assert r.json()["decision"] == "PREGUNTAR" and r.json()["incident_number"] is None


def test_webhook_fuera_del_openapi(make_client):
    client, _ = make_client()
    paths = client.get("/openapi.json").json()["paths"]
    assert set(paths) == {"/v1/triage", "/v1/incidents"}
