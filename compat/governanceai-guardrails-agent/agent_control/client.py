"""Deprecated compatibility shim for ``agent_control.client``. Use ``argorix_agents.client``."""

from argorix_agents.client import (  # noqa: F401
    API_PREFIX,
    DEFAULT_RETRY_STATUS_CODES,
    AgentControlClient,
    AgentControlError,
    AgentEvaluation,
    AgentGuardrailsClient,
    AgentRegistration,
    ArgorixAgentError,
    ArgorixError,
    ControlMatch,
    SDK_VERSION,
    StreamEvent,
)

__all__ = [
    "API_PREFIX",
    "DEFAULT_RETRY_STATUS_CODES",
    "AgentControlClient",
    "AgentControlError",
    "AgentEvaluation",
    "AgentGuardrailsClient",
    "AgentRegistration",
    "ArgorixAgentError",
    "ArgorixError",
    "ControlMatch",
    "SDK_VERSION",
    "StreamEvent",
]
