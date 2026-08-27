"""Tests for custom-agent service helpers."""

import uuid
from types import SimpleNamespace

import pytest

from src.models.sql.agent import Agent

from src.lib.agent_studio.custom_agent_service import (
    CUSTOM_AGENT_PREFIX,
    create_custom_agent,
    custom_main_prompt_for_parent,
    get_custom_agent_group_prompt,
    make_custom_agent_id,
    normalize_custom_overlay_for_parent,
    normalize_editable_group_prompt_overrides,
    normalize_group_prompt_overrides,
    parse_custom_agent_id,
)


def test_make_and_parse_custom_agent_id_round_trip():
    custom_uuid = uuid.uuid4()
    agent_id = make_custom_agent_id(custom_uuid)
    assert agent_id.startswith(CUSTOM_AGENT_PREFIX)
    assert parse_custom_agent_id(agent_id) == custom_uuid


def test_parse_custom_agent_id_rejects_invalid_values():
    assert parse_custom_agent_id("gene") is None
    assert parse_custom_agent_id("ca_not-a-uuid") is None
    assert parse_custom_agent_id("") is None


def test_normalize_group_prompt_overrides_cleans_keys_and_empty_values():
    normalized = normalize_group_prompt_overrides({
        " wb ": "WormBase custom rules",
        "FB": "",
        "": "ignored",
        "mgi": "Mouse rules",
    })

    assert normalized == {
        "WB": "WormBase custom rules",
        "MGI": "Mouse rules",
    }


def test_normalize_editable_group_prompt_overrides_rejects_locked_prompt_markers():
    with pytest.raises(ValueError, match="Locked core/generated prompt contracts"):
        normalize_editable_group_prompt_overrides({
            "WB": "Edited platform runtime contract prose.",
        })


def test_normalize_custom_overlay_removes_exact_parent_layers(monkeypatch):
    import src.lib.agent_studio.custom_agent_service as service

    layers = (
        SimpleNamespace(kind="core_static", content="LOCKED CORE"),
        SimpleNamespace(kind="base_prompt", content="PARENT BASE"),
    )
    monkeypatch.setattr(
        service,
        "build_agent_prompt_layers",
        lambda *_args, **_kwargs: SimpleNamespace(layers=layers),
    )

    result = normalize_custom_overlay_for_parent(
        "gene",
        "LOCKED CORE\n\nPARENT BASE\n\nKeep curator guidance.",
    )

    assert result.status == "deduplicated"
    assert result.content == "Keep curator guidance."
    assert result.removed_layer_kinds == ["core_static", "base_prompt"]


def test_normalize_custom_overlay_flags_ambiguous_locked_copy(monkeypatch):
    import src.lib.agent_studio.custom_agent_service as service

    monkeypatch.setattr(
        service,
        "build_agent_prompt_layers",
        lambda *_args, **_kwargs: SimpleNamespace(layers=()),
    )

    result = normalize_custom_overlay_for_parent(
        "gene",
        "Partial Platform Runtime Contract prose with local edits.",
    )

    assert result.status == "needs_review"
    assert result.warning


def test_normalize_custom_overlay_flags_mixed_exact_and_ambiguous_locked_copy(monkeypatch):
    import src.lib.agent_studio.custom_agent_service as service

    layers = (
        SimpleNamespace(kind="core_static", content="LOCKED CORE"),
        SimpleNamespace(kind="generated_contract", content="Generated contract body."),
    )
    monkeypatch.setattr(
        service,
        "build_agent_prompt_layers",
        lambda *_args, **_kwargs: SimpleNamespace(layers=layers),
    )

    result = normalize_custom_overlay_for_parent(
        "gene",
        (
            "LOCKED CORE\n\n"
            "Edited Platform Runtime Contract prose with local curator edits.\n\n"
            "Keep curator guidance."
        ),
    )

    assert result.status == "needs_review"
    assert result.content == (
        "Edited Platform Runtime Contract prose with local curator edits.\n\n"
        "Keep curator guidance."
    )
    assert result.removed_layer_kinds == ["core_static"]
    assert "Custom-agent prompt" in (result.warning or "")


def test_custom_main_prompt_expands_legacy_curator_overlay_with_parent_base(monkeypatch):
    import src.lib.agent_studio.custom_agent_service as service

    layers = (
        SimpleNamespace(kind="core_static", content="LOCKED CORE"),
        SimpleNamespace(kind="base_prompt", content="PARENT BASE"),
    )
    monkeypatch.setattr(
        service,
        "build_agent_prompt_layers",
        lambda *_args, **_kwargs: SimpleNamespace(layers=layers),
    )

    result = custom_main_prompt_for_parent(
        "gene_expression",
        "<curator_overlay>\nKeep this ZFIN-specific behavior.\n</curator_overlay>",
    )

    assert result == (
        "PARENT BASE\n\n"
        "## Custom instructions\n"
        "Keep this ZFIN-specific behavior."
    )


def test_custom_main_prompt_leaves_full_main_prompt_unchanged(monkeypatch):
    import src.lib.agent_studio.custom_agent_service as service

    def fail_build(*_args, **_kwargs):
        raise AssertionError("parent prompt should not be loaded")

    monkeypatch.setattr(service, "build_agent_prompt_layers", fail_build)

    assert custom_main_prompt_for_parent(
        "gene_expression",
        "Full editable base prompt.\n\n## Custom instructions\nKeep this.",
    ) == "Full editable base prompt.\n\n## Custom instructions\nKeep this."


def test_custom_main_prompt_deduplicates_legacy_copied_locked_layers(monkeypatch):
    import src.lib.agent_studio.custom_agent_service as service

    layers = (
        SimpleNamespace(kind="core_static", content="Platform Runtime Contract\nDo not edit."),
        SimpleNamespace(kind="base_prompt", content="PARENT BASE"),
    )
    monkeypatch.setattr(
        service,
        "build_agent_prompt_layers",
        lambda *_args, **_kwargs: SimpleNamespace(layers=layers),
    )

    result = custom_main_prompt_for_parent(
        "gene_expression",
        "Platform Runtime Contract\nDo not edit.\n\nPARENT BASE\n\nKeep curator guidance.",
    )

    assert result == (
        "PARENT BASE\n\n"
        "## Custom instructions\n"
        "Keep curator guidance."
    )


def test_custom_main_prompt_rejects_ambiguous_locked_prompt_copy(monkeypatch):
    import src.lib.agent_studio.custom_agent_service as service

    monkeypatch.setattr(
        service,
        "build_agent_prompt_layers",
        lambda *_args, **_kwargs: SimpleNamespace(layers=()),
    )

    with pytest.raises(ValueError, match="Custom-agent prompt"):
        custom_main_prompt_for_parent(
            "gene_expression",
            "Partial Platform Runtime Contract prose with curator edits.",
        )


def test_get_custom_agent_group_prompt_prefers_override():
    override_content = get_custom_agent_group_prompt(
        parent_agent_key="gene_validation",
        group_id="WB",
        group_prompt_overrides={"WB": "custom wb rules"},
    )
    assert override_content == "custom wb rules"


def test_get_custom_agent_group_prompt_rejects_locked_override():
    with pytest.raises(ValueError, match="Locked core/generated prompt contracts"):
        get_custom_agent_group_prompt(
            parent_agent_key="gene_validation",
            group_id="WB",
            group_prompt_overrides={"WB": "Platform Runtime Contract\nDo not edit."},
        )


def test_get_custom_agent_group_prompt_falls_back_to_cached_rules(monkeypatch):
    def _get_prompt_optional(agent_name, prompt_type, group_id=None):
        if agent_name == "gene_validation" and prompt_type == "group_rules" and group_id == "WB":
            return type("Prompt", (), {"content": "cached wb rules"})()
        return None

    fake_cache_module = SimpleNamespace(get_prompt_optional=_get_prompt_optional)

    monkeypatch.setitem(__import__("sys").modules, "src.lib.prompts.cache", fake_cache_module)

    content = get_custom_agent_group_prompt(
        parent_agent_key="gene_validation",
        group_id="WB",
        group_prompt_overrides={},
    )
    assert content == "cached wb rules"


def test_create_custom_agent_creates_unified_custom_agent(monkeypatch):
    import src.lib.agent_studio.custom_agent_service as service

    class FakeQuery:
        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return None

    class FakeDB:
        def add(self, _obj):
            return None

        def flush(self):
            return None

        def query(self, *_args, **_kwargs):
            return FakeQuery()

    monkeypatch.setattr(
        service,
        "_resolve_system_template_agent",
        lambda _db, _agent_id, **_kwargs: SimpleNamespace(
            agent_key="gene_validation",
            instructions="base system prompt",
            model_id="gpt-5.5",
            model_temperature=0.1,
            model_reasoning="medium",
            tool_ids=["agr_curation_query"],
            output_schema_key=None,
            category="Validation",
            allowed_group_ids=[],
        ),
    )
    monkeypatch.setattr(
        service,
        "get_model",
        lambda _model_id: SimpleNamespace(model_id=_model_id),
    )

    custom = service.create_custom_agent(
        db=FakeDB(),
        user_id=7,
        template_source="gene_validation",
        name="My Agent",
    )

    assert custom.template_source == "gene_validation"
    assert custom.user_id == 7
    assert custom.agent_key.startswith("ca_")
    assert custom.instructions == ""


@pytest.mark.parametrize("retired_alias", ["gene", "allele", "disease", "chemical"])
def test_system_template_resolver_rejects_retired_alias_before_query(retired_alias):
    import src.lib.agent_studio.custom_agent_service as service

    class QueryRejectingDB:
        def query(self, *_args, **_kwargs):
            raise AssertionError("retired aliases must fail before querying stale rows")

    with pytest.raises(ValueError, match="retired agent ID"):
        service._resolve_system_template_agent(QueryRejectingDB(), retired_alias)


def test_create_custom_agent_requires_model_for_scratch_mode():
    import src.lib.agent_studio.custom_agent_service as service

    class FakeQuery:
        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return None

    class FakeDB:
        def add(self, _obj):
            return None

        def flush(self):
            return None

        def query(self, *_args, **_kwargs):
            return FakeQuery()

    with pytest.raises(ValueError, match="model_id is required when template_source is not provided"):
        service.create_custom_agent(
            db=FakeDB(),
            user_id=7,
            name="Scratch Agent",
            template_source=None,
            model_id=None,
        )


def test_create_custom_agent_rejects_locked_group_prompt_override():
    import src.lib.agent_studio.custom_agent_service as service

    class FakeQuery:
        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return None

    class FakeDB:
        def query(self, *_args, **_kwargs):
            return FakeQuery()

    with pytest.raises(ValueError, match="Locked core/generated prompt contracts"):
        service.create_custom_agent(
            db=FakeDB(),
            user_id=7,
            name="Locked Group Override",
            template_source=None,
            model_id="gpt-5.5",
            group_prompt_overrides={
                "WB": "Generated runtime contract\nCurator tried to copy this.",
            },
        )


def test_plain_structured_output_language_is_allowed_in_editable_prompts():
    import src.lib.agent_studio.custom_agent_service as service

    prompt = (
        "Use structured output when it helps the curator compare candidate "
        "rows, but keep the explanation concise."
    )

    service.reject_locked_prompt_markers(prompt, target="Custom agent main prompt")
    normalization = service.normalize_custom_overlay_for_parent(None, prompt)

    assert normalization.status == "clean"
    assert normalization.content == prompt


def test_create_custom_agent_rejects_non_attachable_tool_ids(monkeypatch):
    import src.lib.agent_studio.custom_agent_service as service

    class FakeQuery:
        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return None

    class FakeDB:
        def add(self, _obj):
            return None

        def flush(self):
            return None

        def query(self, *_args, **_kwargs):
            return FakeQuery()

    monkeypatch.setattr(
        service,
        "get_tool_policy_cache",
        lambda: SimpleNamespace(
            list_all=lambda _db: [
                SimpleNamespace(tool_key="agr_curation_query", allow_attach=True),
                SimpleNamespace(tool_key="admin_only_tool", allow_attach=False),
            ],
        ),
    )
    monkeypatch.setattr(service, "get_model", lambda _model_id: SimpleNamespace(model_id=_model_id))

    with pytest.raises(ValueError, match="not attachable"):
        service.create_custom_agent(
            db=FakeDB(),
            user_id=7,
            name="Tool Guardrail Agent",
            template_source=None,
            model_id="gpt-5.5",
            tool_ids=["admin_only_tool"],
        )


def test_create_custom_agent_allows_inherited_system_managed_tool_ids(monkeypatch):
    import src.lib.agent_studio.custom_agent_service as service

    class FakeQuery:
        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return None

    class FakeDB:
        def add(self, _obj):
            return None

        def flush(self):
            return None

        def query(self, *_args, **_kwargs):
            return FakeQuery()

    monkeypatch.setattr(
        service,
        "_resolve_system_template_agent",
        lambda _db, _agent_id, **_kwargs: SimpleNamespace(
            agent_key="allele_extractor",
            instructions="base system prompt",
            model_id="gpt-5.5",
            model_temperature=0.1,
            model_reasoning="medium",
            tool_ids=[
                "search_document",
                "record_evidence",
                "finalize_allele_extraction",
            ],
            output_schema_key="AlleleVariantExtractionEnvelope",
            category="Extraction",
            allowed_group_ids=[],
            group_tool_policy={
                "rules": [
                    {
                        "tool_id": "search_document",
                        "allowed_group_ids": ["ZFIN"],
                        "field_paths": ["expression_experiment.specimen_genomic_model"],
                    }
                ]
            },
        ),
    )
    monkeypatch.setattr(
        service,
        "get_tool_policy_cache",
        lambda: SimpleNamespace(
            list_all=lambda _db: [
                SimpleNamespace(tool_key="search_document", allow_attach=True),
            ],
        ),
    )
    monkeypatch.setattr(service, "get_model", lambda _model_id: SimpleNamespace(model_id=_model_id))

    custom = service.create_custom_agent(
        db=FakeDB(),
        user_id=7,
        template_source="allele_extractor",
        name="MGI Allele Extractor",
        tool_ids=["search_document", "record_evidence"],
    )

    assert custom.tool_ids == [
        "search_document",
        "record_evidence",
        "finalize_allele_extraction",
    ]
    assert custom.group_tool_policy == {
        "rules": [
            {
                "tool_id": "search_document",
                "allowed_group_ids": ["ZFIN"],
                "field_paths": ["expression_experiment.specimen_genomic_model"],
            }
        ]
    }


def test_create_custom_agent_rejects_envelope_without_finalize_tool(monkeypatch):
    import src.lib.agent_studio.custom_agent_service as service

    class FakeQuery:
        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return None

    class FakeDB:
        def add(self, _obj):
            return None

        def flush(self):
            return None

        def query(self, *_args, **_kwargs):
            return FakeQuery()

    monkeypatch.setattr(
        service,
        "_resolve_system_template_agent",
        lambda _db, _agent_id, **_kwargs: SimpleNamespace(
            agent_key="allele_extractor",
            instructions="base system prompt",
            model_id="gpt-5.5",
            model_temperature=0.1,
            model_reasoning="medium",
            tool_ids=["search_document", "record_evidence"],
            output_schema_key="AlleleVariantExtractionEnvelope",
            category="Extraction",
            allowed_group_ids=[],
        ),
    )
    monkeypatch.setattr(
        service,
        "get_tool_policy_cache",
        lambda: SimpleNamespace(
            list_all=lambda _db: [
                SimpleNamespace(tool_key="search_document", allow_attach=True),
            ],
        ),
    )
    monkeypatch.setattr(
        service,
        "get_model",
        lambda _model_id: SimpleNamespace(model_id=_model_id),
    )
    monkeypatch.setattr(
        service,
        "_builder_finalization_tool_ids",
        lambda: {"finalize_allele_extraction"},
    )

    with pytest.raises(ValueError, match="envelope output schema.*finalize_\\* tool"):
        service.create_custom_agent(
            db=FakeDB(),
            user_id=7,
            template_source="allele_extractor",
            name="MGI Allele Extractor",
            tool_ids=["search_document", "record_evidence"],
        )


def test_create_custom_agent_preserves_inherited_system_managed_tool_ids(monkeypatch):
    import src.lib.agent_studio.custom_agent_service as service

    class FakeQuery:
        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return None

    class FakeDB:
        def add(self, _obj):
            return None

        def flush(self):
            return None

        def query(self, *_args, **_kwargs):
            return FakeQuery()

    monkeypatch.setattr(
        service,
        "_resolve_system_template_agent",
        lambda _db, _agent_id, **_kwargs: SimpleNamespace(
            agent_key="allele_extractor",
            instructions="base system prompt",
            model_id="gpt-5.5",
            model_temperature=0.1,
            model_reasoning="medium",
            tool_ids=[
                "search_document",
                "record_evidence",
                "finalize_allele_extraction",
            ],
            output_schema_key="AlleleVariantExtractionEnvelope",
            category="Extraction",
            allowed_group_ids=[],
        ),
    )
    monkeypatch.setattr(
        service,
        "get_tool_policy_cache",
        lambda: SimpleNamespace(
            list_all=lambda _db: [
                SimpleNamespace(tool_key="search_document", allow_attach=True),
                SimpleNamespace(tool_key="record_evidence", allow_attach=False),
            ],
        ),
    )
    monkeypatch.setattr(service, "get_model", lambda _model_id: SimpleNamespace(model_id=_model_id))

    custom = service.create_custom_agent(
        db=FakeDB(),
        user_id=7,
        template_source="allele_extractor",
        name="MGI Allele Extractor",
        tool_ids=["search_document"],
    )

    assert custom.tool_ids == [
        "search_document",
        "record_evidence",
        "finalize_allele_extraction",
    ]


def test_create_custom_agent_rejects_unknown_inherited_non_runtime_tool_ids(monkeypatch):
    import src.lib.agent_studio.custom_agent_service as service

    class FakeQuery:
        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return None

    class FakeDB:
        def query(self, *_args, **_kwargs):
            return FakeQuery()

    monkeypatch.setattr(
        service,
        "_resolve_system_template_agent",
        lambda _db, _agent_id, **_kwargs: SimpleNamespace(
            agent_key="typo_template",
            instructions="base system prompt",
            model_id="gpt-5.5",
            model_temperature=0.1,
            model_reasoning="medium",
            tool_ids=["search_document", "recrod_evidence"],
            output_schema_key="DemoEnvelope",
            category="Extraction",
            allowed_group_ids=[],
        ),
    )
    monkeypatch.setattr(
        service,
        "get_tool_policy_cache",
        lambda: SimpleNamespace(
            list_all=lambda _db: [
                SimpleNamespace(tool_key="search_document", allow_attach=True),
            ],
        ),
    )
    monkeypatch.setattr(service, "get_model", lambda _model_id: SimpleNamespace(model_id=_model_id))

    with pytest.raises(ValueError, match="Unknown tool_ids: recrod_evidence"):
        service.create_custom_agent(
            db=FakeDB(),
            user_id=7,
            template_source="typo_template",
            name="Broken Template Copy",
            tool_ids=["search_document", "recrod_evidence"],
        )


def test_update_custom_agent_rejects_unknown_tool_ids(monkeypatch):
    import src.lib.agent_studio.custom_agent_service as service

    class FakeQuery:
        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return None

    class FakeDB:
        def add(self, _obj):
            return None

        def query(self, *_args, **_kwargs):
            return FakeQuery()

    custom_agent = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=7,
        name="Existing Agent",
        custom_prompt="Prompt",
        group_prompt_overrides={},
        include_group_rules=True,
        model_id="gpt-5.5",
        model_temperature=0.1,
        model_reasoning=None,
        tool_ids=[],
        output_schema_key=None,
        allowed_group_ids=[],
        inherited_allowed_group_ids=[],
    )

    monkeypatch.setattr(
        service,
        "get_tool_policy_cache",
        lambda: SimpleNamespace(
            list_all=lambda _db: [
                SimpleNamespace(tool_key="agr_curation_query", allow_attach=True),
            ],
        ),
    )

    with pytest.raises(ValueError, match="Unknown tool_ids"):
        service.update_custom_agent(
            db=FakeDB(),
            custom_agent=custom_agent,
            tool_ids=["missing_tool"],
        )


def test_update_custom_agent_rejects_envelope_without_finalize_tool(monkeypatch):
    import src.lib.agent_studio.custom_agent_service as service

    custom_agent = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=7,
        name="Existing Agent",
        custom_prompt="Prompt",
        group_prompt_overrides={},
        include_group_rules=True,
        model_id="gpt-5.5",
        model_temperature=0.1,
        model_reasoning=None,
        tool_ids=["search_document"],
        output_schema_key="AlleleVariantExtractionEnvelope",
        template_source=None,
        allowed_group_ids=[],
        inherited_allowed_group_ids=[],
    )

    monkeypatch.setattr(
        service,
        "_builder_finalization_tool_ids",
        lambda: {"finalize_allele_extraction"},
    )

    with pytest.raises(ValueError, match="envelope output schema.*finalize_\\* tool"):
        service.update_custom_agent(
            db=SimpleNamespace(),
            custom_agent=custom_agent,
            description="Updated description",
        )


def test_update_custom_agent_rejects_locked_group_prompt_override():
    import src.lib.agent_studio.custom_agent_service as service

    custom_agent = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=7,
        name="Existing Agent",
        custom_prompt="Prompt",
        group_prompt_overrides={},
        include_group_rules=True,
        model_id="gpt-5.5",
        model_temperature=0.1,
        model_reasoning=None,
        tool_ids=[],
        output_schema_key=None,
        allowed_group_ids=[],
        inherited_allowed_group_ids=[],
    )

    with pytest.raises(ValueError, match="Locked core/generated prompt contracts"):
        service.update_custom_agent(
            db=SimpleNamespace(),
            custom_agent=custom_agent,
            group_prompt_overrides={
                "WB": "Platform Runtime Contract\nCurator tried to copy this.",
            },
        )

    assert custom_agent.group_prompt_overrides == {}


def test_update_custom_agent_preserves_inherited_system_managed_tool_ids(monkeypatch):
    import src.lib.agent_studio.custom_agent_service as service

    custom_agent = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=7,
        name="Existing Agent",
        custom_prompt="Prompt",
        group_prompt_overrides={},
        include_group_rules=True,
        model_id="gpt-5.5",
        model_temperature=0.1,
        model_reasoning=None,
        tool_ids=[
            "search_document",
            "record_evidence",
            "finalize_allele_extraction",
        ],
        output_schema_key="AlleleVariantExtractionEnvelope",
        template_source="allele_extractor",
        allowed_group_ids=[],
        inherited_allowed_group_ids=[],
    )

    monkeypatch.setattr(
        service,
        "_resolve_system_template_agent",
        lambda _db, _agent_id, **_kwargs: SimpleNamespace(
            tool_ids=[
                "search_document",
                "record_evidence",
                "finalize_allele_extraction",
            ],
        ),
    )
    monkeypatch.setattr(
        service,
        "get_tool_policy_cache",
        lambda: SimpleNamespace(
            list_all=lambda _db: [
                SimpleNamespace(tool_key="search_document", allow_attach=True),
                SimpleNamespace(tool_key="record_evidence", allow_attach=False),
            ],
        ),
    )

    service.update_custom_agent(
        db=SimpleNamespace(),
        custom_agent=custom_agent,
        tool_ids=["search_document"],
    )

    assert custom_agent.tool_ids == [
        "search_document",
        "record_evidence",
        "finalize_allele_extraction",
    ]


def test_update_custom_agent_preserves_inherited_system_managed_tool_ids_when_policy_missing(monkeypatch):
    import src.lib.agent_studio.custom_agent_service as service

    custom_agent = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=7,
        name="Existing Agent",
        custom_prompt="Prompt",
        group_prompt_overrides={},
        include_group_rules=True,
        model_id="gpt-5.5",
        model_temperature=0.1,
        model_reasoning=None,
        tool_ids=[
            "search_document",
            "record_evidence",
            "finalize_allele_extraction",
        ],
        output_schema_key="AlleleVariantExtractionEnvelope",
        template_source="allele_extractor",
        allowed_group_ids=[],
        inherited_allowed_group_ids=[],
    )

    monkeypatch.setattr(
        service,
        "_resolve_system_template_agent",
        lambda _db, _agent_id, **_kwargs: SimpleNamespace(
            tool_ids=[
                "search_document",
                "record_evidence",
                "finalize_allele_extraction",
            ],
        ),
    )
    monkeypatch.setattr(
        service,
        "get_tool_policy_cache",
        lambda: SimpleNamespace(
            list_all=lambda _db: [
                SimpleNamespace(tool_key="search_document", allow_attach=True),
            ],
        ),
    )

    service.update_custom_agent(
        db=SimpleNamespace(),
        custom_agent=custom_agent,
        tool_ids=["search_document", "record_evidence"],
    )

    assert custom_agent.tool_ids == [
        "search_document",
        "record_evidence",
        "finalize_allele_extraction",
    ]


def test_update_custom_agent_rejects_clearing_existing_tool_ids_without_override():
    import src.lib.agent_studio.custom_agent_service as service

    class FakeDB:
        pass

    custom_agent = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=7,
        name="Existing Agent",
        custom_prompt="Prompt",
        group_prompt_overrides={},
        include_group_rules=True,
        model_id="gpt-5.5",
        model_temperature=0.1,
        model_reasoning=None,
        tool_ids=["agr_curation_query"],
        output_schema_key=None,
        allowed_group_ids=[],
        inherited_allowed_group_ids=[],
    )

    with pytest.raises(ValueError, match="Refusing to clear all tool_ids"):
        service.update_custom_agent(
            db=FakeDB(),
            custom_agent=custom_agent,
            tool_ids=[],
        )


def test_create_custom_agent_rejects_unknown_model_id(monkeypatch):
    import src.lib.agent_studio.custom_agent_service as service

    class FakeQuery:
        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return None

    class FakeDB:
        def add(self, _obj):
            return None

        def flush(self):
            return None

        def query(self, *_args, **_kwargs):
            return FakeQuery()

    monkeypatch.setattr(service, "get_model", lambda _model_id: None)

    with pytest.raises(ValueError, match="Unknown model_id"):
        service.create_custom_agent(
            db=FakeDB(),
            user_id=7,
            name="Bad Model Agent",
            template_source=None,
            model_id="unknown-model",
        )


def test_update_custom_agent_rejects_unknown_model_id(monkeypatch):
    import src.lib.agent_studio.custom_agent_service as service

    class FakeQuery:
        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return None

    class FakeDB:
        def add(self, _obj):
            return None

        def query(self, *_args, **_kwargs):
            return FakeQuery()

    custom_agent = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=7,
        name="Existing Agent",
        custom_prompt="Prompt",
        group_prompt_overrides={},
        include_group_rules=True,
        model_id="gpt-5.5",
        model_temperature=0.1,
        model_reasoning=None,
        tool_ids=[],
        output_schema_key=None,
        allowed_group_ids=[],
        inherited_allowed_group_ids=[],
    )
    monkeypatch.setattr(service, "get_model", lambda _model_id: None)

    with pytest.raises(ValueError, match="Unknown model_id"):
        service.update_custom_agent(
            db=FakeDB(),
            custom_agent=custom_agent,
            model_id="not-real",
        )


def test_create_custom_agent_rejects_non_curator_visible_model(monkeypatch):
    import src.lib.agent_studio.custom_agent_service as service

    class FakeQuery:
        def filter(self, *_args, **_kwargs):
            return self

        def first(self):
            return None

    class FakeDB:
        def add(self, _obj):
            return None

        def flush(self):
            return None

        def query(self, *_args, **_kwargs):
            return FakeQuery()

    monkeypatch.setattr(
        service,
        "get_model",
        lambda _model_id: SimpleNamespace(model_id=_model_id, curator_visible=False),
    )

    with pytest.raises(ValueError, match="Model is not selectable in Agent Workshop"):
        create_custom_agent(
            db=FakeDB(),
            user_id=7,
            name="Hidden Model Agent",
            template_source=None,
            model_id="gpt-5.5",
        )


def test_set_custom_agent_visibility_sets_project_membership(monkeypatch):
    import src.lib.agent_studio.custom_agent_service as service

    project_id = uuid.uuid4()
    monkeypatch.setattr(service, "_get_primary_project_id_for_user", lambda _db, _uid: project_id)

    custom_agent = SimpleNamespace(
        user_id=7,
        visibility="private",
        project_id=None,
        shared_at=None,
    )

    updated = service.set_custom_agent_visibility(
        db=SimpleNamespace(),
        custom_agent=custom_agent,
        user_id=7,
        visibility="project",
    )

    assert updated.visibility == "project"
    assert updated.project_id == project_id
    assert updated.shared_at is not None


def test_clone_visible_agent_for_user_clones_from_visible_source(monkeypatch):
    import src.lib.agent_studio.custom_agent_service as service

    source = SimpleNamespace(
        agent_key="ca_source",
        visibility="project",
        name="Shared Agent",
        template_source="gene_validation",
        instructions="prompt",
        group_prompt_overrides={"WB": "rules"},
        description="desc",
        icon="🔧",
        group_rules_enabled=True,
        model_id="gpt-5.5",
        tool_ids=["agr_curation_query"],
        output_schema_key=None,
        category="Validation",
        model_temperature=0.1,
        model_reasoning="medium",
        allowed_group_ids=["RGD"],
        group_tool_policy={
            "rules": [
                {
                    "tool_id": "agr_curation_query",
                    "allowed_group_ids": ["RGD"],
                    "field_paths": ["annotation.subject"],
                }
            ]
        },
    )
    observed = {}

    monkeypatch.setattr(service, "get_agent_by_key", lambda _db, _key, user_id=None, **_kwargs: source)
    monkeypatch.setattr(service, "_generate_clone_name", lambda _db, _uid, _name: "Shared Agent (Copy)")
    monkeypatch.setattr(service, "_has_active_custom_name", lambda _db, _uid, _name: False)
    monkeypatch.setattr(
        service,
        "normalize_custom_overlay_for_parent",
        lambda *_args, **_kwargs: SimpleNamespace(
            content="prompt",
            status="clean",
            removed_layer_kinds=[],
            warning=None,
        ),
    )

    def _fake_create_custom_agent(**kwargs):
        observed.update(kwargs)
        return SimpleNamespace(id=uuid.uuid4())

    monkeypatch.setattr(service, "create_custom_agent", _fake_create_custom_agent)

    service.clone_visible_agent_for_user(
        db=SimpleNamespace(),
        user_id=7,
        source_agent_key="ca_source",
        name=None,
    )

    assert observed["user_id"] == 7
    assert observed["name"] == "Shared Agent (Copy)"
    assert observed["template_source"] == "gene_validation"
    assert observed["custom_prompt"] == "prompt"
    assert observed["allowed_group_ids"] == ["RGD"]
    assert observed["inherited_allowed_group_ids"] == ["RGD"]
    assert observed["inherited_group_tool_policy"] == source.group_tool_policy


def test_clone_visible_agent_rejects_widening_source_restriction(monkeypatch):
    import src.lib.agent_studio.custom_agent_service as service

    source = SimpleNamespace(
        agent_key="restricted",
        visibility="system",
        name="Restricted",
        template_source=None,
        allowed_group_ids=["RGD"],
    )
    monkeypatch.setattr(service, "get_agent_by_key", lambda *_args, **_kwargs: source)
    monkeypatch.setattr(service, "_has_active_custom_name", lambda *_args: False)

    with pytest.raises(ValueError, match="cannot widen"):
        service.clone_visible_agent_for_user(
            db=SimpleNamespace(),
            user_id=7,
            source_agent_key="restricted",
            name="Widened",
            allowed_group_ids=[],
        )


def test_inherited_access_floor_blocks_later_widening():
    import src.lib.agent_studio.custom_agent_service as service

    custom_agent = SimpleNamespace(
        id=uuid.uuid4(),
        template_source="system_template",
        inherited_allowed_group_ids=["RGD"],
    )

    assert service._validate_inherited_access_floor(
        custom_agent, ["RGD"]  # type: ignore[arg-type]
    ) == ["RGD"]
    with pytest.raises(ValueError, match="cannot widen"):
        service._validate_inherited_access_floor(
            custom_agent, []  # type: ignore[arg-type]
        )


def _restricted_custom_agent(**overrides):
    values = {
        "id": uuid.uuid4(),
        "user_id": 7,
        "name": "Restricted clone",
        "instructions": "Current prompt",
        "group_prompt_overrides": {},
        "template_source": "system_template",
        "model_id": "gpt-5.5",
        "model_temperature": 0.1,
        "model_reasoning": None,
        "tool_ids": [],
        "output_schema_key": None,
        "allowed_group_ids": ["WB", "RGD"],
        "inherited_allowed_group_ids": ["WB", "RGD"],
        "version": 4,
    }
    values.update(overrides)
    return Agent(**values)


class _AccessFloorDB:
    def __init__(self, target=None):
        self.target = target
        self.added = []

    def query(self, *_args, **_kwargs):
        return self

    def filter(self, *_args, **_kwargs):
        return self

    def first(self):
        return self.target

    def add(self, value):
        self.added.append(value)


@pytest.mark.parametrize("requested", [[], ["WB", "RGD", "MGI"]])
def test_update_restricted_clone_rejects_access_widening(requested):
    import src.lib.agent_studio.custom_agent_service as service

    custom_agent = _restricted_custom_agent()

    with pytest.raises(ValueError, match="cannot widen"):
        service.update_custom_agent(
            db=_AccessFloorDB(),
            custom_agent=custom_agent,
            allowed_group_ids=requested,
        )

    assert custom_agent.allowed_group_ids == ["WB", "RGD"]
    assert custom_agent.version == 4


def test_update_restricted_clone_allows_narrowing_and_snapshots(monkeypatch):
    import src.lib.agent_studio.custom_agent_service as service

    custom_agent = _restricted_custom_agent()
    db = _AccessFloorDB()
    monkeypatch.setattr(service, "_get_next_version", lambda *_args: 5)

    updated = service.update_custom_agent(
        db=db,
        custom_agent=custom_agent,
        allowed_group_ids=["RGD"],
    )

    assert updated.allowed_group_ids == ["RGD"]
    assert updated.version == 5
    assert len(db.added) == 1
    assert db.added[0].allowed_group_ids == ["WB", "RGD"]


def test_update_restricted_clone_missing_floor_fails_closed():
    import src.lib.agent_studio.custom_agent_service as service

    custom_agent = _restricted_custom_agent()
    custom_agent.inherited_allowed_group_ids = None

    with pytest.raises(TypeError, match="NoneType.*iterable"):
        service.update_custom_agent(
            db=_AccessFloorDB(),
            custom_agent=custom_agent,
            allowed_group_ids=[],
        )

    assert custom_agent.allowed_group_ids == ["WB", "RGD"]


@pytest.mark.parametrize("target_groups", [[], ["WB", "RGD", "MGI"]])
def test_revert_restricted_clone_rejects_access_widening(target_groups, monkeypatch):
    import src.lib.agent_studio.custom_agent_service as service

    custom_agent = _restricted_custom_agent()
    target = SimpleNamespace(
        custom_prompt="Historical prompt",
        group_prompt_overrides={},
        allowed_group_ids=target_groups,
    )
    db = _AccessFloorDB(target)
    monkeypatch.setattr(
        service,
        "build_agent_prompt_layers",
        lambda *_args, **_kwargs: SimpleNamespace(layers=()),
    )

    with pytest.raises(ValueError, match="cannot widen"):
        service.revert_custom_agent_to_version(db, custom_agent, version=2)

    assert custom_agent.instructions == "Current prompt"
    assert custom_agent.allowed_group_ids == ["WB", "RGD"]
    assert db.added == []


def test_revert_restricted_clone_allows_narrowing_and_snapshots(monkeypatch):
    import src.lib.agent_studio.custom_agent_service as service

    custom_agent = _restricted_custom_agent()
    target = SimpleNamespace(
        custom_prompt="Historical prompt",
        group_prompt_overrides={},
        allowed_group_ids=["RGD"],
    )
    db = _AccessFloorDB(target)
    monkeypatch.setattr(service, "_get_next_version", lambda *_args: 5)
    monkeypatch.setattr(
        service,
        "build_agent_prompt_layers",
        lambda *_args, **_kwargs: SimpleNamespace(layers=()),
    )

    updated = service.revert_custom_agent_to_version(db, custom_agent, version=2)

    assert updated.instructions == "Historical prompt"
    assert updated.allowed_group_ids == ["RGD"]
    assert updated.version == 5
    assert len(db.added) == 1
    assert db.added[0].allowed_group_ids == ["WB", "RGD"]
