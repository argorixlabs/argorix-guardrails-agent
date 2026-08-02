"""Deprecated compatibility shim for ``agent_control.decorators``."""

from argorix_agents.decorators import (  # noqa: F401
    ControlEscalationError,
    ControlSteerError,
    ControlViolationError,
    control,
)

__all__ = [
    "ControlEscalationError",
    "ControlSteerError",
    "ControlViolationError",
    "control",
]
