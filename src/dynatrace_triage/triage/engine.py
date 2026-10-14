"""Motor de triage: ejecuta las consultas de la ruta que corresponde y arma el reporte.

Todas las consultas de la ruta se ejecutan siempre. Si una falla, el paso queda
registrado como error y el reporte lo dice; nunca se completa una línea con datos
que no se consultaron.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta

from ..dynatrace import queries
from ..dynatrace.client import DqlError, DqlExecutor, QueryResult
from ..timeutil import fmt_days_hours, fmt_minutes, human, minutes_between
from . import rules
from .models import Confidence, Problem, Route, Step, StepStatus, TriageReport


class ProblemNotFound(LookupError):
    def __init__(self, problem_id: str, lookback_days: int) -> None:
        super().__init__(
            f"No se encontró {problem_id} en los últimos {lookback_days} días "
            "(se buscó en todos los estados)."
        )
        self.problem_id = problem_id
        self.lookback_days = lookback_days


class _Run:
    """Estado de una ejecución: pasos, truncados y cobertura."""

    def __init__(self, dql: DqlExecutor) -> None:
        self._dql = dql
        self.steps: list[Step] = []
        self.cobertura: list[str] = []
        self.truncated = False

    def query(self, name: str, dql: str) -> QueryResult | None:
        try:
            result = self._dql.query(dql)
        except DqlError as exc:
            self.steps.append(Step(name=name, status=StepStatus.ERROR, detail=str(exc)))
            return None
        status = StepStatus.OK if result.records else StepStatus.VACIO
        detail = f"{len(result.records)} filas"
        if result.truncated:
            self.truncated = True
            detail += " (resultado truncado)"
        self.steps.append(Step(name=name, status=status, detail=detail, truncated=result.truncated))
        return result

    def skip(self, name: str, reason: str) -> None:
        self.steps.append(Step(name=name, status=StepStatus.NO_APLICA, detail=reason))


class TriageEngine:
    def __init__(
        self,
        dql: DqlExecutor,
        clusters: Mapping[str, str] | None = None,
        *,
        lookback_days: int = 7,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._dql = dql
        self._clusters = dict(clusters or {})
        self._lookback_days = lookback_days
        self._clock = clock

    def run(self, problem_id: str) -> TriageReport:
        problem_id = queries.validate_problem_id(problem_id)
        run = _Run(self._dql)
        detail = run.query(
            "detalle del problem", queries.problem_detail(problem_id, self._lookback_days)
        )
        if detail is None:
            raise DqlError(run.steps[-1].detail)
        if not detail.records:
            raise ProblemNotFound(problem_id, self._lookback_days)
        problem = Problem.from_record(detail.records[0])
        route = rules.classify_route(problem)
        run.steps.append(Step(name="ruta", status=StepStatus.OK, detail=route.value))
        now = self._clock()
        if route is Route.NODO:
            return self._node_route(problem, run, now)
        return self._route_b(problem, run, now)

    # --- Ruta B --------------------------------------------------------------------

    def _route_b(self, problem: Problem, run: _Run, now: datetime) -> TriageReport:
        end_or_now = problem.end or now
        entity_id = (
            problem.affected_entity_ids[0] if problem.affected_entity_ids else problem.display_id
        )
        cloud_app = problem.cloud_applications[0] if problem.cloud_applications else entity_id
        cluster = rules.pick_cluster(problem, self._clusters)

        # B2
        b2 = run.query(
            "B2 duración y recurrencia",
            queries.recurrence(problem.name, entity_id, self._lookback_days),
        )
        summary = rules.summarize_recurrence(b2.records if b2 else [], problem, now)

        # B3
        b3 = run.query(
            "B3 problems del mismo workload",
            queries.workload_problems(entity_id, cloud_app, self._lookback_days),
        )
        workload_rows = rules.classify_workload_rows(
            b3.records if b3 else [], problem.name, problem.start
        )
        origin = rules.first_origin(workload_rows)

        # B4
        cluster_rows = []
        if cluster:
            win_start, win_end = (
                problem.start - timedelta(hours=1),
                problem.start + timedelta(hours=1),
            )
            b4 = run.query(
                "B4 problems del cluster ±1 h",
                queries.cluster_window(cluster[0], problem.display_id, win_start, win_end),
            )
            cluster_rows = rules.cluster_problem_rows(b4.records if b4 else [])
            run.cobertura.append(f"problems del cluster: {human(win_start)} – {human(win_end)}")
        else:
            run.skip("B4 problems del cluster ±1 h", "el problem no trae cluster")

        # B5
        k8s = []
        namespace = problem.namespaces[0] if problem.namespaces else ""
        if cluster and namespace and problem.workloads:
            ev_start, ev_end = (
                problem.start - timedelta(hours=1),
                end_or_now + timedelta(minutes=15),
            )
            b5 = run.query(
                "B5 eventos de Kubernetes del workload",
                queries.workload_k8s_events(
                    cluster[0], namespace, problem.workloads, ev_start, ev_end
                ),
            )
            k8s = rules.k8s_reasons(b5.records if b5 else [], rules.WORKLOAD_EVIDENCE_REASONS)
            run.cobertura.append(f"eventos K8s: {human(ev_start)} – {human(ev_end)}")
        else:
            run.skip("B5 eventos de Kubernetes del workload", "sin cluster, namespace o workload")
        k8s_evidence = [r for r in k8s if r.evidencia]

        # B6
        strong, weak = [], []
        if problem.workloads or problem.process_groups:
            log_start, log_end = (
                problem.start - timedelta(minutes=5),
                end_or_now + timedelta(minutes=5),
            )
            b6 = run.query(
                "B6 logs de error del servicio",
                queries.error_logs(problem.workloads, problem.process_groups, log_start, log_end),
            )
            strong, weak = rules.split_logs(rules.log_patterns(b6.records if b6 else []))
            run.cobertura.append(f"logs: {human(log_start)} – {human(log_end)}")
        else:
            run.skip("B6 logs de error del servicio", "sin workload ni process group")
        run.cobertura.insert(0, f"problems de la entidad: últimos {self._lookback_days} días")

        # Evidencia
        evidencia = [rules.describe_reason(r) for r in k8s_evidence]
        if any(r.reason == "BackOff" for r in k8s_evidence) and any(
            r.reason == "Pulling" for r in k8s
        ):
            evidencia.append(
                "BackOff junto con Pulling: el contenedor reinicia en bucle o no puede descargar la imagen"
            )
        evidencia += [f"log: {rules.describe_log(p)}" for p in strong[:3]]
        if not strong:
            evidencia.append("logs: sin patrones de error repetidos en la ventana")

        hallazgos = []
        if weak:
            hallazgos.append(
                f"{len(weak)} {'patrón' if len(weak) == 1 else 'patrones'} de log con menos de "
                f"{rules.LOG_EVIDENCE_MIN_LINES} líneas (líneas sueltas, no se usan como evidencia)"
            )

        # Hipótesis, acción y confianza
        dependency = rules.detect_dependency(strong) if strong else None
        service_rows = [r for r in cluster_rows if rules.is_service_problem(r)]
        if origin:
            causa_tipo = "origen_previo"
            hipotesis = (
                f"{origin.name} activo desde {human(origin.primero)}: empezó antes de este problem "
                "y sigue sin resolverse, por lo que es la causa más probable."
            )
            if any(r.reason == "BackOff" for r in k8s_evidence):
                n = next(r.eventos for r in k8s_evidence if r.reason == "BackOff")
                hipotesis += f" Los eventos de Kubernetes muestran {n} BackOff (contenedor reiniciando en bucle)."
            accion = (
                "Revisar los logs de arranque del contenedor y los cambios recientes del workload "
                f"(ArgoCD / change_request) desde {human(origin.primero)}."
            )
        elif strong:
            top = strong[0]
            if dependency:
                dep, code = dependency
                causa_tipo = "dependencia"
                code_text = f" (servicio {code})" if code else ""
                hipotesis = (
                    f"Falla de la dependencia {dep}{code_text}: {top.lineas} líneas de error entre "
                    f"{human(top.primero)} y {human(top.ultimo)} ('{top.patron}'); el servicio no pudo "
                    "completar esas llamadas y subió su tasa de error."
                )
                accion = (
                    f"Revisar disponibilidad y latencia de {dep}{code_text} entre {human(top.primero)} y "
                    f"{human(top.ultimo)}; validar circuit breaker y timeouts del servicio."
                )
            else:
                causa_tipo = "errores_de_aplicacion"
                hipotesis = (
                    f"Errores repetidos del servicio: '{top.patron}' ({top.lineas} líneas entre "
                    f"{human(top.primero)} y {human(top.ultimo)})."
                )
                accion = "Revisar el error repetido en el código o la configuración del servicio."
        elif k8s_evidence:
            causa_tipo = "kubernetes"
            hipotesis = (
                "Eventos de Kubernetes del workload: "
                + "; ".join(rules.describe_reason(r) for r in k8s_evidence)
                + "."
            )
            accion = "Revisar el estado de los pods del workload y sus eventos en el cluster."
        else:
            causa_tipo = "sin_evidencia"
            hipotesis = "Sin evidencia suficiente en logs, eventos ni problems relacionados para proponer una causa."
            accion = (
                "Revisar el problem manualmente en Dynatrace; la evidencia automática no alcanza."
            )

        if summary.problems >= 3 and not summary.continuo:
            hipotesis += (
                f" Alerta recurrente: {summary.problems} veces en {self._lookback_days} días."
            )
        if service_rows and causa_tipo != "origen_previo":
            total = sum(r.problems for r in service_rows)
            hipotesis += (
                f" En la misma hora hubo {total} problems de servicio en el cluster: si comparten "
                "dependencia, refuerza esa hipótesis."
            )

        confianza = rules.confidence_route_b(
            origin=origin is not None,
            log_evidence=bool(strong),
            k8s_evidence=bool(k8s_evidence),
            cluster_context=bool(service_rows),
            truncated=run.truncated,
        )

        cluster_name = cluster[1] if cluster else None
        return TriageReport(
            problem_id=problem.display_id,
            ruta=Route.B,
            resumen=self._summary(problem),
            entidad_afectada=self._entity(problem, cluster_name),
            entorno=problem.environment,
            clave_correlacion=rules.correlation_key(problem, Route.B, cluster_name),
            kb=rules.extract_kb(problem.description),
            dashboard=rules.extract_dashboard(problem.description),
            severidad=self._severity(problem),
            evidencia=evidencia,
            duracion=summary.duracion,
            recurrencia=summary.recurrencia,
            problems_workload=workload_rows,
            problems_cluster=cluster_rows,
            hipotesis=hipotesis,
            causa_tipo=causa_tipo,
            confianza=confianza,
            accion_recomendada=accion,
            hallazgos_secundarios=hallazgos,
            pasos=run.steps,
            cobertura=run.cobertura,
        )

    # --- Ruta de nodo --------------------------------------------------------------

    def _node_route(self, problem: Problem, run: _Run, now: datetime) -> TriageReport:
        cluster = rules.pick_cluster(problem, self._clusters)
        buckets, reasons, restarts = [], [], None
        around, period = [], []
        if cluster:
            cid, cname = cluster
            pattern = run.query(
                "patrón de cluster", queries.cluster_contention_pattern(cid, problem.start)
            )
            buckets = rules.hour_buckets(pattern.records if pattern else [], problem.display_id)

            # Mínimo 24 h de eventos desde 1 h antes del inicio.
            ev_start = problem.start - timedelta(hours=1)
            events = run.query(
                "eventos de Kubernetes del cluster", queries.cluster_k8s_events(cid, ev_start)
            )
            reasons = rules.k8s_reasons(
                events.records if events else [], queries.NODE_LIFECYCLE_REASONS
            )

            rs = run.query("reinicios de pods", queries.pod_restarts(cname, problem.start))
            restarts = rules.read_restarts(rs.records if rs else [], problem.start)

            a_start, a_end = problem.start - timedelta(hours=2), problem.start + timedelta(hours=2)
            a = run.query(
                "problems (A) alrededor del inicio",
                queries.cluster_problems_around(cid, a_start, a_end),
            )
            around = rules.cluster_problem_rows(a.records if a else [])
            b = run.query(
                "problems (B) del periodo", queries.cluster_problems_period(cid, problem.start)
            )
            period = rules.cluster_problem_rows(b.records if b else [])
            run.cobertura += [
                f"patrón de cluster y problems (B): desde {human(problem.start)}",
                f"eventos K8s: desde {human(ev_start)}",
                f"problems (A): {human(a_start)} – {human(a_end)}",
            ]
        else:
            for name in (
                "patrón de cluster",
                "eventos de Kubernetes del cluster",
                "reinicios de pods",
                "problems (A) alrededor del inicio",
                "problems (B) del periodo",
            ):
                run.skip(name, "el problem no trae cluster")
        cluster_name = cluster[1] if cluster else None

        event_bucket = rules.cluster_event_bucket(buckets, problem.start)
        lifecycle = rules.node_lifecycle(reasons)
        evictions = [
            r for r in around if r.name == "Pod eviction event" and r.primero <= problem.start
        ]
        service_impact = [r for r in around if rules.is_service_problem(r)]

        evidencia = [rules.describe_reason(r) for r in lifecycle] or [
            "sin eventos de ciclo de vida de nodos"
        ]
        if restarts:
            evidencia.append(restarts.describe())
        for r in evictions:
            evidencia.append(
                f"{r.problems} Pod eviction event entre {human(r.primero)} y {human(r.ultimo)} (drenado de nodos)"
            )

        hallazgos = [
            f"{rules.describe_reason(r)} — {rules.NODE_SECONDARY_ACTIONS.get(r.reason, 'revisar')}"
            for r in reasons
            if not r.evidencia
        ]

        request_metric = "request saturation" in problem.name.lower()
        metrica = " (reserva por requests, no consumo real)" if request_metric else ""
        if event_bucket:
            hora_fin = event_bucket.hora + timedelta(hours=1)
            hipotesis = (
                f"Evento del cluster: {event_bucket.problems} problems RESOURCE_CONTENTION iniciaron entre "
                f"{event_bucket.hora:%H:%M} y {hora_fin:%H:%M} UTC del {event_bucket.hora:%d/%m}{metrica}."
            )
        else:
            hipotesis = f"Saturación en un nodo aislado{metrica}, sin otros problems del cluster en esa hora."
        if lifecycle or evictions or (restarts and restarts.reemplazo_de_nodos):
            hipotesis += (
                " Coincide con reemplazo o drenado de nodos (rotación de EKS Auto Mode o upgrade)."
            )
        elif restarts:
            hipotesis += " Coincide con un redespliegue de aplicaciones."
        if service_impact:
            hipotesis += f" Posible impacto: {sum(r.problems for r in service_impact)} problems de servicio en ±2 h."
        others = rules.other_event_hours(buckets, problem.start)
        if others:
            hipotesis += (
                " Recurrencia en: "
                + ", ".join(f"{human(b.hora)} ({b.problems})" for b in others)
                + "."
            )
        if problem.is_active and now - problem.start > timedelta(hours=24):
            hipotesis += f" Sigue activo desde {human(problem.start)} ({fmt_days_hours(now - problem.start)})."

        if problem.is_active:
            duracion = (
                f"Activo desde {human(problem.start)} ({fmt_days_hours(now - problem.start)})"
            )
        else:
            duracion = f"Cerrado, duró {fmt_minutes(minutes_between(problem.start, problem.end))}"

        confianza = rules.confidence_node(
            cluster_event=event_bucket is not None,
            second_source=bool(lifecycle or evictions or restarts),
            truncated=run.truncated,
        )
        if not cluster:
            confianza = Confidence.BAJA

        return TriageReport(
            problem_id=problem.display_id,
            ruta=Route.NODO,
            resumen=self._summary(problem),
            entidad_afectada=self._entity(problem, cluster_name),
            entorno=problem.environment,
            clave_correlacion=rules.correlation_key(problem, Route.NODO, cluster_name),
            kb=rules.extract_kb(problem.description),
            dashboard=rules.extract_dashboard(problem.description),
            severidad=self._severity(problem),
            evidencia=evidencia,
            duracion=duracion,
            recurrencia="no aplica",
            patron_cluster=buckets,
            problems_cluster=around,
            problems_cluster_periodo=period,
            hipotesis=hipotesis,
            causa_tipo="evento_de_cluster" if event_bucket else "nodo_aislado",
            confianza=confianza,
            accion_recomendada=(
                "Revisar cambios en ArgoCD / change_request en esa hora y si hubo upgrade o "
                "reemplazo de nodos del cluster."
            ),
            hallazgos_secundarios=hallazgos,
            pasos=run.steps,
            cobertura=run.cobertura,
        )

    # --- Textos comunes ------------------------------------------------------------

    @staticmethod
    def _summary(problem: Problem) -> str:
        entity = ", ".join(problem.affected_entity_names) or problem.display_id
        estado = "activo" if problem.is_active else "cerrado"
        return (
            f"{problem.name} en {entity} ({problem.environment or 'ambiente sin tag'}), {estado}."
        )

    @staticmethod
    def _entity(problem: Problem, cluster_name: str | None) -> str:
        parts = [", ".join(problem.affected_entity_names) or "(sin nombre)"]
        if problem.namespaces:
            parts.append(f"namespace {problem.namespaces[0]}")
        if cluster_name:
            parts.append(f"cluster {cluster_name}")
        if problem.environment:
            parts.append(f"ambiente {problem.environment}")
        return " — ".join(parts)

    @staticmethod
    def _severity(problem: Problem) -> str:
        impact = f" ({', '.join(problem.impact_levels)})" if problem.impact_levels else ""
        return f"{problem.severity or 'sin dato'}{impact}"
