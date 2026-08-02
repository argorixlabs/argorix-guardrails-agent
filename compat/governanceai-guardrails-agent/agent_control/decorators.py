"""Deprecated compatibility shim for ``agent_control.decorators``."""

from argorix_agents.decorators import (  # noqa: F401
    ControlSteerError,
    ControlViolationError,
    control,
)

__all__ = ["ControlSteerError", "ControlViolationError", "control"]
