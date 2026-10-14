"""Reporte en markdown con las mismas líneas de "Salida esperada" del agente original."""

from __future__ import annotations

from ..timeutil import human
from .models import ClusterProblemRow, Route, StepStatus, TriageReport


def _table(headers: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "---|" * len(headers)]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


def _cluster_table(rows: list[ClusterProblemRow]) -> str:
    with_active = any(r.activos is not None for r in rows)
    headers = (
        ["Categoría", "Nombre", "Problems"]
        + (["Activos"] if with_active else [])
        + ["Primero", "Último"]
    )
    body = [
        [r.category, r.name, str(r.problems)]
        + ([str(r.activos or 0)] if with_active else [])
        + [human(r.primero), human(r.ultimo)]
        for r in rows
    ]
    return _table(headers, body)


def _bullets(items: list[str]) -> str:
    return "\n".join(f"- {i}" for i in items)


def render_markdown(report: TriageReport) -> str:
    out = [
        f"**Resumen:** {report.resumen}",
        f"**Entidad afectada:** {report.entidad_afectada}",
        f"**Clave de correlación:** {report.clave_correlacion}",
        f"**KB:** {report.kb}",
        f"**Dashboard:** {report.dashboard}",
        f"**Severidad:** {report.severidad}",
        "**Evidencia:**\n" + _bullets(report.evidencia),
        f"**Duración:** {report.duracion}",
        f"**Recurrencia:** {report.recurrencia}",
    ]

    if report.ruta is Route.NODO:
        if report.patron_cluster:
            rows = [
                [human(b.hora), str(b.problems), "sí" if b.incluye_problem else ""]
                for b in report.patron_cluster
            ]
            out.append(
                "**Patrón de cluster:**\n"
                + _table(["Hora", "Problems", "Incluye este problem"], rows)
            )
        else:
            out.append("**Patrón de cluster:** sin problems RESOURCE_CONTENTION en el periodo")
        out.append("**Problems del mismo workload:** no aplica (problem de nodo/cluster)")
        cluster_parts = []
        if report.problems_cluster:
            cluster_parts.append("(A) ±2 h del inicio:\n" + _cluster_table(report.problems_cluster))
        if report.problems_cluster_periodo:
            cluster_parts.append(
                "(B) desde el inicio:\n" + _cluster_table(report.problems_cluster_periodo)
            )
        out.append(
            "**Problems de servicio del mismo cluster:**\n"
            + ("\n\n".join(cluster_parts) or "ninguno")
        )
    else:
        out.append("**Patrón de cluster:** no aplica")
        if report.problems_workload:
            rows = [
                [
                    r.category,
                    r.name,
                    r.lectura.value,
                    r.patron.value,
                    str(r.problems),
                    str(r.activos),
                    human(r.primero),
                    human(r.ultimo),
                ]
                for r in report.problems_workload
            ]
            headers = [
                "Categoría",
                "Nombre",
                "Lectura",
                "Patrón",
                "Problems",
                "Activos",
                "Primero",
                "Último",
            ]
            out.append("**Problems del mismo workload:**\n" + _table(headers, rows))
        else:
            out.append("**Problems del mismo workload:** ninguno")
        out.append(
            "**Problems de servicio del mismo cluster (±1 h):**\n"
            + (_cluster_table(report.problems_cluster) if report.problems_cluster else "ninguno")
        )

    pasos = " → ".join(
        s.name if s.status is StepStatus.OK else f"{s.name} [{s.status.value}: {s.detail}]"
        for s in report.pasos
    )
    out += [
        f"**Hipótesis:** {report.hipotesis}",
        f"**Confianza:** {report.confianza.value}",
        f"**Acción recomendada:** {report.accion_recomendada}",
        "**Hallazgos secundarios:**"
        + (
            "\n" + _bullets(report.hallazgos_secundarios)
            if report.hallazgos_secundarios
            else " ninguno"
        ),
        f"**Pasos ejecutados:** {report.ruta.value}: {pasos}",
        f"**Cobertura:** {'; '.join(report.cobertura)}",
    ]
    if report.escalamiento:
        e = report.escalamiento
        out.append(f"**Escalamiento:** {e.decision.value} ({e.prioridad}) — {e.motivo}")
    return "\n\n".join(out)


def render_plain(report: TriageReport) -> str:
    """Versión sin markdown para la descripción del incidente en ServiceNow."""
    return render_markdown(report).replace("**", "")
