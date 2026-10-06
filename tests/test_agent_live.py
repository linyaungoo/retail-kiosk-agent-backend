"""Live agent behaviour tests (real OpenAI calls, costs tokens).

Run with:  RUN_LIVE_TESTS=1 pytest -m live
"""

import os
import uuid
from collections.abc import AsyncIterator

import pytest
from openai import AsyncOpenAI

from app.config import get_settings
from app.data.repository import InMemoryBusinessRepository
from app.models.kiosk import KioskContext, Language
from app.models.tools import ToolAction
from app.services.agent_service import AgentReply, AgentService
from app.services.session_store import InMemorySessionStore
from app.tools.context import ToolContext

pytestmark = [
    pytest.mark.live,
    pytest.mark.skipif(os.getenv("RUN_LIVE_TESTS") != "1", reason="set RUN_LIVE_TESTS=1"),
]


@pytest.fixture
async def agent() -> AsyncIterator[AgentService]:
    settings = get_settings()
    if not settings.openai_api_key or not settings.openai_agent_model:
        pytest.skip("OPENAI_API_KEY / OPENAI_AGENT_MODEL not set")
    client = AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value())
    sessions = InMemorySessionStore(ttl_seconds=600, max_turns=6)
    yield AgentService(client=client, settings=settings, sessions=sessions)
    await client.close()


async def _ask(
    agent: AgentService,
    repository: InMemoryBusinessRepository,
    message: str,
    *,
    language: Language = "my-MM",
    session_id: str | None = None,
) -> AgentReply:
    kiosk = KioskContext(
        organization_id="ORG-001",
        store_id="STORE-001",
        kiosk_id="KIOSK-TEST",
        session_id=session_id or f"LIVE-{uuid.uuid4().hex[:8]}",
        language=language,
    )
    return await agent.reply(ToolContext(kiosk=kiosk, repository=repository), message)


def _tools(reply: AgentReply) -> list[str]:
    return [call.name for call in reply.tool_calls]


async def test_product_question_uses_product_tool(
    agent: AgentService, repository: InMemoryBusinessRepository
) -> None:
    reply = await _ask(agent, repository, "Coca Cola ဘယ်မှာရှိလဲ")
    assert "search_product" in _tools(reply)
    assert reply.action is ToolAction.PRODUCT_LOCATION
    assert "A03" in reply.answer


async def test_bmi_missing_weight_asks_for_weight(
    agent: AgentService, repository: InMemoryBusinessRepository
) -> None:
    session = f"LIVE-{uuid.uuid4().hex[:8]}"
    first = await _ask(agent, repository, "BMI တွက်ပေးပါ အရပ် 170 cm ပါ", session_id=session)
    assert first.action is ToolAction.NEED_MORE_INFORMATION
    assert first.data["missing"] == ["weight"]

    second = await _ask(agent, repository, "70 kg", session_id=session)
    assert second.action is ToolAction.BMI
    assert second.data["bmi"] == 24.2


async def test_store_question_uses_store_info(
    agent: AgentService, repository: InMemoryBusinessRepository
) -> None:
    reply = await _ask(agent, repository, "What time do you close?", language="en-US")
    assert "get_store_info" in _tools(reply)
    assert reply.action is ToolAction.STORE_INFO
    assert "9" in reply.answer


async def test_faq_question_uses_faq_data(
    agent: AgentService, repository: InMemoryBusinessRepository
) -> None:
    reply = await _ask(agent, repository, "What is your return policy?", language="en-US")
    assert "search_faq" in _tools(reply)
    assert reply.action is ToolAction.FAQ
    assert "7" in reply.answer
