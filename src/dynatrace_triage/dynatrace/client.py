"""Cliente de solo lectura para la Grail Query API de Dynatrace."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

# Avisos de Grail que significan que el resultado no está completo.
_TRUNCATION_MARKERS = ("BYTE_LIMIT", "LIMIT_ADDED", "TRUNCAT", "maximum bytes", "has been limited")


class DqlError(RuntimeError):
    """La consulta falló, expiró o Grail devolvió un error."""


@dataclass(frozen=True)
class QueryResult:
    records: list[dict[str, Any]]
    notifications: list[dict[str, Any]] = field(default_factory=list)
    scanned_records: int = 0

    @property
    def truncated(self) -> bool:
        for note in self.notifications:
            text = f"{note.get('notificationType', '')} {note.get('message', '')}"
            if any(marker.lower() in text.lower() for marker in _TRUNCATION_MARKERS):
                return True
        return False

    @classmethod
    def from_grail(cls, result: dict[str, Any]) -> QueryResult:
        grail = (result.get("metadata") or {}).get("grail") or {}
        return cls(
            records=list(result.get("records") or []),
            notifications=list(grail.get("notifications") or []),
            scanned_records=int(grail.get("scannedRecords") or 0),
        )


class DqlExecutor(Protocol):
    def query(self, dql: str) -> QueryResult: ...


class GrailClient:
    """Ejecuta DQL con `query:execute` y, si Grail responde RUNNING, sigue con `query:poll`."""

    EXECUTE_PATH = "/platform/storage/query/v1/query:execute"
    POLL_PATH = "/platform/storage/query/v1/query:poll"

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout_s: float = 60.0,
        poll_interval_s: float = 0.5,
        max_records: int = 1000,
        http: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not base_url or not token:
            raise ValueError("Falta DT_ENV_URL o DT_PLATFORM_TOKEN")
        self._timeout_s = timeout_s
        self._poll_interval_s = poll_interval_s
        self._max_records = max_records
        self._sleep = sleep
        self._http = http or httpx.Client(
            base_url=base_url.rstrip("/"),
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=httpx.Timeout(30.0),
        )

    def query(self, dql: str) -> QueryResult:
        body = {
            "query": dql,
            "requestTimeoutMilliseconds": 10_000,
            "maxResultRecords": self._max_records,
            "timezone": "UTC",
            "locale": "en_US",
        }
        payload = self._check(self._http.post(self.EXECUTE_PATH, json=body))
        deadline = time.monotonic() + self._timeout_s
        while payload.get("state") in ("RUNNING", "NOT_STARTED"):
            if time.monotonic() > deadline:
                raise DqlError(f"la consulta superó {self._timeout_s:.0f} s")
            self._sleep(self._poll_interval_s)
            params = {
                "request-token": payload["requestToken"],
                "request-timeout-milliseconds": 5_000,
            }
            payload = self._check(self._http.get(self.POLL_PATH, params=params))
        if payload.get("state") != "SUCCEEDED":
            raise DqlError(f"estado de la consulta: {payload.get('state')}")
        return QueryResult.from_grail(payload.get("result") or {})

    @staticmethod
    def _check(response: httpx.Response) -> dict[str, Any]:
        if response.status_code >= 400:
            try:
                message = response.json().get("error", {}).get("message", response.text)
            except ValueError:
                message = response.text
            raise DqlError(f"HTTP {response.status_code}: {message}")
        return response.json()
