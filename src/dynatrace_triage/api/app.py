"""API HTTP. Foundry la consume como herramienta OpenAPI; Dynatrace llama al webhook."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from functools import lru_cache
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, Field

from ..config import Settings
from ..dynatrace.client import DqlError
from ..dynatrace.queries import PROBLEM_ID_RE
from ..service import ServiceNowDisabled, TriageService, build_service
from ..servicenow.client import ServiceNowError
from ..triage.engine import ProblemNotFound
from ..triage.models import TriageReport
from ..triage.render import render_markdown

ProblemId = Annotated[
    str,
    Field(pattern=PROBLEM_ID_RE.pattern, description="ID del problem de Dynatrace, ej. P-2610334"),
]


class TriageRequest(BaseModel):
    problem_id: ProblemId


class TriageResponse(BaseModel):
    markdown: str = Field(description="Reporte listo para mostrar al usuario, sin modificar")
    report: TriageReport


class IncidentResponse(BaseModel):
    number: str
    url: str
    created: bool = Field(description="false si ya había un incidente abierto con la misma clave")
    problem_id: str


class WebhookResponse(BaseModel):
    problem_id: str
    decision: str
    confianza: str
    incident_number: str | None = None


def create_app(
    service_factory: Callable[[], TriageService] | None = None,
    settings: Settings | None = None,
) -> FastAPI:
    settings = settings or Settings()

    @lru_cache(maxsize=1)
    def default_service() -> TriageService:
        return build_service(settings)

    get_service = service_factory or default_service

    def require_api_key(x_api_key: Annotated[str | None, Header()] = None) -> None:
        keys = settings.api_key_set
        if not keys:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE, "La API no tiene claves configuradas"
            )
        if not x_api_key or not any(secrets.compare_digest(x_api_key, k) for k in keys):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "x-api-key inválida")

    app = FastAPI(
        title="Dynatrace Triage API",
        version="0.1.0",
        description="Triage de solo lectura de problems de Dynatrace y escalamiento a ServiceNow.",
        servers=[{"url": settings.public_base_url}],
    )
    auth = [Depends(require_api_key)]

    def run(fn: Callable):
        try:
            return fn()
        except ProblemNotFound as exc:
            raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc
        except DqlError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Dynatrace: {exc}") from exc
        except ServiceNowDisabled as exc:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, str(exc)) from exc
        except ServiceNowError as exc:
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"ServiceNow: {exc}") from exc

    @app.get("/health", include_in_schema=False)
    def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post(
        "/v1/triage",
        operation_id="triage_problem",
        summary="Analiza un problem de Dynatrace y devuelve la hipótesis de causa raíz",
        response_model=TriageResponse,
        dependencies=auth,
    )
    def triage(body: TriageRequest) -> TriageResponse:
        report = run(lambda: get_service().triage(body.problem_id))
        return TriageResponse(markdown=render_markdown(report), report=report)

    @app.post(
        "/v1/incidents",
        operation_id="create_incident",
        summary="Crea (o reutiliza) el incidente de ServiceNow de un problem",
        description="Llamar solo si la política dice ESCALAR o el usuario confirmó que quiere escalar.",
        response_model=IncidentResponse,
        dependencies=auth,
    )
    def create_incident(body: TriageRequest) -> IncidentResponse:
        report, incident = run(lambda: get_service().open_incident(body.problem_id))
        return IncidentResponse(
            number=incident.number,
            url=incident.url,
            created=incident.created,
            problem_id=report.problem_id,
        )

    @app.post(
        "/v1/webhooks/dynatrace",
        include_in_schema=False,
        response_model=WebhookResponse,
        dependencies=auth,
    )
    def dynatrace_webhook(body: TriageRequest) -> WebhookResponse:
        result = run(lambda: get_service().handle_webhook(body.problem_id))
        return WebhookResponse(
            problem_id=result.report.problem_id,
            decision=result.report.escalamiento.decision.value,
            confianza=result.report.confianza.value,
            incident_number=result.incident.number if result.incident else None,
        )

    return app


app = create_app()
