# Arquitectura

## Por qué Python y no una skill

La skill `dynatrace-problem-triage` de Copilot Studio funcionaba cuando la DQL ya traía
la conclusión calculada (columnas `ruta`, `patron`, `lectura`, `duracion_reportar`), y
fallaba cada vez que una regla quedaba escrita en prosa. Fallos que se vieron en las
pruebas con el tenant:

| Caso | Qué hacía mal el agente | Dónde se resuelve ahora |
|---|---|---|
| P-2610334 | Decía "duró 198 min" en un incidente continuo desde el día anterior | `rules.summarize_recurrence` |
| P-2610334 | Culpaba a una saturación de memoria que coincidía con el inicio, no a la causa previa | `rules.classify_workload_rows` (lectura `ORIGEN_PROBABLE`) |
| P-2610334 | Usaba una línea suelta de DataPower como causa | `rules.split_logs` (umbral de 20 líneas) |
| P-2610407 | Daba confianza Alta con 1 línea de log | `rules.confidence_route_b` |
| Varios | Elegía la ruta de nodo para workloads | `rules.classify_route` |
| Varios | Se salteaba consultas o escribía "pendiente" | `TriageEngine` ejecuta siempre todas y registra el estado de cada paso |

Ahora el modelo de lenguaje no decide nada del diagnóstico: solo muestra el reporte,
agrega contexto de arquitectura y conversa.

## Componentes

```text
                       ┌───────────────────────────────┐
 Usuario ─ Teams ────► │ Copilot Studio                │  único agente orquestador
                       │  instrucciones + agente       │
                       │  externo de Foundry           │
                       └──────────────┬────────────────┘
                                      │ Entra ID
                       ┌──────────────▼────────────────┐
                       │ Foundry: agente               │  modelo + file search
                       │ "dynatrace-triage"            │  (conocimiento saneado)
                       └──────────────┬────────────────┘
                                      │ OpenAPI tool, x-api-key (conexión de Foundry)
 Dynatrace Workflows ─ webhook ─────► │
                       ┌──────────────▼────────────────┐
                       │ API de triage (FastAPI)       │  Azure Container Apps
                       │  TriageEngine + reglas        │
                       │  política de escalamiento     │
                       └───────┬───────────────┬───────┘
                               │               │
                 Grail Query API          ServiceNow Table API
                 (solo lectura)           (lee CMDB/cambios, crea incident)
```

## Flujo de chat

1. El usuario escribe "Analiza el problema P-2610334" en el agente de Copilot Studio.
2. El orquestador deriva al agente de Foundry (agente externo).
3. Foundry llama a `triage_problem` → `POST /v1/triage`.
4. La API ejecuta la ruta que corresponde (B o nodo), calcula todo y devuelve
   `markdown` + `report` (JSON con la decisión de escalamiento).
5. Foundry muestra el `markdown` tal cual y pregunta si escalar.
6. Si el usuario confirma, Foundry llama a `create_incident` → `POST /v1/incidents`. La
   API repite el triage y crea el incidente, o devuelve el abierto con la misma clave de
   correlación.

## Flujo automático

Dynatrace Workflows (problem abierto) → `POST /v1/webhooks/dynatrace`. La API hace el
triage y aplica la política: solo crea incidente con `ESCALAR` (prioridad alta o
confianza Baja). Con `PREGUNTAR` no hay humano a quien preguntar, así que no crea nada.
La notificación a Teams queda como siguiente paso (ver "Pendiente").

Ya no hace falta el Agent Flow de Copilot Studio para este camino: Dynatrace llama
directo a la API.

## Rutas

| Ruta | Cuándo | Consultas |
|---|---|---|
| `RUTA_B` | Servicios, workloads, Custom Alerts | B2 recurrencia · B3 problems del workload · B4 cluster ±1 h · B5 eventos K8s · B6 logs |
| `RUTA_NODO` | `RESOURCE_CONTENTION` o entidad nodo/cluster | patrón por hora · eventos K8s del cluster · reinicios · problems (A) ±2 h · problems (B) del periodo |

## Decisiones

- **La API vuelve a hacer el triage al crear el incidente.** Así el contenido del
  incidente sale de los datos de Dynatrace y no de texto que el modelo pueda alterar.
- **Idempotencia por `correlation_id`.** La clave es el workload padre (o
  cluster + tipo en problems de nodo), para no duplicar incidentes cuando fallan varios
  pods o se repite la alerta.
- **Prioridad por ambiente.** Dynatrace no trae P1–P4: PROD = P2, el resto P3
  (`HIGH_PRIORITY_ENVIRONMENTS`).
- **Consultas secuenciales.** Cada consulta tarda menos de 1 s. Si la latencia molesta,
  B2–B6 son independientes y se pueden paralelizar.

## Pendiente

- Notificación a Teams en el camino automático (tarjeta adaptable vía Power Automate o
  Microsoft Graph).
- Desglose de trazas para Custom Alerts. En la skill se hacía con `create-dql` sobre
  spans y fallaba con `GENERATED_DQL_INVALID`.
- Confirmar los scopes exactos del token de Grail y el mapeo de clusters de DR.
