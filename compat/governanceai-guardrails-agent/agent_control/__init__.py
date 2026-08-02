"""Deprecated compatibility shim. Install and import ``argorix_agents`` instead.

``agent_control`` is the pre-rebrand module of the Argorix Agent Guardrails SDK. This
distribution has no implementation of its own: it depends on ``argorix-guardrails-agent``
and re-exports it, including the shared runtime ``state``, so ``agent_control.init()`` and
``@argorix_agents.control()`` still cooperate inside one process.
"""

import warnings

from argorix_agents import (  # noqa: F401
    AgentControlClient,
    AgentControlError,
    AgentGuardrailsClient,
    ArgorixAgentError,
    ControlEscalationError,
    ControlSteerError,
    ControlViolationError,
    __version__,
    clear_registered_steps,
    control,
    current_agent,
    get_approval,
    init,
    list_agent_controls,
    list_registered_steps,
    reset,
    state,
    wait_for_approval,
)

warnings.warn(
    "The 'agent_control' package is deprecated after the Argorix rebrand. "
    "Run 'pip install argorix-guardrails-agent' and replace 'import agent_control' with "
    "'import argorix_agents'. This shim will stop receiving updates.",
    DeprecationWarning,
    stacklevel=2,
)

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
    "state",
    "wait_for_approval",
]
