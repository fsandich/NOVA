"""Configuración leída de variables de entorno (o de un archivo .env)."""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Dynatrace
    dt_env_url: str = ""
    dt_platform_token: SecretStr = SecretStr("")
    dt_lookback_days: int = 7
    dt_query_timeout_s: float = 60.0
    clusters_file: Path = Path("config/clusters.yaml")
    # Alternativa al archivo para contenedores: "eks-x-prod=KUBERNETES_CLUSTER-1,eks-x-qa=..."
    clusters: str = ""

    # ServiceNow
    snow_instance_url: str = ""
    snow_username: str = ""
    snow_password: SecretStr = SecretStr("")
    snow_assignment_group: str = ""

    # API
    api_keys: str = ""
    public_base_url: str = "http://localhost:8000"
    high_priority_environments: str = "PROD"

    @property
    def api_key_set(self) -> set[str]:
        return {k.strip() for k in self.api_keys.split(",") if k.strip()}

    @property
    def high_priority_env_set(self) -> set[str]:
        return {e.strip().upper() for e in self.high_priority_environments.split(",") if e.strip()}

    @property
    def servicenow_enabled(self) -> bool:
        return bool(self.snow_instance_url and self.snow_username)

    def load_clusters(self) -> dict[str, str]:
        """{nombre de cluster: KUBERNETES_CLUSTER-...} del archivo y de CLUSTERS (gana CLUSTERS)."""
        clusters = load_clusters(self.clusters_file)
        for pair in self.clusters.split(","):
            name, sep, cid = pair.partition("=")
            if sep and name.strip() and cid.strip():
                clusters[name.strip()] = cid.strip()
        return clusters


def load_clusters(path: Path) -> dict[str, str]:
    if not path.is_file():
        return {}
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    clusters = data.get("clusters") or {}
    return {str(name): str(cid) for name, cid in clusters.items()}
