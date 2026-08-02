"""Deprecated compatibility shim for ``agent_control.state``.

Re-exports the same ``state`` singleton ``argorix_agents`` uses, so mixing the old and
new import paths in one process stays consistent.
"""

from argorix_agents.state import state  # noqa: F401

__all__ = ["state"]
