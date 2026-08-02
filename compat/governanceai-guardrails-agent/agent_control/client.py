"""Deprecated compatibility shim for ``agent_control.client``. Use ``argorix_agents.client``."""

from argorix_agents.client import (  # noqa: F401
    DEFAULT_RETRY_STATUS_CODES,
    AgentControlClient,
    AgentControlError,
    StreamEvent,
)

__all__ = [
    "DEFAULT_RETRY_STATUS_CODES",
    "AgentControlClient",
    "AgentControlError",
    "StreamEvent",
]
