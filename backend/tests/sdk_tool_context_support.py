"""Real Agents SDK tool contexts for tests that invoke FunctionTools directly.

openai-agents 0.25 function tools read the context's prepared-argument slot and
``run_config``, so a direct ``tool.on_invoke_tool(...)`` call needs a genuine
``ToolContext`` carrying the same arguments it is invoked with.
"""

from __future__ import annotations

from typing import Any

from agents.tool_context import ToolContext


def tool_context(
    tool_name: str,
    arguments: str = "{}",
    *,
    context: Any = None,
    run_config: Any = None,
    tool_call_id: str = "test-call",
) -> ToolContext:
    return ToolContext(
        context=context,
        tool_name=tool_name,
        tool_call_id=tool_call_id,
        tool_arguments=arguments,
        run_config=run_config,
    )
