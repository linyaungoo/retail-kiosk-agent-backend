"""Realtime calls: creation, the server-side sideband connection, lifecycle, metrics.

Per call:
  kiosk SDP offer -> calls.create (permanent key, server-built session) -> SDP answer
  sideband WebSocket (wss://.../realtime?call_id=...) -> tool calls, metrics, limits
  end: customer/kiosk ends, idle timeout, max duration, replaced by a new session for
       the same kiosk, tampering, or shutdown -> calls.hangup
"""

import asyncio
import contextlib
import time
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass, field
from typing import Any, Protocol

import openai
from openai import AsyncOpenAI
from websockets.exceptions import ConnectionClosed

from app.config import Settings
from app.data.repository import BusinessRepository
from app.errors import AppError, ErrorCode
from app.models.kiosk import KioskContext
from app.realtime.session_config import build_session_config
from app.realtime.tools import ToolExecution, execute_tool
from app.tools.context import ToolContext
from app.utils.logging import get_logger
from app.utils.timing import Timer

logger = get_logger(__name__)

VocabularyProvider = Callable[[], Awaitable[Sequence[str]]]

# Server signals that the assistant's audio for a turn has started (WebRTC sends the
# audio itself over the media track, so the sideband sees these instead).
_AUDIO_STARTED = frozenset(
    {
        "output_audio_buffer.started",
        "response.output_audio.delta",
        "response.output_audio_transcript.delta",
    }
)
_CONNECTION_CLOSED = (ConnectionClosed, openai.WebSocketConnectionClosedError)


class RealtimeConnection(Protocol):
    """The part of openai's AsyncRealtimeConnection the sideband uses (fakeable in tests)."""

    async def recv(self) -> Any: ...

    async def send(self, event: Any) -> None: ...

    async def close(self) -> None: ...


@dataclass(slots=True)
class TurnMetrics:
    speech_started: float | None = None
    speech_stopped: float | None = None
    response_created: float | None = None
    first_audio: float | None = None
    tool_ms: int = 0
    tools: list[str] = field(default_factory=list)


@dataclass(slots=True)
class CallMetrics:
    connection_ms: int = 0  # calls.create round trip (kiosk -> backend -> OpenAI)
    sideband_connect_ms: int | None = None
    first_event_ms: int | None = None  # call created -> first sideband event
    turns: int = 0
    interrupts: int = 0
    tool_calls: int = 0
    tool_errors: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    client: dict[str, Any] = field(default_factory=dict)  # reported by the kiosk


@dataclass(slots=True)
class RealtimeCall:
    call_id: str
    kiosk: KioskContext
    started: float
    last_activity: float
    metrics: CallMetrics = field(default_factory=CallMetrics)
    task: asyncio.Task[None] | None = None
    end_reason: str | None = None
    finished: bool = False
    expected_instructions: str = ""
    expected_tools: frozenset[str] = frozenset()


class RealtimeService:
    def __init__(
        self,
        client: AsyncOpenAI,
        settings: Settings,
        repository: BusinessRepository,
        vocabulary: VocabularyProvider,
        *,
        clock: Callable[[], float] = time.monotonic,
        connect: Callable[[str], Awaitable[RealtimeConnection]] | None = None,
    ) -> None:
        self._client = client
        self._settings = settings
        self._repository = repository
        self._vocabulary = vocabulary
        self._clock = clock
        self._connect = connect or self._connect_sideband
        self._calls: dict[str, RealtimeCall] = {}
        self._call_by_kiosk: dict[str, str] = {}
        self._allowed_tools = settings.realtime_tool_names()

    # ------------------------------------------------------------------ public API

    @property
    def model(self) -> str:
        return self._settings.openai_realtime_model

    def active_calls(self) -> int:
        return len(self._calls)

    def get_call(self, call_id: str) -> RealtimeCall | None:
        return self._calls.get(call_id)

    async def start_call(self, kiosk: KioskContext, sdp_offer: str) -> tuple[RealtimeCall, str]:
        """Create the OpenAI call for this kiosk; returns (call, SDP answer)."""
        # One live conversation per kiosk: a new session means a new customer.
        previous = self._call_by_kiosk.get(kiosk.kiosk_id)
        if previous is not None:
            await self.end_call(previous, reason="replaced")
        if len(self._calls) >= self._settings.realtime_max_concurrent_calls:
            raise AppError(
                ErrorCode.TOO_MANY_SESSIONS, "Too many voice sessions; try again shortly.", 429
            )

        config = build_session_config(
            self._settings, kiosk.language, vocabulary=await self._vocabulary()
        )
        timer = Timer()
        try:
            with timer:
                async with asyncio.timeout(self._settings.realtime_connect_timeout_seconds):
                    raw = await self._client.realtime.calls.with_raw_response.create(
                        sdp=sdp_offer,
                        session=config,  # type: ignore[arg-type]
                    )
        except (TimeoutError, openai.APITimeoutError) as exc:
            logger.warning("realtime_call_timeout", extra={"kiosk_id": kiosk.kiosk_id})
            raise AppError(
                ErrorCode.REALTIME_TIMEOUT, "Starting the voice session took too long.", 504
            ) from exc
        except openai.APIError as exc:
            logger.error(
                "realtime_call_failed",
                extra={
                    "kiosk_id": kiosk.kiosk_id,
                    "error": type(exc).__name__,
                    "detail": str(exc)[:300],
                },
            )
            raise AppError(
                ErrorCode.REALTIME_FAILED, "Could not start the voice session.", 502
            ) from exc

        call_id = raw.headers.get("location", "").rstrip("/").rsplit("/", 1)[-1]
        answer = raw.parse().text
        if not call_id or not answer.startswith("v="):
            logger.error("realtime_call_bad_response", extra={"kiosk_id": kiosk.kiosk_id})
            raise AppError(ErrorCode.REALTIME_FAILED, "Could not start the voice session.", 502)

        now = self._clock()
        call = RealtimeCall(
            call_id=call_id,
            kiosk=kiosk,
            started=now,
            last_activity=now,
            expected_instructions=config["instructions"],
            expected_tools=frozenset(t["name"] for t in config.get("tools", [])),
        )
        call.metrics.connection_ms = timer.ms
        self._calls[call_id] = call
        self._call_by_kiosk[kiosk.kiosk_id] = call_id
        call.task = asyncio.create_task(self._run_sideband(call), name=f"realtime:{call_id}")
        logger.info(
            "realtime_call_started",
            extra={
                **self._ids(call),
                "model": config["model"],
                "voice": self._settings.openai_realtime_voice,
                "tools": sorted(call.expected_tools),
                "realtime_connection_ms": timer.ms,
            },
        )
        return call, answer

    async def end_call(self, call_id: str, *, reason: str) -> None:
        call = self._calls.get(call_id)
        if call is None:
            return
        call.end_reason = call.end_reason or reason
        task = call.task
        if task is not None and not task.done() and task is not asyncio.current_task():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await self._finish(call, reason)

    def record_client_metrics(self, call: RealtimeCall, metrics: dict[str, Any]) -> None:
        call.metrics.client.update(metrics)
        logger.info("realtime_client_metrics", extra={**self._ids(call), **metrics})

    async def shutdown(self) -> None:
        await asyncio.gather(
            *(self.end_call(call_id, reason="shutdown") for call_id in list(self._calls)),
            return_exceptions=True,
        )

    # ------------------------------------------------------------------ sideband

    async def _connect_sideband(self, call_id: str) -> RealtimeConnection:
        connection: RealtimeConnection = await self._client.realtime.connect(
            call_id=call_id
        ).enter()
        return connection

    async def _run_sideband(self, call: RealtimeCall) -> None:
        reason = "closed"
        try:
            with Timer() as timer:
                async with asyncio.timeout(self._settings.realtime_connect_timeout_seconds):
                    connection = await self._connect(call.call_id)
            call.metrics.sideband_connect_ms = timer.ms
            try:
                reason = await self._event_loop(call, connection)
            finally:
                with contextlib.suppress(Exception):  # closing a dead socket may fail
                    await connection.close()
        except asyncio.CancelledError:
            reason = call.end_reason or "cancelled"
            raise
        except Exception as exc:
            logger.error(
                "realtime_sideband_failed", extra={**self._ids(call), "error": type(exc).__name__}
            )
            reason = "sideband_error"
        finally:
            await self._finish(call, call.end_reason or reason)

    def _deadline(self, call: RealtimeCall) -> tuple[float, str]:
        idle = call.last_activity + self._settings.realtime_idle_timeout_seconds
        hard = call.started + self._settings.realtime_max_session_seconds
        return (idle, "idle_timeout") if idle <= hard else (hard, "max_duration")

    async def _event_loop(self, call: RealtimeCall, connection: RealtimeConnection) -> str:
        state = _LoopState()
        while True:
            deadline, reason = self._deadline(call)
            remaining = deadline - self._clock()
            if remaining <= 0:
                return reason
            try:
                async with asyncio.timeout(remaining):
                    event = await connection.recv()
            except TimeoutError:
                continue  # the deadline check above decides
            except _CONNECTION_CLOSED:
                return "remote_closed"
            end = await self._handle(call, connection, event, state)
            if end is not None:
                return end

    async def _handle(
        self, call: RealtimeCall, connection: RealtimeConnection, event: Any, state: "_LoopState"
    ) -> str | None:
        now = self._clock()
        kind = getattr(event, "type", "")
        if call.metrics.first_event_ms is None:
            call.metrics.first_event_ms = round((now - call.started) * 1000)

        if kind == "input_audio_buffer.speech_started":
            call.last_activity = now
            if state.responding or state.speaking:
                call.metrics.interrupts += 1  # customer talked over the assistant
            state.turn = TurnMetrics(speech_started=now)
        elif kind == "input_audio_buffer.speech_stopped":
            call.last_activity = now
            state.turn.speech_stopped = now
        elif kind == "response.created":
            call.last_activity = now
            state.responding = True
            if state.turn.response_created is None:
                state.turn.response_created = now
        elif kind in _AUDIO_STARTED:
            if kind == "output_audio_buffer.started":
                state.speaking = True
            if state.turn.first_audio is None:
                state.turn.first_audio = now
        elif kind in ("output_audio_buffer.stopped", "output_audio_buffer.cleared"):
            state.speaking = False
            call.last_activity = now  # idle time counts from the end of the answer
        elif kind == "conversation.item.input_audio_transcription.completed":
            if self._settings.log_transcripts:
                logger.info(
                    "realtime_transcript",
                    extra={**self._ids(call), "customer_text": getattr(event, "transcript", "")},
                )
        elif kind == "response.done":
            call.last_activity = now
            state.responding = False
            await self._response_done(call, connection, event, state)
        elif kind == "session.updated":
            return self._check_session(call, event)
        elif kind == "error":
            error = getattr(event, "error", None)
            logger.warning(
                "realtime_error_event",
                extra={
                    **self._ids(call),
                    "error_type": getattr(error, "type", None),
                    "error_code": getattr(error, "code", None),
                    "error_message": str(getattr(error, "message", ""))[:300],
                },
            )
        return None

    async def _response_done(
        self, call: RealtimeCall, connection: RealtimeConnection, event: Any, state: "_LoopState"
    ) -> None:
        response = getattr(event, "response", None)
        usage = getattr(response, "usage", None)
        if usage is not None:
            call.metrics.input_tokens += getattr(usage, "input_tokens", 0) or 0
            call.metrics.output_tokens += getattr(usage, "output_tokens", 0) or 0

        status = getattr(response, "status", None)
        function_calls = [
            item
            for item in (getattr(response, "output", None) or [])
            if getattr(item, "type", None) == "function_call"
        ]
        if function_calls and status == "completed":
            await self._run_tools(call, connection, function_calls, state)
            return  # the turn continues with the model's spoken answer

        if status == "completed":
            call.metrics.turns += 1
        self._log_turn(call, state.turn, status=status, response=response)
        state.turn = TurnMetrics()

    async def _run_tools(
        self,
        call: RealtimeCall,
        connection: RealtimeConnection,
        function_calls: list[Any],
        state: "_LoopState",
    ) -> None:
        tools = ToolContext(kiosk=call.kiosk, repository=self._repository)
        with Timer() as timer:
            executions: list[ToolExecution] = await asyncio.gather(
                *(
                    execute_tool(
                        str(item.name),
                        str(item.arguments or "{}"),
                        str(item.call_id),
                        tools,
                        allowed=self._allowed_tools,
                    )
                    for item in function_calls
                )
            )
            for item, execution in zip(function_calls, executions, strict=True):
                await connection.send(
                    {
                        "type": "conversation.item.create",
                        "item": {
                            "type": "function_call_output",
                            "call_id": item.call_id,
                            "output": execution.output,
                        },
                    }
                )
            await connection.send({"type": "response.create"})
        state.turn.tool_ms += timer.ms
        for item, execution in zip(function_calls, executions, strict=True):
            record = execution.record
            failed = record is None or record.error is not None
            call.metrics.tool_calls += 1
            call.metrics.tool_errors += int(failed)
            state.turn.tools.append(execution.name)
            extra: dict[str, Any] = {
                **self._ids(call),
                "tool": execution.name,
                "tool_backend_ms": execution.duration_ms,
                "tool_call_ms": timer.ms,  # all tools + sending results back
                "tool_action": record.action if record else None,
                "tool_error": (record.error if record else "rejected"),
            }
            if self._settings.log_transcripts:
                extra["tool_arguments"] = str(item.arguments)[:500]
            logger.info("realtime_tool", extra=extra)

    def _check_session(self, call: RealtimeCall, event: Any) -> str | None:
        """The backend never updates the session after creating it, so any change of
        instructions or tools came from the client: end the call."""
        session = getattr(event, "session", None)
        instructions = getattr(session, "instructions", None)
        tools = frozenset(
            getattr(tool, "name", "") for tool in (getattr(session, "tools", None) or [])
        )
        if (instructions is not None and instructions != call.expected_instructions) or (
            tools and tools != call.expected_tools
        ):
            logger.warning("realtime_session_tampered", extra=self._ids(call))
            return "session_tampered"
        return None

    def _log_turn(
        self, call: RealtimeCall, turn: TurnMetrics, *, status: Any, response: Any
    ) -> None:
        def ms(start: float | None, end: float | None) -> int | None:
            return round((end - start) * 1000) if start is not None and end is not None else None

        now = self._clock()
        extra: dict[str, Any] = {
            **self._ids(call),
            "status": status,
            "tools": turn.tools,
            "tool_ms": turn.tool_ms,
            # speech end -> first sign of assistant audio on the server side
            "time_to_first_audio_ms": ms(turn.speech_stopped, turn.first_audio),
            "time_to_first_model_event_ms": ms(turn.speech_stopped, turn.response_created),
            "conversation_turn_ms": ms(turn.speech_stopped, now),
        }
        if self._settings.log_transcripts:
            extra["answer_text"] = _response_transcript(response)
        logger.info("realtime_turn", extra=extra)

    # ------------------------------------------------------------------ teardown

    async def _finish(self, call: RealtimeCall, reason: str) -> None:
        if call.finished:
            return
        call.finished = True
        call.end_reason = call.end_reason or reason
        self._calls.pop(call.call_id, None)
        if self._call_by_kiosk.get(call.kiosk.kiosk_id) == call.call_id:
            del self._call_by_kiosk[call.kiosk.kiosk_id]
        try:
            async with asyncio.timeout(5):
                await self._client.realtime.calls.hangup(call.call_id)
        except Exception as exc:  # already gone on OpenAI's side is fine
            logger.debug("realtime_hangup_failed", extra={"error": type(exc).__name__})
        metrics = call.metrics
        logger.info(
            "realtime_call_ended",
            extra={
                **self._ids(call),
                "end_reason": call.end_reason,
                "session_duration_seconds": round(self._clock() - call.started, 1),
                "realtime_connection_ms": metrics.connection_ms,
                "sideband_connect_ms": metrics.sideband_connect_ms,
                "time_to_first_model_event_ms": metrics.first_event_ms,
                "turns": metrics.turns,
                "interrupt_count": metrics.interrupts,
                "tool_calls": metrics.tool_calls,
                "tool_errors": metrics.tool_errors,
                "input_tokens": metrics.input_tokens,
                "output_tokens": metrics.output_tokens,
                **{f"client_{k}": v for k, v in metrics.client.items()},
            },
        )

    @staticmethod
    def _ids(call: RealtimeCall) -> dict[str, Any]:
        return {
            "call_id": call.call_id,
            "session_id": call.kiosk.session_id,
            "kiosk_id": call.kiosk.kiosk_id,
            "store_id": call.kiosk.store_id,
            "language": call.kiosk.language,
        }


@dataclass(slots=True)
class _LoopState:
    turn: TurnMetrics = field(default_factory=TurnMetrics)
    responding: bool = False
    speaking: bool = False


def _response_transcript(response: Any) -> str:
    parts: list[str] = []
    for item in getattr(response, "output", None) or []:
        for content in getattr(item, "content", None) or []:
            text = getattr(content, "transcript", None) or getattr(content, "text", None)
            if text:
                parts.append(str(text))
    return " ".join(parts)[:1000]
