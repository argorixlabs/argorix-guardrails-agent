# Changelog

## 0.3.0 - 2026-08-02

### Rebranding

- La distribución pasa de `governanceai-guardrails-agent` a `argorix-guardrails-agent`,
  y el módulo de `agent_control` a `argorix_agents`. El nombre anterior sigue publicado
  como shim: depende de este paquete, lo reexporta —incluido el mismo objeto `state`— y
  avisa con `DeprecationWarning`.
- `AgentControlClient` y `AgentControlError` mantienen su nombre y además se exportan
  como `AgentGuardrailsClient` y `ArgorixAgentError`.
- `ARGORIX_API_URL`, `ARGORIX_APP_NUMBER` y `ARGORIX_APP_API_KEY` son las variables
  canónicas; `AGENT_CONTROL_*`, `GOVERNANCE_AI_URL`, `APP_NUMBER` y `APP_API_KEY` se
  siguen leyendo como fallback.

### Nuevo

- `evaluate_stream()` y `evaluate_streamed_result()` sobre
  `POST /v1/agent-guardrails/runtime/evaluate/stream`. El stream es perezoso y no se
  reintenta una vez abierto; un evento `error` o un cierre sin `result` levantan
  `AgentControlError` en vez de parecer un allow.
- Marcador `py.typed`.

### Quitado

- `argorix_agents.models` (`AgentEvaluation`, `ControlMatch`, `AgentRegistration`).
  Venían de la 0.2.0 armada desde el respaldo y no correspondían con este cliente,
  que devuelve `dict`. Si los importabas, lee el payload por clave.

### Aviso sobre `argorix-guardrails-agent` 0.2.0 en PyPI

Esa versión se publicó desde un respaldo del repo con cuatro meses de atraso y **no
contiene el contrato ACS**: le faltan `intervention_point`, la decisión `escalate` con
`ControlEscalationError` / `get_approval()` / `wait_for_approval()`, la decisión
`transform`, el fail-closed alineado con el servidor, la redención de recibos
(`consume_receipt()`) y los controles de presupuesto por sesión. Si la instalaste,
actualiza a 0.3.0: no es un incremento menor, es la primera versión de este paquete que
refleja el producto.

## 0.2.0 - 2026-07-28

Alineación con el contrato ACS (Agent Control Specification) del backend.

- Envía `intervention_point` (`pre_tool_call`, `post_model_call`, …) además de
  `stage`, que se mantiene por compatibilidad.
- Nueva decisión `escalate`: lanza `ControlEscalationError` con `approval_id`, y
  añade `get_approval()` / `wait_for_approval()` para resolverla.
- Nueva decisión `transform`: el SDK aplica el payload transformado que devuelve
  el servidor, tanto al input del paso como a su salida. Si la firma no se puede
  reconstruir con seguridad, el evento marca `transform_skipped`.
- Fail-closed alineado con el servidor: un error de evaluación bloquea salvo que
  el control declare `fail_open`. Antes cualquier error rompía el paso.
- `steering_message` se lee del campo propio del resultado, con fallback al
  antiguo `metadata.steering_message`.
- Los eventos runtime incluyen `reason_code`, `fail_closed` y `evaluator_names`.

## 0.1.0 - 2026-03-22

- Created the standalone PyPI package `governanceai-guardrails-agent`.
- Added `AgentControlClient` retry and timeout controls.
- Added structured `AgentControlError` with HTTP status and response body details.
- Added package-specific README, license, tests, and packaging metadata.
