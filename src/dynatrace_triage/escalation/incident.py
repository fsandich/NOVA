"""Creación idempotente del incidente en ServiceNow a partir de un reporte de triage."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ..servicenow.client import ServiceNowClient
from ..triage.models import TriageReport
from ..triage.render import render_plain
from .policy import SNOW_URGENCY_IMPACT


@dataclass(frozen=True)
class IncidentResult:
    number: str
    sys_id: str
    url: str
    created: bool


def _ci_candidates(report: TriageReport) -> list[str]:
    first = report.entidad_afectada.split(" — ")[0]
    names = [report.clave_correlacion, *first.split(", ")]
    return [n for i, n in enumerate(names) if n and n not in names[:i]]


def open_incident(
    report: TriageReport, snow: ServiceNowClient, *, assignment_group: str = ""
) -> IncidentResult:
    """Crea el incidente, o devuelve el abierto con la misma clave de correlación."""
    existing = snow.find_open_incident(report.clave_correlacion)
    if existing:
        return IncidentResult(
            number=existing["number"],
            sys_id=existing["sys_id"],
            url=snow.incident_url(existing["sys_id"]),
            created=False,
        )

    ci = next((c for c in (snow.find_ci(n) for n in _ci_candidates(report)) if c), None)
    changes = snow.recent_changes(ci["sys_id"]) if ci else []

    description = render_plain(report)
    if changes:
        lines = [
            f"- {c['number']}: {c.get('short_description', '')} ({c.get('sys_created_on', '')})"
            for c in changes
        ]
        description += "\n\nCambios de los últimos 7 días sobre el CI:\n" + "\n".join(lines)

    prioridad = report.escalamiento.prioridad if report.escalamiento else "P3"
    urgency, impact = SNOW_URGENCY_IMPACT.get(prioridad, ("3", "3"))
    fields: dict[str, Any] = {
        "short_description": f"[Dynatrace {report.problem_id}] {report.resumen}"[:160],
        "description": description,
        "correlation_id": report.clave_correlacion,
        "correlation_display": "Dynatrace triage",
        "urgency": urgency,
        "impact": impact,
    }
    if ci:
        fields["cmdb_ci"] = ci["sys_id"]
    if assignment_group:
        fields["assignment_group"] = assignment_group

    created = snow.create_incident(fields)
    return IncidentResult(
        number=created["number"],
        sys_id=created["sys_id"],
        url=snow.incident_url(created["sys_id"]),
        created=True,
    )
