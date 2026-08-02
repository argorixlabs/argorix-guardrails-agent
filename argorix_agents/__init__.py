"""Argorix Agent Guardrails SDK for Python agents and tool execution."""

from __future__ import annotations

import os
from typing import Any

from .client import (
    API_PREFIX,
    AgentControlClient,
    AgentControlError,
    AgentGuardrailsClient,
    ArgorixAgentError,
    ArgorixError,
    StreamEvent,
)
from .decorators import ControlSteerError, ControlViolationError, control
from .models import AgentEvaluation, AgentRegistration, ControlMatch
from .registry import clear_registered_steps, list_registered_steps
from .state import AgentRuntimeState, state
from .version import SDK_VERSION

DEFAULT_BASE_URL = "http://127.0.0.1:8001"

_BASE_URL_ENV_VARS = (
    "ARGORIX_API_URL",
    "ARGORIX_BASE_URL",
    "AGENT_CONTROL_URL",
    "GOVERNANCE_AI_URL",
)
_APP_NUMBER_ENV_VARS = ("ARGORIX_APP_NUMBER", "AGENT_CONTROL_APP_NUMBER", "APP_NUMBER")
_APP_API_KEY_ENV_VARS = ("ARGORIX_APP_API_KEY", "AGENT_CONTROL_APP_API_KEY", "APP_API_KEY")

__version__ = SDK_VERSION


def _first_env(names: tuple[str, ...]) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _resolve_base_url(base_url: str | None) -> str:
    resolved = (base_url or _first_env(_BASE_URL_ENV_VARS) or DEFAULT_BASE_URL).strip()
    if not resolved:
        raise ValueError("base_url is required.")
    return resolved


def _resolve_app_number(app_number: int | None) -> int:
    raw = app_number if app_number is not None else _first_env(_APP_NUMBER_ENV_VARS)
    if raw is None or str(raw).strip() == "":
        raise ValueError(
            "app_number is required for argorix_agents.init(). "
            "Pass it explicitly or set ARGORIX_APP_NUMBER."
        )
    return int(raw)


def _resolve_app_api_key(app_api_key: str | None) -> str:
    raw = app_api_key or _first_env(_APP_API_KEY_ENV_VARS)
    if raw is None or not str(raw).strip():
        raise ValueError(
            "app_api_key is required for argorix_agents.init(). "
            "Pass it explicitly or set ARGORIX_APP_API_KEY."
        )
    return str(raw).strip()


def init(
    *,
    agent_name: str,
    agent_description: str | None = None,
    agent_version: str | None = None,
    base_url: str | None = None,
    app_number: int | None = None,
    app_api_key: str | None = None,
    steps: list[dict[str, Any]] | None = None,
    agent_metadata: dict[str, Any] | None = None,
    evaluators: list[dict[str, Any]] | None = None,
    policy_id: str | None = None,
    control_ids: list[str] | None = None,
    default_metadata: dict[str, Any] | None = None,
) -> AgentRegistration:
    """Register the agent and bind process-wide defaults used by ``@control()``."""
    client = AgentGuardrailsClient(
        base_url=_resolve_base_url(base_url),
        app_number=_resolve_app_number(app_number),
        app_api_key=_resolve_app_api_key(app_api_key),
    )
    merged_steps: list[dict[str, Any]] = []
    seen_keys: set[tuple[str, str]] = set()
    for step in (steps or []) + list_registered_steps():
        step_type = str(step.get("type", "")).strip()
        step_name = str(step.get("name", "")).strip()
        key = (step_type, step_name)
        if not step_type or not step_name or key in seen_keys:
            continue
        seen_keys.add(key)
        merged_steps.append(step)

    registration = client.init_agent(
        agent_name=agent_name,
        agent_description=agent_description,
        agent_version=agent_version,
        agent_metadata=agent_metadata or {},
        steps=merged_steps,
        evaluators=evaluators or [],
    )
    state.base_url = client.base_url
    state.app_number = client.app_number
    state.app_api_key = client.app_api_key
    state.registered_steps = merged_steps
    state.agent = registration.agent or {"agent_name": agent_name}
    state.init_response = registration.raw
    state.default_policy_id = (policy_id or "").strip() or None
    state.default_control_ids = [
        str(item).strip() for item in (control_ids or []) if str(item).strip()
    ]
    state.default_metadata = dict(default_metadata or {})
    return registration


def current_agent() -> dict[str, Any] | None:
    """Return the agent registered by the last :func:`init` call."""
    return state.agent


def current_client() -> AgentGuardrailsClient:
    """Build a client from the state bound by :func:`init`."""
    if state.agent is None:
        raise RuntimeError("argorix_agents.init() must be called first.")
    return AgentGuardrailsClient(
        base_url=_resolve_base_url(state.base_url),
        app_number=_resolve_app_number(state.app_number),
        app_api_key=_resolve_app_api_key(state.app_api_key),
    )


def list_agent_controls(
    *,
    agent_name: str | None = None,
    policy_id: str | None = None,
) -> dict[str, Any]:
    """List the controls bound to the initialized agent."""
    if state.agent is None:
        raise RuntimeError("argorix_agents.init() must be called before listing controls.")
    return current_client().list_agent_controls(
        agent_name=agent_name or str(state.agent["agent_name"]),
        policy_id=policy_id or state.default_policy_id,
    )


def evaluate_step(
    *,
    stage: str,
    step: dict[str, Any],
    agent_name: str | None = None,
    policy_id: str | None = None,
    control_ids: list[str] | None = None,
    trace_id: str | None = None,
    span_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> AgentEvaluation:
    """Evaluate a step manually, without the ``@control()`` decorator."""
    if state.agent is None:
        raise RuntimeError("argorix_agents.init() must be called before evaluating steps.")
    return current_client().evaluate(
        agent_name=agent_name or str(state.agent["agent_name"]),
        stage=stage,
        step=step,
        policy_id=policy_id or state.default_policy_id,
        control_ids=control_ids or list(state.default_control_ids),
        trace_id=trace_id,
        span_id=span_id,
        metadata=metadata,
    )


def reset(*, clear_steps: bool = False) -> None:
    """Clear the process-wide state bound by :func:`init`."""
    state.base_url = None
    state.app_number = None
    state.app_api_key = None
    state.agent = None
    state.init_response = None
    state.registered_steps = []
    state.default_policy_id = None
    state.default_control_ids = []
    state.default_metadata = {}
    if clear_steps:
        clear_registered_steps()


__all__ = [
    "API_PREFIX",
    "DEFAULT_BASE_URL",
    "AgentControlClient",
    "AgentControlError",
    "AgentEvaluation",
    "AgentGuardrailsClient",
    "AgentRegistration",
    "AgentRuntimeState",
    "ArgorixAgentError",
    "ArgorixError",
    "ControlMatch",
    "ControlSteerError",
    "ControlViolationError",
    "SDK_VERSION",
    "StreamEvent",
    "clear_registered_steps",
    "control",
    "current_agent",
    "current_client",
    "evaluate_step",
    "init",
    "list_agent_controls",
    "list_registered_steps",
    "reset",
    "state",
    "__version__",
]
