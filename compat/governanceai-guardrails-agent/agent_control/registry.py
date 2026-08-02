"""Deprecated compatibility shim for ``agent_control.registry``."""

from argorix_agents.registry import (  # noqa: F401
    clear_registered_steps,
    derive_step_schema,
    list_registered_steps,
    register_step,
)

__all__ = [
    "clear_registered_steps",
    "derive_step_schema",
    "list_registered_steps",
    "register_step",
]
