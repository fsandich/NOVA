# Agente orquestador en Copilot Studio

Un solo agente en Copilot Studio, sin skills ni herramientas propias de Dynatrace. Todo
el triage lo hace el agente de Foundry, que llama a la API. Así el orquestador no tiene
otra forma de "diagnosticar" que no sea derivar.

## 1. Preparar el agente

Puedes reutilizar "Agente Nova" o crear uno nuevo. Si reutilizas Nova:

1. Abre el agente en [Copilot Studio](https://copilotstudio.microsoft.com).
2. En **Skills**, elimina `dynatrace-problem-triage`.
3. En **Tools**, elimina la herramienta MCP de Dynatrace.
4. En **Knowledge**, quita los archivos de arquitectura: ahora viven en el agente de
   Foundry, junto a los datos que los usan.
5. Confirma en **Settings → Generative AI** que la orquestación generativa está
   activada.

## 2. Conectar el agente de Foundry

1. Abre **Agents → Add an agent**.
2. Elige conectar un agente externo de tipo **Microsoft Foundry**.
3. Crea la conexión con **Microsoft Entra ID**.
4. Completa:
   - **Project endpoint:** `https://<recurso>.services.ai.azure.com/api/projects/<proyecto>/`
     (con barra al final; sin ella la conexión suele fallar con error 400).
   - **Agent ID:** el que imprimió `foundry-setup`.
5. Nombre: `Triage Dynatrace`.
6. Descripción (el orquestador decide con ella cuándo derivar):
   ```
   Diagnostica problems de Dynatrace a partir de un problemId (P-<número>): consulta
   Dynatrace, entrega la hipótesis de causa raíz con su confianza y, si el usuario
   confirma, crea el incidente en ServiceNow.
   ```

> Los nombres de menú pueden variar porque esta función se ha ido moviendo entre
> versiones de Copilot Studio. Revisa la guía actual de Microsoft Learn para conectar
> agentes de Foundry si no encuentras la opción.

## 3. Instrucciones del orquestador

En **Overview → Instructions**, reemplaza el texto por:

```
Eres el agente SRE de triage de Dynatrace. Eres de solo lectura sobre Dynatrace: nunca modificas, reconoces ni silencias un problem.

Cuando el usuario mencione un problemId (P-<número>), pida analizar un problem o quiera escalarlo, deriva siempre al agente "Triage Dynatrace" y muestra su respuesta completa, sin resumirla ni cambiar cifras, la Hipótesis o la Confianza.

No diagnostiques por tu cuenta ni inventes datos de logs o métricas. Si el usuario no da un problemId, pídeselo.

Responde siempre en español, de forma directa y estructurada.
```

La política de escalamiento ya no va en estas instrucciones: la calcula la API y el
agente de Foundry la aplica al preguntar.

## 4. Probar

En el panel **Test**, abre una conversación nueva para cada caso:

1. `Analiza el problema P-2610334`: Duración "Incidente continuo desde…", Hipótesis
   empezando por el Backoff event, sin DataPower.
2. `Analiza el problema P-2610405`: causa DataPower con su código de servicio,
   Confianza Alta, escalamiento ESCALAR (PROD).
3. `Analiza el problema P-260916953`: ruta de nodo, evento de cluster.
4. Responde "sí, escálalo" en el caso 2. Debe devolver el número de incidente. Si
   repites, debe decir que ya existía.

Cuando los cuatro estén bien, publica el agente.
