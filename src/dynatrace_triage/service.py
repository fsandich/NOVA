"""Une triage, política de escalamiento y ServiceNow. La usan la API y el CLI."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .config import Settings
from .dynatrace.client import GrailClient
from .escalation import IncidentResult, decide, open_incident
from .servicenow.client import ServiceNowClient
from .triage.engine import TriageEngine
from .triage.models import EscalationDecision, TriageReport


class ServiceNowDisabled(RuntimeError):
    pass


@dataclass
class WebhookResult:
    report: TriageReport
    incident: IncidentResult | None


class TriageService:
    def __init__(
        self,
        engine: TriageEngine,
        *,
        high_priority_envs: Iterable[str] = ("PROD",),
        snow: ServiceNowClient | None = None,
        assignment_group: str = "",
    ) -> None:
        self._engine = engine
        self._high_envs = tuple(high_priority_envs)
        self._snow = snow
        self._assignment_group = assignment_group

    def triage(self, problem_id: str) -> TriageReport:
        report = self._engine.run(problem_id)
        report.escalamiento = decide(report, self._high_envs)
        return report

    def open_incident(self, problem_id: str) -> tuple[TriageReport, IncidentResult]:
        """Vuelve a hacer el triage (no confía en texto que venga del chat) y crea el incidente."""
        if self._snow is None:
            raise ServiceNowDisabled("ServiceNow no está configurado")
        report = self.triage(problem_id)
        return report, open_incident(report, self._snow, assignment_group=self._assignment_group)

    def handle_webhook(self, problem_id: str) -> WebhookResult:
        """Camino automático: sin humano, solo se crea incidente si la política dice ESCALAR."""
        report = self.triage(problem_id)
        incident = None
        if self._snow is not None and report.escalamiento.decision is EscalationDecision.ESCALAR:
            incident = open_incident(report, self._snow, assignment_group=self._assignment_group)
        return WebhookResult(report, incident)


def build_service(settings: Settings) -> TriageService:
    grail = GrailClient(
        settings.dt_env_url,
        settings.dt_platform_token.get_secret_value(),
        timeout_s=settings.dt_query_timeout_s,
    )
    engine = TriageEngine(grail, settings.load_clusters(), lookback_days=settings.dt_lookback_days)
    snow = None
    if settings.servicenow_enabled:
        snow = ServiceNowClient(
            settings.snow_instance_url,
            settings.snow_username,
            settings.snow_password.get_secret_value(),
        )
    return TriageService(
        engine,
        high_priority_envs=settings.high_priority_env_set,
        snow=snow,
        assignment_group=settings.snow_assignment_group,
    )
