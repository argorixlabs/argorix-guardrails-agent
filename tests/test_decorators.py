import asyncio
import sys
from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import argorix_agents
from argorix_agents.decorators import ControlSteerError, ControlViolationError
from argorix_agents.models import AgentEvaluation


def _allow_result() -> AgentEvaluation:
    return AgentEvaluation.from_payload(
        {
            "overall_decision": "allow",
            "allowed": True,
            "requires_steering": False,
            "confidence": 1.0,
            "evaluated_controls": 1,
            "matches": [],
            "non_matches": [],
            "errors": [],
        }
    )


def _match(action: str, **overrides) -> dict:
    payload = {
        "control_id": "control-1",
        "control_name": "Block SSN",
        "action": action,
        "evaluator_name": "regex",
        "selector_path": "input",
        "matched": True,
        "confidence": 0.99,
        "message": "Pattern matched.",
        "error": None,
        "metadata": {},
    }
    payload.update(overrides)
    return payload


def _result(action: str, **overrides) -> AgentEvaluation:
    return AgentEvaluation.from_payload(
        {
            "overall_decision": action,
            "allowed": action not in {"deny"},
            "requires_steering": action == "steer",
            "confidence": 0.99,
            "evaluated_controls": 1,
            "matches": [_match(action, **overrides)],
            "non_matches": [],
            "errors": [],
        }
    )


class FakeClient:
    def __init__(self, evaluations: list[AgentEvaluation] | None = None) -> None:
        self.evaluations = list(evaluations or [])
        self.evaluate_calls: list[dict] = []
        self.event_calls: list[dict] = []

    def evaluate(self, **kwargs):
        self.evaluate_calls.append(kwargs)
        if self.evaluations:
            return self.evaluations.pop(0)
        return _allow_result()

    def record_event(self, **kwargs):
        self.event_calls.append(kwargs)
        return {"recorded": True}


class AgentGuardrailsDecoratorTests(TestCase):
    def setUp(self) -> None:
        argorix_agents.reset(clear_steps=True)

    def tearDown(self) -> None:
        argorix_agents.reset(clear_steps=True)

    def bind_state(self) -> None:
        argorix_agents.state.base_url = "http://127.0.0.1:8001"
        argorix_agents.state.app_number = 123456
        argorix_agents.state.app_api_key = "ax_live_test"
        argorix_agents.state.agent = {"agent_name": "support_bot"}

    def test_init_merges_registered_steps_and_updates_state(self) -> None:
        @argorix_agents.control("draft_reply")
        def draft_reply(message: str) -> str:
            return message

        with patch.object(
            argorix_agents.AgentGuardrailsClient,
            "init_agent",
            return_value=argorix_agents.AgentRegistration.from_payload(
                {"created": True, "agent": {"agent_name": "support_bot"}, "controls": []}
            ),
        ) as init_agent:
            registration = argorix_agents.init(
                agent_name="support_bot",
                base_url="http://127.0.0.1:8001",
                app_number=123456,
                app_api_key="ax_live_test",
                policy_id="policy-1",
                control_ids=["control-1"],
                default_metadata={"channel": "support"},
            )

        self.assertEqual(registration.agent_name, "support_bot")
        self.assertEqual(registration["agent"]["agent_name"], "support_bot")
        self.assertEqual(argorix_agents.current_agent()["agent_name"], "support_bot")
        self.assertEqual(argorix_agents.state.default_policy_id, "policy-1")
        self.assertEqual(argorix_agents.state.default_control_ids, ["control-1"])
        self.assertEqual(argorix_agents.state.default_metadata, {"channel": "support"})

        _, kwargs = init_agent.call_args
        self.assertEqual(kwargs["agent_name"], "support_bot")
        self.assertEqual(len(kwargs["steps"]), 1)
        self.assertEqual(kwargs["steps"][0]["name"], "draft_reply")

    def test_sync_control_runs_pre_post_checks_and_records_span_event(self) -> None:
        fake_client = FakeClient(evaluations=[_allow_result(), _allow_result()])
        self.bind_state()
        argorix_agents.state.default_policy_id = "policy-default"
        argorix_agents.state.default_control_ids = ["control-default"]
        argorix_agents.state.default_metadata = {"workspace": "argorix"}

        @argorix_agents.control(
            "tool_selection",
            step_type="tool",
            control_ids=["control-override"],
            metadata={"channel": "support"},
        )
        def query_db(query: str, context: dict | None = None) -> str:
            return "ok"

        with patch("argorix_agents.decorators._current_client", return_value=fake_client):
            result = query_db("SELECT 1", context={"tenant": "acme"})

        self.assertEqual(result, "ok")
        self.assertEqual(len(fake_client.evaluate_calls), 2)
        self.assertEqual(fake_client.evaluate_calls[0]["policy_id"], "policy-default")
        self.assertEqual(fake_client.evaluate_calls[0]["control_ids"], ["control-override"])
        self.assertEqual(fake_client.evaluate_calls[0]["step"]["type"], "tool")
        self.assertEqual(fake_client.evaluate_calls[0]["step"]["name"], "tool_selection")
        self.assertEqual(fake_client.evaluate_calls[0]["step"]["input"], {"query": "SELECT 1"})
        self.assertEqual(fake_client.evaluate_calls[0]["step"]["context"], {"tenant": "acme"})
        self.assertEqual(len(fake_client.event_calls), 1)
        self.assertEqual(fake_client.event_calls[0]["event_type"], "step_execution")
        self.assertEqual(fake_client.event_calls[0]["decision"], "allow")
        self.assertEqual(fake_client.event_calls[0]["metadata"]["channel"], "support")
        self.assertEqual(fake_client.event_calls[0]["metadata"]["workspace"], "argorix")

    def test_sync_control_raises_violation_and_records_blocked_event(self) -> None:
        fake_client = FakeClient(evaluations=[_result("deny")])
        self.bind_state()
        executed = {"called": False}

        @argorix_agents.control()
        def chat(message: str) -> str:
            executed["called"] = True
            return f"Echo: {message}"

        with patch("argorix_agents.decorators._current_client", return_value=fake_client):
            with self.assertRaises(ControlViolationError) as exc:
                chat("share ssn")

        self.assertEqual(exc.exception.control_name, "Block SSN")
        self.assertFalse(executed["called"])
        self.assertEqual(len(fake_client.event_calls), 1)
        self.assertEqual(fake_client.event_calls[0]["event_type"], "step_blocked")

    def test_sync_control_raises_steer_with_steering_message(self) -> None:
        fake_client = FakeClient(
            evaluations=[
                _result("steer", metadata={"steering_message": "Ask for a case id instead."})
            ]
        )
        self.bind_state()

        @argorix_agents.control()
        def chat(message: str) -> str:
            return f"Echo: {message}"

        with patch("argorix_agents.decorators._current_client", return_value=fake_client):
            with self.assertRaises(ControlSteerError) as exc:
                chat("share ssn")

        self.assertEqual(exc.exception.steering_message, "Ask for a case id instead.")
        self.assertEqual(fake_client.event_calls[0]["event_type"], "step_steered")

    def test_async_control_runs_pre_post_checks(self) -> None:
        fake_client = FakeClient(evaluations=[_allow_result(), _allow_result()])
        self.bind_state()

        @argorix_agents.control("chat")
        async def chat(message: str) -> str:
            return f"Echo: {message}"

        with patch("argorix_agents.decorators._current_client", return_value=fake_client):
            result = asyncio.run(chat("hello"))

        self.assertEqual(result, "Echo: hello")
        self.assertEqual(len(fake_client.evaluate_calls), 2)
        self.assertEqual(fake_client.event_calls[0]["event_type"], "step_execution")

    def test_control_requires_init(self) -> None:
        @argorix_agents.control("chat")
        def chat(message: str) -> str:
            return message

        with self.assertRaises(RuntimeError):
            chat("hello")
