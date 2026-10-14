from __future__ import annotations

from datetime import UTC, datetime

import pytest

from dynatrace_triage.dynatrace import queries

START = datetime(2026, 10, 1, 13, 23, tzinfo=UTC)
END = datetime(2026, 10, 1, 15, 23, tzinfo=UTC)


class TestValidacion:
    @pytest.mark.parametrize("pid", ["P-2610334", "P-1", "-3082313225680007953_1790864580000V2"])
    def test_ids_validos(self, pid):
        assert queries.validate_problem_id(f" {pid} ") == pid

    @pytest.mark.parametrize("pid", ["", "2610334", "P-12 or true", 'P-1"', "P-1\n| fetch logs"])
    def test_ids_invalidos(self, pid):
        with pytest.raises(ValueError):
            queries.validate_problem_id(pid)

    def test_escape_de_literales(self):
        assert queries.q('a"b\\c') == '"a\\"b\\\\c"'
        assert queries.arr([]) == 'array("ninguno")'


class TestConsultas:
    def test_detalle_busca_en_todos_los_estados(self):
        dql = queries.problem_detail("P-2610334", 7)
        assert "from: now()-7d" in dql
        assert 'display_id == "P-2610334" or event.id == "P-2610334"' in dql
        assert "event.status" not in dql

    def test_ventana_del_cluster_filtra_por_event_start(self):
        # from/to filtran por el timestamp del registro: hace falta el filtro de event.start.
        dql = queries.cluster_window("KC-1", "P-1", START, END)
        assert 'from: "2026-10-01T13:23:00Z", to: "2026-10-01T15:23:00Z"' in dql
        assert 'event.start >= toTimestamp("2026-10-01T13:23:00Z")' in dql
        assert 'display_id != "P-1"' in dql

    @pytest.mark.parametrize(
        "dql",
        [
            queries.cluster_window("KC-1", "P-1", START, END),
            queries.cluster_problems_around("KC-1", START, END),
            queries.cluster_problems_period("KC-1", START),
        ],
    )
    def test_nunca_contains_sobre_la_descripcion(self, dql):
        # from sin to + contains(event.description) dio timeout de 120 s en el tenant.
        assert "event.description" not in dql

    def test_logs_por_workload_y_process_group(self):
        dql = queries.error_logs(["svc-a"], [], START, END)
        assert 'in(k8s.workload.name, array("svc-a"))' in dql
        assert 'in(dt.entity.process_group, array("ninguno"))' in dql
        assert '"JSON:j"' in dql

    def test_eventos_k8s_un_contains_por_workload(self):
        dql = queries.workload_k8s_events("KC-1", "ns", ["a", "b"], START, END)
        assert dql.count("contains(k8s.pod.name") == 2
        assert 'k8s.namespace.name == "ns"' in dql

    def test_ids_de_entidad_se_escapan(self):
        dql = queries.recurrence('nombre "raro"', 'ID"X', 7)
        assert 'event.name == "nombre \\"raro\\""' in dql
        assert 'in("ID\\"X", affected_entity_ids)' in dql
