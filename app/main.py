"""FastAPI application entry point.

Run locally:
    uvicorn app.main:app --reload
"""

import asyncio
import re
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from agents import set_tracing_disabled
from fastapi import FastAPI
from openai import AsyncOpenAI
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.api import admin, agent, health, stt, tts, voice
from app.config import Settings, TTSProviderName, get_settings
from app.data.csv_source import CsvDataSource
from app.data.repository import InMemoryBusinessRepository
from app.data.sources import BusinessDataSource
from app.errors import register_error_handlers
from app.middleware import BodySizeLimitMiddleware
from app.models.kiosk import Language
from app.services.agent_service import AgentService
from app.services.cache_service import BusinessDataCache
from app.services.google_sheets_service import GoogleSheetsDataSource, GoogleTokenProvider
from app.services.openai_client import create_openai_client, keep_warm, warm_connection
from app.services.openai_stt_service import OpenAISpeechToText
from app.services.openai_tts_service import OpenAITextToSpeech
from app.services.session_store import InMemorySessionStore
from app.services.stt_service import MockSpeechToText, SpeechToTextService
from app.services.tts_service import (
    CachingTTS,
    LanguageRoutedTTS,
    MockTextToSpeech,
    TextToSpeechService,
)
from app.utils.logging import configure_logging, get_logger, request_id_var
from app.utils.timing import Timer

logger = get_logger(__name__)

_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class RequestContextMiddleware:
    """Assign a request_id, log one line per request with total_ms.

    Pure ASGI (not BaseHTTPMiddleware) so streamed responses such as TTS audio
    are not buffered.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        incoming = dict(scope["headers"]).get(b"x-request-id", b"").decode("latin-1")
        request_id = incoming if _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        token = request_id_var.set(request_id)
        status_code = 500

        async def send_wrapper(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode("latin-1")))
                message["headers"] = headers
            await send(message)

        timer = Timer()
        try:
            with timer:
                await self.app(scope, receive, send_wrapper)
        finally:
            logger.info(
                "request_completed",
                extra={
                    "method": scope["method"],
                    "path": scope["path"],
                    "status_code": status_code,
                    "total_ms": timer.ms,
                },
            )
            request_id_var.reset(token)


def create_data_source(settings: Settings, http_client: httpx.AsyncClient) -> BusinessDataSource:
    if settings.business_data_source == "google_sheets":
        return GoogleSheetsDataSource(
            spreadsheet_id=settings.google_sheets_id,
            tokens=GoogleTokenProvider(settings.google_service_account_file),
            http_client=http_client,
            timeout_seconds=settings.google_sheets_timeout_seconds,
        )
    return CsvDataSource(settings.mock_data_dir)


def create_stt(settings: Settings, client: AsyncOpenAI | None) -> SpeechToTextService | None:
    if settings.stt_provider == "mock":
        return MockSpeechToText()
    if client is None or not settings.openai_stt_model:
        logger.warning("stt_disabled", extra={"reason": "OPENAI_API_KEY or model not set"})
        return None
    return OpenAISpeechToText(
        client,
        model=settings.openai_stt_model,
        request_timeout=settings.openai_stt_timeout_seconds,
        temperature=settings.openai_stt_temperature,
    )


def _tts_providers(settings: Settings) -> dict[Language, TTSProviderName]:
    return {
        "my-MM": settings.tts_provider_my_mm or settings.tts_provider,
        "en-US": settings.tts_provider_en_us or settings.tts_provider,
    }


def create_tts(settings: Settings, client: AsyncOpenAI | None) -> TextToSpeechService | None:
    """Build the per-language TTS router. Returns None (TTS disabled) if any language's
    provider isn't configured."""
    instances: dict[TTSProviderName, TextToSpeechService] = {}
    routes: dict[Language, TextToSpeechService] = {}
    for language, name in _tts_providers(settings).items():
        if name not in instances:
            if name == "mock":
                instances[name] = MockTextToSpeech()
            elif client is not None and settings.openai_tts_model:
                instances[name] = OpenAITextToSpeech(
                    client,
                    model=settings.openai_tts_model,
                    voice=settings.openai_tts_voice,
                    audio_format=settings.openai_tts_format,
                    speed=settings.openai_tts_speed,
                    request_timeout=settings.openai_tts_timeout_seconds,
                    first_byte_timeout=settings.tts_first_byte_timeout_seconds,
                    attempts=settings.tts_attempts,
                )
            else:
                logger.warning(
                    "tts_disabled",
                    extra={"language": language, "reason": "OPENAI_API_KEY or model not set"},
                )
                return None
        routes[language] = instances[name]
    router = LanguageRoutedTTS(routes)
    if settings.tts_cache_entries > 0:
        return CachingTTS(router, max_entries=settings.tts_cache_entries)
    return router


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Create shared resources once; every request reuses them."""
    settings = get_settings()
    set_tracing_disabled(not settings.openai_agents_tracing)

    # One pooled HTTP client for our own outbound calls (Google Sheets, ...).
    http_client = httpx.AsyncClient(timeout=settings.google_sheets_timeout_seconds)
    cache = BusinessDataCache(
        create_data_source(settings, http_client),
        ttl_seconds=settings.cache_ttl_seconds,
        retry_seconds=settings.cache_retry_seconds,
    )
    await cache.start()
    app.state.cache = cache
    app.state.repository = InMemoryBusinessRepository(cache.get)

    # One OpenAI client (one connection pool) shared by STT, the agent and TTS.
    client = create_openai_client(settings)
    background: list[asyncio.Task[None]] = []
    if client is not None:
        probe_model = settings.openai_agent_model or settings.openai_stt_model or "gpt-4o-mini"
        if settings.openai_warmup:
            background.append(asyncio.create_task(warm_connection(client, probe_model)))
        if settings.openai_keepwarm_interval_seconds > 0:
            background.append(
                asyncio.create_task(
                    keep_warm(client, probe_model, settings.openai_keepwarm_interval_seconds)
                )
            )

    app.state.agent_service = None
    if client is not None and settings.openai_agent_model:
        sessions = InMemorySessionStore(
            ttl_seconds=settings.session_ttl_seconds, max_turns=settings.session_max_turns
        )
        app.state.agent_service = AgentService(client=client, settings=settings, sessions=sessions)
    else:
        logger.warning("agent_disabled", extra={"reason": "OPENAI_API_KEY or model not set"})

    app.state.stt = create_stt(settings, client)
    app.state.tts = create_tts(settings, client)

    logger.info(
        "startup",
        extra={
            "app_env": settings.app_env,
            "agent_model": settings.openai_agent_model,
            "stt": app.state.stt.name if app.state.stt is not None else None,
            "tts": _tts_providers(settings) if app.state.tts is not None else None,
            "data_source": cache.source_name,
            "data_loaded": cache.status()["loaded"],
        },
    )
    yield
    for task in background:
        task.cancel()
    await asyncio.gather(*background, return_exceptions=True)
    await cache.close()
    await http_client.aclose()
    if client is not None:
        await client.close()
    logger.info("shutdown")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)

    # Interactive API docs only outside production; they'd publish the whole API surface.
    docs = settings.app_env != "production"
    app = FastAPI(
        title="Retail Kiosk Backend",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
    )
    # Added first = innermost; RequestContext stays outermost so 413s are logged too.
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_audio_bytes + 1_048_576)
    app.add_middleware(RequestContextMiddleware)
    register_error_handlers(app)

    app.include_router(health.router)
    app.include_router(stt.router)
    app.include_router(agent.router)
    app.include_router(tts.router)
    app.include_router(voice.router)
    app.include_router(admin.router)
    return app


app = create_app()
