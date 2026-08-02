from __future__ import annotations

import asyncio
import functools
import inspect
import os
import time
from typing import Any, Callable, TypeVar
from uuid import uuid4

from .client import AgentControlClient, AgentControlError
from .registry import register_step
from .state import state

F = TypeVar("F", bound=Callable[..., Any])


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


class ControlEscalationError(Exception):
    """The step needs a human verdict before it can run.

    ARGORIX opened an approval request; `approval_id` polls its status through
    `argorix_agents.get_approval()`.
    """

    def __init__(
        self,
        control_name: str,
        message: str,
        approval_id: str | None = None,
        expires_at: str | None = None,
    ) -> None:
        self.control_name = control_name
        self.message = message
        self.approval_id = approval_id
        self.expires_at = expires_at
        super().__init__(
            f"Control escalation [{control_name}]: {message}"
            + (f" | approval {approval_id}" if approval_id else "")
        )


def _current_client() -> AgentControlClient:
    if state.base_url is None or state.app_number is None or state.app_api_key is None:
        raise RuntimeError("argorix_agents.init() must be called before using @control().")
    return AgentControlClient(
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


def _intervention_point(stage: str, step_type: str) -> str:
    is_tool = str(step_type or "").strip().lower() == "tool"
    if stage == "pre":
        return "pre_tool_call" if is_tool else "pre_model_call"
    return "post_tool_call" if is_tool else "post_model_call"


def _llm_input_arg_name(bound_arguments: inspect.BoundArguments) -> str | None:
    """Which argument `_extract_llm_input` read, so a transform can rewrite it."""
    for preferred in ("input", "message", "query", "text", "prompt", "content", "user_input"):
        if preferred in bound_arguments.arguments:
            return preferred
    for name, value in bound_arguments.arguments.items():
        if isinstance(value, str):
            return name
    return None


def _rebind_transformed_input(
    func: Callable[..., Any],
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    transformed_step: dict[str, Any],
    step_type: str,
) -> tuple[tuple[Any, ...], dict[str, Any]] | None:
    """Rebuild the call arguments from a server-side input transform.

    Returns None when the signature cannot be rebound safely (``*args`` /
    ``**kwargs`` / positional-only parameters), so the caller can report that the
    transform was not applied instead of silently running with the raw value.
    """
    signature = inspect.signature(func)
    if any(
        parameter.kind
        in {
            inspect.Parameter.VAR_POSITIONAL,
            inspect.Parameter.VAR_KEYWORD,
            inspect.Parameter.POSITIONAL_ONLY,
        }
        for parameter in signature.parameters.values()
    ):
        return None

    bound = signature.bind(*args, **kwargs)
    bound.apply_defaults()
    arguments = dict(bound.arguments)
    transformed_input = transformed_step.get("input")

    if step_type == "tool":
        if not isinstance(transformed_input, dict):
            return None
        unknown = set(transformed_input) - set(arguments)
        if unknown:
            return None
        arguments.update(transformed_input)
    else:
        target = _llm_input_arg_name(bound)
        if target is None or not isinstance(transformed_input, str):
            return None
        arguments[target] = transformed_input

    return (), arguments


def _transformed_output(result: dict[str, Any], output: Any) -> tuple[Any, bool]:
    transformed_step = result.get("transformed_step")
    if not isinstance(transformed_step, dict):
        return output, False
    if "output" not in transformed_step:
        return output, False
    new_output = transformed_step["output"]
    if new_output == output:
        return output, False
    return new_output, True


def _handle_result(result: dict[str, Any]) -> None:
    for error in result.get("errors") or []:
        # A control that opted into fail_open reports the failure without
        # blocking; anything the server resolved to `deny` stops the step.
        if str(error.get("action") or "deny") != "deny":
            continue
        message = str(error.get("message") or error.get("error") or "Control evaluation error")
        raise RuntimeError(message)

    matches = result.get("matches") or []

    # Same precedence the server applies: deny, then escalate, then steer.
    for action_to_raise in ("deny", "escalate", "steer"):
        for match in matches:
            action = str(match.get("action") or "allow")
            if action != action_to_raise:
                continue
            control_name = str(match.get("control_name") or match.get("control_id") or "unknown")
            message = str(match.get("message") or "Control matched")
            metadata = match.get("metadata") or {}
            steering_message = (
                match.get("steering_message")
                or (metadata.get("steering_message") if isinstance(metadata, dict) else None)
            )
            if action == "deny":
                raise ControlViolationError(control_name=control_name, message=message)
            if action == "escalate":
                raise ControlEscalationError(
                    control_name=control_name,
                    message=message,
                    approval_id=result.get("approval_id"),
                    expires_at=result.get("approval_expires_at"),
                )
            raise ControlSteerError(
                control_name=control_name,
                message=message,
                steering_message=str(steering_message) if steering_message else None,
            )


def _redeem_receipt(
    client: AgentControlClient,
    *,
    result: dict[str, Any],
    step: dict[str, Any],
) -> None:
    """Spend the evaluation's receipt on the step that is about to run.

    Called after the verdict and after any transform rebind, so `step` is what
    the function will actually receive -- redeeming the step we asked about
    instead would check nothing.

    Two failure modes, both of which must stop the call:

    * The server refuses the receipt. The payload changed, it was already
      spent, or it expired. Refusing is the point.
    * The evaluation came back without one. Either the backend predates
      receipts or something dropped it. Proceeding would mean an agent that
      believes it is enforcing while quietly not, which is worse than an
      agent that never turned it on.
    """
    receipt = result.get("receipt")
    if not receipt:
        raise AgentControlError(
            "Receipt redemption is enabled but the evaluation returned no receipt. "
            "The ARGORIX deployment may predate signed receipts; either upgrade it "
            "or call init(redeem_receipts=False) deliberately."
        )
    client.consume_receipt(receipt=str(receipt), step=step)


def _record_step_summary(
    client: AgentControlClient,
    *,
    agent_name: str,
    event_type: str,
    step_type: str,
    step_name: str,
    duration_ms: float,
    stage: str,
    result: dict[str, Any] | None,
    trace_id: str,
    span_id: str,
    metadata: dict[str, Any] | None = None,
) -> None:
    if result is None:
        decision = None
        allowed = None
        matches_total = 0
        errors_total = 0
    else:
        decision = result.get("overall_decision")
        allowed = result.get("allowed")
        matches_total = len(result.get("matches") or [])
        errors_total = len(result.get("errors") or [])
    client.record_event(
        agent_name=agent_name,
        event_type=event_type,
        step_type=step_type,
        step_name=step_name,
        stage=stage,
        trace_id=trace_id,
        span_id=span_id,
        decision=str(decision) if decision is not None else None,
        allowed=bool(allowed) if allowed is not None else None,
        duration_ms=duration_ms,
        matches_total=matches_total,
        errors_total=errors_total,
        metadata=metadata or {},
    )


async def _evaluate_async(client: AgentControlClient, **kwargs: Any) -> dict[str, Any]:
    return await asyncio.to_thread(client.evaluate, **kwargs)


async def _record_event_async(client: AgentControlClient, **kwargs: Any) -> dict[str, Any]:
    return await asyncio.to_thread(client.record_event, **kwargs)


def _safe_record_step_summary(
    client: AgentControlClient,
    *,
    agent_name: str,
    event_type: str,
    step_type: str,
    step_name: str,
    duration_ms: float,
    stage: str,
    result: dict[str, Any] | None,
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
    client: AgentControlClient,
    *,
    agent_name: str,
    event_type: str,
    step_type: str,
    step_name: str,
    duration_ms: float,
    stage: str,
    result: dict[str, Any] | None,
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
            decision=str(result.get("overall_decision")) if result and result.get("overall_decision") is not None else None,
            allowed=bool(result.get("allowed")) if result and result.get("allowed") is not None else None,
            duration_ms=duration_ms,
            matches_total=len(result.get("matches") or []) if result else 0,
            errors_total=len(result.get("errors") or []) if result else 0,
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
                raise RuntimeError("argorix_agents.init() must be called before using @control().")
            client = _current_client()
            agent_name = str(state.agent["agent_name"])
            resolved_step_name = _step_name(func, resolved_step_name_override)
            resolved_step_type = _step_type(func, step_type)
            resolved_policy = resolved_policy_id or state.default_policy_id
            resolved_controls = resolved_control_ids or list(state.default_control_ids)
            trace_id = uuid4().hex
            span_id = uuid4().hex[:16]
            started = time.perf_counter()
            pre_result: dict[str, Any] | None = None
            post_result: dict[str, Any] | None = None
            event_type = "step_execution"
            failure: Exception | None = None
            transform_applied = False
            transform_skipped = False
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
                    intervention_point=_intervention_point("pre", resolved_step_type),
                    step=pre_payload,
                    trace_id=trace_id,
                    span_id=span_id,
                    metadata=_merge_metadata(runtime_metadata, {"evaluation_stage": "pre"}),
                )
                _handle_result(pre_result)

                call_args, call_kwargs = args, kwargs
                if pre_result.get("transformed_step"):
                    rebound = _rebind_transformed_input(
                        func, args, kwargs, pre_result["transformed_step"], resolved_step_type
                    )
                    if rebound is not None:
                        call_args, call_kwargs = rebound
                        transform_applied = True
                    else:
                        transform_skipped = True

                if state.redeem_receipts:
                    _redeem_receipt(
                        client,
                        result=pre_result,
                        step=_create_step_payload(
                            func,
                            call_args,
                            call_kwargs,
                            output=None,
                            step_name_override=resolved_step_name_override,
                            step_type_override=step_type,
                        ),
                    )

                output = await func(*call_args, **call_kwargs)

                post_payload = _create_step_payload(
                    func,
                    call_args,
                    call_kwargs,
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
                    intervention_point=_intervention_point("post", resolved_step_type),
                    step=post_payload,
                    trace_id=trace_id,
                    span_id=span_id,
                    metadata=_merge_metadata(runtime_metadata, {"evaluation_stage": "post"}),
                )
                _handle_result(post_result)
                output, output_transformed = _transformed_output(post_result, output)
                transform_applied = transform_applied or output_transformed
                return output
            except ControlViolationError as exc:
                failure = exc
                event_type = "step_blocked"
                raise
            except ControlEscalationError as exc:
                failure = exc
                event_type = "step_escalated"
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
                            "transform_applied": transform_applied,
                            # True when the server asked for an input transform
                            # this signature could not be rebound to safely.
                            "transform_skipped": transform_skipped,
                        },
                    ),
                )

        @functools.wraps(func)
        def sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            if state.agent is None:
                raise RuntimeError("argorix_agents.init() must be called before using @control().")
            client = _current_client()
            agent_name = str(state.agent["agent_name"])
            resolved_step_name = _step_name(func, resolved_step_name_override)
            resolved_step_type = _step_type(func, step_type)
            resolved_policy = resolved_policy_id or state.default_policy_id
            resolved_controls = resolved_control_ids or list(state.default_control_ids)
            trace_id = uuid4().hex
            span_id = uuid4().hex[:16]
            started = time.perf_counter()
            pre_result: dict[str, Any] | None = None
            post_result: dict[str, Any] | None = None
            event_type = "step_execution"
            failure: Exception | None = None
            transform_applied = False
            transform_skipped = False
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
                    intervention_point=_intervention_point("pre", resolved_step_type),
                    step=pre_payload,
                    trace_id=trace_id,
                    span_id=span_id,
                    metadata=_merge_metadata(runtime_metadata, {"evaluation_stage": "pre"}),
                )
                _handle_result(pre_result)

                call_args, call_kwargs = args, kwargs
                if pre_result.get("transformed_step"):
                    rebound = _rebind_transformed_input(
                        func, args, kwargs, pre_result["transformed_step"], resolved_step_type
                    )
                    if rebound is not None:
                        call_args, call_kwargs = rebound
                        transform_applied = True
                    else:
                        transform_skipped = True

                if state.redeem_receipts:
                    _redeem_receipt(
                        client,
                        result=pre_result,
                        step=_create_step_payload(
                            func,
                            call_args,
                            call_kwargs,
                            output=None,
                            step_name_override=resolved_step_name_override,
                            step_type_override=step_type,
                        ),
                    )

                output = func(*call_args, **call_kwargs)

                post_payload = _create_step_payload(
                    func,
                    call_args,
                    call_kwargs,
                    output=output,
                    step_name_override=resolved_step_name_override,
                    step_type_override=step_type,
                )
                post_result = client.evaluate(
                    agent_name=agent_name,
                    policy_id=resolved_policy,
                    control_ids=resolved_controls,
                    stage="post",
                    intervention_point=_intervention_point("post", resolved_step_type),
                    step=post_payload,
                    trace_id=trace_id,
                    span_id=span_id,
                    metadata=_merge_metadata(runtime_metadata, {"evaluation_stage": "post"}),
                )
                _handle_result(post_result)
                output, output_transformed = _transformed_output(post_result, output)
                transform_applied = transform_applied or output_transformed
                return output
            except ControlViolationError as exc:
                failure = exc
                event_type = "step_blocked"
                raise
            except ControlEscalationError as exc:
                failure = exc
                event_type = "step_escalated"
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
                            "transform_applied": transform_applied,
                            # True when the server asked for an input transform
                            # this signature could not be rebound to safely.
                            "transform_skipped": transform_skipped,
                        },
                    ),
                )

        return async_wrapper if inspect.iscoroutinefunction(func) else sync_wrapper  # type: ignore[return-value]

    return decorator
