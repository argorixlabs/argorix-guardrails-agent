from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Actions a control can return. ``deny`` and ``steer`` interrupt the step.
ControlAction = str


class _RawMapping:
    """Dict-style access to the untouched API payload."""

    raw: dict[str, Any]

    def __getitem__(self, key: str) -> Any:
        return self.raw[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.raw.get(key, default)

    def keys(self):  # noqa: ANN201 - mirrors Mapping.keys
        return self.raw.keys()

    def __contains__(self, key: object) -> bool:
        return key in self.raw


@dataclass
class ControlMatch:
    """One control evaluation result."""

    control_id: str
    control_name: str
    action: ControlAction
    evaluator_name: str
    selector_path: str
    matched: bool
    confidence: float
    message: str | None = None
    error: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def steering_message(self) -> str | None:
        value = self.metadata.get("steering_message")
        return str(value) if value else None

    def __getitem__(self, key: str) -> Any:
        try:
            return getattr(self, key)
        except AttributeError as exc:
            raise KeyError(key) from exc

    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> ControlMatch:
        metadata = payload.get("metadata") or {}
        return cls(
            control_id=str(payload.get("control_id", "")),
            control_name=str(payload.get("control_name", "")),
            action=str(payload.get("action", "allow")),
            evaluator_name=str(payload.get("evaluator_name", "")),
            selector_path=str(payload.get("selector_path", "")),
            matched=bool(payload.get("matched", False)),
            confidence=float(payload.get("confidence", 0.0) or 0.0),
            message=str(payload["message"]) if payload.get("message") else None,
            error=str(payload["error"]) if payload.get("error") else None,
            metadata=dict(metadata if isinstance(metadata, dict) else {}),
        )


@dataclass
class AgentEvaluation(_RawMapping):
    """Outcome of an agent guardrails evaluation for a single step."""

    overall_decision: ControlAction = "allow"
    allowed: bool = True
    requires_steering: bool = False
    confidence: float = 0.0
    evaluated_controls: int = 0
    matches: list[ControlMatch] = field(default_factory=list)
    non_matches: list[ControlMatch] = field(default_factory=list)
    errors: list[ControlMatch] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def denied(self) -> bool:
        return not self.allowed

    def matches_with_action(self, action: ControlAction) -> list[ControlMatch]:
        return [item for item in self.matches if item.action == action]

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> AgentEvaluation:
        def parse(key: str) -> list[ControlMatch]:
            return [
                ControlMatch.from_payload(item)
                for item in (payload.get(key) or [])
                if isinstance(item, dict)
            ]

        return cls(
            overall_decision=str(payload.get("overall_decision", "allow")),
            allowed=bool(payload.get("allowed", True)),
            requires_steering=bool(payload.get("requires_steering", False)),
            confidence=float(payload.get("confidence", 0.0) or 0.0),
            evaluated_controls=int(payload.get("evaluated_controls", 0) or 0),
            matches=parse("matches"),
            non_matches=parse("non_matches"),
            errors=parse("errors"),
            raw=dict(payload),
        )


@dataclass
class AgentRegistration(_RawMapping):
    """Result of registering an agent with the control plane."""

    created: bool = False
    agent: dict[str, Any] = field(default_factory=dict)
    controls: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)

    @property
    def agent_name(self) -> str:
        return str(self.agent.get("agent_name", ""))

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> AgentRegistration:
        agent = payload.get("agent") or {}
        return cls(
            created=bool(payload.get("created", False)),
            agent=dict(agent if isinstance(agent, dict) else {}),
            controls=[item for item in (payload.get("controls") or []) if isinstance(item, dict)],
            raw=dict(payload),
        )
