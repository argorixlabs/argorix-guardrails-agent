from __future__ import annotations

from collections.abc import Iterator
from typing import Any
from urllib import parse

from argorix.transport import (
    DEFAULT_RETRY_STATUS_CODES,
    ArgorixError,
    HttpTransport,
    StreamEvent,
)

from .models import AgentEvaluation, AgentRegistration, ControlMatch
from .version import SDK_VERSION

API_PREFIX = "/v1/agent-guardrails/runtime"

#: Raised when the Argorix control plane rejects or fails an agent guardrails request.
ArgorixAgentError = ArgorixError
#: Pre-0.2 name for :class:`ArgorixAgentError`.
AgentControlError = ArgorixError


class AgentGuardrailsClient:
    """Client for the Argorix agent guardrails runtime.

    Covers agent registration, control resolution, step evaluation (buffered and
    streamed) and runtime event recording.
    """

    def __init__(
        self,
        base_url: str,
        app_number: int,
        app_api_key: str,
        timeout_seconds: float = 10.0,
        max_retries: int = 2,
        retry_backoff_seconds: float = 0.5,
        retry_status_codes: set[int] | None = None,
    ) -> None:
        self._transport = HttpTransport(
            base_url=base_url,
            app_api_key=app_api_key,
            user_agent=f"argorix-agents-python/{SDK_VERSION}",
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
            retry_backoff_seconds=retry_backoff_seconds,
            retry_status_codes=retry_status_codes,
        )
        self.app_number = int(app_number)

    @property
    def base_url(self) -> str:
        return self._transport.base_url

    @property
    def app_api_key(self) -> str:
        return self._transport.app_api_key

    @property
    def timeout_seconds(self) -> float:
        return self._transport.timeout_seconds

    @property
    def max_retries(self) -> int:
        return self._transport.max_retries

    @property
    def retry_backoff_seconds(self) -> float:
        return self._transport.retry_backoff_seconds

    @property
    def retry_status_codes(self) -> set[int]:
        return self._transport.retry_status_codes

    # -- registration -------------------------------------------------------------

    def init_agent(
        self,
        *,
        agent_name: str,
        agent_description: str | None = None,
        agent_version: str | None = None,
        agent_metadata: dict[str, Any] | None = None,
        steps: list[dict[str, Any]] | None = None,
        evaluators: list[dict[str, Any]] | None = None,
    ) -> AgentRegistration:
        """Register or refresh a runtime-visible agent definition."""
        payload = self._transport.request_json(
            "POST",
            f"{API_PREFIX}/agents/init",
            {
                "app_number": self.app_number,
                "agent_name": agent_name,
                "agent_description": agent_description,
                "agent_version": agent_version,
                "agent_metadata": agent_metadata or {},
                "steps": steps or [],
                "evaluators": evaluators or [],
            },
        )
        return AgentRegistration.from_payload(payload)

    def list_agent_controls(
        self,
        *,
        agent_name: str,
        policy_id: str | None = None,
    ) -> dict[str, Any]:
        """List the agent guardrail controls bound to ``agent_name``."""
        query = parse.urlencode(
            {
                "app_number": self.app_number,
                **({"policy_id": policy_id} if policy_id else {}),
            }
        )
        return self._transport.request_json(
            "GET",
            f"{API_PREFIX}/agents/{parse.quote(agent_name)}/controls?{query}",
            None,
        )

    # -- evaluation ---------------------------------------------------------------

    def _evaluate_payload(
        self,
        *,
        agent_name: str,
        stage: str,
        step: dict[str, Any],
        policy_id: str | None,
        control_ids: list[str] | None,
        trace_id: str | None,
        span_id: str | None,
        metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        return {
            "app_number": self.app_number,
            "agent_name": agent_name,
            "policy_id": policy_id,
            "control_ids": control_ids or [],
            "stage": stage,
            "step": step,
            "trace_id": trace_id,
            "span_id": span_id,
            "metadata": metadata or {},
        }

    def evaluate(
        self,
        *,
        agent_name: str,
        stage: str,
        step: dict[str, Any],
        policy_id: str | None = None,
        control_ids: list[str] | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> AgentEvaluation:
        """Evaluate one agentic step against the bound agent guardrail controls."""
        payload = self._transport.request_json(
            "POST",
            f"{API_PREFIX}/evaluate",
            self._evaluate_payload(
                agent_name=agent_name,
                stage=stage,
                step=step,
                policy_id=policy_id,
                control_ids=control_ids,
                trace_id=trace_id,
                span_id=span_id,
                metadata=metadata,
            ),
        )
        return AgentEvaluation.from_payload(payload)

    def evaluate_stream(
        self,
        *,
        agent_name: str,
        stage: str,
        step: dict[str, Any],
        policy_id: str | None = None,
        control_ids: list[str] | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Iterator[StreamEvent]:
        """Stream a step evaluation over SSE (``start``, ``result``, ``end``, ``error``)."""
        return self._transport.stream_sse(
            f"{API_PREFIX}/evaluate/stream",
            self._evaluate_payload(
                agent_name=agent_name,
                stage=stage,
                step=step,
                policy_id=policy_id,
                control_ids=control_ids,
                trace_id=trace_id,
                span_id=span_id,
                metadata=metadata,
            ),
        )

    def evaluate_streamed_result(self, **kwargs: Any) -> AgentEvaluation:
        """Consume :meth:`evaluate_stream` and return the final evaluation.

        Raises :class:`ArgorixAgentError` if the server emits an ``error`` event or
        closes the stream without a ``result``.
        """
        for event in self.evaluate_stream(**kwargs):
            if event.event == "error":
                raise ArgorixAgentError(
                    str(event.data.get("detail") or "Agent guardrails stream failed"),
                    status_code=(
                        int(event.data["code"])
                        if str(event.data.get("code", "")).isdigit()
                        else None
                    ),
                )
            if event.event == "result":
                return AgentEvaluation.from_payload(event.data)
        raise ArgorixAgentError(
            "Agent guardrails stream ended before emitting a result event."
        )

    # -- events -------------------------------------------------------------------

    def record_event(
        self,
        *,
        agent_name: str,
        event_type: str,
        step_type: str | None = None,
        step_name: str | None = None,
        stage: str | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
        decision: str | None = None,
        allowed: bool | None = None,
        duration_ms: float | None = None,
        matches_total: int = 0,
        errors_total: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record an agent runtime event keyed by trace and span identifiers."""
        return self._transport.request_json(
            "POST",
            f"{API_PREFIX}/events",
            {
                "app_number": self.app_number,
                "agent_name": agent_name,
                "event_type": event_type,
                "step_type": step_type,
                "step_name": step_name,
                "stage": stage,
                "trace_id": trace_id,
                "span_id": span_id,
                "decision": decision,
                "allowed": allowed,
                "duration_ms": duration_ms,
                "matches_total": matches_total,
                "errors_total": errors_total,
                "metadata": metadata or {},
            },
        )


#: Pre-0.2 name for :class:`AgentGuardrailsClient`.
AgentControlClient = AgentGuardrailsClient


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
