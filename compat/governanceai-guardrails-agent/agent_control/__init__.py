"""Deprecated compatibility shim. Install and import ``argorix_agents`` instead.

``agent_control`` is the pre-rebrand module of the Argorix Agent Guardrails SDK. This
distribution contains no implementation of its own: it depends on
``argorix-guardrails-agent`` and re-exports it, including the shared runtime ``state``,
so ``agent_control.init()`` and ``@agent_control.control()`` keep working together.
"""

import warnings

from argorix_agents import (  # noqa: F401
    API_PREFIX,
    DEFAULT_BASE_URL,
    AgentControlClient,
    AgentControlError,
    AgentEvaluation,
    AgentGuardrailsClient,
    AgentRegistration,
    AgentRuntimeState,
    ArgorixAgentError,
    ArgorixError,
    ControlMatch,
    ControlSteerError,
    ControlViolationError,
    SDK_VERSION,
    StreamEvent,
    clear_registered_steps,
    control,
    current_agent,
    current_client,
    evaluate_step,
    init,
    list_agent_controls,
    list_registered_steps,
    reset,
    state,
)

warnings.warn(
    "The 'agent_control' package is deprecated after the Argorix rebrand. "
    "Run 'pip install argorix-guardrails-agent' and replace 'import agent_control' with "
    "'import argorix_agents'. This shim will stop receiving updates.",
    DeprecationWarning,
    stacklevel=2,
)

AgentControlState = AgentRuntimeState

__version__ = SDK_VERSION

__all__ = [
    "API_PREFIX",
    "DEFAULT_BASE_URL",
    "AgentControlClient",
    "AgentControlError",
    "AgentControlState",
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
