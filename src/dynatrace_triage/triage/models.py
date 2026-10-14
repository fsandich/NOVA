"""Modelos del dominio: el problem, las filas analizadas y el reporte final."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from ..timeutil import parse_ts


class Route(StrEnum):
    NODO = "RUTA_NODO"
    B = "RUTA_B"


class Confidence(StrEnum):
    ALTA = "Alta"
    MEDIA = "Media"
    BAJA = "Baja"


class Pattern(StrEnum):
    CONTINUO = "CONTINUO"
    RECURRENTE = "RECURRENTE"
    ACTIVO = "ACTIVO"
    AISLADO = "AISLADO"


class Reading(StrEnum):
    ORIGEN_PROBABLE = "ORIGEN_PROBABLE"
    ESTE_PROBLEM_CONTINUO = "ESTE_PROBLEM_CONTINUO"
    ESTE_PROBLEM = "ESTE_PROBLEM"
    CONTEXTO = "CONTEXTO"


class StepStatus(StrEnum):
    OK = "ok"
    VACIO = "vacío"
    ERROR = "error"
    NO_APLICA = "no aplica"


class EscalationDecision(StrEnum):
    ESCALAR = "ESCALAR"
    PREGUNTAR = "PREGUNTAR"


def _list(record: dict[str, Any], key: str) -> list[str]:
    value = record.get(key)
    if value is None:
        return []
    if isinstance(value, list):
        return [str(v) for v in value]
    return [str(value)]


class Problem(BaseModel):
    display_id: str
    event_id: str = ""
    name: str
    category: str
    status: str
    start: datetime
    end: datetime | None = None
    description: str = ""
    severity: str = ""
    impact_levels: list[str] = Field(default_factory=list)
    affected_entity_ids: list[str] = Field(default_factory=list)
    affected_entity_types: list[str] = Field(default_factory=list)
    affected_entity_names: list[str] = Field(default_factory=list)
    related_entity_ids: list[str] = Field(default_factory=list)
    cluster_ids: list[str] = Field(default_factory=list)
    cluster_names: list[str] = Field(default_factory=list)
    namespaces: list[str] = Field(default_factory=list)
    workloads: list[str] = Field(default_factory=list)
    process_groups: list[str] = Field(default_factory=list)
    cloud_applications: list[str] = Field(default_factory=list)
    entity_tags: list[str] = Field(default_factory=list)
    security_context: list[str] = Field(default_factory=list)

    @property
    def is_active(self) -> bool:
        return self.status.upper() == "ACTIVE" or self.end is None

    @property
    def environment(self) -> str | None:
        for tag in self.entity_tags:
            key, _, value = tag.partition(":")
            if key.strip().lower() in ("ambiente", "environment") and value.strip():
                return value.strip().upper()
        return self.security_context[0].upper() if self.security_context else None

    @classmethod
    def from_record(cls, record: dict[str, Any]) -> Problem:
        start = parse_ts(record.get("event.start"))
        if start is None:
            raise ValueError("el problem no trae event.start")
        return cls(
            display_id=str(record.get("display_id", "")),
            event_id=str(record.get("event.id", "")),
            name=str(record.get("event.name", "")),
            category=str(record.get("event.category", "")),
            status=str(record.get("event.status", "")),
            start=start,
            end=parse_ts(record.get("event.end")),
            description=str(record.get("event.description", "")),
            severity=str(record.get("event.severity", "")),
            impact_levels=_list(record, "dt.davis.impact_level"),
            affected_entity_ids=_list(record, "affected_entity_ids"),
            affected_entity_types=_list(record, "affected_entity_types"),
            affected_entity_names=_list(record, "affected_entity_names"),
            related_entity_ids=_list(record, "related_entity_ids"),
            cluster_ids=_list(record, "dt.entity.kubernetes_cluster"),
            cluster_names=_list(record, "k8s.cluster.name"),
            namespaces=_list(record, "k8s.namespace.name"),
            workloads=_list(record, "k8s.workload.name"),
            process_groups=_list(record, "dt.entity.process_group"),
            cloud_applications=_list(record, "dt.entity.cloud_application"),
            entity_tags=_list(record, "entity_tags"),
            security_context=_list(record, "dt.security_context"),
        )


class Step(BaseModel):
    name: str
    status: StepStatus
    detail: str = ""
    truncated: bool = False


class WorkloadRow(BaseModel):
    category: str
    name: str
    lectura: Reading
    patron: Pattern
    problems: int
    activos: int
    primero: datetime
    ultimo: datetime


class LogPattern(BaseModel):
    loglevel: str
    logger: str
    patron: str
    lineas: int
    primero: datetime
    ultimo: datetime


class K8sReason(BaseModel):
    reason: str
    eventos: int
    primero: datetime | None
    ultimo: datetime | None
    evidencia: bool
    namespaces: list[str] = Field(default_factory=list)


class ClusterProblemRow(BaseModel):
    category: str
    name: str
    problems: int
    activos: int | None = None
    primero: datetime
    ultimo: datetime


class HourBucket(BaseModel):
    hora: datetime
    problems: int
    incluye_problem: bool


class Escalation(BaseModel):
    decision: EscalationDecision
    prioridad: str
    motivo: str


class TriageReport(BaseModel):
    problem_id: str
    ruta: Route
    resumen: str
    entidad_afectada: str
    entorno: str | None
    clave_correlacion: str
    kb: str = ""
    dashboard: str = ""
    severidad: str
    evidencia: list[str]
    duracion: str
    recurrencia: str
    patron_cluster: list[HourBucket] = Field(default_factory=list)
    problems_workload: list[WorkloadRow] = Field(default_factory=list)
    problems_cluster: list[ClusterProblemRow] = Field(default_factory=list)
    problems_cluster_periodo: list[ClusterProblemRow] = Field(default_factory=list)
    hipotesis: str
    causa_tipo: str
    confianza: Confidence
    accion_recomendada: str
    hallazgos_secundarios: list[str] = Field(default_factory=list)
    pasos: list[Step] = Field(default_factory=list)
    cobertura: list[str] = Field(default_factory=list)
    escalamiento: Escalation | None = None
