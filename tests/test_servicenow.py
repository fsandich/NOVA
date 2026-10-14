from __future__ import annotations

import json

import httpx
import pytest
import respx

from dynatrace_triage.escalation import decide, open_incident
from dynatrace_triage.servicenow.client import ServiceNowClient, ServiceNowError

SNOW = "https://demo.service-now.com"
TABLE = f"{SNOW}/api/now/table"


@pytest.fixture
def snow():
    return ServiceNowClient(SNOW, "integracion", "secreto")


@pytest.fixture
def report(scenario):
    engine, _ = scenario("servicio_dependencia")
    r = engine.run("P-2610405")
    r.escalamiento = decide(r, {"PROD"})
    return r


def query_of(call) -> str:
    return call.request.url.params["sysparm_query"]


class TestCliente:
    @respx.mock
    def test_find_ci_exacto(self, snow):
        respx.get(f"{TABLE}/cmdb_ci").mock(
            return_value=httpx.Response(200, json={"result": [{"sys_id": "ci1"}]})
        )
        assert snow.find_ci("exchange-deployment-prod") == {"sys_id": "ci1"}

    @respx.mock
    def test_find_ci_ambiguo_devuelve_none(self, snow):
        route = respx.get(f"{TABLE}/cmdb_ci").mock(
            return_value=httpx.Response(200, json={"result": [{"sys_id": "a"}, {"sys_id": "b"}]})
        )
        assert snow.find_ci("exchange") is None
        assert [query_of(c) for c in route.calls] == ["name=exchange", "nameLIKEexchange"]

    @respx.mock
    def test_valores_no_alteran_la_consulta(self, snow):
        route = respx.get(f"{TABLE}/incident").mock(
            return_value=httpx.Response(200, json={"result": []})
        )
        snow.find_open_incident("clave^active=false")
        assert query_of(route.calls.last).startswith("correlation_id=claveactive=false^active=true")

    @respx.mock
    def test_error_http(self, snow):
        respx.post(f"{TABLE}/incident").mock(return_value=httpx.Response(403, text="forbidden"))
        with pytest.raises(ServiceNowError, match="403"):
            snow.create_incident({})


class TestIncidente:
    @respx.mock
    def test_crea_incidente_con_ci_y_cambios(self, snow, report):
        respx.get(f"{TABLE}/incident").mock(return_value=httpx.Response(200, json={"result": []}))
        respx.get(f"{TABLE}/cmdb_ci").mock(
            return_value=httpx.Response(200, json={"result": [{"sys_id": "ci1"}]})
        )
        respx.get(f"{TABLE}/change_request").mock(
            return_value=httpx.Response(
                200,
                json={
                    "result": [
                        {
                            "number": "CHG001",
                            "short_description": "deploy v2",
                            "sys_created_on": "2026-10-01",
                        }
                    ]
                },
            )
        )
        post = respx.post(f"{TABLE}/incident").mock(
            return_value=httpx.Response(
                201, json={"result": {"number": "INC100", "sys_id": "inc1"}}
            )
        )

        result = open_incident(report, snow, assignment_group="grupo-sre")

        assert (result.number, result.created) == ("INC100", True)
        assert result.url.endswith("sys_id=inc1")
        body = json.loads(post.calls.last.request.content)
        assert body["correlation_id"] == "exchange-deployment-prod"
        assert body["short_description"].startswith("[Dynatrace P-2610405] Failure rate increase")
        assert (body["urgency"], body["impact"]) == ("2", "2")
        assert body["cmdb_ci"] == "ci1"
        assert body["assignment_group"] == "grupo-sre"
        assert "CHG001: deploy v2" in body["description"]
        assert "**" not in body["description"]

    @respx.mock
    def test_reutiliza_incidente_abierto(self, snow, report):
        respx.get(f"{TABLE}/incident").mock(
            return_value=httpx.Response(
                200, json={"result": [{"number": "INC050", "sys_id": "old"}]}
            )
        )
        post = respx.post(f"{TABLE}/incident")
        result = open_incident(report, snow)
        assert (result.number, result.created) == ("INC050", False)
        assert not post.called
