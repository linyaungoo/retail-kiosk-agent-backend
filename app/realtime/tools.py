"""Realtime tool adapters: the chained agent's tools, exposed to the Realtime model.

No business logic lives here. Each Realtime function call is run through the same
Agents SDK FunctionTool the chained agent uses, so argument validation, trusted
context injection (store_id/language from ToolContext, never from the model),
result shaping and error handling are identical in both voice modes.
"""

import json
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from agents import FunctionTool
from agents.tool_context import ToolContext as SdkToolContext

from app.services.agent_tools import AGENT_TOOLS, AgentRunContext, ToolCallRecord
from app.tools.context import ToolContext
from app.utils.timing import Timer

# The allow-list: only these names can ever be executed.
TOOLS: dict[str, FunctionTool] = {tool.name: tool for tool in AGENT_TOOLS}

UNKNOWN_TOOL_OUTPUT = json.dumps({"error": "Unknown tool."})


def tool_definitions(names: Iterable[str]) -> list[dict[str, Any]]:
    """Realtime session `tools` for the enabled tool names (unknown names ignored)."""
    return [
        {
            "type": "function",
            "name": TOOLS[name].name,
            "description": TOOLS[name].description,
            "parameters": TOOLS[name].params_json_schema,
        }
        for name in names
        if name in TOOLS
    ]


@dataclass(frozen=True, slots=True)
class ToolExecution:
    name: str
    output: str  # JSON string sent back to the model as function_call_output
    record: ToolCallRecord | None  # what ran (None: unknown tool / bad arguments)
    duration_ms: int


async def execute_tool(
    name: str, arguments: str, call_id: str, tools: ToolContext, *, allowed: frozenset[str]
) -> ToolExecution:
    """Run one allow-listed tool. Never raises: failures become a safe JSON error the
    model can explain to the customer."""
    with Timer() as timer:
        tool = TOOLS.get(name) if name in allowed else None
        if tool is None:
            return ToolExecution(name, UNKNOWN_TOOL_OUTPUT, None, timer.ms)
        run_context = AgentRunContext(tools=tools)
        sdk_context = SdkToolContext(
            context=run_context, tool_name=name, tool_call_id=call_id, tool_arguments=arguments
        )
        output = await tool.on_invoke_tool(sdk_context, arguments)
    record = run_context.calls[-1] if run_context.calls else None
    return ToolExecution(name, str(output), record, timer.ms)
