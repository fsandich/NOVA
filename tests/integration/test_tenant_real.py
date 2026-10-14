"""Pruebas contra el tenant real. Se omiten por defecto.

    pytest -m integration --problem-id P-2610334

Requieren .env con DT_ENV_URL y DT_PLATFORM_TOKEN. Solo leen de Dynatrace; nunca crean
incidentes en ServiceNow.
"""

from __future__ import annotations

import pytest

from dynatrace_triage.config import Settings
from dynatrace_triage.dynatrace.client import GrailClient
from dynatrace_triage.triage.engine import TriageEngine
from dynatrace_triage.triage.models import StepStatus
from dynatrace_triage.triage.render import render_markdown

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def settings():
    s = Settings()
    if not s.dt_env_url or not s.dt_platform_token.get_secret_value():
        pytest.skip("falta DT_ENV_URL / DT_PLATFORM_TOKEN en .env")
    return s


@pytest.fixture(scope="module")
def engine(settings):
    grail = GrailClient(settings.dt_env_url, settings.dt_platform_token.get_secret_value())
    return TriageEngine(grail, settings.load_clusters(), lookback_days=settings.dt_lookback_days)


def test_grail_responde(settings):
    grail = GrailClient(settings.dt_env_url, settings.dt_platform_token.get_secret_value())
    result = grail.query("fetch dt.davis.problems, from: now()-1h | limit 1")
    assert result.scanned_records >= 0


def test_triage_completo(engine, problem_id):
    report = engine.run(problem_id)
    errores = [s for s in report.pasos if s.status is StepStatus.ERROR]
    assert not errores, f"pasos con error: {errores}"
    print("\n" + render_markdown(report))
