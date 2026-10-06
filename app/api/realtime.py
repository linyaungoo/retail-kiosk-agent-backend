"""Realtime voice sessions (WebRTC between the kiosk and OpenAI).

The kiosk sends its SDP offer here; the backend authenticates the kiosk, resolves
its trusted store, creates the OpenAI call with the permanent key and returns only
the SDP answer. Tools run server-side over the sideband; nothing secret reaches
the kiosk.
"""

from typing import Any

from fastapi import APIRouter, Depends

from app.dependencies import (
    KioskPrincipalDep,
    RealtimeDep,
    RepositoryDep,
    SettingsDep,
    verify_kiosk_key,
)
from app.errors import AppError, ErrorCode
from app.models.kiosk import KioskPrincipal, Language
from app.models.realtime import (
    RealtimeClientMetrics,
    RealtimeEndRequest,
    RealtimeSessionRequest,
    RealtimeSessionResponse,
)
from app.realtime.service import RealtimeCall, RealtimeService
from app.services.agent_instructions import GREETING_INSTRUCTIONS
from app.services.kiosk_service import resolve_kiosk


def greeting_event(language: Language) -> dict[str, Any]:
    """One spoken greeting with tools off and its own instructions (otherwise the model
    may 'greet' by looking things up, or apologise for having no tool result)."""
    return {
        "type": "response.create",
        "response": {"tool_choice": "none", "instructions": GREETING_INSTRUCTIONS[language]},
    }


router = APIRouter(
    prefix="/api/realtime", tags=["realtime"], dependencies=[Depends(verify_kiosk_key)]
)


def _own_call(
    service: RealtimeService, call_id: str, kiosk_id: str, principal: KioskPrincipal
) -> RealtimeCall:
    """A kiosk may only touch its own call; others get 'not found' (no existence leak)."""
    call = service.get_call(call_id)
    if (
        call is None
        or call.kiosk.kiosk_id != kiosk_id
        or (principal.kiosk_id is not None and principal.kiosk_id != kiosk_id)
    ):
        raise AppError(
            ErrorCode.REALTIME_SESSION_NOT_FOUND, "Voice session not found or already ended.", 404
        )
    return call


@router.post("/session", response_model=RealtimeSessionResponse)
async def create_session(
    body: RealtimeSessionRequest,
    service: RealtimeDep,
    repository: RepositoryDep,
    settings: SettingsDep,
    principal: KioskPrincipalDep,
) -> RealtimeSessionResponse:
    kiosk = await resolve_kiosk(
        repository,
        principal=principal,
        registry_required=settings.kiosk_registry_required,
        kiosk_id=body.kiosk_id,
        session_id=body.session_id,
        language=body.language,
        organization_id=body.organization_id,
        store_id=body.store_id,
    )
    call, answer = await service.start_call(kiosk, body.sdp)
    return RealtimeSessionResponse(
        session_id=kiosk.session_id,
        language=kiosk.language,
        realtime_session_id=call.call_id,
        sdp=answer,
        model=service.model,
        voice=settings.openai_realtime_voice,
        greeting_event=greeting_event(kiosk.language) if settings.realtime_greeting else None,
        turn_detection=settings.realtime_vad,
        idle_timeout_seconds=settings.realtime_idle_timeout_seconds,
        max_session_seconds=settings.realtime_max_session_seconds,
    )


@router.post("/session/{realtime_session_id}/end")
async def end_session(
    realtime_session_id: str,
    body: RealtimeEndRequest,
    service: RealtimeDep,
    principal: KioskPrincipalDep,
) -> dict[str, bool]:
    """Customer finished (or the kiosk reset): hang up and release the call now."""
    _own_call(service, realtime_session_id, body.kiosk_id, principal)
    await service.end_call(realtime_session_id, reason="client_ended")
    return {"success": True}


@router.post("/session/{realtime_session_id}/metrics")
async def report_metrics(
    realtime_session_id: str,
    body: RealtimeClientMetrics,
    service: RealtimeDep,
    principal: KioskPrincipalDep,
) -> dict[str, bool]:
    call = _own_call(service, realtime_session_id, body.kiosk_id, principal)
    service.record_client_metrics(call, body.model_dump(exclude_none=True, exclude={"kiosk_id"}))
    return {"success": True}
