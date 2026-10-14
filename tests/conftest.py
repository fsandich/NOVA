from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

import pytest

from dynatrace_triage.dynatrace.client import DqlError, QueryResult
from dynatrace_triage.timeutil import parse_ts
from dynatrace_triage.triage.engine import TriageEngine

FIXTURES = Path(__file__).parent / "fixtures"


def pytest_addoption(parser):
    parser.addoption(
        "--problem-id", default="", help="problem real para las pruebas de integración"
    )


# Cada consulta se reconoce por un fragmento propio; gana el primero que coincide.
STEP_MARKERS = [
    ("detalle", "filter display_id == "),
    ("b2", "fields display_id, event.status"),
    ("b3", "min_con_problema"),
    ("b4", "filter display_id != "),
    ("b5", "k8s.pod.name"),
    ("b6", "fetch logs"),
    ("patron", 'event.category == "RESOURCE_CONTENTION"'),
    ("eventos", "InstanceTerminating"),
    ("reinicios", "ONEAGENT_METADATA"),
    ("problems_a", "toTimestamp"),
    ("problems_b", "limit 25"),
]


def step_of(dql: str) -> str:
    for step, marker in STEP_MARKERS:
        if marker in dql:
            return step
    raise AssertionError(f"consulta no reconocida:\n{dql}")


class FakeDql:
    """Devuelve respuestas grabadas por paso; registra las consultas ejecutadas."""

    def __init__(self, responses: dict[str, dict]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, str]] = []

    def query(self, dql: str) -> QueryResult:
        step = step_of(dql)
        self.calls.append((step, dql))
        data = self.responses.get(step, {"records": []})
        if "error" in data:
            raise DqlError(data["error"])
        return QueryResult(
            records=data.get("records", []), notifications=data.get("notifications", [])
        )

    @property
    def steps(self) -> list[str]:
        return [s for s, _ in self.calls]


def load_scenario(name: str) -> tuple[dict[str, dict], datetime]:
    data = json.loads((FIXTURES / f"{name}.json").read_text(encoding="utf-8"))
    return data["responses"], parse_ts(data["now"])


@pytest.fixture
def scenario():
    """scenario("workload_continuo") → (engine, fake). Permite modificar respuestas antes de correr."""

    def _make(name: str, clusters: dict[str, str] | None = None, **overrides):
        responses, now = load_scenario(name)
        responses.update(overrides)
        fake = FakeDql(responses)
        engine = TriageEngine(fake, clusters or {}, clock=lambda: now)
        return engine, fake

    return _make
