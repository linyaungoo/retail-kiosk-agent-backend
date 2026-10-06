"""AgentService orchestration with Runner.run faked (no OpenAI calls)."""

import asyncio
import logging
from types import SimpleNamespace
from typing import Any

import httpx2
import openai
import pytest
from agents import Runner

from app.config import Settings
from app.errors import AppError, ErrorCode
from app.models.tools import ToolAction
from app.services.agent_service import FALLBACK_ANSWERS, AgentService
from app.services.agent_tools import AgentRunContext, ToolCallRecord
from app.services.session_store import InMemorySessionStore
from tests.conftest import MakeContext


def _service(**overrides: Any) -> AgentService:
    settings = Settings(
        _env_file=None,
        openai_api_key="sk-test",
        openai_agent_model="test-model",
        **overrides,
    )
    client = openai.AsyncOpenAI(api_key="sk-test")
    return AgentService(
        client=client,
        settings=settings,
        sessions=InMemorySessionStore(ttl_seconds=600, max_turns=6),
    )


def _fake_runner(answer: str, record: ToolCallRecord | None, inputs: list[Any]) -> Any:
    async def run(_agent: Any, input: Any, *, context: AgentRunContext, **_: Any) -> Any:
        inputs.append(input)
        if record is not None:
            context.calls.append(record)
        usage = SimpleNamespace(
            requests=2,
            input_tokens=100,
            output_tokens=20,
            input_tokens_details=SimpleNamespace(cached_tokens=0),
        )
        return SimpleNamespace(final_output=answer, context_wrapper=SimpleNamespace(usage=usage))

    return run


async def test_reply_uses_tool_outcome_and_session_history(
    make_ctx: MakeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs: list[Any] = []
    record = ToolCallRecord(
        name="calculate_bmi",
        arguments={"height_cm": 170},
        action=ToolAction.NEED_MORE_INFORMATION,
        data={"missing": ["weight"]},
    )
    monkeypatch.setattr(Runner, "run", _fake_runner("Weight please?", record, inputs))
    service = _service()
    ctx = make_ctx()

    first = await service.reply(ctx, "170 cm")
    assert first.action is ToolAction.NEED_MORE_INFORMATION
    assert first.data == {"missing": ["weight"]}
    assert first.llm_calls == 2

    await service.reply(ctx, "70 kg")
    assert inputs[1] == [
        {"role": "user", "content": "170 cm"},
        {"role": "assistant", "content": "Weight please?"},
        {"role": "user", "content": "70 kg"},
    ]


async def test_reply_without_tool_is_general(
    make_ctx: MakeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(Runner, "run", _fake_runner("Hello!", None, []))
    reply = await _service().reply(make_ctx(), "hi")
    assert reply.action is ToolAction.GENERAL and reply.answer == "Hello!"


async def test_empty_answer_uses_fallback_and_is_not_remembered(
    make_ctx: MakeContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs: list[Any] = []
    monkeypatch.setattr(Runner, "run", _fake_runner("  ", None, inputs))
    service = _service()
    reply = await service.reply(make_ctx(language="my-MM"), "...")
    assert reply.answer == FALLBACK_ANSWERS["my-MM"]
    await service.reply(make_ctx(language="my-MM"), "again")
    assert len(inputs[1]) == 1  # nothing stored from the failed turn


async def test_overall_timeout(make_ctx: MakeContext, monkeypatch: pytest.MonkeyPatch) -> None:
    async def slow(*_: Any, **__: Any) -> Any:
        await asyncio.sleep(5)

    monkeypatch.setattr(Runner, "run", slow)
    with pytest.raises(AppError) as err:
        await _service(agent_timeout_seconds=0.05).reply(make_ctx(), "hi")
    assert err.value.code == ErrorCode.AGENT_TIMEOUT and err.value.status_code == 504


@pytest.mark.parametrize(
    ("exc", "code"),
    [
        (
            openai.APITimeoutError(request=httpx2.Request("POST", "https://x")),
            ErrorCode.AGENT_TIMEOUT,
        ),
        (
            openai.APIConnectionError(request=httpx2.Request("POST", "https://x")),
            ErrorCode.AGENT_FAILED,
        ),
    ],
)
async def test_openai_errors_are_mapped(
    make_ctx: MakeContext, monkeypatch: pytest.MonkeyPatch, exc: Exception, code: ErrorCode
) -> None:
    async def fail(*_: Any, **__: Any) -> Any:
        raise exc

    monkeypatch.setattr(Runner, "run", fail)
    with pytest.raises(AppError) as err:
        await _service().reply(make_ctx(), "hi")
    assert err.value.code == code


async def test_transcript_logging_does_not_crash(
    make_ctx: MakeContext, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(Runner, "run", _fake_runner("Hello!", None, []))
    reply = await _service(log_transcripts=True).reply(make_ctx(), "hi")
    assert reply.answer == "Hello!"
    record = next(r for r in caplog.records if r.getMessage() == "agent_completed")
    assert record.customer_text == "hi" and record.answer_text == "Hello!"  # type: ignore[attr-defined]
