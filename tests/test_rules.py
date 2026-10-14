from __future__ import annotations

from datetime import UTC, datetime

import pytest

from dynatrace_triage.timeutil import fmt_minutes, parse_ts
from dynatrace_triage.triage import rules
from dynatrace_triage.triage.models import Confidence, LogPattern, Pattern, Problem, Reading, Route


def make_problem(**kw) -> Problem:
    base = {
        "display_id": "P-1",
        "name": "Failure rate increase",
        "category": "ERROR",
        "status": "CLOSED",
        "start": datetime(2026, 10, 1, 12, 0, tzinfo=UTC),
        "end": datetime(2026, 10, 1, 12, 30, tzinfo=UTC),
    }
    base.update(kw)
    return Problem(**base)


def ts(s: str) -> datetime:
    return parse_ts(s)


class TestTiempo:
    def test_parse_nanosegundos(self):
        assert parse_ts("2026-09-30T20:54:00.123456789Z") == datetime(
            2026, 9, 30, 20, 54, 0, 123456, tzinfo=UTC
        )

    def test_parse_vacio(self):
        assert parse_ts(None) is None and parse_ts("") is None

    def test_parse_invalido(self):
        with pytest.raises(ValueError):
            parse_ts("ayer")

    @pytest.mark.parametrize(
        ("minutos", "texto"), [(10, "10 min"), (119.6, "120 min (2,0 h)"), (198, "198 min (3,3 h)")]
    )
    def test_fmt_minutes(self, minutos, texto):
        assert fmt_minutes(minutos) == texto


class TestRuta:
    @pytest.mark.parametrize(
        ("category", "types", "esperada"),
        [
            ("RESOURCE_CONTENTION", ["dt.entity.service"], Route.NODO),
            ("ERROR", ["dt.entity.kubernetes_node"], Route.NODO),
            ("ERROR", ["dt.entity.kubernetes_cluster"], Route.NODO),
            ("ERROR", ["dt.entity.cloud_application"], Route.B),
            ("CUSTOM_ALERT", ["dt.entity.service"], Route.B),
        ],
    )
    def test_classify_route(self, category, types, esperada):
        assert (
            rules.classify_route(make_problem(category=category, affected_entity_types=types))
            is esperada
        )

    def test_tener_cluster_no_cambia_la_ruta(self):
        p = make_problem(
            cluster_ids=["KUBERNETES_CLUSTER-1"], affected_entity_types=["dt.entity.service"]
        )
        assert rules.classify_route(p) is Route.B


class TestDatosDelProblem:
    def test_kb_y_dashboard(self):
        desc = "Base de conocimiento: KB0017324\n**Dashboard**: https://x.apps.dynatrace.com/ui/d/1"
        assert rules.extract_kb(desc) == "KB0017324"
        assert rules.extract_dashboard(desc) == "https://x.apps.dynatrace.com/ui/d/1"

    def test_sin_kb_ni_dashboard(self):
        assert rules.extract_kb("nada") == "" and rules.extract_dashboard("nada") == ""

    def test_clave_de_correlacion(self):
        assert (
            rules.correlation_key(make_problem(workloads=["wallet-deployment-qa"]), Route.B, None)
            == "wallet-deployment-qa"
        )
        assert (
            rules.correlation_key(make_problem(affected_entity_names=["redis"]), Route.B, None)
            == "redis"
        )
        nodo = make_problem(name="CPU-request saturation on node")
        assert (
            rules.correlation_key(nodo, Route.NODO, "eks-x-prod")
            == "eks-x-prod-cpu-request-saturation-on-node"
        )

    def test_ambiente_por_tag_o_security_context(self):
        assert make_problem(entity_tags=["Ambiente:qa"]).environment == "QA"
        assert make_problem(security_context=["prod"]).environment == "PROD"
        assert make_problem().environment is None


class TestCluster:
    CLUSTERS = {"eks-x-prod": "KC-1", "eks-x-qa": "KC-2"}

    def test_un_solo_cluster(self):
        p = make_problem(cluster_ids=["KC-2"], cluster_names=["eks-x-qa"])
        assert rules.pick_cluster(p, {}) == ("KC-2", "eks-x-qa")

    def test_varios_clusters_elige_por_ambiente(self):
        p = make_problem(
            cluster_ids=["KC-1", "KC-2"],
            cluster_names=["eks-x-qa", "eks-x-prod"],
            entity_tags=["Ambiente:QA"],
        )
        assert rules.pick_cluster(p, self.CLUSTERS) == ("KC-2", "eks-x-qa")

    def test_sin_cluster(self):
        assert rules.pick_cluster(make_problem(), self.CLUSTERS) is None


class TestRecurrencia:
    NOW = datetime(2026, 10, 1, 22, 0, tzinfo=UTC)

    def test_aislado(self):
        p = make_problem()
        rows = [
            {
                "display_id": "P-1",
                "event.status": "CLOSED",
                "event.start": "2026-10-01T12:00:00Z",
                "event.end": "2026-10-01T12:30:00Z",
            }
        ]
        s = rules.summarize_recurrence(rows, p, self.NOW)
        assert s.duracion == "Este problem duró 30 min"
        assert s.recurrencia.startswith("1 vez ")
        assert not s.continuo

    def test_problem_activo(self):
        p = make_problem(status="ACTIVE", end=None, start=datetime(2026, 10, 1, 21, 0, tzinfo=UTC))
        s = rules.summarize_recurrence([], p, self.NOW)
        assert s.duracion == "Este problem activo desde 01/10 21:00 UTC (60 min hasta ahora)"

    def test_huecos_grandes_no_son_continuo(self):
        p = make_problem()
        rows = [
            {
                "display_id": "P-1",
                "event.status": "CLOSED",
                "event.start": "2026-10-01T12:00:00Z",
                "event.end": "2026-10-01T12:30:00Z",
            },
            {
                "display_id": "P-0",
                "event.status": "CLOSED",
                "event.start": "2026-10-01T06:00:00Z",
                "event.end": "2026-10-01T06:10:00Z",
            },
        ]
        assert not rules.summarize_recurrence(rows, p, self.NOW).continuo


class TestWorkload:
    START = ts("2026-10-01T14:23:00Z")

    def row(self, name, problems, activos, primero, fin_max, minutos):
        return {
            "event.category": "ERROR",
            "event.name": name,
            "problems": str(problems),
            "activos": str(activos),
            "primero": primero,
            "ultimo": primero,
            "fin_max": fin_max,
            "min_con_problema": minutos,
        }

    def test_activo_previo_es_origen(self):
        rows = [
            self.row("Backoff event", 1, 1, "2026-09-30T20:43:00Z", "2026-10-01T22:00:00Z", 1517)
        ]
        (r,) = rules.classify_workload_rows(rows, "Deployment stuck", self.START)
        assert (r.patron, r.lectura) == (Pattern.ACTIVO, Reading.ORIGEN_PROBABLE)

    def test_activo_posterior_es_contexto(self):
        rows = [
            self.row("Memory saturation", 1, 1, "2026-10-01T14:30:00Z", "2026-10-01T22:00:00Z", 450)
        ]
        (r,) = rules.classify_workload_rows(rows, "Deployment stuck", self.START)
        assert r.lectura is Reading.CONTEXTO

    def test_mismo_tipo_continuo(self):
        rows = [
            self.row("Deployment stuck", 3, 1, "2026-09-30T20:54:00Z", "2026-10-01T22:00:00Z", 1566)
        ]
        (r,) = rules.classify_workload_rows(rows, "Deployment stuck", self.START)
        assert (r.patron, r.lectura) == (Pattern.CONTINUO, Reading.ESTE_PROBLEM_CONTINUO)

    def test_recurrente_no_es_origen(self):
        rows = [
            self.row("Pod eviction event", 4, 0, "2026-09-28T14:14:00Z", "2026-09-30T22:20:00Z", 60)
        ]
        (r,) = rules.classify_workload_rows(rows, "Deployment stuck", self.START)
        assert (r.patron, r.lectura) == (Pattern.RECURRENTE, Reading.CONTEXTO)


class TestLogs:
    def pattern(self, texto, lineas):
        t = ts("2026-10-01T17:28:00Z")
        return LogPattern(
            loglevel="ERROR", logger="x", patron=texto, lineas=lineas, primero=t, ultimo=t
        )

    def test_umbral_de_evidencia(self):
        strong, weak = rules.split_logs([self.pattern("a", 20), self.pattern("b", 19)])
        assert [p.patron for p in strong] == ["a"] and [p.patron for p in weak] == ["b"]

    @pytest.mark.parametrize(
        ("texto", "esperado"),
        [
            (
                "[RESILIENCE] DataPower service 01234 temporarily unavailable",
                ("DataPower", "01234"),
            ),
            ("CircuitBreaker '04567_HN' is OPEN; datapower down", ("DataPower", "04567")),
            ("Redis connection timeout", ("Redis", "")),
            ("NullPointerException", None),
        ],
    )
    def test_detect_dependency(self, texto, esperado):
        assert rules.detect_dependency([self.pattern(texto, 50)]) == esperado

    def test_codigo_en_otro_patron_de_la_misma_dependencia(self):
        patrones = [
            self.pattern("Error retrieving data from datapower", 661),
            self.pattern("Redis service 99999 slow", 300),
            self.pattern("[RESILIENCE] DataPower service 01234 temporarily unavailable", 295),
        ]
        assert rules.detect_dependency(patrones) == ("DataPower", "01234")


class TestConfianza:
    def test_ruta_b(self):
        kw = dict(
            origin=False,
            log_evidence=False,
            k8s_evidence=False,
            cluster_context=False,
            truncated=False,
        )
        assert rules.confidence_route_b(**kw) is Confidence.BAJA
        assert rules.confidence_route_b(**{**kw, "cluster_context": True}) is Confidence.MEDIA
        assert rules.confidence_route_b(**{**kw, "origin": True}) is Confidence.ALTA
        assert (
            rules.confidence_route_b(**{**kw, "log_evidence": True, "truncated": True})
            is Confidence.MEDIA
        )

    def test_nodo(self):
        assert (
            rules.confidence_node(cluster_event=True, second_source=True, truncated=False)
            is Confidence.ALTA
        )
        assert (
            rules.confidence_node(cluster_event=True, second_source=False, truncated=False)
            is Confidence.MEDIA
        )
        assert (
            rules.confidence_node(cluster_event=False, second_source=False, truncated=False)
            is Confidence.BAJA
        )


class TestReinicios:
    START = ts("2026-09-26T09:20:00Z")

    def test_pocos_procesos_no_es_evidencia(self):
        rows = [{"inicio": "2026-09-26T09:00:00Z", "k8s.namespace.name": "app", "procesos": "2"}]
        assert rules.read_restarts(rows, self.START) is None

    def test_solo_aplicaciones_es_redespliegue(self):
        rows = [{"inicio": "2026-09-26T10:00:00Z", "k8s.namespace.name": "app", "procesos": "8"}]
        lectura = rules.read_restarts(rows, self.START)
        assert lectura and not lectura.reemplazo_de_nodos

    def test_fuera_de_ventana_se_ignora(self):
        rows = [{"inicio": "2026-09-26T13:00:00Z", "k8s.namespace.name": "app", "procesos": "50"}]
        assert rules.read_restarts(rows, self.START) is None
