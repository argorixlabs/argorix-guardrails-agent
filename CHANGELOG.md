# Changelog

## 0.2.0 - 2026-08-01

### Rebranding

- Renombrado de `governanceai-guardrails-agent` a `argorix-guardrails-agent`, y del
  módulo `agent_control` a `argorix_agents`. El paquete anterior queda publicado como
  shim de compatibilidad.
- `AgentControlClient` → `AgentGuardrailsClient`, `AgentControlError` →
  `ArgorixAgentError`. Los nombres anteriores siguen exportados como alias.
- Variables `ARGORIX_API_URL`, `ARGORIX_APP_NUMBER`, `ARGORIX_APP_API_KEY`, con fallback
  a `AGENT_CONTROL_*`, `GOVERNANCE_AI_URL`, `APP_NUMBER` y `APP_API_KEY`.

### Nuevo

- `evaluate_stream()` y `evaluate_streamed_result()` sobre
  `POST /v1/agent-guardrails/runtime/evaluate/stream`.
- `evaluate_step()` para evaluar pasos sin el decorador `@control()`.
- `current_client()` para obtener un cliente ya configurado desde el estado de `init()`.
- Respuestas tipadas: `AgentEvaluation`, `ControlMatch`, `AgentRegistration`, con `raw`
  y acceso tipo diccionario para campos nuevos.
- `evaluation.denied` y `evaluation.matches_with_action(action)`.
- `match.steering_message` como acceso directo al mensaje de steering.
- Header `User-Agent: argorix-agents-python/<versión>` en todas las requests.
- Marcador `py.typed`: el paquete distribuye sus tipos.

### Cambios de comportamiento

- El transporte HTTP ahora vive en `argorix` (dependencia nueva `argorix>=0.2.0`), lo que
  alinea reintentos, timeouts y manejo de errores entre ambos SDKs.
- `init()` devuelve `AgentRegistration` y `client.evaluate()` devuelve `AgentEvaluation`
  en vez de `dict`; el acceso por clave se mantiene.
- `result.matches` pasa de `list[dict]` a `list[ControlMatch]`; el acceso por clave se
  mantiene.

## 0.1.0 - 2026-03-22

- Created the standalone PyPI package `governanceai-guardrails-agent`.
- Added `AgentControlClient` retry and timeout controls.
- Added structured `AgentControlError` with HTTP status and response body details.
- Added package-specific README, license, tests, and packaging metadata.
