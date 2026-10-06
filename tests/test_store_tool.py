import pytest

from app.models.tools import StoreTopic, ToolAction
from app.tools.context import ToolInputError
from app.tools.store_tool import get_store_info
from tests.conftest import MakeContext


async def test_store_001_closing_time(make_ctx: MakeContext) -> None:
    result = await get_store_info(make_ctx(), StoreTopic.CLOSING_TIME)
    assert result.found and result.action is ToolAction.STORE_INFO
    assert result.closing_time == "21:00"
    assert result.store_name == "Demo Store Downtown"


async def test_hours_differ_per_store(make_ctx: MakeContext) -> None:
    result = await get_store_info(make_ctx(store_id="STORE-002"), StoreTopic.OPENING_TIME)
    assert (result.opening_time, result.closing_time) == ("09:00", "22:00")


async def test_parking_differs_per_store(make_ctx: MakeContext) -> None:
    assert (await get_store_info(make_ctx(), "PARKING")).parking_available is True
    store2 = make_ctx(store_id="STORE-002")
    assert (await get_store_info(store2, "PARKING")).parking_available is False


async def test_restroom_other_organization(make_ctx: MakeContext) -> None:
    ctx = make_ctx(store_id="STORE-003", organization_id="ORG-002")
    result = await get_store_info(ctx, StoreTopic.RESTROOM)
    assert result.found and result.restroom_available is False


async def test_customer_service_location(make_ctx: MakeContext) -> None:
    result = await get_store_info(make_ctx(), StoreTopic.CUSTOMER_SERVICE)
    assert result.customer_service_location == "Ground floor near the main entrance"


async def test_topic_returns_only_relevant_fields(make_ctx: MakeContext) -> None:
    result = await get_store_info(make_ctx(), StoreTopic.PARKING)
    assert result.opening_time is None and result.customer_service_location is None


async def test_general_returns_everything(make_ctx: MakeContext) -> None:
    result = await get_store_info(make_ctx(), StoreTopic.GENERAL)
    assert result.opening_time == "08:00" and result.closing_time == "21:00"
    assert result.parking_available is True and result.restroom_available is True


async def test_topic_is_case_insensitive(make_ctx: MakeContext) -> None:
    assert (await get_store_info(make_ctx(), "closing_time")).closing_time == "21:00"


async def test_inactive_store_is_error(make_ctx: MakeContext) -> None:
    result = await get_store_info(make_ctx(store_id="STORE-004"), StoreTopic.GENERAL)
    assert not result.found and result.action is ToolAction.ERROR


async def test_store_from_other_organization_is_error(make_ctx: MakeContext) -> None:
    # STORE-003 belongs to ORG-002; a kiosk in ORG-001 must not read it.
    ctx = make_ctx(store_id="STORE-003", organization_id="ORG-001")
    result = await get_store_info(ctx, StoreTopic.GENERAL)
    assert not result.found and result.action is ToolAction.ERROR


async def test_invalid_topic_rejected(make_ctx: MakeContext) -> None:
    with pytest.raises(ToolInputError):
        await get_store_info(make_ctx(), "WEATHER")
