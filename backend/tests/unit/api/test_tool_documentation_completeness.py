"""Every tool an agent can use must carry curator-facing documentation. This fails
CI when a tool is added to an agent.yaml `tools:` list without a description and
summary in the catalog (sourced from packages/alliance/tools/bindings.yaml), so
tool docs can't silently go missing as agents/tools are added.

Scope note: this guards tool-level docs (description + summary). Per-parameter
description quality is a curator-voice concern, not an anti-rot gate."""
import pytest

from src.lib.config.agent_loader import load_agent_definitions
from src.lib.agent_studio import catalog_service


def _agent_referenced_tools():
    agents = load_agent_definitions()
    return sorted({tool for agent in agents.values() for tool in (agent.tools or [])})


@pytest.mark.parametrize("tool_id", _agent_referenced_tools())
def test_agent_referenced_tool_has_documentation(tool_id):
    entry = catalog_service.get_tool_details(tool_id)
    assert entry, f"{tool_id}: referenced by an agent but has no catalog entry"
    assert (entry.get("description") or "").strip(), (
        f"{tool_id}: missing description "
        "(add a description in packages/alliance/tools/bindings.yaml)"
    )
    documentation = entry.get("documentation") or {}
    assert (documentation.get("summary") or "").strip(), (
        f"{tool_id}: missing documentation.summary "
        "(add a description in packages/alliance/tools/bindings.yaml)"
    )
