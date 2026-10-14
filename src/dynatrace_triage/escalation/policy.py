"""Política de escalamiento del agente, ahora como código.

- Prioridad alta (P1/P2) o confianza Baja → escalar de inmediato.
- Prioridad media/baja → entregar la hipótesis y preguntar si se escala.

Dynatrace no trae P1–P4: la prioridad se deriva del ambiente. Los problems de un
ambiente listado en HIGH_PRIORITY_ENVIRONMENTS (por defecto PROD) son P2; el resto,
P3. Ajusta la regla si el equipo usa otra convención.
"""

from __future__ import annotations

from collections.abc import Iterable

from ..triage.models import Confidence, Escalation, EscalationDecision, TriageReport

HIGH_PRIORITIES = ("P1", "P2")

# Mapa prioridad → (urgency, impact) de ServiceNow.
SNOW_URGENCY_IMPACT = {"P1": ("1", "1"), "P2": ("2", "2"), "P3": ("3", "3"), "P4": ("3", "3")}


def priority_for(report: TriageReport, high_priority_envs: Iterable[str]) -> str:
    envs = {e.upper() for e in high_priority_envs}
    return "P2" if (report.entorno or "").upper() in envs else "P3"


def decide(report: TriageReport, high_priority_envs: Iterable[str]) -> Escalation:
    prioridad = priority_for(report, high_priority_envs)
    if prioridad in HIGH_PRIORITIES:
        return Escalation(
            decision=EscalationDecision.ESCALAR,
            prioridad=prioridad,
            motivo=f"prioridad {prioridad} (ambiente {report.entorno})",
        )
    if report.confianza is Confidence.BAJA:
        return Escalation(
            decision=EscalationDecision.ESCALAR,
            prioridad=prioridad,
            motivo="confianza Baja: falta evidencia para cerrar el diagnóstico",
        )
    return Escalation(
        decision=EscalationDecision.PREGUNTAR,
        prioridad=prioridad,
        motivo=f"prioridad {prioridad} con confianza {report.confianza.value}: preguntar si se escala",
    )
