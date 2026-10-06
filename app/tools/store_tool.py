"""get_store_info: opening hours and facilities of the kiosk's own store."""

from datetime import time
from typing import Any

from app.models.tools import StoreInfoResult, StoreTopic, ToolAction
from app.tools.context import ToolContext, ToolInputError
from app.utils.logging import get_logger

logger = get_logger(__name__)

_ALL_FIELDS = (
    "opening_time",
    "closing_time",
    "parking_available",
    "restroom_available",
    "customer_service_location",
)
# Return only what the topic needs: less for the model to read, faster replies.
_TOPIC_FIELDS: dict[StoreTopic, tuple[str, ...]] = {
    StoreTopic.OPENING_TIME: ("opening_time", "closing_time"),
    StoreTopic.CLOSING_TIME: ("opening_time", "closing_time"),
    StoreTopic.PARKING: ("parking_available",),
    StoreTopic.RESTROOM: ("restroom_available",),
    StoreTopic.CUSTOMER_SERVICE: ("customer_service_location",),
    StoreTopic.GENERAL: _ALL_FIELDS,
}


def _format_time(value: time | None) -> str | None:
    return value.strftime("%H:%M") if value else None


async def get_store_info(ctx: ToolContext, topic: StoreTopic | str) -> StoreInfoResult:
    try:
        topic = StoreTopic(str(topic).strip().upper())
    except ValueError:
        allowed = ", ".join(t.value for t in StoreTopic)
        raise ToolInputError(f"topic must be one of: {allowed}") from None

    store = await ctx.repository.get_store(ctx.kiosk.store_id)
    if store is None or store.organization_id != ctx.kiosk.organization_id:
        # Trusted context points at an unknown/inactive store: configuration problem.
        logger.error(
            "store_not_found",
            extra={"store_id": ctx.kiosk.store_id, "organization_id": ctx.kiosk.organization_id},
        )
        return StoreInfoResult(action=ToolAction.ERROR, found=False, topic=topic)

    values: dict[str, Any] = {
        "opening_time": _format_time(store.opening_time),
        "closing_time": _format_time(store.closing_time),
        "parking_available": store.parking_available,
        "restroom_available": store.restroom_available,
        "customer_service_location": store.customer_service_location or None,
    }
    return StoreInfoResult.model_validate(
        {
            "action": ToolAction.STORE_INFO,
            "found": True,
            "topic": topic,
            "store_name": store.name,
            **{field: values[field] for field in _TOPIC_FIELDS[topic]},
        }
    )
