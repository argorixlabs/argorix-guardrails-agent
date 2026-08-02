# governanceai-guardrails-agent (deprecated)

Governance AI is now **Argorix**. This package has been renamed to
[`argorix-guardrails-agent`](https://pypi.org/project/argorix-guardrails-agent/), and the
`agent_control` module is now `argorix_agents`.

Version 0.2.0 contains no implementation of its own. It depends on
`argorix-guardrails-agent` and re-exports it — including the shared runtime `state`
singleton, so `agent_control.init()` and `@argorix_agents.control()` still cooperate in
the same process. Importing it emits a `DeprecationWarning`.

## Migrate

```bash
pip uninstall governanceai-guardrails-agent
pip install argorix-guardrails-agent
```

```diff
-import agent_control
-from agent_control import control, ControlViolationError
+import argorix_agents
+from argorix_agents import control, ControlViolationError

-agent_control.init(agent_name="support_bot", ...)
+argorix_agents.init(agent_name="support_bot", ...)
```

| Before | After |
| --- | --- |
| `AgentControlClient` | `AgentGuardrailsClient` |
| `AgentControlError` | `ArgorixAgentError` |
| `AGENT_CONTROL_URL` | `ARGORIX_API_URL` |
| `AGENT_CONTROL_APP_NUMBER` | `ARGORIX_APP_NUMBER` |
| `AGENT_CONTROL_APP_API_KEY` | `ARGORIX_APP_API_KEY` |

The old names remain available as aliases inside `argorix_agents`, and the legacy
environment variables are still read as fallbacks.

See the [`argorix-guardrails-agent` README](https://pypi.org/project/argorix-guardrails-agent/)
for the full API, including the streaming evaluation endpoint added in 0.2.0.
