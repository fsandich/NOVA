from __future__ import annotations

from dynatrace_triage.config import Settings


def test_clusters_desde_archivo_y_variable(tmp_path):
    archivo = tmp_path / "clusters.yaml"
    archivo.write_text("clusters:\n  eks-x-prod: KC-1\n  eks-x-qa: KC-2\n", encoding="utf-8")
    s = Settings(
        _env_file=None, clusters_file=archivo, clusters="eks-x-qa=KC-9, eks-x-dev=KC-4,malo"
    )
    assert s.load_clusters() == {"eks-x-prod": "KC-1", "eks-x-qa": "KC-9", "eks-x-dev": "KC-4"}


def test_sin_archivo(tmp_path):
    assert Settings(_env_file=None, clusters_file=tmp_path / "no.yaml").load_clusters() == {}


def test_listas_separadas_por_coma():
    s = Settings(_env_file=None, api_keys=" a, b ,", high_priority_environments="prod, pre-prod")
    assert s.api_key_set == {"a", "b"}
    assert s.high_priority_env_set == {"PROD", "PRE-PROD"}
    assert not s.servicenow_enabled
