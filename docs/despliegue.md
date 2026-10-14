# Despliegue

Orden recomendado: credenciales → API → Foundry → Copilot Studio → webhook de Dynatrace.
Prueba cada pieza antes de pasar a la siguiente.

## 1. Credenciales

### Dynatrace (solo lectura)

1. En Dynatrace, abre **Account Management → Identity & access management → Platform
   tokens** (o crea un OAuth client) y genera un token para la integración.
2. Dale solo scopes de lectura de Grail: `storage:buckets:read`, `storage:events:read`,
   `storage:logs:read` y `storage:entities:read`.
3. Valida el token antes de seguir:
   ```bash
   triage P-2610334
   ```
   Si alguna consulta da HTTP 403, falta un scope: el mensaje dice cuál.

> Los nombres de scopes cambian entre versiones de la plataforma. Usa como referencia
> los que tiene hoy el token del servidor MCP de Dynatrace, que ya consulta las mismas
> tablas.

### ServiceNow

Crea un usuario de integración con acceso de lectura a `cmdb_ci`, `change_request` e
`incident`, y de creación a `incident`, sin permisos de edición ni borrado. Si el
grupo de asignación tiene ACL propias, dale acceso a ese grupo.

## 2. API en Azure Container Apps

```bash
RG=rg-dynatrace-triage
APP=dynatrace-triage
az group create -n $RG -l eastus2

# Construye la imagen desde el Dockerfile y crea la app con ingreso HTTPS
az containerapp up -n $APP -g $RG -l eastus2 --source . --ingress external --target-port 8000

# Secretos (mejor aún: referencias a Key Vault con identidad administrada)
az containerapp secret set -n $APP -g $RG --secrets \
  dt-token="<token>" snow-password="<password>" api-keys="<clave1>,<clave2>"

az containerapp update -n $APP -g $RG --set-env-vars \
  DT_ENV_URL="https://<env>.apps.dynatrace.com" \
  DT_PLATFORM_TOKEN=secretref:dt-token \
  CLUSTERS="<nombre-prod>=KUBERNETES_CLUSTER-...,<nombre-qa>=KUBERNETES_CLUSTER-..." \
  SNOW_INSTANCE_URL="https://<instancia>.service-now.com" \
  SNOW_USERNAME="<usuario>" SNOW_PASSWORD=secretref:snow-password \
  SNOW_ASSIGNMENT_GROUP="<sys_id del grupo>" \
  API_KEYS=secretref:api-keys \
  PUBLIC_BASE_URL="https://<fqdn de la app>"
```

Prueba:

```bash
curl https://<fqdn>/health
curl -X POST https://<fqdn>/v1/triage -H "x-api-key: <clave1>" \
  -H "Content-Type: application/json" -d '{"problem_id":"P-2610334"}'
```

Usa una clave distinta para Foundry y otra para Dynatrace, así puedes rotarlas por
separado (`API_KEYS` acepta varias separadas por coma).

## 3. Agente de Microsoft Foundry

1. En el [portal de Foundry](https://ai.azure.com), crea un proyecto y despliega un
   modelo (por ejemplo `gpt-4.1`).
2. En el proyecto, abre **Management center → Connected resources → New connection →
   Custom keys**. Agrega la clave `x-api-key` con el valor de `<clave1>` y copia el
   **ID de la conexión**.
3. Desde tu máquina (con `az login` hecho):
   ```bash
   pip install -e ".[foundry]"
   export PROJECT_ENDPOINT="https://<recurso>.services.ai.azure.com/api/projects/<proyecto>"
   foundry-setup --public-url https://<fqdn> --connection-id "<id de la conexión>" \
     --model <deployment> --knowledge-dir knowledge
   ```
   Anota el ID del agente que imprime. Para actualizar instrucciones o herramientas:
   `foundry-setup ... --agent-id <id>`.
4. Prueba en el **playground** del agente: "Analiza el problema P-2610334".

> `foundry-setup` usa `azure-ai-agents` 1.x (`OpenApiTool`, `FileSearchTool`). Si el
> SDK cambió, `--dump-spec openapi.json` genera la especificación para cargarla a mano
> en el portal (Agents → Tools → OpenAPI).

## 4. Orquestador en Copilot Studio

Sigue [copilot-studio/orquestador.md](../copilot-studio/orquestador.md).

## 5. Webhook de Dynatrace (camino automático)

1. En Dynatrace, abre **Settings → General → External requests** y agrega el dominio
   de la API a la lista de hosts permitidos.
2. Guarda la clave de Dynatrace en **Credential vault**.
3. Crea un **Workflow**:
   - Trigger: **Davis problem trigger**, filtrado por los ambientes y categorías que
     quieras automatizar.
   - Acción **HTTP request**: `POST https://<fqdn>/v1/webhooks/dynatrace`, header
     `x-api-key` desde el Credential vault, body:
     ```json
     {"problem_id": "{{ event()['display_id'] }}"}
     ```
4. Abre un problem de prueba y verifica la respuesta: `decision` e `incident_number`.
