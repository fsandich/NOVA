from __future__ import annotations

import pytest

from dynatrace_triage.escalation import decide
from dynatrace_triage.triage.models import Confidence, EscalationDecision


@pytest.fixture
def report(scenario):
    engine, _ = scenario("workload_continuo")
    return engine.run("P-2610334")


@pytest.mark.parametrize(
    ("entorno", "confianza", "decision", "prioridad"),
    [
        ("PROD", Confidence.ALTA, EscalationDecision.ESCALAR, "P2"),
        ("QA", Confidence.BAJA, EscalationDecision.ESCALAR, "P3"),
        ("QA", Confidence.ALTA, EscalationDecision.PREGUNTAR, "P3"),
        ("QA", Confidence.MEDIA, EscalationDecision.PREGUNTAR, "P3"),
        (None, Confidence.ALTA, EscalationDecision.PREGUNTAR, "P3"),
    ],
)
def test_politica(report, entorno, confianza, decision, prioridad):
    report.entorno, report.confianza = entorno, confianza
    e = decide(report, {"PROD"})
    assert (e.decision, e.prioridad) == (decision, prioridad)


def test_ambientes_configurables(report):
    report.entorno = "pre-prod"
    assert decide(report, {"PROD", "PRE-PROD"}).decision is EscalationDecision.ESCALAR
