"""Conversión de timestamps de Grail y formato de fechas y duraciones para los reportes."""

from __future__ import annotations

import re
from datetime import UTC, datetime, timedelta

# Grail devuelve nanosegundos ("2026-09-30T20:54:00.000000000Z"); datetime solo admite 6 decimales.
_TS = re.compile(
    r"^(?P<base>\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?P<frac>\.\d+)?(?P<tz>Z|[+-]\d{2}:\d{2})?$"
)


def parse_ts(value: object) -> datetime | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    match = _TS.match(str(value).strip())
    if not match:
        raise ValueError(f"timestamp no reconocido: {value!r}")
    frac = (match["frac"] or "")[:7]
    tz = match["tz"] or "Z"
    tz = "+00:00" if tz == "Z" else tz
    return datetime.fromisoformat(f"{match['base']}{frac}{tz}").astimezone(UTC)


def iso(dt: datetime) -> str:
    """Formato que acepta DQL en from/to y toTimestamp()."""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def human(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%d/%m %H:%M UTC")


def minutes_between(start: datetime, end: datetime) -> float:
    return (end - start).total_seconds() / 60


def fmt_minutes(minutes: float) -> str:
    m = int(round(minutes))
    if m >= 120:
        return f"{m} min ({m / 60:.1f} h)".replace(".", ",")
    return f"{m} min"


def fmt_days_hours(delta: timedelta) -> str:
    hours = int(delta.total_seconds() // 3600)
    days, hours = divmod(hours, 24)
    return f"{days} d {hours} h" if days else f"{hours} h"


def floor_hour(dt: datetime) -> datetime:
    return dt.replace(minute=0, second=0, microsecond=0)
