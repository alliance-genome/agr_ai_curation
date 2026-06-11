"""Unit tests for deterministic prompt layer assembly."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import BaseModel

from src.lib.config.agent_loader import AgentDefinition, CurationConfig
from src.lib.prompts import assembly
from src.lib.prompts.cache import PromptNotFoundError
from src.models.sql.prompts import PromptTemplate


class DemoStructuredOutput(BaseModel):
    value: str


class DemoFinalizationInput(BaseModel):
    answer: str


def _agent(
    *,
    folder_name: str = "demo_agent",
    agent_id: str = "demo_agent_validation",
    output_schema: str | None = "DemoStructuredOutput",
    structured_finalization: dict | None = None,
    category: str = "",
    tools: list[str] | None = None,
    domain_pack_id: str | None = None,
    system_agent_key: str | None = None,
) -> AgentDefinition:
    return AgentDefinition(
        folder_name=folder_name,
        agent_id=agent_id,
        system_agent_key=system_agent_key,
        name="Demo Validation Agent",
        category=category,
        tools=tools or [],
        output_schema=output_schema,
        structured_finalization=structured_finalization,
        curation=CurationConfig(domain_pack_id=domain_pack_id),
    )


def _prompt(
    agent_name: str,
    prompt_type: str,
    content: str,
    *,
    group_id: str | None = None,
    version: int = 1,
) -> PromptTemplate:
    return PromptTemplate(
        id=uuid.uuid4(),
        agent_name=agent_name,
        prompt_type=prompt_type,
        group_id=group_id,
        content=content,
        version=version,
        is_active=True,
        created_at=datetime.now(timezone.utc),
        created_by="tester@example.org",
        source_file=f"packages/test/agents/{agent_name}/{prompt_type}.yaml",
    )


@pytest.fixture
def prompt_cache(monkeypatch):
    prompts = {
        "demo_agent:system:base": _prompt("demo_agent", "system", "Base prompt"),
        "demo_agent:group_rules:GROUP_ALPHA": _prompt(
            "demo_agent",
            "group_rules",
            "Group alpha rules",
            group_id="GROUP_ALPHA",
        ),
        "demo_agent:group_rules:GROUP_BETA": _prompt(
            "demo_agent",
            "group_rules",
            "Group beta rules",
            group_id="GROUP_BETA",
        ),
    }
    monkeypatch.setattr(assembly, "get_all_active_prompts", lambda: prompts)
    return prompts


@pytest.fixture(autouse=True)
def agent_registry(monkeypatch):
    monkeypatch.setattr(
        assembly,
        "load_agent_definitions",
        lambda: {"demo_agent_validation": _agent()},
    )
    monkeypatch.setattr(
        assembly,
        "resolve_output_schema",
        lambda schema_key: {
            "DemoFinalizationInput": DemoFinalizationInput,
            "DemoStructuredOutput": DemoStructuredOutput,
            "PhenotypeResultEnvelope": DemoStructuredOutput,
        }.get(schema_key),
    )


def test_core_prompt_layers_are_locked_and_do_not_use_prompt_templates(prompt_cache):
    bundle = assembly.build_agent_core_prompt("demo_agent")

    assert bundle.agent_id == "demo_agent"
    assert bundle.layer_order == ("core_static", "core_generated")
    assert [layer.editable for layer in bundle.layers] == [False, False]
    assert [layer.locked for layer in bundle.layers] == [True, True]
    assert all("prompt_templates:" not in layer.source_ref for layer in bundle.layers)
    assert "DemoStructuredOutput structured output" in bundle.layers[1].content
    assert "Base prompt" not in bundle.render()
    assert "Group alpha rules" not in bundle.render()


def test_core_prompt_uses_structured_finalization_input_schema(monkeypatch, prompt_cache):
    monkeypatch.setattr(
        assembly,
        "load_agent_definitions",
        lambda: {
            "demo_agent_validation": _agent(
                output_schema="DemoStructuredOutput",
                structured_finalization={
                    "enabled": True,
                    "tool_name": "finalize_demo",
                    "input_schema": "DemoFinalizationInput",
                },
            )
        },
    )

    bundle = assembly.build_agent_core_prompt("demo_agent")
    generated_content = bundle.layers[1].content

    assert "DemoFinalizationInput structured output" in generated_content
    assert "produce JSON matching DemoFinalizationInput" in generated_content
    assert "DemoStructuredOutput structured output" not in generated_content
    assert "structured_finalization_input_schema:DemoFinalizationInput" in bundle.layers[1].source_ref


def test_core_generated_contract_summarizes_tool_and_domain_metadata(monkeypatch):
    monkeypatch.setattr(
        assembly,
        "load_agent_definitions",
        lambda: {
            "phenotype_extractor": _agent(
                folder_name="phenotype_extractor",
                agent_id="phenotype_extractor",
                category="Extraction",
                tools=[
                    "search_document",
                    "read_section",
                    "record_evidence",
                    "get_agent_contract",
                    "agr_curation_query",
                ],
                output_schema="PhenotypeResultEnvelope",
                domain_pack_id="agr.alliance.phenotype",
            )
        },
    )
    monkeypatch.setattr(
        assembly,
        "_domain_pack_validation_registries",
        lambda: {"agr.alliance.phenotype": _phenotype_registry_stub()},
    )

    bundle = assembly.build_agent_core_prompt("phenotype_extractor")
    generated = bundle.layers[1].content

    # KEEP — action-relevant lines
    assert "call at least one document retrieval tool" in generated
    assert "get_agent_contract" in generated
    assert "Domain envelope pack: agr.alliance.phenotype v0.1.0" in generated
    assert "No extractor should invent exact ontology CURIEs" in generated
    # NEW — single compact validator-owned-fields line replaces the per-field map
    assert "Validators own these fields" in generated
    assert "do not invent" in generated
    assert "PhenotypeTerm.curie" in generated  # at least one field named in the capped list

    # REMOVED — audit enumeration must no longer be inlined
    assert "Tool inventory from agent.yaml" not in generated
    assert "PhenotypeAnnotation(PhenotypeAnnotationPayload role=" not in generated
    assert "Pending unresolved shapes:" not in generated
    assert "->phenotype_term_ontology_validator" not in generated
    assert "accepted_prefixes<-literal:" not in generated

    # Tighter size bounds for the compact contract. NOTE: this test's fixture
    # agent sets output_schema="PhenotypeResultEnvelope" (stubbed to
    # DemoStructuredOutput), so the core_generated ``generated`` layer here also
    # carries the fixed ~9-line/~127-word "## CRITICAL ... STRUCTURED OUTPUT"
    # block plus a blank separator -- making this fixture 19 lines / 314 words.
    # The real phenotype_extractor has output_schema=None and no such block, so
    # its contract is smaller. The ceilings below sit just above this fixture
    # (runtime contract alone is ~9 lines / ~187 words, vs >30 lines pre-slim).
    assert len(generated.splitlines()) <= 20
    assert len(generated.split()) <= 330
    assert "prompt_templates:" not in bundle.layers[1].source_ref
    assert "domain_pack:agr.alliance.phenotype" in bundle.layers[1].source_ref


def test_phenotype_editable_prompts_do_not_duplicate_generated_contract_facts():
    agent_prompt_dir = (
        Path(__file__).resolve().parents[5]
        / "packages/alliance/agents/phenotype_extractor"
    )
    editable_prompt_paths = [
        agent_prompt_dir / "prompt.yaml",
        *sorted((agent_prompt_dir / "group_rules").glob("*.yaml")),
    ]
    content_by_path = {
        prompt_path: prompt_path.read_text(encoding="utf-8")
        for prompt_path in editable_prompt_paths
    }

    # Generated-contract INTERNALS that must never be hand-written into editable
    # prompts (they are injected by the runtime contract). NOTE: ``<tools>``,
    # ``<active_validator_binding_policy>``, ``PhenotypeResultEnvelope`` and the bare
    # ``curatable_objects[]`` were intentionally dropped from this list -- the
    # builder-pattern migration moved that domain guidance into the editable prompt
    # (e.g. "Do not hand-author `curatable_objects[]`", which the disease contract
    # test now *requires*), and the ``<tools>`` section predates extractors having
    # tool access. This test still guards the truly generated-only fragments below.
    forbidden_fragments = [
        "<search_infrastructure>",
        "<output_contract>",
        "<evidence_record_contract>",
        "phenotype_term_ontology_validator",
        "accepted_prefixes",
        "schema_ref.schema_id",
        "alliance_linkml",
        "1b11d0888f19eba4ca72022200bb7d96b30d4a52",
        "curatable_objects[] is the only semantic object list",
        "Shared domain-envelope output contract",
        "top-level `items[]`",
        "`annotations[]`",
        "PhenotypeAnnotation",
        "`object_role: curatable_unit`",
        "`model_ref: PhenotypeAnnotationPayload`",
        "`definition_state: in_development`",
        "blocked export/write metadata",
        "PhenotypeSubject",
        "PhenotypeTerm",
        "EvidenceQuote",
    ]
    for prompt_path, content in content_by_path.items():
        for fragment in forbidden_fragments:
            assert fragment not in content, f"{fragment!r} found in {prompt_path}"


def test_prompt_layers_keep_expected_order_and_editability(prompt_cache):
    bundle = assembly.build_agent_prompt_layers(
        "demo_agent_validation",
        group_id=["group_alpha", "GROUP_BETA", "group_alpha"],
        overlay="Curator emphasis",
        runtime_context={
            "document": "paper.pdf",
            "active_groups": ["GROUP_BETA", "GROUP_ALPHA"],
        },
    )

    assert bundle.layer_order == (
        "core_static",
        "core_generated",
        "base_prompt",
        "group_rules",
        "curator_overlay",
        "runtime_context",
    )

    by_kind = {layer.kind: layer for layer in bundle.layers}
    assert by_kind["base_prompt"].editable is True
    assert by_kind["base_prompt"].locked is False
    assert by_kind["base_prompt"].provenance == "prompt_template:system"
    assert "prompt_templates:" in by_kind["base_prompt"].source_ref

    assert by_kind["group_rules"].editable is True
    assert by_kind["group_rules"].locked is False
    assert by_kind["group_rules"].content.index("## GROUP_ALPHA") < by_kind[
        "group_rules"
    ].content.index("## GROUP_BETA")
    assert "Group alpha rules" in by_kind["group_rules"].content
    assert "Group beta rules" in by_kind["group_rules"].content

    assert by_kind["curator_overlay"].editable is True
    assert by_kind["curator_overlay"].locked is False
    assert by_kind["runtime_context"].editable is False
    assert by_kind["runtime_context"].locked is True
    assert by_kind["runtime_context"].content == (
        '{"active_groups":["GROUP_BETA","GROUP_ALPHA"],"document":"paper.pdf"}'
    )


def test_hashes_are_stable_for_same_inputs(prompt_cache):
    first = assembly.build_agent_prompt_layers(
        "demo_agent",
        group_id="GROUP_ALPHA",
        overlay="Curator emphasis",
        runtime_context={"document": "paper.pdf"},
    )
    second = assembly.build_agent_prompt_layers(
        "demo_agent",
        group_id="GROUP_ALPHA",
        overlay="Curator emphasis",
        runtime_context={"document": "paper.pdf"},
    )

    assert second.hash == first.hash
    assert [layer.hash for layer in second.layers] == [
        layer.hash for layer in first.layers
    ]
    assert first.to_manifest() == second.to_manifest()


def test_prompt_layers_reject_noncanonical_folder_alias(monkeypatch, prompt_cache):
    monkeypatch.setattr(
        assembly,
        "load_agent_definitions",
        lambda: {
            "ontology_term_validation": _agent(
                folder_name="ontology_term",
                agent_id="ontology_term_validation",
                system_agent_key="ontology_term_validation",
            )
        },
    )
    prompt_cache.clear()
    prompt_cache["ontology_term_validation:system:base"] = _prompt(
        "ontology_term_validation",
        "system",
        "Ontology term prompt",
    )

    bundle = assembly.build_agent_prompt_layers("ontology_term_validation")
    assert bundle.agent_id == "ontology_term_validation"

    with pytest.raises(ValueError, match="Unknown system agent 'ontology_term'"):
        assembly.build_agent_prompt_layers("ontology_term")


def test_base_prompt_is_required(prompt_cache):
    prompt_cache.pop("demo_agent:system:base")

    with pytest.raises(PromptNotFoundError):
        assembly.build_agent_prompt_layers("demo_agent")


def test_unregistered_output_schema_fails_core_builder(monkeypatch, prompt_cache):
    monkeypatch.setattr(assembly, "resolve_output_schema", lambda _schema_key: None)

    with pytest.raises(ValueError, match="DemoStructuredOutput"):
        assembly.build_agent_core_prompt("demo_agent")


def test_prompt_template_content_is_required(prompt_cache):
    prompt_cache["demo_agent:system:base"].content = None

    with pytest.raises(ValueError, match="demo_agent:system:base"):
        assembly.build_agent_prompt_layers("demo_agent")


def _value(value: str) -> SimpleNamespace:
    return SimpleNamespace(value=value)


def _field(
    field_path: str,
    *,
    required: bool = False,
    validator_binding_id: str | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        field_path=field_path,
        field_type=_value("string"),
        required=required,
        metadata=(
            {"validator_binding_id": validator_binding_id}
            if validator_binding_id
            else {}
        ),
    )


def _selector(source: str, *, path: str | None = None, value=None) -> SimpleNamespace:
    return SimpleNamespace(source=source, path=path, value=value)


def _phenotype_registry_stub() -> SimpleNamespace:
    metadata = SimpleNamespace(
        pack_id="agr.alliance.phenotype",
        version="0.1.0",
        status=_value("in_development"),
        metadata_api_version="1.0.0",
        metadata={"semantic_source": "domain_envelope.objects"},
        schema_refs=[
            SimpleNamespace(
                provider="alliance_linkml",
                name="PhenotypeAnnotation",
                version="1b11d0888f19eba4ca72022200bb7d96b30d4a52",
            )
        ],
        object_definitions=[
            SimpleNamespace(
                object_type="PhenotypeAnnotation",
                model_ref="PhenotypeAnnotationPayload",
                metadata={"object_role": "curatable_unit"},
                fields=[
                    _field("phenotype_annotation_object", required=True),
                    _field(
                        "phenotype_terms[0].curie",
                        required=True,
                        validator_binding_id="phenotype_term_ontology_validator",
                    ),
                    _field("evidence_record_ids[0]", required=True),
                ],
            ),
            SimpleNamespace(
                object_type="PhenotypeTerm",
                model_ref="PhenotypeTermPayload",
                metadata={
                    "object_role": "validated_reference",
                    "validation_state": "pending_ontology_resolution",
                },
                fields=[
                    _field(
                        "curie",
                        required=True,
                        validator_binding_id="phenotype_term_ontology_validator",
                    ),
                    _field(
                        "label",
                        validator_binding_id="phenotype_term_ontology_validator",
                    ),
                ],
            ),
        ],
    )
    binding = SimpleNamespace(
        state=assembly.ValidationBindingState.ACTIVE,
        binding_id="phenotype_term_ontology_validator",
        object_types=("PhenotypeTerm",),
        field_paths=("curie", "label"),
        input_fields={
            "curie": _selector("payload", path="curie"),
            "label": _selector("payload", path="label"),
            "ontology_family": _selector("literal", value="phenotype"),
            "accepted_prefixes": _selector(
                "literal",
                value=["MP", "WBPhenotype", "ZP"],
            ),
        },
        required=True,
        blocking=False,
        allow_opt_out=True,
    )
    return SimpleNamespace(
        domain_pack=SimpleNamespace(metadata=metadata),
        bindings=(binding,),
    )
