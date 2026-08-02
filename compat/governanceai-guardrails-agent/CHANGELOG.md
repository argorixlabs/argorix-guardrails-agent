# Changelog

## 0.2.0 - 2026-08-01

- Deprecated in favour of `argorix-guardrails-agent` following the Governance AI →
  Argorix rebrand.
- Replaced the implementation with a thin shim that depends on
  `argorix-guardrails-agent>=0.2.0` and re-exports it, so `import agent_control` keeps
  working unchanged.
- The shim re-exports the same `state` singleton, so mixing `agent_control` and
  `argorix_agents` imports in one process stays consistent.
- Importing the package now emits a `DeprecationWarning` with migration instructions.

## 0.1.0 - 2026-03-22

- Created the standalone PyPI package `governanceai-guardrails-agent`.
- Added `AgentControlClient` retry and timeout controls.
- Added structured `AgentControlError` with HTTP status and response body details.
- Added package-specific README, license, tests, and packaging metadata.
