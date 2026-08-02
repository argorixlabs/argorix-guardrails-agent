"""Deprecated compatibility shim for ``agent_control.state``.

Re-exports the same ``state`` singleton used by ``argorix_agents`` so that mixing the
old and new import paths in one process stays consistent.
"""

from argorix_agents.state import AgentControlState, AgentRuntimeState, state  # noqa: F401

__all__ = ["AgentControlState", "AgentRuntimeState", "state"]
