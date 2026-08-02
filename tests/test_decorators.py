import asyncio
from unittest import TestCase
from unittest.mock import patch

import argorix_agents
from argorix_agents import AgentControlError
from argorix_agents.decorators import ControlViolationError


def _allow_result() -> dict:
    return {
        "overall_decision": "allow",
        "allowed": True,
        "requires_steering": False,
        "confidence": 1.0,
        "evaluated_controls": 1,
        "matches": [],
        "non_matches": [],
        "errors": [],
    }


class FakeClient:
    def __init__(self, evaluations: list[dict] | None = None) -> None:
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


class ReceiptClient(FakeClient):
    """A FakeClient whose evaluations carry a receipt, and that records redemptions.

    `refuse` is what the server raises when it rejects the receipt; `receipt`
    set to None reproduces a backend that does not issue them at all.
    """

    def __init__(
        self,
        evaluations: list[dict] | None = None,
        *,
        receipt: str | None = "receipt-token",
        refuse: Exception | None = None,
    ) -> None:
        super().__init__(evaluations=evaluations)
        self._receipt = receipt
        self._refuse = refuse
        self.consumed: list[dict] = []

    def evaluate(self, **kwargs):
        result = dict(super().evaluate(**kwargs))
        if self._receipt is not None:
            result.setdefault("receipt", self._receipt)
        return result

    def consume_receipt(self, *, receipt: str, step: dict):
        self.consumed.append({"receipt": receipt, "step": step})
        if self._refuse is not None:
            raise self._refuse
        return {"consumed": True, "action_id": "action-1"}


class AgentControlSdkTests(TestCase):
    def setUp(self) -> None:
        argorix_agents.reset(clear_steps=True)

    def tearDown(self) -> None:
        argorix_agents.reset(clear_steps=True)

    def test_init_merges_registered_steps_and_updates_state(self) -> None:
        @argorix_agents.control("draft_reply")
        def draft_reply(message: str) -> str:
            return message

        with patch.object(
            argorix_agents.AgentControlClient,
            "init_agent",
            return_value={
                "created": True,
                "agent": {"agent_name": "support_bot"},
                "controls": [],
            },
        ) as init_agent:
            response = argorix_agents.init(
                agent_name="support_bot",
                base_url="http://127.0.0.1:8001",
                app_number=123456,
                app_api_key="ga_live_test",
                policy_id="policy-1",
                control_ids=["control-1"],
                default_metadata={"channel": "support"},
            )

        self.assertEqual(response["agent"]["agent_name"], "support_bot")
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
        argorix_agents.state.base_url = "http://127.0.0.1:8001"
        argorix_agents.state.app_number = 123456
        argorix_agents.state.app_api_key = "ga_live_test"
        argorix_agents.state.agent = {"agent_name": "support_bot"}
        argorix_agents.state.default_policy_id = "policy-default"
        argorix_agents.state.default_control_ids = ["control-default"]
        argorix_agents.state.default_metadata = {"workspace": "governance"}

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
        self.assertEqual(fake_client.event_calls[0]["metadata"]["channel"], "support")
        self.assertEqual(fake_client.event_calls[0]["metadata"]["workspace"], "governance")

    def test_sync_control_raises_violation_and_records_blocked_event(self) -> None:
        fake_client = FakeClient(
            evaluations=[
                {
                    "overall_decision": "deny",
                    "allowed": False,
                    "requires_steering": False,
                    "confidence": 0.99,
                    "evaluated_controls": 1,
                    "matches": [
                        {
                            "control_id": "control-1",
                            "control_name": "Block SSN",
                            "action": "deny",
                            "evaluator_name": "regex",
                            "selector_path": "input",
                            "matched": True,
                            "confidence": 0.99,
                            "message": "Pattern matched.",
                            "error": None,
                            "metadata": {},
                        }
                    ],
                    "non_matches": [],
                    "errors": [],
                }
            ]
        )
        argorix_agents.state.base_url = "http://127.0.0.1:8001"
        argorix_agents.state.app_number = 123456
        argorix_agents.state.app_api_key = "ga_live_test"
        argorix_agents.state.agent = {"agent_name": "support_bot"}

        executed = {"called": False}

        @argorix_agents.control()
        def chat(message: str) -> str:
            executed["called"] = True
            return f"Echo: {message}"

        with patch("argorix_agents.decorators._current_client", return_value=fake_client):
            with self.assertRaises(ControlViolationError):
                chat("share ssn")

        self.assertFalse(executed["called"])
        self.assertEqual(len(fake_client.event_calls), 1)
        self.assertEqual(fake_client.event_calls[0]["event_type"], "step_blocked")

    def _connect_state(self) -> None:
        argorix_agents.state.base_url = "http://127.0.0.1:8001"
        argorix_agents.state.app_number = 123456
        argorix_agents.state.app_api_key = "ga_live_test"
        argorix_agents.state.agent = {"agent_name": "support_bot"}

    def test_sends_the_intervention_point_alongside_the_legacy_stage(self) -> None:
        fake_client = FakeClient(evaluations=[_allow_result(), _allow_result()])
        self._connect_state()

        @argorix_agents.control("tool_selection", step_type="tool")
        def query_db(query: str) -> str:
            return "ok"

        with patch("argorix_agents.decorators._current_client", return_value=fake_client):
            query_db("SELECT 1")

        self.assertEqual(fake_client.evaluate_calls[0]["stage"], "pre")
        self.assertEqual(fake_client.evaluate_calls[0]["intervention_point"], "pre_tool_call")
        self.assertEqual(fake_client.evaluate_calls[1]["intervention_point"], "post_tool_call")

    def test_escalate_raises_with_the_approval_id(self) -> None:
        fake_client = FakeClient(
            evaluations=[
                {
                    "overall_decision": "escalate",
                    "allowed": False,
                    "requires_steering": False,
                    "requires_approval": True,
                    "approval_id": "approval-1",
                    "approval_expires_at": "2026-07-28T12:00:00+00:00",
                    "confidence": 0.99,
                    "evaluated_controls": 1,
                    "matches": [
                        {
                            "control_id": "control-1",
                            "control_name": "Escalate refunds",
                            "action": "escalate",
                            "evaluator_name": "regex",
                            "selector_path": "input",
                            "matched": True,
                            "confidence": 0.99,
                            "message": "Pattern matched.",
                            "metadata": {},
                        }
                    ],
                    "non_matches": [],
                    "errors": [],
                }
            ]
        )
        self._connect_state()
        executed = {"called": False}

        @argorix_agents.control()
        def issue_refund(message: str) -> str:
            executed["called"] = True
            return "done"

        with patch("argorix_agents.decorators._current_client", return_value=fake_client):
            with self.assertRaises(argorix_agents.ControlEscalationError) as caught:
                issue_refund("wire the refund to a new account")

        self.assertFalse(executed["called"])
        self.assertEqual(caught.exception.approval_id, "approval-1")
        self.assertEqual(fake_client.event_calls[0]["event_type"], "step_escalated")

    def test_transform_rewrites_the_input_and_the_output(self) -> None:
        pre_transform = {
            **_allow_result(),
            "overall_decision": "transform",
            "transformed_step": {
                "type": "tool",
                "name": "send_email",
                "input": {"body": "SSN [REDACTED]"},
            },
        }
        post_transform = {
            **_allow_result(),
            "overall_decision": "transform",
            "transformed_step": {
                "type": "tool",
                "name": "send_email",
                "input": {"body": "SSN [REDACTED]"},
                "output": "sent to [REDACTED-PII]",
            },
        }
        fake_client = FakeClient(evaluations=[pre_transform, post_transform])
        self._connect_state()
        seen: dict[str, str] = {}

        @argorix_agents.control("send_email", step_type="tool")
        def send_email(body: str) -> str:
            seen["body"] = body
            return "sent to person@example.com"

        with patch("argorix_agents.decorators._current_client", return_value=fake_client):
            result = send_email("SSN 123-45-6789")

        self.assertEqual(seen["body"], "SSN [REDACTED]")
        self.assertEqual(result, "sent to [REDACTED-PII]")
        self.assertTrue(fake_client.event_calls[0]["metadata"]["transform_applied"])

    def test_fail_open_evaluation_error_does_not_block_the_step(self) -> None:
        fail_open_error = {
            **_allow_result(),
            "errors": [
                {
                    "control_id": "control-1",
                    "control_name": "Optional check",
                    "action": "warn",
                    "evaluator_name": "regex",
                    "selector_path": "input",
                    "matched": False,
                    "confidence": 0.0,
                    "message": "Evaluation failed: bad pattern",
                    "error": "bad pattern",
                    "metadata": {"fail_open": True},
                }
            ],
        }
        fake_client = FakeClient(evaluations=[fail_open_error, _allow_result()])
        self._connect_state()

        executed = {"called": False}

        @argorix_agents.control()
        def chat(message: str) -> str:
            executed["called"] = True
            return f"Echo: {message}"

        with patch("argorix_agents.decorators._current_client", return_value=fake_client):
            self.assertEqual(chat("hello"), "Echo: hello")

        # Asserted positively on purpose. This is the control for the
        # fail-closed test below: a counter that is never observed True proves
        # nothing when it later reads False.
        self.assertTrue(executed["called"])

    def test_fail_closed_evaluation_error_blocks_the_step(self) -> None:
        fail_closed_error = {
            **_allow_result(),
            "allowed": False,
            "overall_decision": "deny",
            "fail_closed": True,
            "errors": [
                {
                    "control_id": "control-1",
                    "control_name": "Broken check",
                    "action": "deny",
                    "evaluator_name": "regex",
                    "selector_path": "input",
                    "matched": False,
                    "confidence": 0.0,
                    "message": "Evaluation failed: bad pattern",
                    "error": "bad pattern",
                    "metadata": {"fail_open": False},
                }
            ],
        }
        fake_client = FakeClient(evaluations=[fail_closed_error])
        self._connect_state()

        executed = {"called": False}

        @argorix_agents.control()
        def chat(message: str) -> str:
            executed["called"] = True
            return f"Echo: {message}"

        with patch("argorix_agents.decorators._current_client", return_value=fake_client):
            with self.assertRaises(RuntimeError):
                chat("hello")

        # The point of the test. Raising is not the guarantee -- a decorator
        # that ran the body and then raised would satisfy `assertRaises` while
        # having already sent the request the control existed to stop.
        self.assertFalse(executed["called"])
        # A broken evaluator is recorded as `step_error`, not `step_blocked`:
        # the step stopped because we could not ask, not because policy said
        # no. The server-side `fail_closed` flag is what carries the safety
        # meaning. Pinned here so a reclassification is a decision, not a drift.
        self.assertEqual(len(fake_client.event_calls), 1)
        self.assertEqual(fake_client.event_calls[0]["event_type"], "step_error")

    def _blocks_the_step_when_evaluate_raises(self, error: Exception) -> None:
        """Shared body: whatever `evaluate` raises, the sink stays untouched.

        The engine failing and the engine going quiet are different incidents
        for an operator, but they have to be the same outcome for the step.
        """

        class RaisingClient(FakeClient):
            def evaluate(self, **kwargs):
                raise error

        fake_client = RaisingClient()
        self._connect_state()

        executed = {"called": False}

        @argorix_agents.control()
        def chat(message: str) -> str:
            executed["called"] = True
            return f"Echo: {message}"

        with patch("argorix_agents.decorators._current_client", return_value=fake_client):
            with self.assertRaises(Exception):
                chat("hello")

        self.assertFalse(executed["called"])

    def test_a_policy_engine_timeout_does_not_reach_the_sink(self) -> None:
        """The engine going quiet must not become an implicit allow.

        Today this holds because `socket.timeout` descends from `OSError` and
        lands in the client's retry handler, which eventually raises. Nothing
        declared that as a requirement, so this test is what keeps a transport
        swap from turning silence into permission.
        """
        self._blocks_the_step_when_evaluate_raises(
            AgentControlError("Unable to call http://engine: timed out")
        )

    def test_an_unreadable_engine_response_does_not_reach_the_sink(self) -> None:
        """A 200 with a garbled body answers nothing, so nothing may proceed."""
        self._blocks_the_step_when_evaluate_raises(
            AgentControlError("Unable to call http://engine: Expecting value")
        )

    def test_an_unexpected_client_failure_does_not_reach_the_sink(self) -> None:
        """The catch-all. An error nobody anticipated still fails closed.

        Deliberately an exception type the SDK knows nothing about: the
        guarantee cannot depend on having enumerated every failure mode in
        advance, because the ones that matter are the ones nobody listed.
        """
        self._blocks_the_step_when_evaluate_raises(MemoryError("out of memory"))

    def test_async_fail_closed_evaluation_error_blocks_the_step(self) -> None:
        """The async wrapper is its own code path and needs its own proof.

        `deny` and `escalate` are covered on both wrappers, but the fail-closed
        evaluator error was only ever exercised on the sync one. An async agent
        -- which is most of them -- had no test saying the coroutine body never
        runs when the policy engine cannot answer.
        """
        fail_closed_error = {
            **_allow_result(),
            "allowed": False,
            "overall_decision": "deny",
            "fail_closed": True,
            "errors": [
                {
                    "control_id": "control-1",
                    "control_name": "Broken check",
                    "action": "deny",
                    "evaluator_name": "regex",
                    "selector_path": "input",
                    "matched": False,
                    "confidence": 0.0,
                    "message": "Evaluation failed: bad pattern",
                    "error": "bad pattern",
                    "metadata": {"fail_open": False},
                }
            ],
        }
        fake_client = FakeClient(evaluations=[fail_closed_error])
        self._connect_state()

        executed = {"called": False}

        @argorix_agents.control()
        async def chat(message: str) -> str:
            executed["called"] = True
            return f"Echo: {message}"

        with patch("argorix_agents.decorators._current_client", return_value=fake_client):
            with self.assertRaises(RuntimeError):
                asyncio.run(chat("hello"))

        self.assertFalse(executed["called"])
        self.assertEqual(len(fake_client.evaluate_calls), 1)
        self.assertEqual(fake_client.event_calls[0]["event_type"], "step_error")

    def test_redemption_is_off_until_asked_for(self) -> None:
        """A `pip install -U` must not start requiring an endpoint.

        This SDK versions independently of the backend it talks to. Defaulting
        redemption on would break every agent whose ARGORIX predates the
        endpoint -- an outage delivered by an upgrade, which is the worst way
        to find out a security feature exists.
        """
        fake_client = ReceiptClient(evaluations=[_allow_result(), _allow_result()])
        self._connect_state()

        @argorix_agents.control()
        def chat(message: str) -> str:
            return f"Echo: {message}"

        with patch("argorix_agents.decorators._current_client", return_value=fake_client):
            self.assertEqual(chat("hello"), "Echo: hello")

        self.assertEqual(fake_client.consumed, [])

    def test_the_step_redeemed_is_the_step_about_to_run(self) -> None:
        fake_client = ReceiptClient(evaluations=[_allow_result(), _allow_result()])
        self._connect_state()
        argorix_agents.state.redeem_receipts = True

        @argorix_agents.control("query_db", step_type="tool")
        def query_db(query: str) -> str:
            return "ok"

        with patch("argorix_agents.decorators._current_client", return_value=fake_client):
            query_db("SELECT 1")

        self.assertEqual(len(fake_client.consumed), 1)
        redeemed = fake_client.consumed[0]
        self.assertEqual(redeemed["receipt"], "receipt-token")
        self.assertEqual(redeemed["step"]["input"], {"query": "SELECT 1"})
        # No output yet: the step has not run. Redeeming a payload that carried
        # a result would be redeeming something that already happened.
        self.assertIsNone(redeemed["step"]["output"])

    def test_a_refused_receipt_does_not_reach_the_sink(self) -> None:
        """The reason the whole receipt chain exists, observed at the sink."""
        fake_client = ReceiptClient(
            evaluations=[_allow_result()],
            refuse=AgentControlError("Receipt does not cover this payload."),
        )
        self._connect_state()
        argorix_agents.state.redeem_receipts = True

        executed = {"called": False}

        @argorix_agents.control()
        def chat(message: str) -> str:
            executed["called"] = True
            return f"Echo: {message}"

        with patch("argorix_agents.decorators._current_client", return_value=fake_client):
            with self.assertRaises(AgentControlError):
                chat("hello")

        self.assertFalse(executed["called"])

    def test_a_missing_receipt_does_not_reach_the_sink(self) -> None:
        """Enabled but unanswered is the dangerous middle state.

        An agent that believes it is enforcing while quietly not is worse than
        one that never turned it on, so a verdict with no receipt stops the
        step and says why.
        """
        fake_client = ReceiptClient(evaluations=[_allow_result()], receipt=None)
        self._connect_state()
        argorix_agents.state.redeem_receipts = True

        executed = {"called": False}

        @argorix_agents.control()
        def chat(message: str) -> str:
            executed["called"] = True
            return f"Echo: {message}"

        with patch("argorix_agents.decorators._current_client", return_value=fake_client):
            with self.assertRaises(AgentControlError) as exc:
                chat("hello")

        self.assertFalse(executed["called"])
        self.assertIn("no receipt", str(exc.exception))

    def test_a_transformed_step_redeems_what_the_transform_produced(self) -> None:
        """The case the payload binding could have broken.

        A control rewrote the input, so the receipt covers the rewritten step
        and the function will receive the rewritten arguments. Redeeming the
        original would fail against an honest server; redeeming after the
        rebind is what keeps transforms and receipts compatible.
        """
        transformed = {
            **_allow_result(),
            "transformed_step": {
                "type": "tool",
                "name": "query_db",
                "input": {"query": "SELECT 1 -- redacted"},
            },
        }
        fake_client = ReceiptClient(evaluations=[transformed, _allow_result()])
        self._connect_state()
        argorix_agents.state.redeem_receipts = True

        seen = {}

        @argorix_agents.control("query_db", step_type="tool")
        def query_db(query: str) -> str:
            seen["query"] = query
            return "ok"

        with patch("argorix_agents.decorators._current_client", return_value=fake_client):
            query_db("SELECT 1")

        self.assertEqual(seen["query"], "SELECT 1 -- redacted")
        self.assertEqual(
            fake_client.consumed[0]["step"]["input"], {"query": "SELECT 1 -- redacted"}
        )

    def test_async_redemption_also_guards_the_sink(self) -> None:
        fake_client = ReceiptClient(
            evaluations=[_allow_result()],
            refuse=AgentControlError("Receipt has already been redeemed."),
        )
        self._connect_state()
        argorix_agents.state.redeem_receipts = True

        executed = {"called": False}

        @argorix_agents.control()
        async def chat(message: str) -> str:
            executed["called"] = True
            return f"Echo: {message}"

        with patch("argorix_agents.decorators._current_client", return_value=fake_client):
            with self.assertRaises(AgentControlError):
                asyncio.run(chat("hello"))

        self.assertFalse(executed["called"])

    def test_async_control_runs_pre_post_checks(self) -> None:
        fake_client = FakeClient(evaluations=[_allow_result(), _allow_result()])
        argorix_agents.state.base_url = "http://127.0.0.1:8001"
        argorix_agents.state.app_number = 123456
        argorix_agents.state.app_api_key = "ga_live_test"
        argorix_agents.state.agent = {"agent_name": "support_bot"}

        @argorix_agents.control("chat")
        async def chat(message: str) -> str:
            return f"Echo: {message}"

        with patch("argorix_agents.decorators._current_client", return_value=fake_client):
            result = asyncio.run(chat("hello"))

        self.assertEqual(result, "Echo: hello")
        self.assertEqual(len(fake_client.evaluate_calls), 2)
        self.assertEqual(fake_client.event_calls[0]["event_type"], "step_execution")
