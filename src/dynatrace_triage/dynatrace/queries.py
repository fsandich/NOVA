"""Constructores de DQL.

Las consultas vienen de las validadas en el tenant con el agente de Copilot Studio
(SKILL.md: PASO R, B2–B6 y la ruta de nodo). Diferencia clave: aquí la DQL solo
trae y agrega datos; las etiquetas (ruta, patrón, lectura, peso, confianza) se
calculan en Python (`triage.rules`), donde se prueban con pytest.

Hechos del tenant que estas consultas respetan:
- En dt.davis.problems, from/to filtran por el timestamp del registro, no por
  event.start: las ventanas acotadas llevan además un filtro explícito de event.start.
- `from` sin `to` combinado con `contains(event.description, ...)` dio timeout de
  120 s: no se usa contains sobre la descripción.
- Los logs se filtran por k8s.workload.name (con OR al process group); solo por
  process group devuelven vacío.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime

from ..timeutil import iso

PROBLEM_ID_RE = re.compile(r"^(P-\d{1,12}|-?\d{1,20}_\d{1,20}(V2)?)$")

NODE_LIFECYCLE_REASONS = (
    "InstanceTerminating",
    "Evicted",
    "NodeNotReady",
    "DisruptionTerminating",
    "TerminationGracePeriodExpiring",
)
NODE_SECONDARY_REASONS = (
    "FailedDraining",
    "IPAllocationFailed",
    "FailedScheduling",
    "FailedGetResourceMetric",
    "OOMKilling",
    "BackOff",
)


def validate_problem_id(problem_id: str) -> str:
    problem_id = problem_id.strip()
    if not PROBLEM_ID_RE.match(problem_id):
        raise ValueError(f"problemId inválido: {problem_id!r} (se espera P-<número>)")
    return problem_id


def q(value: str) -> str:
    """Literal de texto DQL con escape de comillas y barras."""
    return '"' + str(value).replace("\\", "\\\\").replace('"', '\\"') + '"'


def arr(values: Iterable[str]) -> str:
    items = [q(v) for v in values] or [q("ninguno")]
    return f"array({', '.join(items)})"


def problem_detail(problem_id: str, lookback_days: int) -> str:
    pid = q(validate_problem_id(problem_id))
    return (
        f"fetch dt.davis.problems, from: now()-{lookback_days}d\n"
        f"| filter display_id == {pid} or event.id == {pid}\n"
        "| dedup display_id, sort: {timestamp desc}"
    )


# --- Ruta B ---------------------------------------------------------------------------


def recurrence(event_name: str, entity_id: str, lookback_days: int) -> str:
    """B2: cada problem con el mismo event.name sobre la misma entidad."""
    return (
        f"fetch dt.davis.problems, from: now()-{lookback_days}d\n"
        f"| filter event.name == {q(event_name)} and in({q(entity_id)}, affected_entity_ids)\n"
        "| dedup display_id, sort: {timestamp desc}\n"
        "| fields display_id, event.status, event.start, event.end\n"
        "| sort event.start desc\n"
        "| limit 50"
    )


def workload_problems(entity_id: str, cloud_app_id: str, lookback_days: int) -> str:
    """B3: problems del mismo workload agregados por tipo."""
    e, ca = q(entity_id), q(cloud_app_id)
    return (
        f"fetch dt.davis.problems, from: now()-{lookback_days}d\n"
        f"| filter in({e}, affected_entity_ids) or in({ca}, affected_entity_ids)"
        f" or in({ca}, related_entity_ids)\n"
        "| dedup display_id, sort: {timestamp desc}\n"
        "| fieldsAdd fin = coalesce(event.end, now())\n"
        "| fieldsAdd dur_min = (fin - event.start) / 1m\n"
        "| summarize by: {event.category, event.name}, {problems = count(),"
        ' activos = countIf(event.status == "ACTIVE"), primero = min(event.start),'
        " ultimo = max(event.start), fin_max = max(fin), min_con_problema = sum(dur_min)}\n"
        "| sort primero asc"
    )


def cluster_window(cluster_id: str, exclude_problem_id: str, start: datetime, end: datetime) -> str:
    """B4: otros problems del cluster con inicio dentro de [start, end]."""
    s, e = q(iso(start)), q(iso(end))
    return (
        f"fetch dt.davis.problems, from: {s}, to: {e}\n"
        f"| filter in({q(cluster_id)}, related_entity_ids)\n"
        f"| filter event.start >= toTimestamp({s}) and event.start <= toTimestamp({e})\n"
        f"| filter display_id != {q(exclude_problem_id)}\n"
        "| dedup display_id, sort: {timestamp desc}\n"
        "| summarize by: {event.category, event.name}, {problems = count(),"
        " primero = min(event.start), ultimo = max(event.start)}\n"
        "| sort problems desc\n"
        "| limit 20"
    )


def workload_k8s_events(
    cluster_id: str, namespace: str, workloads: list[str], start: datetime, end: datetime
) -> str:
    """B5: eventos de Kubernetes de los pods del workload, agrupados por motivo."""
    contains = " or ".join(
        f"contains(k8s.pod.name, {q(w)}) or contains(k8s.workload.name, {q(w)})" for w in workloads
    )
    return (
        f"fetch events, from: {q(iso(start))}, to: {q(iso(end))}\n"
        f"| filter dt.entity.kubernetes_cluster == {q(cluster_id)}"
        f' and event.provider == "KUBERNETES_EVENT" and k8s.namespace.name == {q(namespace)}\n'
        f"| filter {contains}\n"
        "| summarize by: {dt.kubernetes.event.reason}, {eventos = count(),"
        " primero = min(event.start), ultimo = max(timestamp)}\n"
        "| sort eventos desc"
    )


def error_logs(
    workloads: list[str], process_groups: list[str], start: datetime, end: datetime
) -> str:
    """B6: patrones de logs de error del servicio."""
    return (
        f"fetch logs, from: {q(iso(start))}, to: {q(iso(end))}\n"
        f"| filter in(k8s.workload.name, {arr(workloads)})"
        f" or in(dt.entity.process_group, {arr(process_groups)})\n"
        '| filter in(loglevel, array("ERROR", "WARN", "SEVERE", "FATAL"))\n'
        '| parse content, "JSON:j"\n'
        "| fieldsAdd mensaje = coalesce(toString(j[message]), content)\n"
        "| summarize by: {loglevel, logger = toString(j[logger_name]),"
        " patron = substring(mensaje, from: 0, to: 160)}, {lineas = count(),"
        " primero = min(timestamp), ultimo = max(timestamp)}\n"
        "| sort lineas desc\n"
        "| limit 20"
    )


# --- Ruta de nodo/cluster -------------------------------------------------------------


def cluster_contention_pattern(cluster_id: str, start: datetime) -> str:
    """Problems RESOURCE_CONTENTION del cluster por hora desde el inicio del problem."""
    return (
        f"fetch dt.davis.problems, from: {q(iso(start))}\n"
        f'| filter event.category == "RESOURCE_CONTENTION" and in({q(cluster_id)}, related_entity_ids)\n'
        "| dedup display_id, sort: {timestamp desc}\n"
        "| summarize by: {hora = bin(event.start, 1h)}, {problems = count(),"
        " ids = collectDistinct(display_id)}\n"
        "| sort hora asc"
    )


def cluster_k8s_events(cluster_id: str, start: datetime) -> str:
    reasons = arr(NODE_LIFECYCLE_REASONS + NODE_SECONDARY_REASONS)
    return (
        f"fetch events, from: {q(iso(start))}\n"
        f'| filter dt.entity.kubernetes_cluster == {q(cluster_id)} and event.provider == "KUBERNETES_EVENT"\n'
        f"| filter in(dt.kubernetes.event.reason, {reasons})\n"
        "| summarize by: {dt.kubernetes.event.reason}, {eventos = count(),"
        " primero = min(event.start), ultimo = max(timestamp),"
        " namespaces = collectDistinct(k8s.namespace.name)}\n"
        "| sort eventos desc"
    )


def pod_restarts(cluster_name: str, start: datetime) -> str:
    """Procesos con OneAgent vivos, agrupados por la hora en que arrancaron."""
    return (
        f"fetch events, from: {q(iso(start))}\n"
        f'| filter k8s.cluster.name == {q(cluster_name)} and event.type == "ONEAGENT_METADATA"\n'
        "| dedup dt.agent.module.id, sort: {timestamp desc}\n"
        "| summarize by: {inicio = bin(dt.agent.module.startup_timestamp, 1h),"
        " k8s.namespace.name}, {procesos = count()}\n"
        "| sort inicio asc"
    )


def cluster_problems_around(cluster_id: str, start: datetime, end: datetime) -> str:
    """(A) Problems no-contention del cluster que empezaron en [start, end]."""
    s, e = q(iso(start)), q(iso(end))
    return (
        f"fetch dt.davis.problems, from: {s}, to: {e}\n"
        f"| filter in({q(cluster_id)}, related_entity_ids)\n"
        '| filter event.category != "RESOURCE_CONTENTION"\n'
        f"| filter event.start >= toTimestamp({s}) and event.start <= toTimestamp({e})\n"
        "| dedup display_id, sort: {timestamp desc}\n"
        "| summarize by: {event.category, event.name}, {problems = count(),"
        " primero = min(event.start), ultimo = max(event.start)}\n"
        "| sort primero asc"
    )


def cluster_problems_period(cluster_id: str, start: datetime) -> str:
    """(B) Resumen de problems no-contention del cluster desde el inicio hasta ahora."""
    return (
        f"fetch dt.davis.problems, from: {q(iso(start))}\n"
        f"| filter in({q(cluster_id)}, related_entity_ids)\n"
        '| filter event.category != "RESOURCE_CONTENTION"\n'
        "| dedup display_id, sort: {timestamp desc}\n"
        "| summarize by: {event.category, event.name}, {problems = count(),"
        ' activos = countIf(event.status == "ACTIVE"), primero = min(event.start),'
        " ultimo = max(event.start)}\n"
        "| sort problems desc\n"
        "| limit 25"
    )
