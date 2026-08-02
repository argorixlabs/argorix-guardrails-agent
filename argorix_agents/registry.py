from __future__ import annotations

import inspect
from typing import Any, Callable

_registered_steps: dict[tuple[str, str], dict[str, Any]] = {}


def _annotation_to_schema(annotation: Any) -> dict[str, Any] | None:
    if annotation is inspect.Signature.empty:
        return None
    if annotation in {str, "str"}:
        return {"type": "string"}
    if annotation in {int, "int"}:
        return {"type": "integer"}
    if annotation in {float, "float"}:
        return {"type": "number"}
    if annotation in {bool, "bool"}:
        return {"type": "boolean"}
    if annotation in {dict, "dict"}:
        return {"type": "object"}
    if annotation in {list, "list"}:
        return {"type": "array"}
    return None


def _normalize_step_type(func: Callable[..., Any], step_type: str | None) -> str:
    normalized = (step_type or "").strip().lower()
    if normalized:
        return normalized
    tool_name = getattr(func, "name", None) or getattr(func, "tool_name", None)
    return "tool" if tool_name else "llm"


def derive_step_schema(
    func: Callable[..., Any],
    step_name: str | None = None,
    step_type: str | None = None,
) -> dict[str, Any]:
    signature = inspect.signature(func)
    tool_name = getattr(func, "name", None) or getattr(func, "tool_name", None)
    resolved_name = (step_name or tool_name or func.__name__).strip()
    resolved_type = _normalize_step_type(func, step_type)

    properties: dict[str, Any] = {}
    required: list[str] = []
    for parameter in signature.parameters.values():
        if parameter.kind not in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
            inspect.Parameter.KEYWORD_ONLY,
        ):
            continue
        schema = _annotation_to_schema(parameter.annotation)
        properties[parameter.name] = schema or {"type": "string"}
        if parameter.default is inspect.Signature.empty:
            required.append(parameter.name)

    input_schema = (
        {"type": "object", "properties": properties, "required": required}
        if resolved_type == "tool"
        else {"type": "string"}
    )
    output_schema = _annotation_to_schema(signature.return_annotation) or {"type": "string"}
    return {
        "type": resolved_type,
        "name": resolved_name,
        "description": inspect.getdoc(func),
        "input_schema": input_schema,
        "output_schema": output_schema,
    }


def register_step(
    func: Callable[..., Any],
    step_name: str | None = None,
    step_type: str | None = None,
) -> None:
    step = derive_step_schema(func, step_name=step_name, step_type=step_type)
    _registered_steps[(step["type"], step["name"])] = step


def list_registered_steps() -> list[dict[str, Any]]:
    return list(_registered_steps.values())


def clear_registered_steps() -> None:
    _registered_steps.clear()
