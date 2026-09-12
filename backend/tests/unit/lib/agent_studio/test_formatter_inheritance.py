"""Formatter helpers remain source-inherited and subject to execution policy."""
from types import SimpleNamespace

import pytest

from src.lib.agent_studio import capability_catalog, catalog_service, custom_agent_service as service
from src.lib.agent_studio.authoring_validation import AgentToolValidationRecord, AgentValidationSources


@pytest.mark.parametrize('tool_id', catalog_service._OUTPUT_FORMATTER_RUNTIME_TOOL_IDS)
@pytest.mark.parametrize('inherited,executable,installed,accepted', [
    (True, True, True, True), (False, True, True, False),
    (True, False, True, False), (True, True, False, False),
])
def test_formatter_helpers_require_inheritance_and_live_permission(
    monkeypatch, tool_id, inherited, executable, installed, accepted,
):
    policy = SimpleNamespace(allow_attach=False, allow_execute=executable, config={})
    monkeypatch.setattr(service, '_tool_policy_by_key', lambda _: {tool_id: policy})
    monkeypatch.setattr(service, '_builder_finalization_tool_ids', lambda: set())
    monkeypatch.setattr(service, 'has_tool_binding', lambda _: installed)
    monkeypatch.setattr(capability_catalog, 'build_authorized_capability_catalog', lambda **_: [])
    sources = AgentValidationSources(
        models={}, tools={tool_id: AgentToolValidationRecord(tool_id, False, installed)},
        output_schema_keys=frozenset(), group_ids=frozenset(), builder_finalization_tool_ids=frozenset(),
    )
    filtered = service.authorized_agent_validation_sources(
        object(), user_id=5, active_group_ids=[], sources=sources,
        inherited_tool_ids=[tool_id] if inherited else [],
    )
    assert (tool_id in filtered.tools) is accepted
    if accepted:
        assert filtered.tools[tool_id].system_managed
    with pytest.raises(ValueError, match='not attachable'):
        service._validate_requested_tool_ids(object(), [tool_id], inherited_tool_ids=[])
