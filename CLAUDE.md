# CLAUDE.md

Responde en español.

## Regla principal

La lógica de triage vive en Python (`src/dynatrace_triage/triage/rules.py`), nunca en las
instrucciones del agente de Foundry ni de Copilot Studio. Con la skill anterior se
comprobó que el modelo ignora reglas en prosa y solo respeta datos ya calculados. Si
una conclusión del reporte sale mal, corrige o agrega una regla en Python con su test;
no agregues frases al prompt.

## Convenciones

- Las DQL de `dynatrace/queries.py` solo traen y agregan datos. Respeta los hechos del
  tenant anotados en su docstring (filtro explícito de `event.start`, nada de
  `contains(event.description)`, logs por `k8s.workload.name`).
- Cada caso real que falle se agrega como fixture anonimizado en `tests/fixtures/` con un
  test en `tests/test_engine_scenarios.py`.
- Nada de secretos, IDs reales de cluster, nombres de personas ni correos en el
  repositorio: van en `.env` y `config/clusters.yaml` (en `.gitignore`).
- Antes de terminar: `ruff check src tests`, `ruff format src tests` y `pytest`.
