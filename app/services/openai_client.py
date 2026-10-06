"""The shared OpenAI client: one connection pool for STT, the agent and TTS."""

import asyncio

import httpx2
from openai import AsyncOpenAI, DefaultAsyncHttpxClient

from app.config import Settings
from app.utils.logging import get_logger
from app.utils.timing import Timer

logger = get_logger(__name__)


def create_openai_client(settings: Settings) -> AsyncOpenAI | None:
    if settings.openai_api_key is None:
        return None
    return AsyncOpenAI(
        api_key=settings.openai_api_key.get_secret_value(),
        timeout=settings.openai_timeout_seconds,
        max_retries=settings.openai_max_retries,
        http_client=DefaultAsyncHttpxClient(
            limits=httpx2.Limits(
                max_connections=100,
                max_keepalive_connections=20,
                keepalive_expiry=settings.openai_keepalive_seconds,
            )
        ),
    )


async def warm_connection(client: AsyncOpenAI, model: str) -> None:
    """Open (or refresh) a pooled HTTPS connection with a free metadata request."""
    with Timer() as timer:
        try:
            async with asyncio.timeout(5):
                await client.models.retrieve(model)
        except Exception as exc:  # warm-up is best effort
            logger.warning("openai_warmup_failed", extra={"error": type(exc).__name__})
            return
    logger.info("openai_warmup", extra={"warmup_ms": timer.ms})


async def keep_warm(client: AsyncOpenAI, model: str, interval_seconds: float) -> None:
    """Background loop: refresh the connection before it goes idle for too long."""
    while True:
        await asyncio.sleep(interval_seconds)
        await warm_connection(client, model)
