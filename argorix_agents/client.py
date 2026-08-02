from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from http.client import HTTPConnection, HTTPSConnection, HTTPResponse
import json
import time
from typing import Any
from urllib import parse

DEFAULT_RETRY_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})


class StreamEvent:
    """One Server-Sent Event from a streaming evaluation."""

    __slots__ = ("event", "data")

    def __init__(self, event: str, data: dict[str, Any]) -> None:
        self.event = event
        self.data = data

    def __repr__(self) -> str:
        return f"StreamEvent(event={self.event!r}, data={self.data!r})"


def _decode_sse_data(raw: str) -> dict[str, Any]:
    try:
        decoded = json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}
    return decoded if isinstance(decoded, dict) else {"raw": decoded}


def _iter_sse_events(response: HTTPResponse) -> Iterator[StreamEvent]:
    event_name = "message"
    data_lines: list[str] = []
    for raw_line in response:
        line = raw_line.decode("utf-8", errors="replace").rstrip("\r\n")
        if line == "":
            if data_lines:
                yield StreamEvent(event_name, _decode_sse_data("\n".join(data_lines)))
            event_name = "message"
            data_lines = []
            continue
        if line.startswith(":"):
            continue
        field, _, value = line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field == "event":
            event_name = value
        elif field == "data":
            data_lines.append(value)
    if data_lines:
        yield StreamEvent(event_name, _decode_sse_data("\n".join(data_lines)))


class AgentControlError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response_body: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response_body = response_body


@dataclass
class AgentControlClient:
    base_url: str
    app_number: int
    app_api_key: str
    timeout_seconds: float = 10.0
    max_retries: int = 2
    retry_backoff_seconds: float = 0.5
    retry_status_codes: set[int] | None = None

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        self.app_number = int(self.app_number)
        self.app_api_key = self.app_api_key.strip()
        self.max_retries = max(0, int(self.max_retries))
        self.retry_backoff_seconds = max(0.0, float(self.retry_backoff_seconds))
        self.retry_status_codes = set(self.retry_status_codes or DEFAULT_RETRY_STATUS_CODES)

    def init_agent(
        self,
        *,
        agent_name: str,
        agent_description: str | None = None,
        agent_version: str | None = None,
        agent_metadata: dict[str, Any] | None = None,
        steps: list[dict[str, Any]] | None = None,
        evaluators: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return self._request_json(
            "POST",
            "/v1/agent-guardrails/runtime/agents/init",
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

    def list_agent_controls(
        self,
        *,
        agent_name: str,
        policy_id: str | None = None,
    ) -> dict[str, Any]:
        query = parse.urlencode(
            {
                "app_number": self.app_number,
                **({"policy_id": policy_id} if policy_id else {}),
            }
        )
        return self._request_json(
            "GET",
            f"/v1/agent-guardrails/runtime/agents/{parse.quote(agent_name)}/controls?{query}",
            None,
        )

    def evaluate(
        self,
        *,
        agent_name: str,
        stage: str,
        step: dict[str, Any],
        intervention_point: str | None = None,
        policy_id: str | None = None,
        control_ids: list[str] | None = None,
        session_id: str | None = None,
        token_count_delta: int = 0,
        tool_call_depth: int | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Evaluate one step against the controls bound to this agent.

        Pass `session_id` to make budget controls work. They compare against
        totals ARGORIX keeps per session -- tokens spent, tool calls made, how
        deep a tool chain went -- which no single step can show. Report
        `token_count_delta` for this step only; the running total is the
        server's. Without a `session_id` those controls are skipped.
        """
        body: dict[str, Any] = {
            "app_number": self.app_number,
            "agent_name": agent_name,
            "policy_id": policy_id,
            "control_ids": control_ids or [],
            "stage": stage,
            "intervention_point": intervention_point,
            "step": step,
            "trace_id": trace_id,
            "span_id": span_id,
            "metadata": metadata or {},
        }
        if session_id:
            body["session"] = {
                "session_id": session_id,
                "token_count_delta": token_count_delta,
                "tool_call_depth": tool_call_depth,
            }
        return self._request_json(
            "POST",
            "/v1/agent-guardrails/runtime/evaluate",
            body,
        )

    def evaluate_stream(
        self,
        *,
        agent_name: str,
        stage: str,
        step: dict[str, Any],
        intervention_point: str | None = None,
        policy_id: str | None = None,
        control_ids: list[str] | None = None,
        session_id: str | None = None,
        token_count_delta: int = 0,
        tool_call_depth: int | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Iterator[StreamEvent]:
        """Stream one evaluation over SSE, yielding `start`, `result`, `end` or `error`.

        Same request as `evaluate()`. Worth it when a slow evaluation sits in front of a
        user and the wait needs to show progress; the buffered `evaluate()` is the right
        default everywhere else.

        The iterator is lazy -- nothing is sent until it is consumed. Retries cover the
        connection and the response status only: once the stream is open the server has
        already begun emitting, so replaying it would double the work.
        """
        body: dict[str, Any] = {
            "app_number": self.app_number,
            "agent_name": agent_name,
            "policy_id": policy_id,
            "control_ids": control_ids or [],
            "stage": stage,
            "intervention_point": intervention_point,
            "step": step,
            "trace_id": trace_id,
            "span_id": span_id,
            "metadata": metadata or {},
        }
        if session_id:
            body["session"] = {
                "session_id": session_id,
                "token_count_delta": token_count_delta,
                "tool_call_depth": tool_call_depth,
            }
        return self._stream_sse("/v1/agent-guardrails/runtime/evaluate/stream", body)

    def evaluate_streamed_result(self, **kwargs: Any) -> dict[str, Any]:
        """Consume `evaluate_stream()` and return the final evaluation payload.

        Raises `AgentControlError` when the server emits an `error` event or closes the
        stream without a `result`, so a failed stream cannot be mistaken for an allow.
        """
        for event in self.evaluate_stream(**kwargs):
            if event.event == "error":
                detail = str(event.data.get("detail") or "Agent guardrails stream failed")
                code = event.data.get("code")
                raise AgentControlError(
                    detail,
                    status_code=int(code) if str(code).strip().isdigit() else None,
                )
            if event.event == "result":
                return event.data
        raise AgentControlError(
            "Agent guardrails stream ended before emitting a result event."
        )

    def consume_receipt(self, *, receipt: str, step: dict[str, Any]) -> dict[str, Any]:
        """Redeem a receipt for one execution of exactly `step`.

        Call it immediately before running the step, with the payload actually
        about to run -- not the one that was evaluated, if those have diverged.
        Telling ARGORIX what it already authorized would answer a question
        nobody asked; the whole check is whether the two are still the same.

        Raises `AgentControlError` when the receipt is refused. A refusal is
        never a return value here, because a caller that forgets to inspect a
        return value proceeds, and a caller that forgets to catch does not.
        """
        return self._request_json(
            "POST",
            "/v1/agent-guardrails/runtime/receipts/consume",
            {
                "app_number": self.app_number,
                "receipt": receipt,
                "step": step,
            },
        )

    def get_approval(self, *, approval_id: str) -> dict[str, Any]:
        """Read the status of an approval opened by an `escalate` control."""
        query = parse.urlencode({"app_number": self.app_number})
        return self._request_json(
            "GET",
            f"/v1/agent-guardrails/runtime/approvals/{parse.quote(approval_id)}?{query}",
            None,
        )

    def record_event(
        self,
        *,
        agent_name: str,
        event_type: str,
        step_type: str | None = None,
        step_name: str | None = None,
        stage: str | None = None,
        intervention_point: str | None = None,
        trace_id: str | None = None,
        span_id: str | None = None,
        decision: str | None = None,
        allowed: bool | None = None,
        reason_code: str | None = None,
        fail_closed: bool = False,
        duration_ms: float | None = None,
        matches_total: int = 0,
        errors_total: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._request_json(
            "POST",
            "/v1/agent-guardrails/runtime/events",
            {
                "app_number": self.app_number,
                "agent_name": agent_name,
                "event_type": event_type,
                "step_type": step_type,
                "step_name": step_name,
                "stage": stage,
                "intervention_point": intervention_point,
                "trace_id": trace_id,
                "span_id": span_id,
                "decision": decision,
                "allowed": allowed,
                "reason_code": reason_code,
                "fail_closed": fail_closed,
                "duration_ms": duration_ms,
                "matches_total": matches_total,
                "errors_total": errors_total,
                "metadata": metadata or {},
            },
        )

    def _request_json(
        self,
        method: str,
        path: str,
        payload: dict[str, Any] | None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        parsed = parse.urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise AgentControlError(f"Unsupported endpoint URL: {url}")
        request_path = parsed.path or "/"
        if parsed.query:
            request_path = f"{request_path}?{parsed.query}"
        last_error: AgentControlError | None = None
        for attempt in range(self.max_retries + 1):
            body = json.dumps(payload).encode("utf-8") if payload is not None else None
            headers = {
                "Authorization": f"Bearer {self.app_api_key}",
                "Content-Type": "application/json",
            }
            connection_cls = HTTPSConnection if parsed.scheme == "https" else HTTPConnection
            connection = connection_cls(parsed.hostname, parsed.port, timeout=self.timeout_seconds)
            try:
                connection.request(method, request_path, body=body, headers=headers)
                response: HTTPResponse = connection.getresponse()
                raw = response.read().decode("utf-8")
                if response.status >= 400:
                    error = AgentControlError(
                        f"HTTP {response.status} calling {url}: {raw}",
                        status_code=response.status,
                        response_body=raw,
                    )
                    if attempt < self.max_retries and response.status in self.retry_status_codes:
                        last_error = error
                        self._sleep_before_retry(attempt + 1)
                        continue
                    raise error
                return json.loads(raw) if raw else {}
            except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
                # A truncated, garbled or non-UTF-8 body is the same class of
                # failure as a dropped connection: a response we cannot act on.
                # Neither `JSONDecodeError` nor `UnicodeDecodeError` descends
                # from `OSError`, so before this they escaped the retry loop
                # unwrapped -- the caller got a parse error it had no way to
                # classify, and a response worth retrying was never retried.
                error = AgentControlError(f"Unable to call {url}: {exc}")
                if attempt < self.max_retries:
                    last_error = error
                    self._sleep_before_retry(attempt + 1)
                    continue
                raise error from exc
            finally:
                connection.close()
        if last_error is not None:
            raise last_error
        raise AgentControlError(f"Unexpected request failure calling {url}")

    def _stream_sse(self, path: str, payload: dict[str, Any]) -> Iterator[StreamEvent]:
        url = f"{self.base_url}{path}"
        parsed = parse.urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise AgentControlError(f"Unsupported endpoint URL: {url}")
        request_path = parsed.path or "/"
        if parsed.query:
            request_path = f"{request_path}?{parsed.query}"

        last_error: AgentControlError | None = None
        for attempt in range(self.max_retries + 1):
            connection_cls = HTTPSConnection if parsed.scheme == "https" else HTTPConnection
            connection = connection_cls(parsed.hostname, parsed.port, timeout=self.timeout_seconds)
            try:
                connection.request(
                    "POST",
                    request_path,
                    body=json.dumps(payload).encode("utf-8"),
                    headers={
                        "Authorization": f"Bearer {self.app_api_key}",
                        "Content-Type": "application/json",
                        "Accept": "text/event-stream",
                    },
                )
                response: HTTPResponse = connection.getresponse()
                if response.status >= 400:
                    raw = response.read().decode("utf-8", errors="replace")
                    error = AgentControlError(
                        f"HTTP {response.status} calling {url}: {raw}",
                        status_code=response.status,
                        response_body=raw,
                    )
                    if attempt < self.max_retries and response.status in self.retry_status_codes:
                        last_error = error
                        connection.close()
                        self._sleep_before_retry(attempt + 1)
                        continue
                    raise error
            except OSError as exc:
                connection.close()
                error = AgentControlError(f"Unable to call {url}: {exc}")
                if attempt < self.max_retries:
                    last_error = error
                    self._sleep_before_retry(attempt + 1)
                    continue
                raise error from exc
            except BaseException:
                connection.close()
                raise

            # Past the status line the stream is live, so it is consumed rather than
            # retried: the server has already started doing the work.
            try:
                yield from _iter_sse_events(response)
            finally:
                connection.close()
            return

        if last_error is not None:
            raise last_error
        raise AgentControlError(f"Unexpected request failure calling {url}")

    def _sleep_before_retry(self, attempt: int) -> None:
        if self.retry_backoff_seconds <= 0:
            return
        time.sleep(self.retry_backoff_seconds * (2 ** max(0, attempt - 1)))
