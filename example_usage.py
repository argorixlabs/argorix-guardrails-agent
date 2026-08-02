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
    argorix_agents.init(
        agent_name="support_bot",
        agent_description="Customer support assistant",
        base_url="http://127.0.0.1:8001",
        app_number=123456,
        app_api_key="ga_live_replace_me",
        default_metadata={"environment": "local"},
    )

    try:
        print(await chat("test"))
        print(await query_db("SELECT * FROM tickets", context={"tenant": "acme"}))
    except ControlViolationError as exc:
        print(f"Blocked by {exc.control_name}: {exc.message}")
    except ControlSteerError as exc:
        print(f"Steered by {exc.control_name}: {exc.steering_message or exc.message}")


if __name__ == "__main__":
    asyncio.run(main())
