"""Retail Store Assistant: one agent, four tools, short per-session memory."""

import asyncio
from dataclasses import dataclass
from typing import Any, cast

import openai
from agents import (
    Agent,
    AgentsException,
    FunctionToolResult,
    MaxTurnsExceeded,
    ModelSettings,
    ModelTimeoutError,
    OpenAIResponsesModel,
    RunConfig,
    RunContextWrapper,
    Runner,
    ToolsToFinalOutputResult,
    TResponseInputItem,
)
from openai import AsyncOpenAI
from openai.types.shared import Reasoning

from app.config import Settings
from app.errors import AppError, ErrorCode
from app.models.kiosk import Language
from app.models.tools import ToolAction
from app.services.agent_instructions import instructions_for
from app.services.agent_tools import AGENT_TOOLS, AgentRunContext, ToolCallRecord
from app.services.answer_templates import render_answer
from app.services.session_store import SessionStore
from app.tools.context import ToolContext
from app.utils.logging import get_logger
from app.utils.timing import Timer

logger = get_logger(__name__)

AGENT_NAME = "Retail Store Assistant"

FALLBACK_ANSWERS: dict[Language, str] = {
    "my-MM": "တောင်းပန်ပါတယ်၊ နောက်တစ်ခေါက် ပြန်ပြောပေးပါ။",
    "en-US": "Sorry, could you please say that again?",
}


@dataclass(frozen=True, slots=True)
class AgentReply:
    answer: str
    action: ToolAction
    data: dict[str, Any]
    tool_calls: list[ToolCallRecord]
    agent_ms: int
    tool_ms: int
    llm_calls: int
    input_tokens: int
    output_tokens: int
    cached_tokens: int = 0
    templated: bool = False  # answer rendered in Python, second LLM call skipped


_NOT_FINAL = ToolsToFinalOutputResult(is_final_output=False, final_output=None)


def finish_with_template(
    wrapper: RunContextWrapper[AgentRunContext], results: list[FunctionToolResult]
) -> ToolsToFinalOutputResult:
    """Agents SDK tool_use_behavior: after a single, templatable tool call, the answer
    is rendered in Python and the run ends, skipping the LLM's rephrasing call."""
    context = wrapper.context
    if len(results) != 1 or not context.calls:
        return _NOT_FINAL
    record = context.calls[-1]
    answer = render_answer(record, context.tools.kiosk.language)
    if answer is None:
        return _NOT_FINAL
    context.templated = True
    return ToolsToFinalOutputResult(is_final_output=True, final_output=answer)


def resolve_outcome(calls: list[ToolCallRecord]) -> tuple[ToolAction, dict[str, Any]]:
    """Action and data from the last successful tool call, never from the model's text."""
    for call in reversed(calls):
        if call.action is not None:
            return call.action, call.data
    if any(call.error == "tool_failed" for call in calls):
        return ToolAction.ERROR, {}
    return ToolAction.GENERAL, {}


def _instructions(
    wrapper: RunContextWrapper[AgentRunContext], agent: Agent[AgentRunContext]
) -> str:
    return instructions_for(wrapper.context.tools.kiosk.language)


def _model_settings(settings: Settings) -> ModelSettings:
    effort = settings.openai_agent_reasoning_effort.strip().lower()
    return ModelSettings(
        max_tokens=settings.openai_agent_max_output_tokens,
        # History is resent by us each turn; nothing needs to be stored at OpenAI.
        store=False,
        reasoning=Reasoning(effort=effort) if effort else None,
        # With store=False, reasoning items must travel encrypted between tool turns.
        response_include=["reasoning.encrypted_content"] if effort and effort != "none" else None,
    )


def _history_items(history: list[tuple[str, str]], message: str) -> list[TResponseInputItem]:
    items: list[dict[str, str]] = []
    for user, assistant in history:
        items.append({"role": "user", "content": user})
        items.append({"role": "assistant", "content": assistant})
    items.append({"role": "user", "content": message})
    return cast(list[TResponseInputItem], items)


class AgentService:
    def __init__(self, *, client: AsyncOpenAI, settings: Settings, sessions: SessionStore) -> None:
        self._model_name = settings.openai_agent_model
        self._agent = Agent[AgentRunContext](
            name=AGENT_NAME,
            instructions=_instructions,
            model=OpenAIResponsesModel(
                model=settings.openai_agent_model,
                openai_client=client.with_options(
                    timeout=settings.openai_agent_request_timeout_seconds
                ),
            ),
            model_settings=_model_settings(settings),
            tools=list(AGENT_TOOLS),
            tool_use_behavior=(
                finish_with_template if settings.agent_template_answers else "run_llm_again"
            ),
        )
        self._run_config = RunConfig(
            tracing_disabled=not settings.openai_agents_tracing,
            trace_include_sensitive_data=False,
            workflow_name="retail-kiosk-agent",
        )
        self._sessions = sessions
        self._timeout = settings.agent_timeout_seconds
        self._max_turns = settings.agent_max_turns
        self._log_transcripts = settings.log_transcripts

    @property
    def model_name(self) -> str:
        return self._model_name

    async def reply(self, tools: ToolContext, message: str) -> AgentReply:
        kiosk = tools.kiosk
        session_key = f"{kiosk.kiosk_id}:{kiosk.session_id}"
        history = await self._sessions.get_history(session_key)
        run_context = AgentRunContext(tools=tools)
        log_ids = {
            "session_id": kiosk.session_id,
            "kiosk_id": kiosk.kiosk_id,
            "store_id": kiosk.store_id,
            "language": kiosk.language,
        }

        timer = Timer()
        try:
            with timer:
                async with asyncio.timeout(self._timeout):
                    result = await Runner.run(
                        self._agent,
                        _history_items(history, message),
                        context=run_context,
                        max_turns=self._max_turns,
                        run_config=self._run_config,
                    )
        except (TimeoutError, ModelTimeoutError, openai.APITimeoutError) as exc:
            logger.warning(
                "agent_timeout",
                extra={**log_ids, "agent_ms": timer.ms, "error": type(exc).__name__},
            )
            raise AppError(
                ErrorCode.AGENT_TIMEOUT, "The assistant took too long to respond.", 504
            ) from exc
        except (MaxTurnsExceeded, AgentsException, openai.APIError) as exc:
            logger.error(
                "agent_failed", extra={**log_ids, "agent_ms": timer.ms, "error": type(exc).__name__}
            )
            raise AppError(ErrorCode.AGENT_FAILED, "The assistant is unavailable.", 502) from exc

        answer = str(result.final_output or "").strip()
        if answer:
            await self._sessions.append_turn(session_key, message, answer)
        else:
            answer = FALLBACK_ANSWERS[kiosk.language]

        action, data = resolve_outcome(run_context.calls)
        usage = result.context_wrapper.usage
        reply = AgentReply(
            answer=answer,
            action=action,
            data=data,
            tool_calls=run_context.calls,
            agent_ms=timer.ms,
            tool_ms=sum(call.duration_ms for call in run_context.calls),
            llm_calls=usage.requests,
            input_tokens=usage.input_tokens,
            output_tokens=usage.output_tokens,
            cached_tokens=usage.input_tokens_details.cached_tokens,
            templated=run_context.templated,
        )

        extra: dict[str, Any] = {
            **log_ids,
            "model": self._model_name,
            "action": action,
            "tools": [call.name for call in run_context.calls],
            "agent_ms": reply.agent_ms,
            "tool_ms": reply.tool_ms,
            "llm_calls": reply.llm_calls,
            "input_tokens": reply.input_tokens,
            "output_tokens": reply.output_tokens,
            "cached_tokens": reply.cached_tokens,
            "templated": reply.templated,
            "history_turns": len(history),
        }
        if self._log_transcripts:
            # Not "message": that key is reserved by logging.LogRecord.
            extra |= {"customer_text": message, "answer_text": answer}
        logger.info("agent_completed", extra=extra)
        return reply
