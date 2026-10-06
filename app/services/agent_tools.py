"""Agents SDK function tools wrapping the plain Python tools.

The model only sees the arguments declared here (query, topic, measurements).
Trusted values (store_id, organization_id, language) come from the run context.
Each call is recorded so the API action/data are derived from tool results,
not from the model's wording.
"""

import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from agents import FunctionTool, RunContextWrapper, function_tool

from app.models.tools import StoreTopic, ToolAction, ToolResult
from app.tools.bmi_tool import bmi_from_measurements
from app.tools.context import ToolContext, ToolInputError
from app.tools.faq_tool import search_faq as run_search_faq
from app.tools.product_tool import search_product as run_search_product
from app.tools.store_tool import get_store_info as run_get_store_info
from app.utils.logging import get_logger
from app.utils.timing import Timer

logger = get_logger(__name__)

UNAVAILABLE_MESSAGE = "This information is temporarily unavailable."

# Fields the model does not need (saves tokens, avoids reading IDs aloud).
_HIDE_FROM_MODEL: dict[str, Any] = {
    "search_product": {
        "action": True,
        "products": {"__all__": {"product_id"}},
        "suggestions": {"__all__": {"product_id"}},
    },
    "search_faq": {"action": True, "answers": {"__all__": {"faq_id", "question"}}},
}


@dataclass(slots=True)
class ToolCallRecord:
    name: str
    arguments: dict[str, Any]
    action: ToolAction | None = None
    data: dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0
    error: str | None = None


@dataclass(slots=True)
class AgentRunContext:
    """Per-request context handed to Runner.run; never serialized to the model."""

    tools: ToolContext
    calls: list[ToolCallRecord] = field(default_factory=list)
    templated: bool = False  # final answer rendered from a template


async def _execute(
    wrapper: RunContextWrapper[AgentRunContext],
    name: str,
    arguments: dict[str, Any],
    call: Callable[[], Awaitable[ToolResult]],
) -> str:
    record = ToolCallRecord(name=name, arguments=arguments)
    timer = Timer()
    output: dict[str, Any]
    try:
        with timer:
            result = await call()
    except ToolInputError as exc:
        record.error = "invalid_input"
        output = {"error": str(exc)}
    except Exception:
        logger.exception("tool_failed", extra={"tool": name})
        record.error = "tool_failed"
        output = {"error": UNAVAILABLE_MESSAGE}
    else:
        record.action = result.action
        record.data = result.model_dump(mode="json", exclude_none=True, exclude={"action"})
        output = result.model_dump(
            mode="json", exclude_none=True, exclude=_HIDE_FROM_MODEL.get(name, {"action"})
        )
    record.duration_ms = timer.ms
    wrapper.context.calls.append(record)
    logger.info(
        "tool_called",
        extra={
            "tool": name,
            "tool_ms": record.duration_ms,
            "tool_action": record.action,
            "tool_error": record.error,
        },
    )
    return json.dumps(output, ensure_ascii=False, separators=(",", ":"))


@function_tool(name_override="search_product")
async def search_product_tool(
    ctx: RunContextWrapper[AgentRunContext], query: str, price_requested: bool
) -> str:
    """Find products in this store with their location, availability and price.

    Args:
        query: Product name, brand, product type, SKU or barcode in English as printed on
            the pack, e.g. "Coca Cola", "Colgate toothpaste", "shampoo".
        price_requested: True only if the customer asked about the price.
    """
    return await _execute(
        ctx,
        "search_product",
        {"query": query, "price_requested": price_requested},
        lambda: run_search_product(ctx.context.tools, query),
    )


@function_tool(name_override="get_store_info")
async def get_store_info_tool(ctx: RunContextWrapper[AgentRunContext], topic: StoreTopic) -> str:
    """Get this store's opening hours and facilities.

    Args:
        topic: What the customer is asking about. Use GENERAL if unsure.
    """
    return await _execute(
        ctx,
        "get_store_info",
        {"topic": str(topic)},
        lambda: run_get_store_info(ctx.context.tools, topic),
    )


@function_tool(name_override="search_faq")
async def search_faq_tool(ctx: RunContextWrapper[AgentRunContext], query: str) -> str:
    """Look up approved store policy answers: payment, returns, refunds, exchanges,
    membership, loyalty points, delivery, bags and other policies.

    Args:
        query: The customer's question as a short English phrase, e.g. "return policy".
    """
    return await _execute(
        ctx, "search_faq", {"query": query}, lambda: run_search_faq(ctx.context.tools, query)
    )


@function_tool(name_override="calculate_bmi")
async def calculate_bmi_tool(
    ctx: RunContextWrapper[AgentRunContext],
    height_cm: float | None,
    weight_kg: float | None,
    height_feet: float | None,
    height_inches: float | None,
    weight_lb: float | None,
) -> str:
    """Calculate BMI. Pass every measurement the customer has given, in the unit they used,
    and null for anything not given. Reports which measurements are still missing.

    Args:
        height_cm: Height in centimetres (convert metres to cm).
        weight_kg: Weight in kilograms.
        height_feet: Feet part of a height given in feet and inches.
        height_inches: Inches part of a height given in feet and inches.
        weight_lb: Weight in pounds (ပေါင်).
    """
    measurements = {
        "height_cm": height_cm,
        "weight_kg": weight_kg,
        "height_feet": height_feet,
        "height_inches": height_inches,
        "weight_lb": weight_lb,
    }

    async def run() -> ToolResult:
        return bmi_from_measurements(**measurements)

    return await _execute(
        ctx,
        "calculate_bmi",
        {k: v for k, v in measurements.items() if v is not None},
        run,
    )


AGENT_TOOLS: list[FunctionTool] = [
    search_product_tool,
    get_store_info_tool,
    search_faq_tool,
    calculate_bmi_tool,
]
