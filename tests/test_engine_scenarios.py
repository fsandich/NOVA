"""Regresión con los casos reales que se probaron con el agente de Copilot Studio.

Cada test fija una conclusión que el agente se equivocaba al sacar con reglas en prosa.
"""

from __future__ import annotations

import pytest

from dynatrace_triage.dynatrace.client import DqlError
from dynatrace_triage.triage.engine import ProblemNotFound
from dynatrace_triage.triage.models import Confidence, Reading, Route, StepStatus
from dynatrace_triage.triage.render import render_markdown

ROUTE_B_STEPS = ["detalle", "b2", "b3", "b4", "b5", "b6"]
NODE_STEPS = ["detalle", "patron", "eventos", "reinicios", "problems_a", "problems_b"]


class TestWorkloadContinuo:
    """P-2610334: el agente decía '198 min' y culpaba a DataPower por una línea de log."""

    @pytest.fixture
    def result(self, scenario):
        engine, fake = scenario("workload_continuo")
        return engine.run("P-2610334"), fake

    def test_sigue_la_ruta_b_completa(self, result):
        report, fake = result
        assert report.ruta is Route.B
        assert fake.steps == ROUTE_B_STEPS

    def test_duracion_es_incidente_continuo(self, result):
        report, _ = result
        assert report.duracion == (
            "Incidente continuo desde 30/09 20:54 UTC (5 problems seguidos, 1 activo); "
            "este problem duró 198 min (3,3 h)"
        )
        assert report.recurrencia.startswith("5 veces 'Deployment stuck'")

    def test_la_causa_es_el_origen_previo(self, result):
        report, _ = result
        lecturas = {r.name: r.lectura for r in report.problems_workload}
        assert lecturas["Backoff event"] is Reading.ORIGEN_PROBABLE
        assert lecturas["Not all pods ready"] is Reading.ORIGEN_PROBABLE
        assert lecturas["Deployment stuck"] is Reading.ESTE_PROBLEM_CONTINUO
        assert lecturas["Pod eviction event"] is Reading.CONTEXTO
        assert report.hipotesis.startswith("Backoff event activo desde 30/09 20:43 UTC")
        assert report.causa_tipo == "origen_previo"
        assert report.confianza is Confidence.ALTA

    def test_lineas_sueltas_de_log_no_son_causa(self, result):
        report, _ = result
        texto = " ".join([report.hipotesis, report.accion_recomendada, *report.evidencia]).lower()
        assert "datapower" not in texto
        assert "logs: sin patrones de error repetidos en la ventana" in report.evidencia
        assert any(
            "4 patrones de log con menos de 20 líneas" in h for h in report.hallazgos_secundarios
        )
        assert "Datapower" not in render_markdown(report)

    def test_evidencia_de_kubernetes(self, result):
        report, _ = result
        assert report.evidencia[0].startswith("54 eventos BackOff")
        assert any("BackOff junto con Pulling" in e for e in report.evidencia)


class TestServicioDependencia:
    """P-2610405: la causa sale de los logs repetidos de la dependencia."""

    @pytest.fixture
    def report(self, scenario):
        engine, _ = scenario("servicio_dependencia")
        return engine.run("P-2610405")

    def test_causa_dependencia_con_codigo(self, report):
        assert report.causa_tipo == "dependencia"
        assert report.hipotesis.startswith("Falla de la dependencia DataPower (servicio 01234)")
        assert "DataPower (servicio 01234)" in report.accion_recomendada
        assert report.confianza is Confidence.ALTA

    def test_sin_origen_previo(self, report):
        assert all(r.lectura is not Reading.ORIGEN_PROBABLE for r in report.problems_workload)

    def test_duracion_y_recurrencia(self, report):
        assert report.duracion == "Este problem duró 10 min"
        assert report.recurrencia.startswith("4 veces 'Failure rate increase'")
        assert "Alerta recurrente: 4 veces" in report.hipotesis

    def test_cita_maximo_tres_patrones(self, report):
        logs = [e for e in report.evidencia if e.startswith("log: ")]
        assert len(logs) == 3
        assert "661 líneas" in logs[0]

    def test_b5_vacio_queda_registrado(self, report):
        b5 = next(s for s in report.pasos if s.name.startswith("B5"))
        assert b5.status is StepStatus.VACIO


class TestNodoEventoCluster:
    """P-260916953: regresión de la ruta de nodo."""

    @pytest.fixture
    def result(self, scenario):
        engine, fake = scenario("nodo_evento_cluster")
        return engine.run("P-260916953"), fake

    def test_ruta_de_nodo_completa(self, result):
        report, fake = result
        assert report.ruta is Route.NODO
        assert fake.steps == NODE_STEPS

    def test_hipotesis_evento_de_cluster(self, result):
        report, _ = result
        assert report.hipotesis.startswith(
            "Evento del cluster: 24 problems RESOURCE_CONTENTION iniciaron entre 09:00 y 10:00 UTC "
            "del 26/09 (reserva por requests, no consumo real)."
        )
        assert "reemplazo o drenado de nodos" in report.hipotesis
        assert "Recurrencia en: 28/09 03:00 UTC (4)" in report.hipotesis
        assert "Sigue activo desde 26/09 09:20 UTC (3 d 12 h)" in report.hipotesis
        assert report.confianza is Confidence.ALTA

    def test_evidencia_y_hallazgos(self, result):
        report, _ = result
        assert any(e.startswith("227 eventos Evicted") for e in report.evidencia)
        assert any(
            "42 procesos" in e and "probable reemplazo de nodos" in e for e in report.evidencia
        )
        assert any("6 Pod eviction event" in e for e in report.evidencia)
        assert report.hallazgos_secundarios == [
            "40 eventos FailedGetResourceMetric entre 26/09 08:30 UTC y 29/09 21:00 UTC "
            "(namespaces: app-backend) — HPA sin requests.cpu: definir resources.requests.cpu en el workload"
        ]

    def test_datos_del_problem(self, result):
        report, _ = result
        assert report.clave_correlacion == "eks-demo-prod-cpu-request-saturation-on-node"
        assert report.duracion == "Activo desde 26/09 09:20 UTC (3 d 12 h)"
        assert report.recurrencia == "no aplica"
        assert [b.incluye_problem for b in report.patron_cluster] == [True, False, False]

    def test_sin_eventos_baja_a_media(self, scenario):
        engine, _ = scenario(
            "nodo_evento_cluster",
            eventos={"records": []},
            reinicios={"records": []},
            problems_a={"records": []},
        )
        report = engine.run("P-260916953")
        assert report.confianza is Confidence.MEDIA
        assert report.evidencia == ["sin eventos de ciclo de vida de nodos"]


class TestCustomAlert:
    """P-2610407: una sola línea de log no alcanza para confianza Alta."""

    @pytest.fixture
    def report(self, scenario):
        engine, _ = scenario("custom_alert")
        return engine.run("P-2610407")

    def test_kb_y_dashboard(self, report):
        assert report.kb == "KB0012345"
        assert (
            report.dashboard
            == "https://demo.apps.dynatrace.com/ui/apps/dynatrace.dashboards/dashboard/abc-123"
        )

    def test_confianza_media_sin_evidencia_directa(self, report):
        assert report.causa_tipo == "sin_evidencia"
        assert report.confianza is Confidence.MEDIA
        assert "HTTP 500" not in render_markdown(report)
        assert any("1 patrón de log" in h for h in report.hallazgos_secundarios)

    def test_contexto_del_cluster(self, report):
        assert "4 problems de servicio en el cluster" in report.hipotesis


class TestErroresYCasosBorde:
    def test_problem_inexistente(self, scenario):
        engine, _ = scenario("workload_continuo", detalle={"records": []})
        with pytest.raises(ProblemNotFound, match="últimos 7 días"):
            engine.run("P-2610334")

    def test_error_en_el_detalle_se_propaga(self, scenario):
        engine, _ = scenario("workload_continuo", detalle={"error": "HTTP 401"})
        with pytest.raises(DqlError):
            engine.run("P-2610334")

    def test_error_en_un_paso_no_inventa_datos(self, scenario):
        engine, fake = scenario("servicio_dependencia", b6={"error": "timeout"})
        report = engine.run("P-2610405")
        b6 = next(s for s in report.pasos if s.name.startswith("B6"))
        assert b6.status is StepStatus.ERROR
        assert fake.steps == ROUTE_B_STEPS
        assert report.causa_tipo != "dependencia"
        assert "B6 logs de error del servicio [error: timeout]" in render_markdown(report)

    def test_resultado_truncado_limita_confianza(self, scenario):
        engine, fake = scenario("servicio_dependencia")
        fake.responses["b6"]["notifications"] = [
            {"notificationType": "BYTE_LIMIT_ADDED", "message": "result has been limited"}
        ]
        report = engine.run("P-2610405")
        assert report.confianza is Confidence.MEDIA

    def test_problem_id_invalido_no_llega_a_dql(self, scenario):
        engine, fake = scenario("workload_continuo")
        with pytest.raises(ValueError):
            engine.run('P-1" or true or "')
        assert fake.calls == []

    def test_sin_workload_salta_b5_y_b6(self, scenario):
        engine, fake = scenario("servicio_dependencia")
        detalle = fake.responses["detalle"]["records"][0]
        detalle.pop("k8s.workload.name")
        detalle.pop("dt.entity.process_group")
        report = engine.run("P-2610405")
        assert fake.steps == ["detalle", "b2", "b3", "b4"]
        estados = {s.name[:2]: s.status for s in report.pasos}
        assert estados["B5"] is StepStatus.NO_APLICA
        assert estados["B6"] is StepStatus.NO_APLICA
