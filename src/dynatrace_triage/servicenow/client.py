"""Cliente de la Table API de ServiceNow.

Permisos mínimos del usuario de integración: lectura de cmdb_ci, change_request e
incident; creación (no edición ni borrado) de incident.
"""

from __future__ import annotations

from typing import Any

import httpx


class ServiceNowError(RuntimeError):
    pass


def _clean(value: str) -> str:
    """Evita que un valor cambie la consulta encoded (^ separa condiciones)."""
    return value.replace("^", "").replace("\n", " ").replace("\r", " ").strip()


class ServiceNowClient:
    def __init__(
        self,
        instance_url: str,
        username: str,
        password: str,
        *,
        http: httpx.Client | None = None,
    ) -> None:
        self.instance_url = instance_url.rstrip("/")
        self._http = http or httpx.Client(
            base_url=self.instance_url,
            auth=(username, password),
            headers={"Accept": "application/json", "Content-Type": "application/json"},
            timeout=httpx.Timeout(30.0),
        )

    def _get(self, table: str, query: str, fields: str, limit: int) -> list[dict[str, Any]]:
        params = {
            "sysparm_query": query,
            "sysparm_fields": fields,
            "sysparm_limit": str(limit),
            "sysparm_exclude_reference_link": "true",
        }
        response = self._http.get(f"/api/now/table/{table}", params=params)
        if response.status_code >= 400:
            raise ServiceNowError(f"GET {table}: HTTP {response.status_code} {response.text[:200]}")
        return response.json().get("result", [])

    def find_ci(self, name: str) -> dict[str, Any] | None:
        """Primero nombre exacto; si no hay, LIKE solo cuando el resultado es único."""
        name = _clean(name)
        if not name:
            return None
        exact = self._get("cmdb_ci", f"name={name}", "sys_id,name,sys_class_name", 2)
        if len(exact) == 1:
            return exact[0]
        like = self._get("cmdb_ci", f"nameLIKE{name}", "sys_id,name,sys_class_name", 2)
        return like[0] if len(like) == 1 else None

    def recent_changes(self, ci_sys_id: str, days: int = 7) -> list[dict[str, Any]]:
        query = (
            f"cmdb_ci={_clean(ci_sys_id)}^sys_created_on>=javascript:gs.daysAgoStart({int(days)})"
            "^ORDERBYDESCsys_created_on"
        )
        return self._get(
            "change_request", query, "number,short_description,state,start_date,sys_created_on", 10
        )

    def find_open_incident(self, correlation_id: str) -> dict[str, Any] | None:
        rows = self._get(
            "incident",
            f"correlation_id={_clean(correlation_id)}^active=true^ORDERBYDESCsys_created_on",
            "sys_id,number,state,short_description",
            1,
        )
        return rows[0] if rows else None

    def create_incident(self, fields: dict[str, Any]) -> dict[str, Any]:
        response = self._http.post("/api/now/table/incident", json=fields)
        if response.status_code >= 400:
            raise ServiceNowError(
                f"POST incident: HTTP {response.status_code} {response.text[:200]}"
            )
        return response.json()["result"]

    def incident_url(self, sys_id: str) -> str:
        return f"{self.instance_url}/nav_to.do?uri=incident.do?sys_id={sys_id}"
