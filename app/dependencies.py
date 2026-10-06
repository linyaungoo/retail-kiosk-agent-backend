"""FastAPI dependencies shared across routers."""

import hmac
from typing import Annotated

from fastapi import Depends, Header, Request

from app.config import Settings, get_settings
from app.data.repository import BusinessRepository
from app.errors import AppError, ErrorCode
from app.models.kiosk import KioskPrincipal
from app.realtime.service import RealtimeService
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


def get_realtime(request: Request) -> RealtimeService:
    service: RealtimeService | None = getattr(request.app.state, "realtime", None)
    if service is None:
        raise AppError(ErrorCode.REALTIME_NOT_CONFIGURED, "Realtime voice is not available.", 503)
    return service


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
RealtimeDep = Annotated[RealtimeService, Depends(get_realtime)]
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
) -> KioskPrincipal:
    """Check X-Kiosk-Key when KIOSK_AUTH_ENABLED=true (fails closed if no keys are set)
    and return which kiosk the key is bound to, if any."""
    if not settings.kiosk_auth_enabled:
        return KioskPrincipal()
    bindings = settings.kiosk_key_bindings()
    if not bindings:
        logger.error("kiosk_auth_misconfigured")
    matched: KioskPrincipal | None = None
    if x_kiosk_key:
        for key, kiosk_id in bindings.items():
            # Constant-time compare against every key (no early exit on a match).
            if hmac.compare_digest(x_kiosk_key.encode(), key.encode()):
                matched = KioskPrincipal(kiosk_id=kiosk_id)
    if matched is None:
        raise AppError(ErrorCode.UNAUTHORIZED, "Invalid or missing kiosk key.", 401)
    return matched


KioskPrincipalDep = Annotated[KioskPrincipal, Depends(verify_kiosk_key)]
