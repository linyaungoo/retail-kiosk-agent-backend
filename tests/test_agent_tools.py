"""Agents SDK tool wrappers: schemas, trusted context, recording, error handling."""

import json
from typing import Any

import pytest
from agents import FunctionTool
from agents.tool_context import ToolContext as SdkToolContext

from app.models.tools import ToolAction
from app.services.agent_service import resolve_outcome
from app.services.agent_tools import (
    AGENT_TOOLS,
    UNAVAILABLE_MESSAGE,
    AgentRunContext,
    ToolCallRecord,
)
from app.tools.context import ToolContext
from tests.conftest import MakeContext

TOOLS = {tool.name: tool for tool in AGENT_TOOLS}
TRUSTED_FIELDS = {"store_id", "organization_id", "kiosk_id", "session_id", "language"}


async def _invoke(run_ctx: AgentRunContext, name: str, **arguments: Any) -> dict[str, Any]:
    tool = TOOLS[name]
    raw = json.dumps(arguments)
    sdk_ctx = SdkToolContext(
        context=run_ctx, tool_name=name, tool_call_id="call-1", tool_arguments=raw
    )
    output = await tool.on_invoke_tool(sdk_ctx, raw)
    result: dict[str, Any] = json.loads(output)
    return result


def test_exactly_four_tools() -> None:
    assert set(TOOLS) == {"search_product", "get_store_info", "search_faq", "calculate_bmi"}


@pytest.mark.parametrize("tool", AGENT_TOOLS, ids=lambda t: t.name)
def test_model_cannot_set_trusted_context(tool: FunctionTool) -> None:
    params = set(tool.params_json_schema["properties"])
    assert not params & TRUSTED_FIELDS


def test_tool_parameters() -> None:
    def params(name: str) -> set[str]:
        return set(TOOLS[name].params_json_schema["properties"])

    assert params("search_product") == {"query", "price_requested"}
    assert params("search_faq") == {"query"}
    assert params("get_store_info") == {"topic"}
    assert params("calculate_bmi") == {
        "height_cm",
        "weight_kg",
        "height_feet",
        "height_inches",
        "weight_lb",
    }


async def test_product_tool_uses_trusted_store_and_hides_ids(make_ctx: MakeContext) -> None:
    run_ctx = AgentRunContext(tools=make_ctx(store_id="STORE-002"))
    output = await _invoke(run_ctx, "search_product", query="Coca Cola 1L", price_requested=False)

    product = output["products"][0]
    assert product["aisle"] == "B02"  # STORE-002 location, not STORE-001
    assert "product_id" not in product and "action" not in output

    record = run_ctx.calls[0]
    assert record.action is ToolAction.PRODUCT_LOCATION
    assert record.data["products"][0]["product_id"] == "P001"  # API data keeps IDs


async def test_bmi_missing_weight_needs_more_information(make_ctx: MakeContext) -> None:
    run_ctx = AgentRunContext(tools=make_ctx())
    bmi_args = dict(weight_kg=None, height_feet=None, height_inches=None, weight_lb=None)
    output = await _invoke(run_ctx, "calculate_bmi", height_cm=170, **bmi_args)
    assert output == {"missing": ["weight"]}
    assert run_ctx.calls[0].action is ToolAction.NEED_MORE_INFORMATION


async def test_invalid_input_is_reported_to_model(make_ctx: MakeContext) -> None:
    run_ctx = AgentRunContext(tools=make_ctx())
    output = await _invoke(run_ctx, "search_product", query="   ", price_requested=False)
    assert "error" in output
    assert run_ctx.calls[0].error == "invalid_input" and run_ctx.calls[0].action is None


async def test_backend_failure_hides_details(make_ctx: MakeContext) -> None:
    class BrokenRepository:
        async def search_products(self, *args: Any, **kwargs: Any) -> Any:
            raise ConnectionError("sheets.googleapis.com unreachable, key=secret")

    good = make_ctx()
    broken = ToolContext(kiosk=good.kiosk, repository=BrokenRepository())  # type: ignore[arg-type]
    run_ctx = AgentRunContext(tools=broken)
    output = await _invoke(run_ctx, "search_product", query="Coca Cola", price_requested=False)
    assert output == {"error": UNAVAILABLE_MESSAGE}
    assert run_ctx.calls[0].error == "tool_failed"


def _record(action: ToolAction | None, error: str | None = None) -> ToolCallRecord:
    return ToolCallRecord(name="t", arguments={}, action=action, data={"a": 1}, error=error)


def test_outcome_uses_last_successful_call() -> None:
    calls = [_record(ToolAction.PRODUCT_NOT_FOUND), _record(ToolAction.PRODUCT_LOCATION)]
    assert resolve_outcome(calls) == (ToolAction.PRODUCT_LOCATION, {"a": 1})


def test_outcome_ignores_trailing_invalid_input() -> None:
    calls = [_record(ToolAction.STORE_INFO), _record(None, "invalid_input")]
    assert resolve_outcome(calls)[0] is ToolAction.STORE_INFO


def test_outcome_without_tools_is_general() -> None:
    assert resolve_outcome([]) == (ToolAction.GENERAL, {})


def test_outcome_with_only_failed_tool_is_error() -> None:
    assert resolve_outcome([_record(None, "tool_failed")]) == (ToolAction.ERROR, {})
