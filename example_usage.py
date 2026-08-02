"""End-to-end example for the Argorix Agent Guardrails SDK.

Run with:
    ARGORIX_API_URL=https://api.argorix.com \
    ARGORIX_APP_NUMBER=123456 \
    ARGORIX_APP_API_KEY=ax_live_replace_me \
    python example_usage.py
"""

import asyncio

import argorix_agents
from argorix_agents import ControlSteerError, ControlViolationError, control


@control("draft_reply")
async def chat(message: str) -> str:
    if "test" in message.lower():
        return "Customer SSN is 123-45-6789"
    return f"Echo: {message}"


@control("query_db", step_type="tool")
async def query_db(query: str, context: dict | None = None) -> str:
    return f"Executed: {query}"


async def main() -> None:
    registration = argorix_agents.init(
        agent_name="support_bot",
        agent_description="Customer support assistant",
        default_metadata={"environment": "local"},
    )
    print("Agent registered:", registration.agent_name, "created:", registration.created)
    print("Bound controls:", len(registration.controls))

    try:
        print(await chat("test"))
        print(await query_db("SELECT * FROM tickets", context={"tenant": "acme"}))
    except ControlViolationError as exc:
        print(f"Blocked by {exc.control_name}: {exc.message}")
    except ControlSteerError as exc:
        print(f"Steered by {exc.control_name}: {exc.steering_message or exc.message}")

    # Manual evaluation, without the decorator.
    evaluation = argorix_agents.evaluate_step(
        stage="pre",
        step={"type": "llm", "name": "summary", "input": "Summarize ticket 42"},
    )
    print("Manual decision:", evaluation.overall_decision, "allowed:", evaluation.allowed)


if __name__ == "__main__":
    asyncio.run(main())
