"""FastAPI dependencies shared across routers."""

import hmac
from typing import Annotated

from fastapi import Depends, Header, Request

from app.config import Settings, get_settings
from app.data.repository import BusinessRepository
from app.errors import AppError, ErrorCode
from app.services.agent_service import AgentService
from app.services.cache_service import BusinessDataCache
from app.services.stt_service import SpeechToTextService
from app.services.tts_service import TextToSpeechService
from app.utils.logging import get_logger

logger = get_logger(__name__)

SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_repository(request: Request) -> BusinessRepository:
    repository: BusinessRepository = request.app.state.repository
    return repository


def get_agent_service(request: Request) -> AgentService:
    service: AgentService | None = getattr(request.app.state, "agent_service", None)
    if service is None:
        raise AppError(ErrorCode.AGENT_NOT_CONFIGURED, "The assistant is not available.", 503)
    return service


def get_cache(request: Request) -> BusinessDataCache:
    cache: BusinessDataCache = request.app.state.cache
    return cache


def get_stt(request: Request) -> SpeechToTextService:
    stt: SpeechToTextService | None = getattr(request.app.state, "stt", None)
    if stt is None:
        raise AppError(ErrorCode.STT_NOT_CONFIGURED, "Speech input is not available.", 503)
    return stt


def get_tts(request: Request) -> TextToSpeechService:
    tts: TextToSpeechService | None = getattr(request.app.state, "tts", None)
    if tts is None:
        raise AppError(ErrorCode.TTS_NOT_CONFIGURED, "Speech output is not available.", 503)
    return tts


RepositoryDep = Annotated[BusinessRepository, Depends(get_repository)]
AgentServiceDep = Annotated[AgentService, Depends(get_agent_service)]
CacheDep = Annotated[BusinessDataCache, Depends(get_cache)]
STTDep = Annotated[SpeechToTextService, Depends(get_stt)]
TTSDep = Annotated[TextToSpeechService, Depends(get_tts)]


async def verify_admin_access(
    settings: SettingsDep,
    x_admin_key: Annotated[str | None, Header()] = None,
) -> None:
    """/admin/*: X-Admin-Key when ADMIN_API_KEY is set; otherwise development only."""
    if settings.admin_api_key is not None:
        expected = settings.admin_api_key.get_secret_value().encode()
        if x_admin_key and hmac.compare_digest(x_admin_key.encode(), expected):
            return
        raise AppError(ErrorCode.UNAUTHORIZED, "Invalid or missing admin key.", 401)
    if settings.app_env != "development":
        raise AppError(ErrorCode.NOT_FOUND, "Not Found", 404)


async def verify_kiosk_key(
    settings: SettingsDep,
    x_kiosk_key: Annotated[str | None, Header()] = None,
) -> None:
    """Check X-Kiosk-Key when KIOSK_AUTH_ENABLED=true. Fails closed if no keys are set."""
    if not settings.kiosk_auth_enabled:
        return
    valid_keys = settings.kiosk_key_set()
    if not valid_keys:
        logger.error("kiosk_auth_misconfigured")
    if not x_kiosk_key or not any(
        hmac.compare_digest(x_kiosk_key.encode(), key.encode()) for key in valid_keys
    ):
        raise AppError(ErrorCode.UNAUTHORIZED, "Invalid or missing kiosk key.", 401)
