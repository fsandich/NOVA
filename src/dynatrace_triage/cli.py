"""CLI para probar el triage contra el tenant real: `triage P-2610334`."""

from __future__ import annotations

import argparse
import sys

from .config import Settings
from .service import build_service
from .triage.engine import ProblemNotFound
from .triage.render import render_markdown


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Triage de un problem de Dynatrace (solo lectura)")
    parser.add_argument("problem_id", help="ej. P-2610334")
    parser.add_argument("--json", action="store_true", help="imprime el reporte completo en JSON")
    args = parser.parse_args(argv)

    service = build_service(Settings())
    try:
        report = service.triage(args.problem_id)
    except ProblemNotFound as exc:
        print(exc, file=sys.stderr)
        return 1
    print(report.model_dump_json(indent=2) if args.json else render_markdown(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
