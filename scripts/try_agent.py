"""Try the Retail Store Assistant from the command line (real OpenAI calls).

Examples:
    python -m scripts.try_agent
    python -m scripts.try_agent --model gpt-4.1-mini "Coca Cola ဘယ်မှာရှိလဲ"
    python -m scripts.try_agent --conversation "အရပ် 170 cm ပါ" "70 kg"

Each message gets a new session unless --conversation is given.
"""

import argparse
import asyncio
import json
import logging
import uuid

from app.config import get_settings
from app.data.csv_source import load_business_data_from_csv
from app.data.repository import InMemoryBusinessRepository
from app.models.kiosk import KioskContext, Language
from app.services.agent_service import AgentService
from app.services.session_store import InMemorySessionStore
from app.tools.context import ToolContext
from app.utils.logging import configure_logging

DEFAULT_MESSAGES = [
    "Coca Cola ဘယ်မှာရှိလဲ",
    "Head & Shoulders shampoo ရှိလား",
    "Colgate toothpaste ဘယ် aisle မှာလဲ",
    "Parking ရှိလား",
    "ဆိုင် ဘယ်အချိန်ပိတ်လဲ",
    "အရပ် 170 cm weight 70 kg BMI တွက်ပေးပါ",
]


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("messages", nargs="*", default=DEFAULT_MESSAGES)
    parser.add_argument("--model", help="override OPENAI_AGENT_MODEL")
    parser.add_argument("--effort", help="override OPENAI_AGENT_REASONING_EFFORT")
    parser.add_argument("--store", default="STORE-001")
    parser.add_argument("--org", default="ORG-001")
    parser.add_argument("--lang", default="my-MM", choices=["my-MM", "en-US"])
    parser.add_argument("--conversation", action="store_true", help="one session for all")
    parser.add_argument("--verbose", action="store_true", help="show JSON logs")
    args = parser.parse_args()

    configure_logging("INFO")
    if not args.verbose:
        logging.disable(logging.CRITICAL)

    updates: dict[str, object] = {}
    if args.model:
        updates["openai_agent_model"] = args.model
    if args.effort is not None:
        updates["openai_agent_reasoning_effort"] = args.effort
    settings = get_settings().model_copy(update=updates)
    if not settings.openai_api_key or not settings.openai_agent_model:
        raise SystemExit("Set OPENAI_API_KEY and OPENAI_AGENT_MODEL (or pass --model).")

    from openai import AsyncOpenAI

    client = AsyncOpenAI(
        api_key=settings.openai_api_key.get_secret_value(),
        timeout=settings.openai_timeout_seconds,
        max_retries=settings.openai_max_retries,
    )
    repository = InMemoryBusinessRepository.from_data(
        load_business_data_from_csv(settings.mock_data_dir)
    )
    sessions = InMemorySessionStore(ttl_seconds=600, max_turns=settings.session_max_turns)
    agent = AgentService(client=client, settings=settings, sessions=sessions)
    language: Language = args.lang

    print(f"model={settings.openai_agent_model} effort={settings.openai_agent_reasoning_effort!r}")
    session_id = f"CLI-{uuid.uuid4().hex[:8]}"
    for message in args.messages:
        if not args.conversation:
            session_id = f"CLI-{uuid.uuid4().hex[:8]}"
        kiosk = KioskContext(
            organization_id=args.org,
            store_id=args.store,
            kiosk_id="KIOSK-CLI",
            session_id=session_id,
            language=language,
        )
        reply = await agent.reply(ToolContext(kiosk=kiosk, repository=repository), message)
        tools = ", ".join(
            f"{c.name}({json.dumps(c.arguments, ensure_ascii=False)})" for c in reply.tool_calls
        )
        print(f"\n> {message}")
        print(f"  answer : {reply.answer}")
        print(f"  action : {reply.action}   tools: {tools or '-'}")
        print(
            f"  timing : agent {reply.agent_ms} ms (tools {reply.tool_ms} ms, "
            f"{reply.llm_calls} LLM calls, "
            f"{reply.input_tokens}/{reply.output_tokens} tokens in/out)"
        )
    await client.close()


if __name__ == "__main__":
    asyncio.run(main())
