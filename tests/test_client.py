import json
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest import TestCase

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from argorix_agents import AgentGuardrailsClient, ArgorixAgentError
from argorix_agents.models import AgentEvaluation

DENY_RESULT = {
    "overall_decision": "deny",
    "allowed": False,
    "requires_steering": False,
    "confidence": 0.99,
    "evaluated_controls": 2,
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
            "metadata": {"steering_message": "Ask for a case id instead."},
        }
    ],
    "non_matches": [],
    "errors": [],
}


class RuntimeHandler(BaseHTTPRequestHandler):
    responses: list[tuple[int, dict]] = []
    sse_responses: list[list[tuple[str, dict]]] = []
    requests: list[dict] = []

    def do_GET(self) -> None:  # noqa: N802
        self._record()
        self._respond_json()

    def do_POST(self) -> None:  # noqa: N802
        self._record()
        if self.path.endswith("/stream"):
            self._respond_sse()
            return
        self._respond_json()

    def _record(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length).decode("utf-8") if length else ""
        RuntimeHandler.requests.append(
            {
                "method": self.command,
                "path": self.path,
                "headers": dict(self.headers.items()),
                "payload": json.loads(raw_body) if raw_body else {},
            }
        )

    def _respond_json(self) -> None:
        status, body = RuntimeHandler.responses.pop(0)
        encoded = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def _respond_sse(self) -> None:
        events = RuntimeHandler.sse_responses.pop(0)
        body = b"".join(
            f"event: {name}\ndata: {json.dumps(data)}\n\n".encode("utf-8") for name, data in events
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return


class AgentGuardrailsClientTests(TestCase):
    def setUp(self) -> None:
        RuntimeHandler.responses = []
        RuntimeHandler.sse_responses = []
        RuntimeHandler.requests = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), RuntimeHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def build_client(self, **kwargs) -> AgentGuardrailsClient:
        return AgentGuardrailsClient(
            base_url=self.base_url,
            app_number=123456,
            app_api_key="ax_live_test",
            retry_backoff_seconds=0,
            **kwargs,
        )

    def test_init_agent_retries_transient_failure(self) -> None:
        RuntimeHandler.responses = [
            (503, {"detail": "retry later"}),
            (200, {"created": True, "agent": {"agent_name": "support_bot"}, "controls": []}),
        ]
        client = self.build_client(max_retries=1)

        registration = client.init_agent(agent_name="support_bot")

        self.assertTrue(registration.created)
        self.assertEqual(registration.agent_name, "support_bot")
        self.assertEqual(registration["created"], True)
        self.assertEqual(len(RuntimeHandler.requests), 2)
        self.assertEqual(
            RuntimeHandler.requests[0]["headers"]["Authorization"], "Bearer ax_live_test"
        )
        self.assertTrue(
            RuntimeHandler.requests[0]["headers"]["User-Agent"].startswith("argorix-agents-python/")
        )
        self.assertEqual(
            RuntimeHandler.requests[0]["path"], "/v1/agent-guardrails/runtime/agents/init"
        )

    def test_init_agent_raises_structured_error(self) -> None:
        RuntimeHandler.responses = [(400, {"detail": "invalid app"})]
        client = self.build_client(max_retries=0)

        with self.assertRaises(ArgorixAgentError) as exc:
            client.init_agent(agent_name="support_bot")

        self.assertEqual(exc.exception.status_code, 400)
        self.assertIn("invalid app", exc.exception.response_body or "")

    def test_list_agent_controls_sends_app_number_and_policy(self) -> None:
        RuntimeHandler.responses = [(200, {"agent_name": "support_bot", "controls": []})]
        client = self.build_client(max_retries=0)

        client.list_agent_controls(agent_name="support_bot", policy_id="policy-1")

        request = RuntimeHandler.requests[0]
        self.assertEqual(request["method"], "GET")
        self.assertIn("/v1/agent-guardrails/runtime/agents/support_bot/controls", request["path"])
        self.assertIn("app_number=123456", request["path"])
        self.assertIn("policy_id=policy-1", request["path"])

    def test_evaluate_returns_typed_matches(self) -> None:
        RuntimeHandler.responses = [(200, DENY_RESULT)]
        client = self.build_client(max_retries=0)

        result = client.evaluate(
            agent_name="support_bot",
            stage="pre",
            step={"type": "llm", "name": "chat", "input": "share ssn"},
        )

        self.assertIsInstance(result, AgentEvaluation)
        self.assertTrue(result.denied)
        self.assertEqual(result.overall_decision, "deny")
        self.assertEqual(result.matches[0].control_name, "Block SSN")
        self.assertEqual(result.matches[0].steering_message, "Ask for a case id instead.")
        self.assertEqual(result.matches_with_action("deny"), result.matches)
        self.assertEqual(result["evaluated_controls"], 2)

    def test_evaluate_stream_yields_events(self) -> None:
        RuntimeHandler.sse_responses = [
            [
                ("start", {"status": "started", "stage": "pre"}),
                ("result", DENY_RESULT),
                ("end", {"status": "completed", "allowed": False, "decision": "deny"}),
            ]
        ]
        client = self.build_client(max_retries=0)

        events = list(
            client.evaluate_stream(
                agent_name="support_bot",
                stage="pre",
                step={"type": "llm", "name": "chat", "input": "share ssn"},
            )
        )

        self.assertEqual([event.event for event in events], ["start", "result", "end"])
        self.assertEqual(
            RuntimeHandler.requests[0]["path"], "/v1/agent-guardrails/runtime/evaluate/stream"
        )

    def test_streamed_result_returns_evaluation(self) -> None:
        RuntimeHandler.sse_responses = [
            [("start", {"status": "started"}), ("result", DENY_RESULT), ("end", {"status": "ok"})]
        ]
        client = self.build_client(max_retries=0)

        result = client.evaluate_streamed_result(
            agent_name="support_bot",
            stage="pre",
            step={"type": "llm", "name": "chat", "input": "share ssn"},
        )

        self.assertFalse(result.allowed)
        self.assertEqual(result.matches[0].control_id, "control-1")

    def test_streamed_result_raises_on_error_event(self) -> None:
        RuntimeHandler.sse_responses = [
            [("error", {"status": "error", "detail": "unknown agent", "code": 400})]
        ]
        client = self.build_client(max_retries=0)

        with self.assertRaises(ArgorixAgentError) as exc:
            client.evaluate_streamed_result(
                agent_name="ghost",
                stage="pre",
                step={"type": "llm", "name": "chat", "input": "hi"},
            )

        self.assertEqual(exc.exception.status_code, 400)

    def test_record_event_posts_span_payload(self) -> None:
        RuntimeHandler.responses = [(200, {"recorded": True, "event_id": "e1", "created_at": "x"})]
        client = self.build_client(max_retries=0)

        client.record_event(
            agent_name="support_bot",
            event_type="step_blocked",
            decision="deny",
            allowed=False,
            duration_ms=12.5,
            matches_total=1,
        )

        payload = RuntimeHandler.requests[0]["payload"]
        self.assertEqual(payload["event_type"], "step_blocked")
        self.assertEqual(payload["matches_total"], 1)
        self.assertEqual(RuntimeHandler.requests[0]["path"], "/v1/agent-guardrails/runtime/events")

    def test_legacy_client_alias_still_resolves(self) -> None:
        from argorix_agents import AgentControlClient, AgentControlError

        self.assertIs(AgentControlClient, AgentGuardrailsClient)
        self.assertIs(AgentControlError, ArgorixAgentError)
