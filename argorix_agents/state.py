from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentRuntimeState:
    """Process-wide state populated by :func:`argorix_agents.init`."""

    base_url: str | None = None
    app_number: int | None = None
    app_api_key: str | None = None
    agent: dict[str, Any] | None = None
    init_response: dict[str, Any] | None = None
    registered_steps: list[dict[str, Any]] = field(default_factory=list)
    default_policy_id: str | None = None
    default_control_ids: list[str] = field(default_factory=list)
    default_metadata: dict[str, Any] = field(default_factory=dict)


#: Pre-0.2 name for :class:`AgentRuntimeState`.
AgentControlState = AgentRuntimeState

state = AgentRuntimeState()

__all__ = ["AgentControlState", "AgentRuntimeState", "state"]
