"""POST /api/agent: text in, assistant answer out (development / pre-voice testing)."""

from fastapi import APIRouter, Depends

from app.config import Settings
from app.dependencies import AgentServiceDep, RepositoryDep, SettingsDep, verify_kiosk_key
from app.models.agent import AgentRequest, AgentResponse, Timing, ToolCallInfo
from app.services.agent_service import AgentReply
from app.services.kiosk_service import resolve_kiosk
from app.tools.context import ToolContext
from app.utils.timing import Timer

router = APIRouter(prefix="/api", tags=["agent"], dependencies=[Depends(verify_kiosk_key)])


@router.post("/agent", response_model=AgentResponse, response_model_exclude_none=True)
async def run_agent(
    body: AgentRequest,
    repository: RepositoryDep,
    agent: AgentServiceDep,
    settings: SettingsDep,
) -> AgentResponse:
    with Timer() as total:
        kiosk = await resolve_kiosk(
            repository,
            organization_id=body.organization_id,
            store_id=body.store_id,
            kiosk_id=body.kiosk_id,
            session_id=body.session_id,
            language=body.language,
        )
        reply = await agent.reply(ToolContext(kiosk=kiosk, repository=repository), body.message)

    return AgentResponse(
        session_id=kiosk.session_id,
        language=kiosk.language,
        action=reply.action,
        answer=reply.answer,
        data=reply.data,
        duration_ms=total.ms,
        timing=Timing(agent_ms=reply.agent_ms, tool_ms=reply.tool_ms, total_ms=total.ms),
        tool_calls=debug_tool_calls(reply, settings),
    )


def debug_tool_calls(reply: AgentReply, settings: Settings) -> list[ToolCallInfo] | None:
    """Tool calls for debugging; only exposed when APP_ENV=development."""
    if settings.app_env != "development":
        return None
    return [
        ToolCallInfo(
            name=call.name,
            arguments=call.arguments,
            action=call.action,
            duration_ms=call.duration_ms,
            error=call.error,
        )
        for call in reply.tool_calls
    ]
