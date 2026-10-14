# dynatrace-triage

Triage de problems de Dynatrace en Python, con escalamiento a ServiceNow, un agente en
Microsoft Foundry y un único agente orquestador en Copilot Studio.

Reemplaza la skill `dynatrace-problem-triage` de Copilot Studio. En las pruebas con esa
skill, el modelo ignoraba las reglas escritas en prosa (duración continua, causa previa,
peso de los logs) y solo respetaba lo que ya venía calculado en las tablas. Aquí esas
reglas son código: Python consulta Grail, clasifica y arma el reporte, y pytest
verifica cada regla con los casos reales que fallaban.

## Arquitectura

```text
Usuario (Teams / web)
   │
   ▼
Copilot Studio ── agente orquestador (único)
   │  agente externo de Microsoft Foundry (Entra ID)
   ▼
Foundry ── agente "dynatrace-triage"
   │  herramienta OpenAPI (x-api-key en una conexión de Foundry)
   │  + file search sobre el conocimiento de arquitectura
   ▼
API de triage (FastAPI, Azure Container Apps) ◄── webhook de Dynatrace Workflows
   ├─► Dynatrace Grail Query API (solo lectura)
   └─► ServiceNow Table API (crea incidentes, idempotente por clave de correlación)
```

| Pieza | Qué decide |
|---|---|
| Python (`triage/rules.py`) | Ruta, patrón, lectura, duración, peso de logs, causa candidata, confianza, prioridad y escalamiento |
| Agente de Foundry | Llama a la API, muestra el reporte tal cual, completa el mapeo de la entidad con el conocimiento y pregunta antes de crear el incidente |
| Copilot Studio | Canal y orquestación: deriva al agente de Foundry |

Detalle en [docs/arquitectura.md](docs/arquitectura.md).

## Estructura

```text
src/dynatrace_triage/
  dynatrace/client.py    cliente de la Grail Query API (execute + poll)
  dynatrace/queries.py   DQL validadas en el tenant (solo traen y agregan datos)
  triage/rules.py        reglas puras: lo que antes era prosa en el SKILL.md
  triage/engine.py       ejecuta la ruta B o la de nodo y arma el reporte
  triage/render.py       reporte en el formato "Salida esperada"
  escalation/            política de escalamiento e incidente en ServiceNow
  servicenow/client.py   Table API (cmdb_ci, change_request, incident)
  api/app.py             FastAPI: /v1/triage, /v1/incidents, /v1/webhooks/dynatrace
  foundry/               instrucciones y script que crea el agente de Foundry
copilot-studio/          instrucciones y pasos del agente orquestador
tests/                   pytest; fixtures con respuestas reales anonimizadas
```

## Empezar

```bash
uv venv && uv pip install -e ".[dev]"      # o: python -m venv .venv && pip install -e ".[dev]"
cp .env.example .env                        # completa los valores
cp config/clusters.example.yaml config/clusters.yaml

pytest                                      # tests unitarios y de regresión (sin red)
triage P-2610334                            # triage real contra el tenant
uvicorn dynatrace_triage.api.app:app --reload
```

Pruebas contra el tenant real (solo lectura, se omiten por defecto):

```bash
pytest -m integration --problem-id P-2610334 -s
```

## Despliegue

1. API en Azure Container Apps: [docs/despliegue.md](docs/despliegue.md).
2. Agente de Foundry: `foundry-setup` (requiere `pip install -e ".[foundry]"`).
3. Orquestador en Copilot Studio: [copilot-studio/orquestador.md](copilot-studio/orquestador.md).

## Seguridad

- Dynatrace: token de plataforma con scopes de solo lectura.
- ServiceNow: el usuario de integración solo lee `cmdb_ci`, `change_request` e
  `incident`, y solo crea `incident`.
- La API rechaza todo si `API_KEYS` está vacío. `problem_id` se valida antes de llegar
  a la DQL, y los valores que vienen de Dynatrace se escapan.
- `/v1/incidents` vuelve a hacer el triage en vez de aceptar texto del chat, así el
  modelo no puede escribir contenido arbitrario en ServiceNow.
- `.env`, `config/clusters.yaml` y `knowledge/` están en `.gitignore`. No subas los
  ADRs originales: tienen API keys, cuentas y correos.
