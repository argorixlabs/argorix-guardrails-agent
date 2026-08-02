import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest import TestCase

from argorix_agents import AgentControlClient, AgentControlError


class RuntimeHandler(BaseHTTPRequestHandler):
    #: Each entry is `(status, body)`. A dict body is serialized as JSON; raw
    #: `bytes` are written through untouched, which is how the malformed-body
    #: cases reproduce a proxy returning an HTML error page with a 200.
    responses: list[tuple[int, object]] = []
    requests: list[dict] = []
    #: Held before answering, to reproduce a policy engine that stops replying.
    delay_seconds: float = 0.0

    def do_POST(self) -> None:  # noqa: N802
        length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(length).decode("utf-8")
        payload = json.loads(raw_body) if raw_body else {}
        RuntimeHandler.requests.append(
            {
                "path": self.path,
                "headers": dict(self.headers.items()),
                "payload": payload,
            }
        )

        if RuntimeHandler.delay_seconds:
            time.sleep(RuntimeHandler.delay_seconds)

        status, body = RuntimeHandler.responses.pop(0)
        encoded = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(encoded)))
        self.end_headers()
        self.wfile.write(encoded)

    def log_message(self, format: str, *args) -> None:  # noqa: A003
        return


class QuietThreadingHTTPServer(ThreadingHTTPServer):
    """Swallows the broken pipe a timed-out client leaves behind.

    The timeout tests hang up mid-response on purpose; without this the server
    prints a traceback that looks like a test failure and is not one.
    """

    def handle_error(self, request, client_address) -> None:
        return


class AgentControlClientTests(TestCase):
    def setUp(self) -> None:
        RuntimeHandler.responses = []
        RuntimeHandler.requests = []
        RuntimeHandler.delay_seconds = 0.0
        self.server = QuietThreadingHTTPServer(("127.0.0.1", 0), RuntimeHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.server.server_port}"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_init_agent_retries_transient_failure(self) -> None:
        RuntimeHandler.responses = [
            (503, {"detail": "retry later"}),
            (200, {"created": True, "agent": {"agent_name": "support_bot"}}),
        ]
        client = AgentControlClient(
            base_url=self.base_url,
            app_number=123456,
            app_api_key="ga_live_test",
            max_retries=1,
            retry_backoff_seconds=0,
        )

        response = client.init_agent(agent_name="support_bot")

        self.assertTrue(response["created"])
        self.assertEqual(len(RuntimeHandler.requests), 2)
        self.assertEqual(RuntimeHandler.requests[0]["headers"]["Authorization"], "Bearer ga_live_test")

    def test_init_agent_raises_structured_error(self) -> None:
        RuntimeHandler.responses = [(400, {"detail": "invalid app"})]
        client = AgentControlClient(
            base_url=self.base_url,
            app_number=123456,
            app_api_key="ga_live_test",
            max_retries=0,
            retry_backoff_seconds=0,
        )

        with self.assertRaises(AgentControlError) as exc:
            client.init_agent(agent_name="support_bot")

        self.assertEqual(exc.exception.status_code, 400)
        self.assertIn("invalid app", exc.exception.response_body or "")

    def _client(self, **overrides) -> AgentControlClient:
        options = {
            "base_url": self.base_url,
            "app_number": 123456,
            "app_api_key": "ga_live_test",
            "max_retries": 0,
            "retry_backoff_seconds": 0,
        }
        options.update(overrides)
        return AgentControlClient(**options)

    # -- malformed responses --------------------------------------------------

    def test_a_malformed_body_surfaces_as_a_client_error(self) -> None:
        """A 200 whose body is not JSON must not escape as a parse error.

        `json.JSONDecodeError` does not descend from `OSError`, so it used to
        slip past the retry loop unwrapped. The caller then had to know about
        the client's internals to tell "the engine refused" from "the engine
        answered something unreadable".
        """
        RuntimeHandler.responses = [(200, b"<html>502 Bad Gateway</html>")]

        with self.assertRaises(AgentControlError):
            self._client().init_agent(agent_name="support_bot")

    def test_a_non_utf8_body_surfaces_as_a_client_error(self) -> None:
        """Same class of failure one layer earlier: the decode, not the parse."""
        RuntimeHandler.responses = [(200, b"\xff\xfe\x00garbage")]

        with self.assertRaises(AgentControlError):
            self._client().init_agent(agent_name="support_bot")

    def test_a_malformed_body_is_retried_like_any_unusable_response(self) -> None:
        """An unreadable answer is a transient failure, and transient failures retry.

        Before, this response type bypassed the retry loop entirely: a garbled
        body from a proxy failed the call outright even when the very next
        attempt would have succeeded.
        """
        RuntimeHandler.responses = [
            (200, b"not json at all"),
            (200, {"created": True, "agent": {"agent_name": "support_bot"}}),
        ]

        response = self._client(max_retries=1).init_agent(agent_name="support_bot")

        self.assertTrue(response["created"])
        self.assertEqual(len(RuntimeHandler.requests), 2)

    # -- timeout --------------------------------------------------------------

    def test_a_policy_engine_that_stops_answering_raises_rather_than_hangs(self) -> None:
        """The engine going quiet has to end the call, not wait on it.

        This currently works because `socket.timeout` happens to descend from
        `OSError` and lands in the retry handler. That is a load-bearing
        inheritance relationship nobody declared: swapping the transport for one
        whose timeout is not an `OSError` would turn this into a fail-open
        without a single line of the guardrail engine changing.
        """
        RuntimeHandler.delay_seconds = 1.2
        RuntimeHandler.responses = [(200, {"created": True})]

        started = time.monotonic()
        with self.assertRaises(AgentControlError):
            self._client(timeout_seconds=0.25).init_agent(agent_name="support_bot")
        elapsed = time.monotonic() - started

        # It gave up on its own deadline instead of riding the server's.
        self.assertLess(elapsed, 1.0)
