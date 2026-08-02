from __future__ import annotations

import asyncio
import functools
import inspect
import os
import time
from typing import Any, Callable, TypeVar
from uuid import uuid4

from .client import AgentGuardrailsClient
from .models import AgentEvaluation
from .registry import register_step
from .state import state

F = TypeVar("F", bound=Callable[..., Any])

_INIT_REQUIRED = "argorix_agents.init() must be called before using @control()."


class ControlViolationError(Exception):
    def __init__(self, control_name: str, message: str) -> None:
        self.control_name = control_name
        self.message = message
        super().__init__(f"Control violation [{control_name}]: {message}")


class ControlSteerError(Exception):
    def __init__(self, control_name: str, message: str, steering_message: str | None) -> None:
        self.control_name = control_name
        self.message = message
        self.steering_message = steering_message
        super().__init__(
            f"Control steering [{control_name}]: {message}"
            + (f" | {steering_message}" if steering_message else "")
        )


def _current_client() -> AgentGuardrailsClient:
    if state.base_url is None or state.app_number is None or state.app_api_key is None:
        raise RuntimeError(_INIT_REQUIRED)
    return AgentGuardrailsClient(
        base_url=state.base_url,
        app_number=state.app_number,
        app_api_key=state.app_api_key,
    )


def _step_name(func: Callable[..., Any], override: str | None) -> str:
    return (override or getattr(func, "name", None) or getattr(func, "tool_name", None) or func.__name__).strip()


def _step_type(func: Callable[..., Any], override: str | None = None) -> str:
    normalized = (override or "").strip().lower()
    if normalized:
        return normalized
    return "tool" if getattr(func, "name", None) or getattr(func, "tool_name", None) else "llm"


def _extract_llm_input(bound_arguments: inspect.BoundArguments) -> str:
    for preferred in ("input", "message", "query", "text", "prompt", "content", "user_input"):
        if preferred in bound_arguments.arguments:
            value = bound_arguments.arguments[preferred]
            return "" if value is None else str(value)
    for value in bound_arguments.arguments.values():
        if isinstance(value, str):
            return value
    return str(dict(bound_arguments.arguments))


def _extract_context(bound_arguments: inspect.BoundArguments) -> dict[str, Any] | None:
    value = bound_arguments.arguments.get("context")
    if isinstance(value, dict):
        return dict(value)
    return None


def _create_step_payload(
    func: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    *,
    output: Any = None,
    step_name_override: str | None = None,
    step_type_override: str | None = None,
) -> dict[str, Any]:
    signature = inspect.signature(func)
    bound = signature.bind(*args, **kwargs)
    bound.apply_defaults()
    step_type = _step_type(func, step_type_override)
    resolved_name = _step_name(func, step_name_override)
    context = _extract_context(bound)
    if step_type == "tool":
        tool_input = dict(bound.arguments)
        tool_input.pop("context", None)
        return {
            "type": step_type,
            "name": resolved_name,
            "input": tool_input,
            "output": output,
            "context": context,
        }
    return {
        "type": step_type,
        "name": resolved_name,
        "input": _extract_llm_input(bound),
        "output": output,
        "context": context,
    }


def _handle_result(result: AgentEvaluation) -> None:
    for error in result.errors:
        raise RuntimeError(error.message or error.error or "Control evaluation error")

    for match in result.matches:
        if match.action == "deny":
            raise ControlViolationError(
                control_name=match.control_name or match.control_id or "unknown",
                message=match.message or "Control matched",
            )
        if match.action == "steer":
            raise ControlSteerError(
                control_name=match.control_name or match.control_id or "unknown",
                message=match.message or "Control matched",
                steering_message=match.steering_message,
            )


def _record_step_summary(
    client: AgentGuardrailsClient,
    *,
    agent_name: str,
    event_type: str,
    step_type: str,
    step_name: str,
    duration_ms: float,
    stage: str,
    result: AgentEvaluation | None,
    trace_id: str,
    span_id: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    client.record_event(
        agent_name=agent_name,
        event_type=event_type,
        step_type=step_type,
        step_name=step_name,
        stage=stage,
        trace_id=trace_id,
        span_id=span_id,
        decision=result.overall_decision if result is not None else None,
        allowed=result.allowed if result is not None else None,
        duration_ms=duration_ms,
        matches_total=len(result.matches) if result is not None else 0,
        errors_total=len(result.errors) if result is not None else 0,
        metadata=metadata or {},
    )


async def _evaluate_async(client: AgentGuardrailsClient, **kwargs: Any) -> AgentEvaluation:
    return await asyncio.to_thread(client.evaluate, **kwargs)


async def _record_event_async(client: AgentGuardrailsClient, **kwargs: Any) -> dict[str, Any]:
    return await asyncio.to_thread(client.record_event, **kwargs)


def _safe_record_step_summary(
    client: AgentGuardrailsClient,
    *,
    agent_name: str,
    event_type: str,
    step_type: str,
    step_name: str,
    duration_ms: float,
    stage: str,
    result: AgentEvaluation | None,
    trace_id: str,
    span_id: str,
    metadata: dict[str, Any],
) -> None:
    try:
        _record_step_summary(
            client,
            agent_name=agent_name,
            event_type=event_type,
            step_type=step_type,
            step_name=step_name,
            duration_ms=duration_ms,
            stage=stage,
            result=result,
            trace_id=trace_id,
            span_id=span_id,
            metadata=metadata,
        )
    except Exception:
        return


async def _safe_record_step_summary_async(
    client: AgentGuardrailsClient,
    *,
    agent_name: str,
    event_type: str,
    step_type: str,
    step_name: str,
    duration_ms: float,
    stage: str,
    result: AgentEvaluation | None,
    trace_id: str,
    span_id: str,
    metadata: dict[str, Any],
) -> None:
    try:
        await _record_event_async(
            client,
            agent_name=agent_name,
            event_type=event_type,
            step_type=step_type,
            step_name=step_name,
            stage=stage,
            trace_id=trace_id,
            span_id=span_id,
            decision=result.overall_decision if result is not None else None,
            allowed=result.allowed if result is not None else None,
            duration_ms=duration_ms,
            matches_total=len(result.matches) if result is not None else 0,
            errors_total=len(result.errors) if result is not None else 0,
            metadata=metadata,
        )
    except Exception:
        return


def _merge_metadata(
    *payloads: dict[str, Any] | None,
) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for payload in payloads:
        if not payload:
            continue
        merged.update(payload)
    return merged


def control(
    name: str | None = None,
    *,
    step_name: str | None = None,
    policy: str | None = None,
    policy_id: str | None = None,
    step_type: str | None = None,
    control_ids: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> Callable[[F], F]:
    """Evaluate the decorated agent step before and after it runs.

    A control returning ``deny`` raises :class:`ControlViolationError`; ``steer`` raises
    :class:`ControlSteerError`. Both sync and async callables are supported.
    """
    resolved_step_name_override = step_name or name
    resolved_policy_id = (policy_id or policy or "").strip() or None
    resolved_control_ids = [str(item).strip() for item in (control_ids or []) if str(item).strip()]
    resolved_metadata = dict(metadata or {})

    def decorator(func: F) -> F:
        register_step(
            func,
            step_name=resolved_step_name_override,
            step_type=step_type,
        )

        @functools.wraps(func)
        async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
            if state.agent is None:
                raise RuntimeError(_INIT_REQUIRED)
            client = _current_client()
            agent_name = str(state.agent["agent_name"])
            resolved_step_name = _step_name(func, resolved_step_name_override)
            resolved_step_type = _step_type(func, step_type)
            resolved_policy = resolved_policy_id or state.default_policy_id
            resolved_controls = resolved_control_ids or list(state.default_control_ids)
            trace_id = uuid4().hex
            span_id = uuid4().hex[:16]
            started = time.perf_counter()
            pre_result: AgentEvaluation | None = None
            post_result: AgentEvaluation | None = None
            event_type = "step_execution"
            failure: Exception | None = None
            runtime_metadata = _merge_metadata(
                state.default_metadata,
                resolved_metadata,
                {"runtime": "python", "pid": os.getpid()},
            )

            try:
                pre_payload = _create_step_payload(
                    func,
                    args,
                    kwargs,
                    output=None,
                    step_name_override=resolved_step_name_override,
                    step_type_override=step_type,
                )
                pre_result = await _evaluate_async(
                    client,
                    agent_name=agent_name,
                    policy_id=resolved_policy,
                    control_ids=resolved_controls,
                    stage="pre",
                    step=pre_payload,
                    trace_id=trace_id,
                    span_id=span_id,
                    metadata=_merge_metadata(runtime_metadata, {"evaluation_stage": "pre"}),
                )
                _handle_result(pre_result)

                output = await func(*args, **kwargs)

                post_payload = _create_step_payload(
                    func,
                    args,
                    kwargs,
                    output=output,
                    step_name_override=resolved_step_name_override,
                    step_type_override=step_type,
                )
                post_result = await _evaluate_async(
                    client,
                    agent_name=agent_name,
                    policy_id=resolved_policy,
                    control_ids=resolved_controls,
                    stage="post",
                    step=post_payload,
                    trace_id=trace_id,
                    span_id=span_id,
                    metadata=_merge_metadata(runtime_metadata, {"evaluation_stage": "post"}),
                )
                _handle_result(post_result)
                return output
            except ControlViolationError as exc:
                failure = exc
                event_type = "step_blocked"
                raise
            except ControlSteerError as exc:
                failure = exc
                event_type = "step_steered"
                raise
            except Exception as exc:
                failure = exc
                event_type = "step_error"
                raise
            finally:
                duration_ms = (time.perf_counter() - started) * 1000.0
                summary_result = post_result or pre_result
                await _safe_record_step_summary_async(
                    client,
                    agent_name=agent_name,
                    event_type=event_type,
                    step_type=resolved_step_type,
                    step_name=resolved_step_name,
                    duration_ms=duration_ms,
                    stage="span",
                    result=summary_result,
                    trace_id=trace_id,
                    span_id=span_id,
                    metadata=_merge_metadata(
                        runtime_metadata,
                        {
                            "event_type": event_type,
                            "policy_id": resolved_policy,
                            "control_ids": resolved_controls,
                            "error": str(failure) if failure else None,
                        },
                    ),
                )

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            if state.agent is None:
                raise RuntimeError(_INIT_REQUIRED)
            client = _current_client()
            agent_name = str(state.agent["agent_name"])
            resolved_step_name = _step_name(func, resolved_step_name_override)
            resolved_step_type = _step_type(func, step_type)
            resolved_policy = resolved_policy_id or state.default_policy_id
            resolved_controls = resolved_control_ids or list(state.default_control_ids)
            trace_id = uuid4().hex
            span_id = uuid4().hex[:16]
            started = time.perf_counter()
            pre_result: AgentEvaluation | None = None
            post_result: AgentEvaluation | None = None
            event_type = "step_execution"
            failure: Exception | None = None
            runtime_metadata = _merge_metadata(
                state.default_metadata,
                resolved_metadata,
                {"runtime": "python", "pid": os.getpid()},
            )

            try:
                pre_payload = _create_step_payload(
                    func,
                    args,
                    kwargs,
                    output=None,
                    step_name_override=resolved_step_name_override,
                    step_type_override=step_type,
                )
                pre_result = client.evaluate(
                    agent_name=agent_name,
                    policy_id=resolved_policy,
                    control_ids=resolved_controls,
                    stage="pre",
                    step=pre_payload,
                    trace_id=trace_id,
                    span_id=span_id,
                    metadata=_merge_metadata(runtime_metadata, {"evaluation_stage": "pre"}),
                )
                _handle_result(pre_result)

                output = func(*args, **kwargs)

                post_payload = _create_step_payload(
                    func,
                    args,
                    kwargs,
                    output=output,
                    step_name_override=resolved_step_name_override,
                    step_type_override=step_type,
                )
                post_result = client.evaluate(
                    agent_name=agent_name,
                    policy_id=resolved_policy,
                    control_ids=resolved_controls,
                    stage="post",
                    step=post_payload,
                    trace_id=trace_id,
                    span_id=span_id,
                    metadata=_merge_metadata(runtime_metadata, {"evaluation_stage": "post"}),
                )
                _handle_result(post_result)
                return output
            except ControlViolationError as exc:
                failure = exc
                event_type = "step_blocked"
                raise
            except ControlSteerError as exc:
                failure = exc
                event_type = "step_steered"
                raise
            except Exception as exc:
                failure = exc
                event_type = "step_error"
                raise
            finally:
                duration_ms = (time.perf_counter() - started) * 1000.0
                summary_result = post_result or pre_result
                _safe_record_step_summary(
                    client,
                    agent_name=agent_name,
                    event_type=event_type,
                    step_type=resolved_step_type,
                    step_name=resolved_step_name,
                    duration_ms=duration_ms,
                    stage="span",
                    result=summary_result,
                    trace_id=trace_id,
                    span_id=span_id,
                    metadata=_merge_metadata(
                        runtime_metadata,
                        {
                            "event_type": event_type,
                            "policy_id": resolved_policy,
                            "control_ids": resolved_controls,
                            "error": str(failure) if failure else None,
                        },
                    ),
                )

        return async_wrapper if inspect.iscoroutinefunction(func) else sync_wrapper  # type: ignore[return-value]

    return decorator
