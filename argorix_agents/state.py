from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentControlState:
    base_url: str | None = None
    app_number: int | None = None
    app_api_key: str | None = None
    agent: dict[str, Any] | None = None
    init_response: dict[str, Any] | None = None
    registered_steps: list[dict[str, Any]] = field(default_factory=list)
    default_policy_id: str | None = None
    default_control_ids: list[str] = field(default_factory=list)
    default_metadata: dict[str, Any] = field(default_factory=dict)
    #: Redeem the receipt from each evaluation before running the step, and
    #: refuse to run when the redemption is refused.
    #:
    #: Off by default, and the reason is compatibility rather than caution:
    #: this SDK ships on PyPI and is upgraded independently of the backend it
    #: talks to. Turning it on by default would make every upgrade fail against
    #: an ARGORIX that predates the redemption endpoint -- an outage caused by
    #: `pip install -U`, which is the worst way to learn about a security
    #: feature. Turn it on once the backend is known to support it; from then
    #: on a step whose receipt is refused does not run.
    redeem_receipts: bool = False


state = AgentControlState()
