from __future__ import annotations

import os
from typing import Any

import time

from .client import AgentControlClient, AgentControlError
from .decorators import (
    ControlEscalationError,
    ControlSteerError,
    ControlViolationError,
    control,
)
from .registry import clear_registered_steps, list_registered_steps
from .state import state

__version__ = "0.3.0"

#: Canonical names first, then the ones this SDK used before the Argorix rebrand.
#: Deployments already exporting the old ones keep working untouched.
_BASE_URL_ENV_VARS = (
    "ARGORIX_API_URL",
    "ARGORIX_BASE_URL",
    "AGENT_CONTROL_URL",
    "GOVERNANCE_AI_URL",
)
_APP_NUMBER_ENV_VARS = ("ARGORIX_APP_NUMBER", "AGENT_CONTROL_APP_NUMBER", "APP_NUMBER")
_APP_API_KEY_ENV_VARS = ("ARGORIX_APP_API_KEY", "AGENT_CONTROL_APP_API_KEY", "APP_API_KEY")


def _first_env(names: tuple[str, ...]) -> str | None:
    for name in names:
        value = os.getenv(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _resolve_base_url(base_url: str | None) -> str:
    resolved = (base_url or _first_env(_BASE_URL_ENV_VARS) or "http://127.0.0.1:8001").strip()
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
    redeem_receipts: bool = False,
) -> dict[str, Any]:
    """Register this agent and configure the runtime.

    Set `redeem_receipts=True` to have every controlled step redeem its
    signed receipt before running. That is what makes a step that was
    authorized-then-altered, replayed or expired fail to execute rather
    than merely be recorded. It requires an ARGORIX backend that exposes
    the redemption endpoint, which is why it is opt-in: this SDK upgrades
    independently of the backend, and defaulting it on would turn a
    `pip install -U` into an outage against an older deployment.
    """
    client = AgentControlClient(
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

    response = client.init_agent(
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
    state.agent = response.get("agent") or {"agent_name": agent_name}
    state.init_response = response
    state.default_policy_id = (policy_id or "").strip() or None
    state.default_control_ids = [
        str(item).strip() for item in (control_ids or []) if str(item).strip()
    ]
    state.default_metadata = dict(default_metadata or {})
    state.redeem_receipts = bool(redeem_receipts)
    return response


def current_agent() -> dict[str, Any] | None:
    return state.agent


def list_agent_controls(*, agent_name: str | None = None, policy_id: str | None = None) -> dict[str, Any]:
    if state.agent is None:
        raise RuntimeError("argorix_agents.init() must be called before listing controls.")
    client = AgentControlClient(
        base_url=_resolve_base_url(state.base_url),
        app_number=_resolve_app_number(state.app_number),
        app_api_key=_resolve_app_api_key(state.app_api_key),
    )
    return client.list_agent_controls(
        agent_name=agent_name or str(state.agent["agent_name"]),
        policy_id=policy_id or state.default_policy_id,
    )


def get_approval(approval_id: str) -> dict[str, Any]:
    """Read one approval opened by an `escalate` control."""
    if state.agent is None:
        raise RuntimeError("argorix_agents.init() must be called before reading approvals.")
    client = AgentControlClient(
        base_url=_resolve_base_url(state.base_url),
        app_number=_resolve_app_number(state.app_number),
        app_api_key=_resolve_app_api_key(state.app_api_key),
    )
    return client.get_approval(approval_id=approval_id)


def wait_for_approval(
    approval_id: str,
    *,
    timeout_seconds: float = 300.0,
    poll_seconds: float = 5.0,
) -> dict[str, Any]:
    """Block until a human resolves an approval, or the wait runs out.

    Returns the final approval payload. A caller that cannot block should keep
    the `approval_id` from `ControlEscalationError` and poll `get_approval()`
    on its own schedule instead.
    """
    deadline = time.monotonic() + max(0.0, float(timeout_seconds))
    interval = max(1.0, float(poll_seconds))
    while True:
        approval = get_approval(approval_id)
        if str(approval.get("status")) != "pending":
            return approval
        if time.monotonic() >= deadline:
            return approval
        time.sleep(min(interval, max(0.0, deadline - time.monotonic()) or interval))


def reset(*, clear_steps: bool = False) -> None:
    state.base_url = None
    state.app_number = None
    state.app_api_key = None
    state.agent = None
    state.init_response = None
    state.registered_steps = []
    state.default_policy_id = None
    state.default_control_ids = []
    state.default_metadata = {}
    state.redeem_receipts = False
    if clear_steps:
        clear_registered_steps()


#: Post-rebrand names. The `AgentControl*` spelling stays exported so existing code and
#: the `agent_control` compatibility shim keep resolving.
AgentGuardrailsClient = AgentControlClient
ArgorixAgentError = AgentControlError


__all__ = [
    "AgentControlClient",
    "AgentControlError",
    "AgentGuardrailsClient",
    "ArgorixAgentError",
    "ControlEscalationError",
    "ControlSteerError",
    "ControlViolationError",
    "__version__",
    "clear_registered_steps",
    "control",
    "current_agent",
    "get_approval",
    "init",
    "list_agent_controls",
    "list_registered_steps",
    "reset",
    "wait_for_approval",
]
