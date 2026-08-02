# Argorix Agent Guardrails SDK for Python

SDK oficial para instrumentar agentes, tools y pasos LLM con **Argorix Agent Guardrails**.

```bash
pip install argorix-guardrails-agent
```

```python
import argorix_agents
from argorix_agents import ControlSteerError, ControlViolationError, control
```

> **Rebranding.** Este paquete se llamaba `governanceai-guardrails-agent` y su módulo era
> `agent_control`. Ambos siguen publicados como shim de compatibilidad. Ver
> [Migración](#migración-desde-agent_control).

## API cubierta

| Endpoint | Método del SDK |
| --- | --- |
| `POST /v1/agent-guardrails/runtime/agents/init` | `init()`, `AgentGuardrailsClient.init_agent()` |
| `GET /v1/agent-guardrails/runtime/agents/{agent_name}/controls` | `list_agent_controls()` |
| `POST /v1/agent-guardrails/runtime/evaluate` | `@control()`, `evaluate_step()`, `client.evaluate()` |
| `POST /v1/agent-guardrails/runtime/evaluate/stream` | `client.evaluate_stream()`, `client.evaluate_streamed_result()` |
| `POST /v1/agent-guardrails/runtime/events` | `client.record_event()` |

Los guardrails clásicos (`/v1/guardrails/*`) viven en el paquete
[`argorix`](../python/README.md), del que este depende.

## Flujo que resuelve

1. `argorix_agents.init(...)` registra el agente, sus steps y sus evaluadores.
2. Los decoradores `@control(...)` envuelven pasos sync y async.
3. El SDK evalúa cada paso en `pre` y en `post`.
4. Según la decisión: bloquea (`ControlViolationError`), sugiere steering
   (`ControlSteerError`) o deja pasar, y registra el evento runtime.
5. El backend deja trazabilidad por `agent_name`, `step`, `trace_id` y `span_id`.

## Autenticación

- `app_number` en cada request
- `Authorization: Bearer <APP_API_KEY>`

| Variable | Uso | Fallback legado |
| --- | --- | --- |
| `ARGORIX_API_URL` | `base_url` | `ARGORIX_BASE_URL`, `AGENT_CONTROL_URL`, `GOVERNANCE_AI_URL` |
| `ARGORIX_APP_NUMBER` | `app_number` | `AGENT_CONTROL_APP_NUMBER`, `APP_NUMBER` |
| `ARGORIX_APP_API_KEY` | `app_api_key` | `AGENT_CONTROL_APP_API_KEY`, `APP_API_KEY` |

Sin `base_url` explícito ni variable de entorno, cae a `http://127.0.0.1:8001` para
desarrollo local.

## Quick start

```python
import asyncio

import argorix_agents
from argorix_agents import ControlViolationError, control


@control("query_db", step_type="tool")
async def query_db(query: str, context: dict | None = None) -> str:
    return f"Executed: {query}"


async def main() -> None:
    argorix_agents.init(
        agent_name="support_bot",
        agent_description="Customer support automation",
        base_url="https://api.argorix.com",
        app_number=123456,
        app_api_key="ax_live_replace_me",
        default_metadata={"environment": "production"},
    )

    try:
        print(await query_db("SELECT * FROM tickets", context={"tenant": "acme"}))
    except ControlViolationError as exc:
        print(f"Blocked by {exc.control_name}: {exc.message}")


asyncio.run(main())
```

## Evaluación manual

Cuando el decorador no encaja (frameworks con su propio wrapping, orquestadores
externos), evalúa el paso a mano:

```python
evaluation = argorix_agents.evaluate_step(
    stage="pre",
    step={"type": "tool", "name": "lookup_booking", "input": {"email": "a@b.com"}},
)

if evaluation.denied:
    raise RuntimeError(evaluation.matches[0].message)
```

## Streaming (SSE)

```python
client = argorix_agents.current_client()

for event in client.evaluate_stream(
    agent_name="support_bot",
    stage="pre",
    step={"type": "llm", "name": "chat", "input": prompt},
):
    print(event.event, event.data.get("status", ""))
```

`evaluate_streamed_result(...)` consume el stream y devuelve la `AgentEvaluation` final;
levanta `ArgorixAgentError` si llega un evento `error` o si el stream cierra sin `result`.

## Modelos de respuesta

`AgentEvaluation`: `overall_decision`, `allowed` / `denied`, `requires_steering`,
`confidence`, `evaluated_controls`, `matches`, `non_matches`, `errors`, `raw`, más
`matches_with_action(action)`.

`ControlMatch`: `control_id`, `control_name`, `action`, `evaluator_name`,
`selector_path`, `matched`, `confidence`, `message`, `error`, `metadata` y
`steering_message`.

`AgentRegistration`: `created`, `agent`, `agent_name`, `controls`, `raw`.

Los tres aceptan acceso tipo diccionario para leer campos que el control plane agregue
después de esta versión.

## Errores, timeout y retry

`AgentGuardrailsClient` acepta `timeout_seconds`, `max_retries`,
`retry_backoff_seconds` y `retry_status_codes`. Los fallos levantan
`ArgorixAgentError` (alias: `AgentControlError`) con `status_code` y `response_body`.

## Cómo se refleja en la consola

- `Guardrails for Agents`: catálogo, bindings y agentes detectados
- `Guardrails Log`: eventos runtime y decisiones
- `AI Applications > Risk & Governance`: consolidación de señal agentic y runtime

## Migración desde `agent_control`

```bash
pip uninstall governanceai-guardrails-agent
pip install argorix-guardrails-agent
```

| Antes | Ahora |
| --- | --- |
| `import agent_control` | `import argorix_agents` |
| `agent_control.AgentControlClient` | `argorix_agents.AgentGuardrailsClient` |
| `AgentControlError` | `ArgorixAgentError` |
| `AGENT_CONTROL_URL` | `ARGORIX_API_URL` |

Los nombres viejos siguen exportados como alias desde `argorix_agents`. Cambios de
comportamiento a revisar:

- `init()` devuelve `AgentRegistration` y `client.evaluate()` devuelve `AgentEvaluation`
  en vez de `dict`. El acceso por clave se mantiene (`registration["agent"]`).
- `result.matches` ahora son `ControlMatch`; `match["action"]` sigue funcionando.

El paquete `governanceai-guardrails-agent` 0.2.0 reexporta este SDK —incluido el mismo
objeto `state`—, así que mezclar ambos imports en un mismo proceso es consistente.

## Desarrollo

```bash
pip install -e ./sdk/python -e ./sdk/python-agents
python -m pytest ./sdk/python-agents/tests
python -m build ./sdk/python-agents
```

## Semver y changelog

- Versión actual: `0.2.0`
- Historial: [`CHANGELOG.md`](./CHANGELOG.md)
- Licencia: [`LICENSE`](./LICENSE)
