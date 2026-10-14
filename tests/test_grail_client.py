from __future__ import annotations

import httpx
import pytest
import respx

from dynatrace_triage.dynatrace.client import DqlError, GrailClient, QueryResult

BASE = "https://demo.apps.dynatrace.com"
EXECUTE = f"{BASE}{GrailClient.EXECUTE_PATH}"
POLL = f"{BASE}{GrailClient.POLL_PATH}"

SUCCEEDED = {
    "state": "SUCCEEDED",
    "result": {
        "records": [{"display_id": "P-1"}],
        "metadata": {"grail": {"scannedRecords": 698221, "notifications": []}},
    },
}


@pytest.fixture
def client():
    return GrailClient(BASE, "dt0s16.TOKEN", sleep=lambda _: None)


@respx.mock
def test_respuesta_inmediata(client):
    route = respx.post(EXECUTE).mock(return_value=httpx.Response(200, json=SUCCEEDED))
    result = client.query("fetch logs")
    assert result.records == [{"display_id": "P-1"}]
    assert result.scanned_records == 698221
    request = route.calls.last.request
    assert request.headers["Authorization"] == "Bearer dt0s16.TOKEN"
    assert b'"query":"fetch logs"' in request.content


@respx.mock
def test_sigue_con_poll_si_esta_running(client):
    respx.post(EXECUTE).mock(
        return_value=httpx.Response(202, json={"state": "RUNNING", "requestToken": "tok"})
    )
    poll = respx.get(POLL).mock(
        side_effect=[
            httpx.Response(200, json={"state": "RUNNING", "requestToken": "tok"}),
            httpx.Response(200, json=SUCCEEDED),
        ]
    )
    assert client.query("fetch logs").records
    assert poll.call_count == 2
    assert poll.calls.last.request.url.params["request-token"] == "tok"


@respx.mock
def test_error_http_con_mensaje(client):
    respx.post(EXECUTE).mock(
        return_value=httpx.Response(
            400, json={"error": {"message": "PARSE_ERROR_UNEXPECTED_TOKEN"}}
        )
    )
    with pytest.raises(DqlError, match="HTTP 400: PARSE_ERROR"):
        client.query("fetch mal")


@respx.mock
def test_estado_fallido(client):
    respx.post(EXECUTE).mock(return_value=httpx.Response(200, json={"state": "FAILED"}))
    with pytest.raises(DqlError, match="FAILED"):
        client.query("fetch logs")


@respx.mock
def test_timeout_de_poll():
    client = GrailClient(BASE, "t", timeout_s=0, sleep=lambda _: None)
    respx.post(EXECUTE).mock(
        return_value=httpx.Response(202, json={"state": "RUNNING", "requestToken": "t"})
    )
    with pytest.raises(DqlError, match="superó"):
        client.query("fetch logs")


def test_requiere_url_y_token():
    with pytest.raises(ValueError):
        GrailClient("", "")


@pytest.mark.parametrize(
    ("note", "truncado"),
    [
        (
            {
                "notificationType": "PARAMETERS_SHOULD_BE_GROUPED",
                "message": "The parameters should be grouped",
            },
            False,
        ),
        ({"notificationType": "BYTE_LIMIT_ADDED", "message": "x"}, True),
        (
            {"notificationType": "OTHER", "message": "The result has been limited to 1000 records"},
            True,
        ),
    ],
)
def test_deteccion_de_truncado(note, truncado):
    assert QueryResult(records=[], notifications=[note]).truncated is truncado
