"""Reglas de triage como funciones puras.

Cada regla reemplaza algo que en el agente de Copilot Studio era prosa del
SKILL.md o una etiqueta calculada en la DQL (ruta, patron, lectura,
duracion_reportar, peso de logs). Al ser funciones puras se prueban con pytest
con datos reales anonimizados.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from ..dynatrace.queries import NODE_LIFECYCLE_REASONS
from ..timeutil import floor_hour, fmt_minutes, human, minutes_between, parse_ts
from .models import (
    ClusterProblemRow,
    Confidence,
    HourBucket,
    K8sReason,
    LogPattern,
    Pattern,
    Problem,
    Reading,
    Route,
    WorkloadRow,
)

# --- Ruta -----------------------------------------------------------------------------

NODE_ENTITY_TYPES = ("dt.entity.kubernetes_node", "dt.entity.kubernetes_cluster")


def classify_route(problem: Problem) -> Route:
    """PASO R: nodo/cluster o RESOURCE_CONTENTION → ruta de nodo; todo lo demás → ruta B.

    No usa dt.entity.kubernetes_cluster: casi todos los problems lo traen.
    """
    if problem.category == "RESOURCE_CONTENTION":
        return Route.NODO
    if any(t in NODE_ENTITY_TYPES for t in problem.affected_entity_types):
        return Route.NODO
    return Route.B


# --- Datos del problem ----------------------------------------------------------------

_KB_RE = re.compile(r"\bKB\d{7}\b")
_DASHBOARD_RE = re.compile(r"Dashboard\**\s*:\s*\**\s*(https?://[^\s)\]>*]+)", re.IGNORECASE)


def extract_kb(description: str) -> str:
    match = _KB_RE.search(description or "")
    return match.group(0) if match else ""


def extract_dashboard(description: str) -> str:
    match = _DASHBOARD_RE.search(description or "")
    return match.group(1) if match else ""


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def correlation_key(problem: Problem, route: Route, cluster_name: str | None) -> str:
    """Clave para no duplicar incidentes: workload padre, o cluster + tipo para nodos.

    En EKS Auto Mode los nodos rotan y cambian de nombre, así que nunca se usa el nodo.
    """
    if route is Route.NODO:
        return f"{cluster_name or 'cluster'}-{slug(problem.name)}"
    if problem.workloads:
        return problem.workloads[0]
    if problem.affected_entity_names:
        return problem.affected_entity_names[0]
    return problem.display_id


def pick_cluster(problem: Problem, clusters: Mapping[str, str]) -> tuple[str, str] | None:
    """Devuelve (id, nombre) del cluster del problem.

    Con un solo ID lo usa; con varios elige el del ambiente del problem según el mapa
    nombre → ID (los IDs y nombres del detalle no vienen en el mismo orden).
    """
    ids = problem.cluster_ids
    by_id = {cid: name for name, cid in clusters.items()}
    if not ids:
        return None
    if len(ids) == 1:
        cid = ids[0]
        name = by_id.get(cid) or (
            problem.cluster_names[0] if len(problem.cluster_names) == 1 else cid
        )
        return cid, name
    env = (problem.environment or "").lower()
    if env:
        for name, cid in clusters.items():
            if cid in ids and name.lower().endswith(f"-{env}"):
                return cid, name
    return ids[0], by_id.get(ids[0], ids[0])


# --- B2: duración y recurrencia -------------------------------------------------------


@dataclass(frozen=True)
class RecurrenceSummary:
    duracion: str
    recurrencia: str
    problems: int
    activos: int
    cobertura: int
    continuo: bool


def summarize_recurrence(
    rows: Iterable[Mapping[str, Any]], problem: Problem, now: datetime
) -> RecurrenceSummary:
    """Equivale a duracion_reportar / recurrencia_reportar de B2.

    Si las ocurrencias cubren ≥ 90 % del tiempo desde la primera, es un incidente
    continuo: la duración relevante es desde la primera, no la de este problem.
    """
    parsed = []
    for row in rows:
        start = parse_ts(row.get("event.start"))
        if start is None:
            continue
        end = parse_ts(row.get("event.end"))
        parsed.append((str(row.get("display_id")), str(row.get("event.status", "")), start, end))
    if not any(pid == problem.display_id for pid, *_ in parsed):
        parsed.append((problem.display_id, problem.status, problem.start, problem.end))

    first = min(start for _, _, start, _ in parsed)
    last = max(start for _, _, start, _ in parsed)
    last_end = max((end or now) for _, _, _, end in parsed)
    total = sum(minutes_between(start, end or now) for _, _, start, end in parsed)
    span = minutes_between(first, last_end)
    coverage = 100 if span <= 0 else round(100 * total / span)
    count = len(parsed)
    active = sum(1 for _, status, _, end in parsed if status.upper() == "ACTIVE" or end is None)
    continuous = count >= 2 and coverage >= 90

    this_minutes = minutes_between(problem.start, problem.end or now)
    if problem.is_active:
        this_text = f"activo desde {human(problem.start)} ({fmt_minutes(this_minutes)} hasta ahora)"
    else:
        this_text = f"duró {fmt_minutes(this_minutes)}"

    if continuous:
        duracion = (
            f"Incidente continuo desde {human(first)} ({count} problems seguidos, "
            f"{active} activo{'s' if active != 1 else ''}); este problem {this_text}"
        )
    else:
        duracion = f"Este problem {this_text}"

    recurrencia = (
        f"{count} {'vez' if count == 1 else 'veces'} '{problem.name}' sobre esta entidad en "
        f"7 días, entre {human(first)} y {human(last)}; activos: {active}"
    )
    return RecurrenceSummary(duracion, recurrencia, count, active, coverage, continuous)


# --- B3: problems del mismo workload --------------------------------------------------


def _int(value: Any) -> int:
    return int(float(value)) if value not in (None, "") else 0


def classify_workload_rows(
    rows: Iterable[Mapping[str, Any]], event_name: str, problem_start: datetime
) -> list[WorkloadRow]:
    """Calcula patron y lectura de cada tipo de problem del workload.

    ORIGEN_PROBABLE: otro tipo de problem, continuo o activo, que empezó ANTES de
    este. Lo que coincide con el inicio del problem es contexto, no causa.
    """
    result = []
    for row in rows:
        primero = parse_ts(row.get("primero"))
        ultimo = parse_ts(row.get("ultimo"))
        fin_max = parse_ts(row.get("fin_max")) or ultimo
        if primero is None or ultimo is None:
            continue
        problems = _int(row.get("problems"))
        activos = _int(row.get("activos"))
        total = float(row.get("min_con_problema") or 0)
        span = minutes_between(primero, fin_max) if fin_max else 0
        coverage = 100 if span <= 0 else round(100 * total / span)

        if problems >= 2 and coverage >= 90:
            patron = Pattern.CONTINUO
        elif problems >= 3:
            patron = Pattern.RECURRENTE
        elif activos > 0:
            patron = Pattern.ACTIVO
        else:
            patron = Pattern.AISLADO

        name = str(row.get("event.name", ""))
        if name == event_name:
            lectura = (
                Reading.ESTE_PROBLEM_CONTINUO
                if patron is Pattern.CONTINUO
                else Reading.ESTE_PROBLEM
            )
        elif patron in (Pattern.CONTINUO, Pattern.ACTIVO) and primero < problem_start:
            lectura = Reading.ORIGEN_PROBABLE
        else:
            lectura = Reading.CONTEXTO

        result.append(
            WorkloadRow(
                category=str(row.get("event.category", "")),
                name=name,
                lectura=lectura,
                patron=patron,
                problems=problems,
                activos=activos,
                primero=primero,
                ultimo=ultimo,
            )
        )
    return sorted(result, key=lambda r: r.primero)


def first_origin(rows: Iterable[WorkloadRow]) -> WorkloadRow | None:
    return next((r for r in rows if r.lectura is Reading.ORIGEN_PROBABLE), None)


# --- B4 / problems del cluster --------------------------------------------------------


def cluster_problem_rows(rows: Iterable[Mapping[str, Any]]) -> list[ClusterProblemRow]:
    result = []
    for row in rows:
        primero, ultimo = parse_ts(row.get("primero")), parse_ts(row.get("ultimo"))
        if primero is None or ultimo is None:
            continue
        result.append(
            ClusterProblemRow(
                category=str(row.get("event.category", "")),
                name=str(row.get("event.name", "")),
                problems=_int(row.get("problems")),
                activos=_int(row["activos"]) if "activos" in row else None,
                primero=primero,
                ultimo=ultimo,
            )
        )
    return result


SERVICE_PROBLEM_NAMES = (
    "Failure rate increase",
    "Response time degradation",
    "Mobile app slow user actions",
)


def is_service_problem(row: ClusterProblemRow) -> bool:
    return row.category == "CUSTOM_ALERT" or row.name in SERVICE_PROBLEM_NAMES


# --- B5 / eventos de Kubernetes -------------------------------------------------------

WORKLOAD_EVIDENCE_REASONS = (
    "BackOff",
    "Failed",
    "FailedScheduling",
    "OOMKilling",
    "Unhealthy",
    "Evicted",
    "FailedMount",
    "FailedCreatePodSandBox",
    "ErrImagePull",
)

NODE_SECONDARY_ACTIONS = {
    "FailedGetResourceMetric": "HPA sin requests.cpu: definir resources.requests.cpu en el workload",
    "FailedScheduling": "pods sin nodo donde ubicarse: revisar capacidad/NodePool en esa hora",
    "FailedDraining": "nodo sin drenar: revisar PodDisruptionBudget y terminationGracePeriodSeconds",
    "IPAllocationFailed": "sin IPs para pods o nodos: revisar IPs libres en las subnets",
    "OOMKilling": "contenedores sin memoria: revisar límites de memoria y logs del workload",
    "BackOff": "contenedores reiniciando en bucle: revisar logs del workload",
}


def k8s_reasons(
    rows: Iterable[Mapping[str, Any]], evidence_reasons: Iterable[str]
) -> list[K8sReason]:
    evidence = set(evidence_reasons)
    result = []
    for row in rows:
        reason = str(row.get("dt.kubernetes.event.reason", ""))
        result.append(
            K8sReason(
                reason=reason,
                eventos=_int(row.get("eventos")),
                primero=parse_ts(row.get("primero")),
                ultimo=parse_ts(row.get("ultimo")),
                evidencia=reason in evidence,
                namespaces=[str(n) for n in (row.get("namespaces") or [])],
            )
        )
    return sorted(result, key=lambda r: (not r.evidencia, -r.eventos))


def describe_reason(r: K8sReason) -> str:
    rango = f" entre {human(r.primero)} y {human(r.ultimo)}" if r.primero and r.ultimo else ""
    ns = f" (namespaces: {', '.join(r.namespaces)})" if r.namespaces else ""
    return f"{r.eventos} eventos {r.reason}{rango}{ns}"


# --- B6 / logs ------------------------------------------------------------------------

LOG_EVIDENCE_MIN_LINES = 20


def log_patterns(rows: Iterable[Mapping[str, Any]]) -> list[LogPattern]:
    result = []
    for row in rows:
        primero, ultimo = parse_ts(row.get("primero")), parse_ts(row.get("ultimo"))
        if primero is None or ultimo is None:
            continue
        result.append(
            LogPattern(
                loglevel=str(row.get("loglevel", "")),
                logger=str(row.get("logger") or ""),
                patron=str(row.get("patron", "")),
                lineas=_int(row.get("lineas")),
                primero=primero,
                ultimo=ultimo,
            )
        )
    return sorted(result, key=lambda p: -p.lineas)


def split_logs(
    patterns: list[LogPattern], min_lines: int = LOG_EVIDENCE_MIN_LINES
) -> tuple[list[LogPattern], list[LogPattern]]:
    """(evidencia, sueltas). Las líneas sueltas nunca se usan como causa ni se citan."""
    strong = [p for p in patterns if p.lineas >= min_lines]
    weak = [p for p in patterns if p.lineas < min_lines]
    return strong, weak


DEPENDENCIES = ("DataPower", "Redis", "OpenFGA", "Ping", "Aurora", "DynamoDB", "PEGA")
_SERVICE_CODE_RE = re.compile(
    r"(?:service|servicio)\s+(\d{4,6})\b|'(\d{4,6})_[A-Z]{2}'", re.IGNORECASE
)


def detect_dependency(patterns: Iterable[LogPattern]) -> tuple[str, str] | None:
    """Busca la dependencia nombrada en los patrones de logs y su código de servicio.

    La dependencia es la del patrón con más líneas; el código puede venir en otro patrón
    de la misma dependencia (ej. "Error retrieving data from datapower" no trae código,
    pero "[RESILIENCE] DataPower service 01234 ..." sí).
    """
    patterns = list(patterns)
    dep = None
    for p in patterns:
        dep = next((d for d in DEPENDENCIES if d.lower() in p.patron.lower()), None)
        if dep:
            break
    if dep is None:
        return None
    for p in patterns:
        if dep.lower() not in p.patron.lower():
            continue
        match = _SERVICE_CODE_RE.search(p.patron)
        if match:
            return dep, next(g for g in match.groups() if g)
    return dep, ""


def describe_log(p: LogPattern) -> str:
    return f"'{p.patron}' — {p.lineas} líneas entre {human(p.primero)} y {human(p.ultimo)}" + (
        f" ({p.logger})" if p.logger else ""
    )


# --- Ruta de nodo ---------------------------------------------------------------------

CLUSTER_EVENT_MIN_PROBLEMS = 3
RESTART_MIN_PROCESSES = 5


def hour_buckets(rows: Iterable[Mapping[str, Any]], problem_id: str) -> list[HourBucket]:
    result = []
    for row in rows:
        hora = parse_ts(row.get("hora"))
        if hora is None:
            continue
        ids = [str(i) for i in (row.get("ids") or [])]
        result.append(
            HourBucket(
                hora=hora, problems=_int(row.get("problems")), incluye_problem=problem_id in ids
            )
        )
    return sorted(result, key=lambda b: b.hora)


def cluster_event_bucket(buckets: list[HourBucket], start: datetime) -> HourBucket | None:
    """La hora del problem, si tiene 3 o más problems RESOURCE_CONTENTION (evento de cluster)."""
    hour = floor_hour(start)
    bucket = next((b for b in buckets if b.hora == hour), None)
    return bucket if bucket and bucket.problems >= CLUSTER_EVENT_MIN_PROBLEMS else None


def other_event_hours(buckets: list[HourBucket], start: datetime) -> list[HourBucket]:
    hour = floor_hour(start)
    return [b for b in buckets if b.hora != hour and b.problems >= CLUSTER_EVENT_MIN_PROBLEMS]


@dataclass(frozen=True)
class RestartReading:
    procesos: int
    namespaces: list[str]
    hora: datetime
    reemplazo_de_nodos: bool

    def describe(self) -> str:
        texto = (
            f"reinicio o redespliegue masivo de {self.procesos} procesos en "
            f"{', '.join(self.namespaces)} a las {human(self.hora)}"
        )
        if self.reemplazo_de_nodos:
            texto += " (incluye agentes de plataforma: probable reemplazo de nodos)"
        return texto


def read_restarts(rows: Iterable[Mapping[str, Any]], start: datetime) -> RestartReading | None:
    """Procesos vivos que arrancaron en la hora del problem (±1 h).

    Solo muestra cuándo arrancaron, no por qué; sin filas no prueba que no hubo reinicios.
    """
    hour = floor_hour(start)
    window = {hour - timedelta(hours=1), hour, hour + timedelta(hours=1)}
    total, namespaces, best_hour = 0, [], hour
    by_hour: dict[datetime, int] = {}
    for row in rows:
        inicio = parse_ts(row.get("inicio"))
        if inicio is None or floor_hour(inicio) not in window:
            continue
        procesos = _int(row.get("procesos"))
        total += procesos
        ns = str(row.get("k8s.namespace.name", ""))
        if ns and ns not in namespaces:
            namespaces.append(ns)
        by_hour[floor_hour(inicio)] = by_hour.get(floor_hour(inicio), 0) + procesos
    if total < RESTART_MIN_PROCESSES:
        return None
    best_hour = max(by_hour, key=by_hour.get)
    platform = any(ns == "dynatrace" for ns in namespaces)
    apps = any(ns != "dynatrace" for ns in namespaces)
    return RestartReading(total, namespaces, best_hour, reemplazo_de_nodos=platform and apps)


def node_lifecycle(reasons: list[K8sReason]) -> list[K8sReason]:
    return [r for r in reasons if r.reason in NODE_LIFECYCLE_REASONS]


# --- Confianza ------------------------------------------------------------------------


def cap(confidence: Confidence, limit: Confidence) -> Confidence:
    order = [Confidence.BAJA, Confidence.MEDIA, Confidence.ALTA]
    return order[min(order.index(confidence), order.index(limit))]


def confidence_route_b(
    *,
    origin: bool,
    log_evidence: bool,
    k8s_evidence: bool,
    cluster_context: bool,
    truncated: bool,
) -> Confidence:
    """Alta solo con evidencia directa: logs repetidos (≥ 20 líneas) o un origen previo y activo."""
    if origin or log_evidence:
        level = Confidence.ALTA
    elif k8s_evidence or cluster_context:
        level = Confidence.MEDIA
    else:
        level = Confidence.BAJA
    return cap(level, Confidence.MEDIA) if truncated else level


def confidence_node(*, cluster_event: bool, second_source: bool, truncated: bool) -> Confidence:
    """Alta solo con dos fuentes distintas que coinciden (patrón de cluster + eventos o reinicios)."""
    if cluster_event and second_source:
        level = Confidence.ALTA
    elif cluster_event or second_source:
        level = Confidence.MEDIA
    else:
        level = Confidence.BAJA
    return cap(level, Confidence.MEDIA) if truncated else level
