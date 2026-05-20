"""Focused helper tests for streaming_tools core runtime behavior."""

import json
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from agents import AgentOutputSchema
from pydantic import BaseModel

from src.lib.openai_agents import streaming_tools
from src.lib.openai_agents.models import GeneExtractionResultEnvelope
from src.lib.prompts.context import (
    bind_prompt_run,
    clear_prompt_context,
    commit_pending_prompts,
    get_used_prompt_runs,
    set_pending_prompts,
)
from src.models.sql.prompts import PromptTemplate
from src.schemas.domain_envelope import CuratableObjectEnvelope, DomainEnvelope
from src.schemas.models.domain_envelope_extraction import DomainEnvelopeExtractionResult


REPO_ROOT = Path(__file__).resolve().parents[5]
REPO_PACKAGES_DIR = REPO_ROOT / "packages"


class _Envelope(BaseModel):
    value: str


@pytest.fixture(autouse=True)
def _reset_streaming_state():
    clear_prompt_context()
    streaming_tools.reset_consecutive_call_tracker()
    streaming_tools.clear_collected_events()
    streaming_tools.set_live_event_list(None)
    yield
    clear_prompt_context()
    streaming_tools.reset_consecutive_call_tracker()
    streaming_tools.clear_collected_events()
    streaming_tools.set_live_event_list(None)


def test_extract_model_identifier_handles_string_and_object():
    assert streaming_tools._extract_model_identifier("gpt-4o") == "gpt-4o"
    assert streaming_tools._extract_model_identifier(SimpleNamespace(model=" groq/llama ")) == "groq/llama"
    assert streaming_tools._extract_model_identifier(SimpleNamespace()) == ""


def test_build_json_only_instruction_includes_schema_when_available():
    text = streaming_tools._build_json_only_instruction(_Envelope)
    assert "IMPORTANT OUTPUT FORMAT REQUIREMENT" in text
    assert "model_json_schema" not in text
    assert "value" in text


def test_build_json_only_instruction_without_schema():
    text = streaming_tools._build_json_only_instruction(None)
    assert "IMPORTANT OUTPUT FORMAT REQUIREMENT" in text
    assert "schema exactly" not in text.lower()


def test_domain_envelope_output_schema_uses_relaxed_sdk_strictness():
    source_agent = SimpleNamespace(
        name="Gene Extraction",
        output_type=GeneExtractionResultEnvelope,
    )

    runtime_agent = streaming_tools._apply_relaxed_output_schema_if_needed(
        source_agent,
        GeneExtractionResultEnvelope,
    )

    assert runtime_agent is not source_agent
    assert isinstance(runtime_agent.output_type, AgentOutputSchema)
    assert runtime_agent.output_type.output_type is GeneExtractionResultEnvelope
    assert runtime_agent.output_type.is_strict_json_schema() is False


def test_non_domain_envelope_output_schema_keeps_default_sdk_strictness():
    source_agent = SimpleNamespace(name="Simple Agent", output_type=_Envelope)

    runtime_agent = streaming_tools._apply_relaxed_output_schema_if_needed(
        source_agent,
        _Envelope,
    )

    assert runtime_agent is source_agent


def test_domain_envelope_reduction_prioritizes_materialized_fields_for_supervisor():
    envelope_output = json.dumps(
        {
            "envelope_id": "env-test-gene",
            "domain_pack_id": "gene",
            "domain_pack_version": "0.1.0",
            "objects": [
                {
                    "object_type": "gene_mention_evidence",
                    "object_role": "validated_reference",
                    "pending_ref_id": "gene-mention-evidence-1",
                    "status": "validated",
                    "payload": {
                        "mention": "Crumbs",
                        "proposed_primary_external_id": "FB:stale",
                        "primary_external_id": "FB:FBgn0259685",
                        "gene_symbol": "crb",
                        "taxon": "NCBITaxon:7227",
                        "verified_quote": "Crumbs regulates R8 cell fate.",
                    },
                }
            ],
            "validation_findings": [
                {
                    "code": "domain_pack.validator_resolved",
                    "severity": "info",
                    "status": "resolved",
                    "message": "Resolved Crumbs to crb.",
                }
            ],
        }
    )

    result = streaming_tools._reduce_specialist_output_for_supervisor(
        envelope_output,
        expected_output_type=GeneExtractionResultEnvelope,
    )

    assert "Use these validated/materialized values" in result
    assert "primary_external_id=FB:FBgn0259685" in result
    assert "gene_symbol=crb" in result
    assert "taxon=NCBITaxon:7227" in result
    assert "proposed_primary_external_id" not in result
    assert "verified_quote" not in result
    assert "Validation findings: resolved=1." in result
    with pytest.raises(json.JSONDecodeError):
        json.loads(result)


def test_runtime_instruction_append_updates_pending_prompt_assembly():
    clear_prompt_context()
    prompt = PromptTemplate(
        id=uuid.uuid4(),
        agent_name="gene",
        prompt_type="system",
        content="base",
        version=1,
        is_active=True,
    )
    source_agent = SimpleNamespace(name="Gene Specialist", instructions="base")
    prompt_run_id = set_pending_prompts(
        "Gene Specialist",
        [prompt],
        effective_prompt_hash="hash-1",
        layer_manifest={
            "agent_id": "gene",
            "layers": [
                {
                    "id": "gene:base_prompt",
                    "kind": "base_prompt",
                    "title": "Editable base prompt",
                    "content": "base",
                    "provenance": "prompt_template:system",
                    "editable": True,
                    "locked": False,
                    "source_ref": "prompt_templates:active:gene:system:base:v1",
                    "hash": "base-layer-hash",
                }
            ],
            "hash": "hash-1",
        },
    )
    bind_prompt_run(source_agent, prompt_run_id)

    runtime_agent = streaming_tools._append_agent_runtime_instruction(
        source_agent,
        source_agent,
        instruction="Runtime-only instruction.",
        layer_id_suffix="runtime_test",
        title="Runtime test instruction",
        source_ref="test:runtime_instruction",
    )
    commit_pending_prompts(runtime_agent)

    assert runtime_agent is not source_agent
    assert runtime_agent.instructions == "base\n\nRuntime-only instruction."
    used_run = get_used_prompt_runs()[0]
    assert used_run.assembly is not None
    assert used_run.assembly.effective_prompt_hash != "hash-1"
    assert used_run.assembly.layer_manifest["layers"][-1]["id"] == (
        "gene:runtime_context:runtime_test"
    )
    assert used_run.assembly.layer_manifest["layers"][-1]["content"] == (
        "Runtime-only instruction."
    )


def test_runtime_instruction_append_does_not_accumulate_on_reused_source_agent():
    clear_prompt_context()
    prompt = PromptTemplate(
        id=uuid.uuid4(),
        agent_name="gene",
        prompt_type="system",
        content="base",
        version=1,
        is_active=True,
    )
    source_agent = SimpleNamespace(name="Gene Specialist", instructions="base")
    prompt_run_id = set_pending_prompts(
        "Gene Specialist",
        [prompt],
        effective_prompt_hash="hash-1",
        layer_manifest={
            "agent_id": "gene",
            "layers": [
                {
                    "id": "gene:base_prompt",
                    "kind": "base_prompt",
                    "title": "Editable base prompt",
                    "content": "base",
                    "provenance": "prompt_template:system",
                    "editable": True,
                    "locked": False,
                    "source_ref": "prompt_templates:active:gene:system:base:v1",
                    "hash": "base-layer-hash",
                }
            ],
            "hash": "hash-1",
        },
    )
    bind_prompt_run(source_agent, prompt_run_id)

    first_runtime_agent = streaming_tools._append_agent_runtime_instruction(
        source_agent,
        source_agent,
        instruction="first runtime",
        layer_id_suffix="first",
        title="First runtime",
        source_ref="test:first_runtime",
    )
    commit_pending_prompts(first_runtime_agent)

    second_runtime_agent = streaming_tools._append_agent_runtime_instruction(
        source_agent,
        source_agent,
        instruction="second runtime",
        layer_id_suffix="second",
        title="Second runtime",
        source_ref="test:second_runtime",
    )
    commit_pending_prompts(second_runtime_agent)

    used_runs = get_used_prompt_runs()
    first_layers = used_runs[0].assembly.layer_manifest["layers"]
    second_layers = used_runs[1].assembly.layer_manifest["layers"]

    assert [layer["id"] for layer in first_layers] == [
        "gene:base_prompt",
        "gene:runtime_context:first",
    ]
    assert [layer["id"] for layer in second_layers] == [
        "gene:base_prompt",
        "gene:runtime_context:second",
    ]
    assert first_layers[-1]["content"] == "first runtime"
    assert second_layers[-1]["content"] == "second runtime"


def test_extract_tool_name_prefers_name_then_tool_name():
    assert streaming_tools._extract_tool_name(SimpleNamespace(name="search_document")) == "search_document"
    assert streaming_tools._extract_tool_name(SimpleNamespace(tool_name="agr_curation_query")) == "agr_curation_query"
    assert streaming_tools._extract_tool_name(SimpleNamespace()) == ""


def test_required_tool_names_for_agent_returns_agr_when_only_agr_tool_present():
    agent = SimpleNamespace(tools=[SimpleNamespace(name="agr_curation_query")])
    assert streaming_tools._required_tool_names_for_agent(agent) == {"agr_curation_query"}


def test_agent_tool_names_normalizes_known_tools():
    agent = SimpleNamespace(
        tools=[
            SimpleNamespace(name="search_document"),
            SimpleNamespace(tool_name="read_section"),
            SimpleNamespace(name="  "),
        ]
    )
    assert streaming_tools._agent_tool_names(agent) == {"search_document", "read_section"}


def test_estimate_bulk_entity_count_filters_noise_and_deduplicates():
    query = """
    Query: validate genes
    List:
    daf-16, lin-3, daf-16, , notes: ignore this, unc-54
    """
    assert streaming_tools._estimate_bulk_entity_count(query) == 3


def test_build_tool_efficiency_instruction_only_for_large_agr_lists():
    agr_agent = SimpleNamespace(tools=[SimpleNamespace(name="agr_curation_query")])
    non_agr_agent = SimpleNamespace(tools=[SimpleNamespace(name="search_document")])
    small_query = "List: a, b, c"
    large_query = "List: " + ", ".join(f"gene_{idx}" for idx in range(10))

    assert streaming_tools._build_tool_efficiency_instruction(non_agr_agent, large_query) == ""
    assert streaming_tools._build_tool_efficiency_instruction(agr_agent, small_query) == ""
    assert "TOOL EFFICIENCY REQUIREMENT" in streaming_tools._build_tool_efficiency_instruction(agr_agent, large_query)


def test_consecutive_tracker_and_batching_nudge_generation(monkeypatch):
    streaming_tools.reset_consecutive_call_tracker()
    monkeypatch.setattr(
        streaming_tools,
        "get_batching_config",
        lambda: {
            "ask_gene_specialist": {
                "entity": "genes",
                "example": 'ask_gene_specialist("Look up these genes: daf-16, lin-3")',
            }
        },
    )

    assert streaming_tools._track_specialist_call("ask_gene_specialist") == 1
    assert streaming_tools._generate_batching_nudge("ask_gene_specialist", 1) is None
    assert streaming_tools._track_specialist_call("ask_gene_specialist") == 2
    nudge = streaming_tools._generate_batching_nudge("ask_gene_specialist", 3)
    assert nudge is not None
    assert "individual genes" in nudge


def test_collected_events_and_live_list_modes():
    streaming_tools.clear_collected_events()
    streaming_tools.set_live_event_list(None)

    event_a = {"type": "TOOL_START"}
    streaming_tools.add_specialist_event(event_a)
    assert streaming_tools.get_collected_events() == [event_a]

    live = []
    streaming_tools.set_live_event_list(live)
    event_b = {"type": "TOOL_COMPLETE"}
    streaming_tools.add_specialist_event(event_b)
    assert live == [event_b]
    assert streaming_tools.get_collected_events() == [event_a]

    streaming_tools.set_live_event_list(None)


def test_emit_chunk_provenance_from_search_document_emits_events(monkeypatch):
    emitted = []
    monkeypatch.setattr(streaming_tools, "add_specialist_event", emitted.append)

    output = {
        "hits": [
            {"chunk_id": "chunk-1", "doc_items": [{"page": 1}]},
            {"chunk_id": "chunk-2", "page_number": 2},
        ]
    }
    streaming_tools._emit_chunk_provenance_from_output("search_document", output)

    assert len(emitted) == 2
    assert emitted[0]["type"] == "CHUNK_PROVENANCE"
    assert emitted[0]["chunk_id"] == "chunk-1"
    assert emitted[1]["doc_items"] == [{"page": 2}]


def test_emit_chunk_provenance_from_read_section_emits_when_doc_items_present(monkeypatch):
    emitted = []
    monkeypatch.setattr(streaming_tools, "add_specialist_event", emitted.append)

    output = {
        "section": {
            "section_title": "Methods",
            "doc_items": [{"page": 3, "bbox": [0, 0, 1, 1]}],
        }
    }
    streaming_tools._emit_chunk_provenance_from_output("read_section", output)

    assert len(emitted) == 1
    assert emitted[0]["chunk_id"] == "section:Methods"


def test_emit_chunk_provenance_handles_invalid_json_string_gracefully():
    # Should not raise
    streaming_tools._emit_chunk_provenance_from_output("search_document", "{bad json")


def test_required_tool_failure_message_for_document_tools():
    agent = SimpleNamespace(tools=[SimpleNamespace(name="search_document")])
    msg = streaming_tools._required_tool_failure_message(
        agent=agent,
        specialist_name="PDF Specialist",
        tool_calls=[SimpleNamespace(tool_name="read_metadata")],
    )
    assert msg is not None
    assert "required document tools" in msg


def _gene_extractor_domain_output() -> str:
    return json.dumps(
        {
            "summary": "Retained one crumbs gene mention.",
            "curatable_objects": [
                {
                    "object_type": "gene_mention_evidence",
                    "object_role": "validated_reference",
                    "pending_ref_id": "gene-mention-evidence-crumbs-1",
                    "model_ref": "GeneMentionEvidencePayload",
                    "schema_ref": {
                        "schema_id": "alliance.linkml.Gene",
                        "provider": "alliance_linkml",
                        "name": "Gene",
                        "version": "1b11d0888f19eba4ca72022200bb7d96b30d4a52",
                    },
                    "definition_state": "in_development",
                    "definition_notes": [
                        "Envelope-only validated reference evidence; this object does not create or mutate Alliance Gene rows."
                    ],
                    "payload": {
                        "mention": "crumbs",
                        "species": "Drosophila melanogaster",
                        "taxon_hint": "NCBITaxon:7227",
                        "data_provider_hint": "FB",
                        "proposed_primary_external_id": None,
                        "proposed_gene_symbol": "crb",
                        "proposed_taxon": None,
                        "identity_resolution_notes": [],
                        "confidence": "high",
                        "evidence_record_id": "evidence-crumbs-1",
                        "verified_quote": "Crumbs protein acts as a positional cue for rhabdomere morphogenesis.",
                        "page": 1,
                        "section": "Results",
                        "chunk_id": "chunk-crumbs-1",
                    },
                    "evidence_record_ids": ["evidence-crumbs-1"],
                    "metadata_refs": [
                        {"metadata_path": "raw_mentions[0]", "role": "source_mention"},
                        {"metadata_path": "evidence_records[0]", "role": "supporting_evidence"},
                    ],
                }
            ],
            "metadata": {
                "raw_mentions": [
                    {
                        "mention": "crumbs",
                        "entity_type": "gene",
                        "evidence_record_ids": ["evidence-crumbs-1"],
                    }
                ],
                "evidence_records": [
                    {
                        "evidence_record_id": "evidence-crumbs-1",
                        "entity": "crumbs",
                        "verified_quote": "Crumbs protein acts as a positional cue for rhabdomere morphogenesis.",
                        "page": 1,
                        "section": "Results",
                        "chunk_id": "chunk-crumbs-1",
                    }
                ],
                "normalization_notes": [],
                "exclusions": [],
                "ambiguities": [],
                "notes": [],
                "provenance": {},
            },
            "run_summary": {
                "candidate_count": 1,
                "kept_count": 1,
                "excluded_count": 0,
                "ambiguous_count": 0,
                "warnings": [],
            },
        }
    )


def _resolved_gene_validator_payload(request):
    return {
        "status": "resolved",
        "request_id": request.request_id,
        "validator_binding_id": request.validator_binding_id,
        "validator_agent": request.validator_agent.model_dump(mode="json"),
        "target": request.target.model_dump(mode="json"),
        "resolved_values": {
            "curie": "FB:FBgn0259685",
            "symbol": "crb",
            "taxon": "NCBITaxon:7227",
        },
        "resolved_objects": [],
        "missing_expected_fields": [],
        "candidates": [],
        "lookup_attempts": [
            {
                "provider": "agr_curation_query",
                "method": "search_genes",
                "query": {"gene_symbol": request.selected_inputs["mention"]},
                "result_count": 1,
                "outcome": "success",
            }
        ],
        "curator_message": None,
        "explanation": "Resolved crumbs against FlyBase.",
    }


def _errored_gene_validator_payload(request):
    return {
        "status": "unresolved",
        "request_id": request.request_id,
        "validator_binding_id": request.validator_binding_id,
        "validator_agent": request.validator_agent.model_dump(mode="json"),
        "target": request.target.model_dump(mode="json"),
        "resolved_values": {},
        "resolved_objects": [],
        "missing_expected_fields": ["curie", "symbol", "taxon"],
        "candidates": [],
        "lookup_attempts": [
            {
                "provider": "domain_validator_dispatch",
                "method": "validator_agent_error",
                "query": {
                    "request_id": request.request_id,
                    "selected_inputs": dict(request.selected_inputs),
                },
                "result_count": 0,
                "outcome": "error",
                "message": "Validator agent execution failed: schema compile error",
            }
        ],
        "curator_message": "Validator agent execution failed: schema compile error",
        "explanation": "Validator agent execution failed: schema compile error",
    }


def _generic_unresolved_validator_payload(request):
    return {
        "status": "unresolved",
        "request_id": request.request_id,
        "validator_binding_id": request.validator_binding_id,
        "validator_agent": request.validator_agent.model_dump(mode="json"),
        "target": request.target.model_dump(mode="json"),
        "resolved_values": {},
        "resolved_objects": [],
        "missing_expected_fields": list(request.target.expected_fields),
        "candidates": [],
        "lookup_attempts": [
            {
                "provider": "contract_fixture",
                "method": "package_scoped_validator",
                "query": dict(request.selected_inputs),
                "result_count": 0,
                "outcome": "not_found",
            }
        ],
        "curator_message": None,
        "explanation": "Contract fixture unresolved validator result.",
    }


def _chat_dispatch_probe_output() -> str:
    return json.dumps(
        {
            "summary": "Retained one candidate.",
            "curatable_objects": [{"object_type": "dispatch_probe"}],
            "metadata": {},
            "run_summary": {"candidate_count": 1},
        }
    )


def _chat_dispatch_domain_cases():
    return [
        pytest.param(
            "ask_allele_extractor_specialist",
            "allele_extractor",
            "allele",
            DomainEnvelope(
                envelope_id="chat-allele-env",
                domain_pack_id="agr.alliance.allele",
                objects=[
                    CuratableObjectEnvelope(
                        object_type="AlleleMention",
                        pending_ref_id="allele-mention-1",
                        payload={
                            "mention": {"text": "crb 11A22"},
                            "associated_gene": {"symbol": "crb"},
                            "taxon": {"curie": "NCBITaxon:7227"},
                        },
                    )
                ],
            ),
            {"allele_mention_reference_validation"},
            id="allele",
        ),
        pytest.param(
            "ask_disease_extractor_specialist",
            "disease_extractor",
            "disease",
            DomainEnvelope(
                envelope_id="chat-disease-env",
                domain_pack_id="agr.alliance.disease",
                objects=[
                    CuratableObjectEnvelope(
                        object_type="DiseaseAnnotation",
                        pending_ref_id="disease-annotation-1",
                        payload={
                            "disease_annotation_object": {
                                "curie": "DOID:0050434",
                                "name": "Andersen-Tawil syndrome",
                            },
                            "disease_relation_name": "is_model_of",
                            "condition_relations": [
                                {
                                    "condition_relation_type": {
                                        "name": "has_condition",
                                    }
                                }
                            ],
                            "data_provider": {"abbreviation": "MGI"},
                        },
                    )
                ],
            ),
            {
                "disease_ontology_term_lookup",
                "disease_relation_cv_lookup",
                "disease_condition_relation_lookup",
                "disease_data_provider_lookup",
            },
            id="disease",
        ),
        pytest.param(
            "ask_chemical_extractor_specialist",
            "chemical_extractor",
            "chemical",
            DomainEnvelope(
                envelope_id="chat-chemical-env",
                domain_pack_id="agr.alliance.chemical_condition",
                objects=[
                    CuratableObjectEnvelope(
                        object_type="ChemicalCondition",
                        pending_ref_id="chemical-condition-1",
                        payload={
                            "condition_chemical": {
                                "curie": "CHEBI:23965",
                                "name": "estradiol",
                            },
                            "condition_class": {
                                "curie": "ZECO:0000101",
                                "name": "chemical treatment",
                            },
                            "condition_relation_type": {
                                "name": "has_condition",
                            },
                        },
                    ),
                    CuratableObjectEnvelope(
                        object_type="ChemicalTerm",
                        pending_ref_id="chemical-term-1",
                        payload={
                            "curie": "CHEBI:23965",
                            "name": "estradiol",
                        },
                    ),
                ],
            ),
            {
                "chemical_condition.chebi_api_lookup",
                "chemical_condition.term_chebi_api_lookup",
                "chemical_condition.condition_ontology_lookup",
                "chemical_condition.condition_relation_type_lookup",
            },
            id="chemical",
        ),
        pytest.param(
            "ask_phenotype_extractor_specialist",
            "phenotype_extractor",
            "phenotype",
            DomainEnvelope(
                envelope_id="chat-phenotype-env",
                domain_pack_id="agr.alliance.phenotype",
                objects=[
                    CuratableObjectEnvelope(
                        object_type="PhenotypeTerm",
                        object_role="validated_reference",
                        pending_ref_id="phenotype-term-1",
                        payload={
                            "resolution_state": "pending_ontology_resolution",
                            "curie": "WBPhenotype:0000886",
                            "label": "reduced brood size",
                            "ontology_lookup_hint": {
                                "data_provider": "WB",
                                "taxon_id": "NCBITaxon:6239",
                            },
                        },
                    )
                ],
            ),
            {"phenotype_term_ontology_validator"},
            id="phenotype",
        ),
        pytest.param(
            "ask_gene_expression_specialist",
            "gene_expression",
            "gene_expression",
            DomainEnvelope(
                envelope_id="chat-gene-expression-env",
                domain_pack_id="agr.alliance.gene_expression",
                objects=[
                    CuratableObjectEnvelope(
                        object_type="GeneExpressionAnnotation",
                        pending_ref_id="gene-expression-annotation-1",
                        payload={
                            "relation": {"name": "is_expressed_in"},
                            "data_provider": {"abbreviation": "ZFIN"},
                        },
                    )
                ],
            ),
            {
                "relation_vocabulary_validation",
                "data_provider_validation",
            },
            id="gene-expression",
        ),
    ]


@pytest.mark.asyncio
async def test_chat_domain_envelope_dispatch_runs_before_supervisor_reduction(monkeypatch):
    emitted = []
    monkeypatch.setattr(streaming_tools, "add_specialist_event", emitted.append)

    from src.lib.curation_workspace import adapter_registry
    from src.lib.curation_workspace import curation_prep_service, extraction_results
    from src.lib.domain_packs import validator_dispatch

    monkeypatch.setattr(
        extraction_results,
        "_get_agent_curation_metadata",
        lambda agent_key: {"adapter_key": "gene", "launchable": True},
    )

    source_envelope = SimpleNamespace(
        domain_pack_id="gene",
        objects=[SimpleNamespace()],
    )
    monkeypatch.setattr(
        curation_prep_service,
        "_domain_envelope_from_extraction_result",
        lambda _record: source_envelope,
    )
    monkeypatch.setattr(
        adapter_registry,
        "resolve_curation_domain_pack_by_id",
        lambda domain_pack_id: SimpleNamespace(pack_id=domain_pack_id),
    )

    dispatched = {}

    validated_envelope = SimpleNamespace(
        model_dump=lambda mode="json": {
            "envelope_id": "chat-runtime",
            "domain_pack_id": "gene",
            "objects": [
                {
                    "object_type": "gene_mention_evidence",
                    "payload": {
                        "mention": "crumbs",
                        "primary_external_id": "FB:FBgn0259685",
                        "gene_symbol": "crb",
                        "taxon": "NCBITaxon:7227",
                    },
                }
            ],
            "validation_findings": [],
            "metadata": {"validated": True},
        }
    )

    def _fake_dispatch(envelope, domain_pack, **kwargs):
        dispatched["envelope"] = envelope
        dispatched["domain_pack"] = domain_pack
        dispatched["kwargs"] = kwargs
        return SimpleNamespace(
            envelope=validated_envelope,
            matched_bindings=(SimpleNamespace(),),
            validator_results=(SimpleNamespace(),),
            appended_findings=(),
        )

    monkeypatch.setattr(
        validator_dispatch,
        "dispatch_active_validator_bindings",
        _fake_dispatch,
    )

    extractor_output = json.dumps(
        {
            "summary": "Retained crumbs.",
            "curatable_objects": [{"object_type": "gene_mention_evidence"}],
            "metadata": {},
            "run_summary": {"candidate_count": 1},
        }
    )

    result = await streaming_tools._dispatch_domain_envelope_validators_for_chat(
        extractor_output,
        expected_output_type=GeneExtractionResultEnvelope,
        specialist_name="Gene Extraction",
        tool_name="ask_gene_extractor_specialist",
    )

    payload = json.loads(result)
    assert payload["metadata"]["validated"] is True
    assert payload["objects"][0]["payload"]["primary_external_id"] == "FB:FBgn0259685"
    assert dispatched["envelope"] is source_envelope
    assert dispatched["domain_pack"].pack_id == "gene"
    assert dispatched["kwargs"]["source_envelope_revision"] == 1
    assert [event["type"] for event in emitted] == ["TOOL_START", "TOOL_COMPLETE"]
    assert emitted[0]["details"]["toolName"] == "dispatch_active_validator_bindings"


@pytest.mark.asyncio
async def test_chat_domain_envelope_dispatch_uses_real_gene_binding(monkeypatch):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))
    emitted = []
    monkeypatch.setattr(streaming_tools, "add_specialist_event", emitted.append)

    from src.lib.curation_workspace import adapter_registry, extraction_results
    from src.lib.domain_packs import validator_dispatch

    adapter_registry.load_curation_adapter_registry.cache_clear()
    monkeypatch.setattr(
        extraction_results,
        "_get_agent_curation_metadata",
        lambda agent_key: {"adapter_key": "gene", "launchable": True},
    )

    captured_requests = []

    def _fake_validator_agent(request, *, binding):
        captured_requests.append(request)
        return _resolved_gene_validator_payload(request)

    monkeypatch.setattr(
        validator_dispatch,
        "run_package_scoped_validator_agent",
        _fake_validator_agent,
    )

    result = await streaming_tools._dispatch_domain_envelope_validators_for_chat(
        _gene_extractor_domain_output(),
        expected_output_type=GeneExtractionResultEnvelope,
        specialist_name="Gene Extraction",
        tool_name="ask_gene_extractor_specialist",
    )

    payload = json.loads(result)
    assert captured_requests
    request = captured_requests[0]
    assert request.validator_binding_id == "alliance_gene_reference_lookup"
    assert request.validator_agent.agent_id == "gene_validation"
    assert request.selected_inputs["mention"] == "crumbs"
    assert request.selected_inputs["data_provider_hint"] == "FB"
    assert request.selected_inputs["taxon_hint"] == "NCBITaxon:7227"
    assert request.selected_inputs["evidence_quote"].startswith("Crumbs protein")
    assert payload["objects"][0]["payload"]["primary_external_id"] == "FB:FBgn0259685"
    assert payload["objects"][0]["payload"]["gene_symbol"] == "crb"
    assert payload["objects"][0]["payload"]["taxon"] == "NCBITaxon:7227"
    assert payload["validation_findings"]
    assert emitted[0]["details"]["toolArgs"]["domain_pack_id"] == "gene"
    lookup_events = [
        event
        for event in emitted
        if event["details"]["toolName"] == "agr_curation_query"
    ]
    assert [event["type"] for event in lookup_events] == [
        "TOOL_START",
        "TOOL_COMPLETE",
    ]
    assert lookup_events[0]["details"]["toolArgs"] == {
        "gene_symbol": "crumbs",
        "method": "search_genes",
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "tool_name,agent_key,adapter_key,envelope,expected_binding_ids",
    _chat_dispatch_domain_cases(),
)
async def test_chat_domain_envelope_dispatch_covers_launchable_active_validator_domains(
    monkeypatch,
    tool_name,
    agent_key,
    adapter_key,
    envelope,
    expected_binding_ids,
):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))
    emitted = []
    monkeypatch.setattr(streaming_tools, "add_specialist_event", emitted.append)

    from src.lib.curation_workspace import (
        adapter_registry,
        curation_prep_service,
        extraction_results,
    )
    from src.lib.domain_packs import validator_dispatch

    adapter_registry.load_curation_adapter_registry.cache_clear()
    monkeypatch.setattr(
        extraction_results,
        "_get_agent_curation_metadata",
        lambda requested_agent_key: {
            "adapter_key": adapter_key,
            "domain_pack_id": envelope.domain_pack_id,
            "launchable": True,
        }
        if requested_agent_key == agent_key
        else None,
    )
    monkeypatch.setattr(
        curation_prep_service,
        "_domain_envelope_from_extraction_result",
        lambda _record: envelope,
    )

    captured_requests = []

    def _fake_validator_agent(request, *, binding):
        captured_requests.append(request)
        return _generic_unresolved_validator_payload(request)

    monkeypatch.setattr(
        validator_dispatch,
        "run_package_scoped_validator_agent",
        _fake_validator_agent,
    )

    result = await streaming_tools._dispatch_domain_envelope_validators_for_chat(
        _chat_dispatch_probe_output(),
        expected_output_type=DomainEnvelopeExtractionResult,
        specialist_name=f"{agent_key} chat extraction",
        tool_name=tool_name,
    )

    payload = json.loads(result)
    captured_binding_ids = {
        request.validator_binding_id for request in captured_requests
    }
    assert captured_binding_ids == expected_binding_ids
    assert payload["domain_pack_id"] == envelope.domain_pack_id
    assert payload["validation_findings"]

    dispatch_complete = [
        event
        for event in emitted
        if event["details"]["toolName"] == "dispatch_active_validator_bindings"
        and event["type"] == "TOOL_COMPLETE"
    ][0]
    assert len(captured_requests) >= len(expected_binding_ids)
    assert dispatch_complete["details"]["matchedBindingCount"] == len(
        captured_requests
    )
    assert dispatch_complete["details"]["validatorResultCount"] == len(
        captured_requests
    )


@pytest.mark.asyncio
async def test_chat_domain_envelope_dispatch_surfaces_validator_lookup_errors(
    monkeypatch,
):
    monkeypatch.setenv("AGR_RUNTIME_PACKAGES_DIR", str(REPO_PACKAGES_DIR))
    emitted = []
    monkeypatch.setattr(streaming_tools, "add_specialist_event", emitted.append)

    from src.lib.curation_workspace import adapter_registry, extraction_results
    from src.lib.domain_packs import validator_dispatch

    adapter_registry.load_curation_adapter_registry.cache_clear()
    monkeypatch.setattr(
        extraction_results,
        "_get_agent_curation_metadata",
        lambda agent_key: {"adapter_key": "gene", "launchable": True},
    )
    monkeypatch.setattr(
        validator_dispatch,
        "run_package_scoped_validator_agent",
        lambda request, *, binding: _errored_gene_validator_payload(request),
    )

    result = await streaming_tools._dispatch_domain_envelope_validators_for_chat(
        _gene_extractor_domain_output(),
        expected_output_type=GeneExtractionResultEnvelope,
        specialist_name="Gene Extraction",
        tool_name="ask_gene_extractor_specialist",
    )

    payload = json.loads(result)
    assert payload["validation_findings"]
    dispatch_complete = [
        event
        for event in emitted
        if event["details"]["toolName"] == "dispatch_active_validator_bindings"
        and event["type"] == "TOOL_COMPLETE"
    ][0]
    assert dispatch_complete["details"]["success"] is False
    assert "(unresolved 1)" in dispatch_complete["details"]["friendlyName"]

    lookup_complete = [
        event
        for event in emitted
        if event["details"]["toolName"] == "domain_validator_lookup"
        and event["type"] == "TOOL_COMPLETE"
    ][0]
    assert lookup_complete["details"]["success"] is False
    assert lookup_complete["details"]["outcome"] == "error"
    assert lookup_complete["details"]["error"].startswith(
        "Validator agent execution failed"
    )


@pytest.mark.asyncio
async def test_chat_domain_envelope_dispatch_fails_closed_without_tool_agent_key():
    with pytest.raises(streaming_tools.SpecialistOutputError, match="source agent"):
        await streaming_tools._dispatch_domain_envelope_validators_for_chat(
            _gene_extractor_domain_output(),
            expected_output_type=GeneExtractionResultEnvelope,
            specialist_name="Gene Extraction",
            tool_name=None,
        )


@pytest.mark.asyncio
async def test_chat_domain_envelope_dispatch_fails_closed_without_curation_adapter(
    monkeypatch,
):
    from src.lib.curation_workspace import extraction_results

    monkeypatch.setattr(
        extraction_results,
        "_get_agent_curation_metadata",
        lambda agent_key: None,
    )

    with pytest.raises(streaming_tools.SpecialistOutputError, match="adapter ownership"):
        await streaming_tools._dispatch_domain_envelope_validators_for_chat(
            _gene_extractor_domain_output(),
            expected_output_type=GeneExtractionResultEnvelope,
            specialist_name="Gene Extraction",
            tool_name="ask_gene_extractor_specialist",
        )
