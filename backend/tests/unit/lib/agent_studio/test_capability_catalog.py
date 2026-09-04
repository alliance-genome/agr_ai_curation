from types import SimpleNamespace

import pytest

import src.lib.agent_studio.capability_catalog as catalog
import src.lib.agent_studio.flow_tools as flow_tools


def _agent(agent_id: str, *, visibility: str, user_id: int | None = None):
    return SimpleNamespace(
        agent_key=agent_id,
        name=agent_id.replace("_", " ").title(),
        description=f"Description for {agent_id}",
        visibility=visibility,
        user_id=user_id,
        category="Extraction",
        supervisor_enabled=False,
        show_in_palette=True,
        tool_ids=[],
        group_tool_policy={},
        output_schema_key=None,
        allowed_group_ids=[],
        model_id="gpt-test",
        model_reasoning="medium",
        version=3,
        updated_at="2026-09-04T00:00:00Z",
        template_source=None,
    )


@pytest.fixture
def sources(monkeypatch):
    agents = [
        _agent("system_agent", visibility="system"),
        _agent("ca_owned", visibility="private", user_id=7),
        _agent("ca_shared", visibility="shared", user_id=9),
    ]
    monkeypatch.setattr(catalog, "list_agents_visible_to_user", lambda *_: agents)
    monkeypatch.setattr(catalog, "resolve_group_tool_policy", lambda tools, *_: SimpleNamespace(tool_ids=tools))
    monkeypatch.setattr(catalog, "_domain_envelope_detail", lambda _agent: None)
    monkeypatch.setattr(catalog, "list_model_definitions", lambda: [])
    monkeypatch.setattr(catalog, "list_groups", lambda: [])
    monkeypatch.setattr(
        catalog,
        "get_tool_policy_cache",
        lambda: SimpleNamespace(refresh=lambda _db: []),
    )
    monkeypatch.setattr(flow_tools, "list_available_flow_templates", lambda **_: [])
    return agents


def test_catalog_discovers_system_owned_and_shared_agents_with_current_identity(sources):
    records = catalog.build_authorized_capability_catalog(
        db=object(),
        context=catalog.CapabilityCatalogContext(user_id=7),
    )

    by_id = {record.resource_id: record for record in records if record.kind == "agent"}
    assert set(by_id) == {"system_agent", "ca_owned", "ca_shared"}
    assert by_id["system_agent"].authorization_scope == "system"
    assert by_id["ca_owned"].authorization_scope == "owned"
    assert by_id["ca_shared"].authorization_scope == "shared"
    assert by_id["ca_owned"].selectable is True
    assert by_id["ca_owned"].detail["identity_contract"] == {
        "phase": "saved_agent_id",
        "agent_revision_id": None,
        "profile_revision_id": None,
    }


def test_catalog_keeps_none_distinct_and_future_extensions_discoverable(sources):
    class Extension:
        def list_capabilities(self, **_kwargs):
            return [
                catalog.CapabilityRecord(
                    kind="validator_capability",
                    resource_id="future_validator",
                    name="Future validator",
                    description="Registered through the extension boundary",
                    selectable=False,
                    availability="incompatible",
                )
            ]

    result = catalog.search_capabilities(
        db=object(),
        context=catalog.CapabilityCatalogContext(user_id=7),
        extensions=[Extension()],
    )

    indexed = {(item["kind"], item["resource_id"]): item for item in result["results"]}
    assert indexed[("output_contract", "none")]["selectable"] is True
    assert indexed[("validator_capability", "future_validator")]["selectable"] is False
    assert not any(key == ("output_contract", "unprofiled_generic") for key in indexed)


def test_detail_reauthorizes_fingerprint_and_uses_hash_addressed_chunks(sources):
    context = catalog.CapabilityCatalogContext(user_id=7)
    search = catalog.search_capabilities(
        db=object(), context=context, kinds=["agent"], limit=1
    )
    item = search["results"][0]
    continuation = search["next_call"]["arguments"]
    assert continuation["catalog_fingerprint"] == search["catalog_fingerprint"]
    summary = catalog.get_capability_detail(
        db=object(),
        context=context,
        kind=item["kind"],
        resource_id=item["resource_id"],
        catalog_fingerprint=search["catalog_fingerprint"],
    )
    chunk = catalog.get_capability_detail(
        db=object(),
        context=context,
        kind=item["kind"],
        resource_id=item["resource_id"],
        catalog_fingerprint=search["catalog_fingerprint"],
        detail_hash=summary["detail_hash"],
        start=0,
        max_chars=80,
    )
    assert chunk["authorization"] == "reauthorized"
    assert chunk["content"]
    with pytest.raises(catalog.CapabilityCatalogRequestError, match="Search again"):
        catalog.get_capability_detail(
            db=object(),
            context=context,
            kind=item["kind"],
            resource_id=item["resource_id"],
            catalog_fingerprint="sha256:stale",
        )


def test_search_continuation_rejects_changed_authorization_snapshot(sources):
    context = catalog.CapabilityCatalogContext(user_id=7)
    first = catalog.search_capabilities(
        db=object(), context=context, kinds=["agent"], limit=1
    )
    continuation = first["next_call"]["arguments"]
    sources.pop()

    with pytest.raises(catalog.CapabilityCatalogRequestError, match="changed"):
        catalog.search_capabilities(
            db=object(),
            context=context,
            kinds=continuation["kinds"],
            cursor=continuation["cursor"],
            limit=continuation["limit"],
            catalog_fingerprint=continuation["catalog_fingerprint"],
        )


def test_catalog_bound_reports_only_sanitized_counts(sources, monkeypatch):
    monkeypatch.setattr(catalog, "get_agent_studio_capability_catalog_max_records", lambda: 1)
    with pytest.raises(catalog.CapabilityCatalogUnavailable) as raised:
        catalog.build_authorized_capability_catalog(
            db=object(),
            context=catalog.CapabilityCatalogContext(user_id=7),
        )
    context = raised.value.sanitized_context()
    assert context["bound_exceeded"] is True
    assert context["candidate_count"] > context["bound"]
    assert "system_agent" not in str(context)


def test_catalog_projects_live_models_tools_and_registered_output_contracts(
    sources, monkeypatch
):
    sources[1].output_schema_key = "gene_result"
    models = [
        SimpleNamespace(
            model_id="gpt-visible",
            name="Visible model",
            description="Available to curators",
            curator_visible=True,
            supports_reasoning=True,
            supports_temperature=False,
            reasoning_options=["low", "high"],
            provider="openai",
            guidance="Use for extraction",
            default=True,
            default_reasoning="high",
            reasoning_descriptions={"high": "More analysis"},
            recommended_for=["extraction"],
            avoid_for=[],
        ),
        SimpleNamespace(model_id="hidden", curator_visible=False),
    ]
    policies = [
        SimpleNamespace(
            tool_key="search_document",
            display_name="Search document",
            description="Find evidence",
            category="Documents",
            curator_visible=True,
            allow_attach=True,
            allow_execute=True,
            config={"allowed_group_ids": ["RGD"]},
        ),
        SimpleNamespace(
            tool_key="blocked_tool",
            display_name="Blocked",
            description="Not executable",
            category="Other",
            curator_visible=True,
            allow_attach=False,
            allow_execute=False,
            config={},
        ),
    ]
    monkeypatch.setattr(catalog, "list_model_definitions", lambda: models)
    monkeypatch.setattr(
        catalog,
        "get_tool_policy_cache",
        lambda: SimpleNamespace(refresh=lambda _db: policies),
    )
    monkeypatch.setattr(catalog, "has_tool_binding", lambda tool_id: tool_id == "search_document")
    monkeypatch.setattr(catalog, "tool_requires_document", lambda tool_id: tool_id == "search_document")

    class GeneResult:
        """Structured gene result."""

        @classmethod
        def model_json_schema(cls):
            return {"type": "object", "properties": {"gene_id": {"type": "string"}}}

    monkeypatch.setattr(
        catalog,
        "resolve_output_schema",
        lambda key: GeneResult if key == "gene_result" else None,
    )
    records = catalog.build_authorized_capability_catalog(
        db=object(),
        context=catalog.CapabilityCatalogContext(
            user_id=7, active_group_ids=("RGD",)
        ),
    )
    indexed = {(record.kind, record.resource_id): record for record in records}
    assert ("model", "gpt-visible") in indexed
    assert ("model", "hidden") not in indexed
    document_tool = indexed[("tool", "search_document")]
    assert document_tool.compatibility["requires_document"] is True
    assert document_tool.selectable is True
    assert indexed[("tool", "blocked_tool")].availability == "unavailable"
    assert indexed[("output_contract", "gene_result")].detail["json_schema"][
        "type"
    ] == "object"


def test_custom_output_remains_discoverable_but_not_flow_selectable(sources):
    sources[1].category = "Output"
    records = catalog.build_authorized_capability_catalog(
        db=object(),
        context=catalog.CapabilityCatalogContext(user_id=7),
    )
    custom = next(record for record in records if record.resource_id == "ca_owned")
    assert custom.selectable is True
    assert custom.compatibility["flow_selectable"] is False


def test_search_reauthorizes_tools_despite_warm_library_cache(sources, monkeypatch):
    import time
    from src.lib.agent_studio.tool_policy_service import ToolPolicyCacheService

    cache = ToolPolicyCacheService()
    cache._entries = [SimpleNamespace(
        tool_key="revoked_tool", display_name="Revoked tool", description="Hidden now",
        category="Other", curator_visible=True, allow_attach=True, allow_execute=True,
        config={},
    )]
    cache._loaded_at_monotonic = time.monotonic()
    monkeypatch.setattr(cache, "_load", lambda _db: [SimpleNamespace(curator_visible=False)])
    monkeypatch.setattr(catalog, "get_tool_policy_cache", lambda: cache)
    monkeypatch.setattr(catalog, "has_tool_binding", lambda _: True)
    monkeypatch.setattr(catalog, "tool_requires_document", lambda _: False)
    result = catalog.search_capabilities(
        db=object(), context=catalog.CapabilityCatalogContext(user_id=7), kinds=["tool"],
    )
    assert result["results"] == []


def test_search_cannot_return_a_nonadvancing_empty_page(sources, monkeypatch):
    sources[0].description = "x" * 10_000
    monkeypatch.setattr(catalog, "get_agent_studio_provider_tool_result_inline_max_chars", lambda: 2000)
    with pytest.raises(catalog.CapabilityCatalogUnavailable):
        catalog.search_capabilities(
            db=object(), context=catalog.CapabilityCatalogContext(user_id=7),
            kinds=["agent"], query="system_agent",
        )
