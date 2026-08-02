# Argorix Agent Guardrails SDK for Python

SDK oficial para instrumentar agentes, tools y pasos LLM con **Argorix Agent Guardrails**.

Package publicado:

```bash
pip install argorix-guardrails-agent
```

Import principal:

```python
import argorix_agents
from argorix_agents import ControlViolationError, ControlSteerError, control
```

## API cubierta

- `POST /v1/agent-guardrails/runtime/agents/init`
- `GET /v1/agent-guardrails/runtime/agents/{agent_name}/controls`
- `POST /v1/agent-guardrails/runtime/evaluate`
- `POST /v1/agent-guardrails/runtime/evaluate/stream`
- `GET /v1/agent-guardrails/runtime/approvals/{approval_id}`
- `POST /v1/agent-guardrails/runtime/receipts/consume`
- `POST /v1/agent-guardrails/runtime/events`
- Legacy aliases without `/v1` remain available for compatibility

## Flujo que resuelve

1. `argorix_agents.init(...)` registra el agente y su contexto base.
2. Los decoradores `@control(...)` envuelven pasos sync/async.
3. El SDK evalúa antes y después del paso, enviando el punto de intervención
   ACS (`pre_tool_call`, `post_model_call`, …) junto al `stage` legacy.
4. Si corresponde:
   - bloquea (`ControlViolationError`)
   - pide aprobación humana (`ControlEscalationError`)
   - sugiere steering (`ControlSteerError`)
   - aplica el payload transformado que devuelve el servidor
   - registra eventos runtime
5. El backend deja trazabilidad por `agent_name`, `step`, `trace_id` y `span_id`.

## Aprobaciones humanas

Un control `escalate` detiene el paso y abre una solicitud de aprobación:

```python
from argorix_agents import ControlEscalationError

try:
    issue_refund(request)
except ControlEscalationError as exc:
    approval = argorix_agents.wait_for_approval(exc.approval_id, timeout_seconds=600)
    if approval["status"] == "approved":
        issue_refund(request)
```

`wait_for_approval` bloquea; si tu agente no puede esperar, guarda
`exc.approval_id` y consulta `argorix_agents.get_approval(...)` cuando quieras.

## Transformaciones

Un control `transform` devuelve el paso ya redactado y el SDK lo aplica: al
input antes de ejecutar la función, y a la salida antes de devolverla. Cuando la
firma de la función no permite reconstruir los argumentos con seguridad
(`*args`, `**kwargs` o parámetros posicionales puros), el SDK no inventa nada:
ejecuta con el valor original y marca `transform_skipped` en el evento runtime.

## Autenticacion

El SDK usa:

- `app_number` en requests
- `Authorization: Bearer <APP_API_KEY>`

Puedes configurar por argumentos o por variables:

- `AGENT_CONTROL_URL`
- `AGENT_CONTROL_APP_NUMBER`
- `AGENT_CONTROL_APP_API_KEY`
- `GOVERNANCE_AI_URL`

No depende de `127.0.0.1` salvo que tú lo configures explícitamente para desarrollo.

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
        app_api_key="ga_live_replace_me",
        default_metadata={"environment": "production"},
    )

    try:
        print(await query_db("SELECT * FROM tickets", context={"tenant": "acme"}))
    except ControlViolationError as exc:
        print(f"Blocked by {exc.control_name}: {exc.message}")


asyncio.run(main())
```

## Que resuelve

- Registro del agente y sus pasos
- Decoradores `@control()` para pasos sync/async
- Evaluacion `pre` y `post` por step
- Eventos de ejecucion, bloqueo, steering y error
- Metadata runtime por trace/span

## Flujo runtime documentado

1. `argorix_agents.init(...)` registra el agente en ARGORIX.
2. El runtime resuelve controles por `agent_name`.
3. Cada step decorado puede evaluarse `pre` y `post`.
4. El SDK registra:
   - decision
   - trace_id
   - span_id
   - duration_ms
   - errores operativos
5. El backend deja telemetría visible en `Guardrails Agents` y `Guardrails Log`.

En la consola actual esto aparece separado de forma más explícita en:

- `Guardrails for Agents` para catálogo, bindings y agentes detectados
- `Guardrails Log` para eventos runtime y decisiones
- `AI Applications > Risk & Governance` cuando el profile consolida señal agentic y runtime

## Tipos de integración recomendados

- agentes internos Python
- tools wrappers
- orquestadores async
- workers de automatización
- asistentes con múltiples steps y decisiones runtime

## Errores, timeout y retry

`AgentControlClient` soporta:

- `timeout_seconds`
- `max_retries`
- `retry_backoff_seconds`
- `retry_status_codes`

Errores del cliente:

- `AgentControlError`
- `status_code`
- `response_body`

Configuración recomendada:

- `base_url` explícito por código para producción
- `AGENT_CONTROL_URL` o `GOVERNANCE_AI_URL` para entornos gestionados
- localhost solo si realmente estás corriendo el backend local

## Tests y release

Instalacion editable:

```bash
pip install -e ./sdk/python-agents
```

Tests:

```bash
python -m pytest ./sdk/python-agents/tests
```

Build:

```bash
python -m build ./sdk/python-agents
```

Publicacion:

```bash
python -m twine upload dist/*
```

## Release checklist cubierto

- README
- quickstart
- errores y retries
- tests
- changelog
- licencia
- package naming alineado a `argorix-guardrails-agent`, con `governanceai-guardrails-agent` como shim

## Semver y changelog

- Version actual: `0.1.0`
- Historial: [`CHANGELOG.md`](./CHANGELOG.md)
- Licencia: [`LICENSE`](./LICENSE)

## Migración desde `agent_control`

```bash
pip uninstall governanceai-guardrails-agent
pip install argorix-guardrails-agent
```

| Antes | Ahora |
| --- | --- |
| `import agent_control` | `import argorix_agents` |
| `AgentControlClient` | `AgentGuardrailsClient` (el nombre viejo sigue exportado) |
| `AgentControlError` | `ArgorixAgentError` (idem) |
| `AGENT_CONTROL_URL` | `ARGORIX_API_URL` |
| `AGENT_CONTROL_APP_NUMBER` | `ARGORIX_APP_NUMBER` |
| `AGENT_CONTROL_APP_API_KEY` | `ARGORIX_APP_API_KEY` |

Las variables viejas se siguen leyendo como fallback, y el paquete
`governanceai-guardrails-agent` 0.3.0 reexporta este SDK —incluido el mismo objeto
`state`—, así que mezclar ambos imports en un proceso es consistente.

> **Si instalaste `argorix-guardrails-agent` 0.2.0, actualiza.** Esa versión salió de un
> respaldo del repo con cuatro meses de atraso y no trae el contrato ACS: le faltan
> `intervention_point`, `escalate` con aprobaciones, `transform`, el fail-closed del
> servidor, la redención de recibos y los controles de presupuesto por sesión.
