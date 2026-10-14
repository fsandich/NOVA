from __future__ import annotations

import json

from dynatrace_triage.foundry.setup_agent import build_openapi_spec, load_instructions, main


def test_spec_para_foundry():
    spec = build_openapi_spec("https://triage.example.com")
    assert spec["servers"] == [{"url": "https://triage.example.com"}]
    assert set(spec["paths"]) == {"/v1/triage", "/v1/incidents"}
    ops = {op["operationId"] for path in spec["paths"].values() for op in path.values()}
    assert ops == {"triage_problem", "create_incident"}
    assert spec["components"]["securitySchemes"]["apiKeyHeader"]["name"] == "x-api-key"
    # La clave va por securitySchemes, no como parámetro que el modelo pueda rellenar.
    params = [
        p
        for path in spec["paths"].values()
        for op in path.values()
        for p in op.get("parameters", [])
    ]
    assert all(p["name"] != "x-api-key" for p in params)


def test_dump_spec_sin_azure(tmp_path):
    out = tmp_path / "openapi.json"
    assert main(["--public-url", "https://triage.example.com", "--dump-spec", str(out)]) == 0
    assert json.loads(out.read_text())["info"]["title"] == "Dynatrace Triage API"


def test_instrucciones_no_permiten_reescribir_el_reporte():
    texto = load_instructions()
    assert "triage_problem" in texto and "create_incident" in texto
    assert "tal cual" in texto
