"""Focused helper tests for streaming_tools core runtime behavior."""

import json
import os
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from agents import AgentOutputSchema
from pydantic import BaseModel

from src.lib.curation_workspace import adapter_registry
from src.lib.config import schema_discovery
from src.lib.openai_agents import streaming_tools
from src.lib.openai_agents.models import (
    GeneExtractionResultEnvelope,
    PdfExtractionResultEnvelope,
)
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


class _FakeRunResult:
    def __init__(self, events=None, final_output=None, new_items=None):
        self._events = events or []
        self.final_output = final_output
        self.new_items = new_items or []

    async def stream_events(self):
        for event in self._events:
            yield event

    def to_input_list(self):
        return [{"role": "user", "content": "prior query"}]


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


@pytest.fixture(scope="module")
def _repo_package_curation_registry():
    # The repo package registry is intentionally shared: rebuilding it costs
    # about 1.5s, while these dispatch tests vary metadata/env around a stable
    # package set rather than mutating the package registry itself.
    original_packages_dir = os.environ.get("AGR_RUNTIME_PACKAGES_DIR")
    os.environ["AGR_RUNTIME_PACKAGES_DIR"] = str(REPO_PACKAGES_DIR)
    adapter_registry.load_curation_adapter_registry.cache_clear()
    try:
        yield
    finally:
        adapter_registry.load_curation_adapter_registry.cache_clear()
        if original_packages_dir is None:
            os.environ.pop("AGR_RUNTIME_PACKAGES_DIR", None)
        else:
            os.environ["AGR_RUNTIME_PACKAGES_DIR"] = original_packages_dir


def test_extract_model_identifier_handles_string_and_object():
    assert streaming_tools._extract_model_identifier("gpt-4o") == "gpt-4o"
    assert streaming_tools._extract_model_identifier(SimpleNamespace(model=" groq/llama ")) == "groq/llama"
    assert streaming_tools._extract_model_identifier(SimpleNamespace()) == ""


@pytest.mark.asyncio
async def test_run_specialist_preserves_parent_tracing_and_enables_sensitive_data(monkeypatch):
    captured = {}

    def _run_streamed(_agent, *args, **kwargs):
        captured["run_config"] = kwargs["run_config"]
        return _FakeRunResult(events=[], final_output="specialist output", new_items=[])

    monkeypatch.setattr(streaming_tools, "commit_pending_prompts", lambda _agent: None)
    monkeypatch.setattr(streaming_tools.Runner, "run_streamed", _run_streamed)

    parent_config = streaming_tools.RunConfig(
        tracing_disabled=False,
        trace_include_sensitive_data=False,
        workflow_name="parent workflow",
        group_id="session-1",
    )
    agent = SimpleNamespace(
        name="Plain Text Specialist",
        tools=[],
        output_type=None,
        instructions="",
        model="gpt-4o",
    )

    result = await streaming_tools.run_specialist_with_events(
        agent=agent,
        input_text="summarize findings",
        specialist_name="Plain Text Specialist",
        run_config=parent_config,
        max_turns=3,
        tool_name=None,
    )

    assert result == "specialist output"
    assert captured["run_config"].tracing_disabled is False
    assert captured["run_config"].trace_include_sensitive_data is True
    assert captured["run_config"].workflow_name == "parent workflow"
    assert captured["run_config"].group_id == "session-1"


@pytest.mark.asyncio
async def test_run_specialist_without_parent_config_keeps_sdk_tracing_disabled(monkeypatch):
    captured = {}

    def _run_streamed(_agent, *args, **kwargs):
        captured["run_config"] = kwargs["run_config"]
        return _FakeRunResult(events=[], final_output="specialist output", new_items=[])

    monkeypatch.setattr(streaming_tools, "commit_pending_prompts", lambda _agent: None)
    monkeypatch.setattr(streaming_tools.Runner, "run_streamed", _run_streamed)

    agent = SimpleNamespace(
        name="Plain Text Specialist",
        tools=[],
        output_type=None,
        instructions="",
        model="gpt-4o",
    )

    result = await streaming_tools.run_specialist_with_events(
        agent=agent,
        input_text="summarize findings",
        specialist_name="Plain Text Specialist",
        max_turns=3,
        tool_name=None,
    )

    assert result == "specialist output"
    assert captured["run_config"].tracing_disabled is True
    assert captured["run_config"].trace_include_sensitive_data is True


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


def test_builder_materializer_agent_detection_uses_finalization_tool_name():
    assert streaming_tools._is_builder_materializer_agent(
        SimpleNamespace(
            tools=[
                SimpleNamespace(name="search_document"),
                SimpleNamespace(name="finalize_gene_expression_extraction"),
            ]
        )
    )
    assert not streaming_tools._is_builder_materializer_agent(
        SimpleNamespace(tools=[SimpleNamespace(name="search_document")])
    )


def _pdf_live_evidence_record() -> dict:
    return {
        "evidence_record_id": "evidence-pdf-1",
        "entity": "principal finding",
        "verified_quote": "The principal finding is supported by this exact sentence.",
        "page": 2,
        "section": "Results",
        "chunk_id": "chunk-pdf-1",
    }


def _pdf_finalization_payload(*, evidence_record_id: str = "evidence-pdf-1") -> dict:
    evidence_record = _pdf_live_evidence_record()
    if evidence_record_id != evidence_record["evidence_record_id"]:
        evidence_record = {**evidence_record, "evidence_record_id": evidence_record_id}
    return {
        "answer": "The principal finding was supported.",
        "summary": "Checked the Results section and retained one supported claim.",
        "items": [
            {
                "label": "principal finding",
                "entity_type": "claim",
                "source_mentions": ["principal finding"],
                "evidence_record_ids": [evidence_record_id],
            }
        ],
        "raw_mentions": [],
        "evidence_records": [evidence_record],
        "normalization_notes": [],
        "exclusions": [],
        "ambiguities": [],
        "run_summary": {
            "candidate_count": 1,
            "kept_count": 1,
            "excluded_count": 0,
            "ambiguous_count": 0,
            "warnings": [],
        },
    }


def _package_schema(schema_name: str):
    schemas = schema_discovery.discover_agent_schemas(force_reload=True)
    return schemas[schema_name]


def _finalization_config(tool_name: str) -> dict:
    return streaming_tools._agent_structured_finalization_config(
        SimpleNamespace(),
        tool_name=tool_name,
    )


def _validator_result_payload(
    *,
    status: str = "resolved",
    agent_id: str = "gene_ontology_lookup",
    target_inputs: dict | None = None,
    expected_fields: list[str] | None = None,
    lookup_attempts: list[dict] | None = None,
    missing_expected_fields: list[str] | None = None,
    resolved_values: dict | None = None,
    resolved_objects: list[dict] | None = None,
    **schema_fields,
) -> dict:
    return {
        "status": status,
        "request_id": "request-lookup-1",
        "validator_binding_id": "binding-lookup-1",
        "validator_agent": {
            "package_id": "agr.alliance",
            "agent_id": agent_id,
        },
        "target": {
            "domain_pack_id": "lookup",
            "object_type": "lookup_target",
            "object_id": "lookup-target-1",
            "object_role": "validated_reference",
            "field_path": "payload.term",
            "expected_fields": expected_fields or ["results"],
            "input_values": target_inputs or {"go_id": "GO:0003677"},
        },
        "resolved_values": resolved_values or {},
        "resolved_objects": resolved_objects or [],
        "missing_expected_fields": missing_expected_fields or [],
        "candidates": [],
        "lookup_attempts": lookup_attempts or [],
        "curator_message": "Lookup checked.",
        "explanation": "Lookup result is tied to API evidence.",
        **schema_fields,
    }


def _lookup_attempt(
    *,
    provider: str = "quickgo_api_call",
    url: str = "https://www.ebi.ac.uk/QuickGO/services/ontology/go/terms/GO:0003677",
    outcome: str = "success",
    result_count: int = 1,
) -> dict:
    return {
        "provider": provider,
        "method": "GET",
        "query": {"url": url},
        "result_count": result_count,
        "outcome": outcome,
    }


def _lookup_tool_call(
    *,
    tool_name: str = "quickgo_api_call",
    url: str = "https://www.ebi.ac.uk/QuickGO/services/ontology/go/terms/GO:0003677",
    status: str = "ok",
    status_code: int = 200,
    data: dict | None = None,
) -> streaming_tools.SpecialistToolCall:
    return streaming_tools.SpecialistToolCall(
        tool_name=tool_name,
        tool_args={"url": url, "method": "GET"},
        output_payload={
            "status": status,
            "status_code": status_code,
            "data": data if data is not None else {"results": [{"id": "GO:0003677"}]},
        },
    )


def _search_document_stream_events() -> list[SimpleNamespace]:
    return [
        SimpleNamespace(
            type="run_item_stream_event",
            item=SimpleNamespace(
                type="tool_call_item",
                name="search_document",
                raw_item=SimpleNamespace(arguments='{"query":"principal finding"}'),
            ),
        ),
        SimpleNamespace(
            type="run_item_stream_event",
            item=SimpleNamespace(
                type="tool_call_output_item",
                output='{"summary":"found relevant Results passage"}',
                raw_item=SimpleNamespace(),
            ),
        ),
    ]


def test_pdf_structured_finalization_feedback_accepts_live_evidence_payload():
    feedback = streaming_tools._structured_specialist_finalization_feedback(
        _pdf_finalization_payload(),
        expected_output_type=PdfExtractionResultEnvelope,
        finalization_config=_finalization_config("ask_pdf_extraction_specialist"),
        tool_calls=[streaming_tools.SpecialistToolCall(tool_name="search_document")],
        live_evidence_records=[_pdf_live_evidence_record()],
    )

    assert feedback.accepted_payload is not None
    assert feedback.message == "PdfExtractionResultEnvelope accepted."
    assert feedback.summary["live_evidence_record_count"] == 1
    assert feedback.accepted_payload["items"][0]["evidence_record_ids"] == [
        "evidence-pdf-1"
    ]


def test_pdf_structured_finalization_feedback_rejects_invented_evidence_id():
    feedback = streaming_tools._structured_specialist_finalization_feedback(
        _pdf_finalization_payload(evidence_record_id="invented-evidence"),
        expected_output_type=PdfExtractionResultEnvelope,
        finalization_config=_finalization_config("ask_pdf_extraction_specialist"),
        tool_calls=[streaming_tools.SpecialistToolCall(tool_name="search_document")],
        live_evidence_records=[_pdf_live_evidence_record()],
    )

    assert feedback.accepted_payload is None
    assert feedback.field_errors
    assert any(
        error.get("field") == "evidence_record_ids"
        for error in feedback.field_errors
    )


def test_pdf_structured_finalization_feedback_requires_document_retrieval():
    feedback = streaming_tools._structured_specialist_finalization_feedback(
        _pdf_finalization_payload(),
        expected_output_type=PdfExtractionResultEnvelope,
        finalization_config=_finalization_config("ask_pdf_extraction_specialist"),
        tool_calls=[],
        live_evidence_records=[_pdf_live_evidence_record()],
    )

    assert feedback.accepted_payload is None
    assert any(error.get("field") == "tool_calls" for error in feedback.field_errors)


def test_pdf_structured_finalization_rejects_hallucinated_empty_answer_evidence():
    payload = {
        "answer": "No relevant findings were found.",
        "summary": "Searches did not find relevant passages.",
        "items": [],
        "raw_mentions": [],
        "evidence_records": [
            {
                "evidence_record_id": "invented-empty-answer-evidence",
                "entity": "negative result",
                "verified_quote": "This quote was not returned by record_evidence.",
                "page": 1,
                "section": "Results",
                "chunk_id": "chunk-invented",
            }
        ],
        "normalization_notes": [],
        "exclusions": [],
        "ambiguities": [],
        "run_summary": {
            "candidate_count": 0,
            "kept_count": 0,
            "excluded_count": 0,
            "ambiguous_count": 0,
            "warnings": [],
        },
    }

    feedback = streaming_tools._structured_specialist_finalization_feedback(
        payload,
        expected_output_type=PdfExtractionResultEnvelope,
        finalization_config=_finalization_config("ask_pdf_extraction_specialist"),
        tool_calls=[streaming_tools.SpecialistToolCall(tool_name="search_document")],
        live_evidence_records=[],
    )

    assert feedback.accepted_payload is None
    assert any(
        error.get("field") == "evidence_records"
        for error in feedback.field_errors
    )


def test_structured_finalization_caps_rejected_attempts():
    def passthrough_tool_factory(**_kwargs):
        def decorate(func):
            return func

        return decorate

    state = streaming_tools._StructuredSpecialistFinalizationState(
        required=True,
        tool_name="finalize_pdf_extraction",
        agent_name="General PDF Extraction Agent",
        output_type_name="PdfExtractionResultEnvelope",
        config={"checks": ["pdf_evidence"]},
        max_attempts=2,
    )
    finalizer = streaming_tools._build_structured_specialist_finalization_tool(
        expected_output_type=PdfExtractionResultEnvelope,
        finalization_state=state,
        tool_calls=[],
        live_evidence_records=[],
        function_tool_factory=passthrough_tool_factory,
    )

    first = finalizer(_pdf_finalization_payload())
    assert first["status"] == "rejected"
    assert state.attempt_limit_exceeded is False

    second = finalizer(_pdf_finalization_payload())
    assert second["status"] == "rejected"
    assert state.attempt_limit_exceeded is True
    assert "structured_finalization_attempt_limit_exceeded" in second["warnings"]

    third = finalizer(_pdf_finalization_payload())
    assert third["status"] == "rejected"
    assert "structured_finalization_attempt_limit_exceeded" in third["warnings"]
    assert third["field_errors"][0]["field"] == "finalization_attempts"


def test_structured_finalization_attempt_config_defaults_to_six_and_hard_caps():
    assert streaming_tools._structured_specialist_finalization_max_attempts({}) == 6
    assert (
        streaming_tools._structured_specialist_finalization_max_attempts(
            {"max_attempts": "bad"}
        )
        == 6
    )
    assert (
        streaming_tools._structured_specialist_finalization_max_attempts(
            {"max_attempts": 0}
        )
        == 6
    )
    assert (
        streaming_tools._structured_specialist_finalization_max_attempts(
            {"max_attempts": 999}
        )
        == 20
    )


def test_lookup_structured_finalization_tool_names_are_enabled():
    assert (
        streaming_tools._structured_specialist_finalization_tool_name(
            _finalization_config("ask_gene_specialist")
        )
        == "finalize_gene_lookup"
    )
    assert (
        streaming_tools._structured_specialist_finalization_tool_name(
            _finalization_config("ask_allele_specialist")
        )
        == "finalize_allele_lookup"
    )
    assert (
        streaming_tools._structured_specialist_finalization_tool_name(
            _finalization_config("ask_disease_specialist")
        )
        == "finalize_disease_lookup"
    )
    assert (
        streaming_tools._structured_specialist_finalization_tool_name(
            _finalization_config("ask_gene_ontology_specialist")
        )
        == "finalize_go_term_lookup"
    )
    assert (
        streaming_tools._structured_specialist_finalization_tool_name(
            _finalization_config("ask_go_annotations_specialist")
        )
        == "finalize_go_annotations_lookup"
    )
    assert (
        streaming_tools._structured_specialist_finalization_tool_name(
            _finalization_config("ask_orthologs_specialist")
        )
        == "finalize_orthologs_lookup"
    )
    assert (
        streaming_tools._structured_specialist_finalization_tool_name(
            _finalization_config("ask_chemical_specialist")
        )
        == "finalize_chemical_lookup"
    )
    assert (
        streaming_tools._structured_specialist_finalization_tool_name(
            _finalization_config("ask_reference_specialist")
        )
        == "finalize_reference_lookup"
    )
    assert (
        streaming_tools._structured_specialist_finalization_tool_name(
            _finalization_config("ask_ontology_term_validation_specialist")
        )
        == "finalize_ontology_term_lookup"
    )
    assert (
        streaming_tools._structured_specialist_finalization_tool_name(
            _finalization_config("ask_controlled_vocabulary_specialist")
        )
        == "finalize_controlled_vocabulary_lookup"
    )
    assert (
        streaming_tools._structured_specialist_finalization_tool_name(
            _finalization_config("ask_data_provider_specialist")
        )
        == "finalize_data_provider_lookup"
    )
    assert (
        streaming_tools._structured_specialist_finalization_tool_name(
            _finalization_config("ask_subject_entity_specialist")
        )
        == "finalize_subject_entity_lookup"
    )
    assert (
        streaming_tools._structured_specialist_finalization_tool_name(
            _finalization_config("ask_agm_specialist")
        )
        == "finalize_agm_lookup"
    )
    assert (
        streaming_tools._structured_specialist_finalization_tool_name(
            _finalization_config("ask_experimental_condition_specialist")
        )
        == "finalize_experimental_condition_lookup"
    )


def test_go_term_finalization_rejects_resolved_result_without_lookup_call(
    _repo_package_curation_registry,
):
    payload = _validator_result_payload(
        lookup_attempts=[_lookup_attempt()],
        resolved_values={"go_id": "GO:0003677"},
        results=[
            {
                "go_id": "GO:0003677",
                "name": "DNA binding",
                "aspect": "molecular_function",
            }
        ],
        query_summary="Resolved one GO term.",
        not_found=[],
    )

    feedback = streaming_tools._structured_specialist_finalization_feedback(
        payload,
        expected_output_type=_package_schema("GOTermResultEnvelope"),
        finalization_config=_finalization_config("ask_gene_ontology_specialist"),
        tool_calls=[],
        live_evidence_records=[],
    )

    assert feedback.accepted_payload is None
    assert any(error.get("field") == "tool_calls" for error in feedback.field_errors)


def test_gene_finalization_rejects_invented_resolved_gene(
    _repo_package_curation_registry,
):
    payload = _validator_result_payload(
        agent_id="gene_validation",
        target_inputs={"gene_symbol": "daf-16"},
        expected_fields=["primary_external_id", "gene_symbol"],
        lookup_attempts=[
            {
                "provider": "agr_curation_query",
                "method": "search_genes",
                "query": {"gene_symbol": "daf-16", "data_provider": "WB"},
                "outcome": "success",
                "result_count": 1,
            }
        ],
        resolved_values={
            "primary_external_id": "WB:FAKE00000001",
            "gene_symbol": "daf-16",
        },
        gene_candidates=[
            {
                "gene_id": "WB:FAKE00000001",
                "symbol": "daf-16",
                "species": "Caenorhabditis elegans",
                "taxon": "NCBITaxon:6239",
                "data_provider": "WB",
            }
        ],
    )

    feedback = streaming_tools._structured_specialist_finalization_feedback(
        payload,
        expected_output_type=_package_schema("GeneResultEnvelope"),
        finalization_config=_finalization_config("ask_gene_specialist"),
        tool_calls=[
            streaming_tools.SpecialistToolCall(
                tool_name="agr_curation_query",
                tool_args={
                    "method": "search_genes",
                    "gene_symbol": "daf-16",
                    "data_provider": "WB",
                },
                output_payload={
                    "status": "ok",
                    "status_code": 200,
                    "data": {
                        "results": [
                            {
                                "primary_external_id": "WB:WBGene00000912",
                                "gene_id": "WB:WBGene00000912",
                                "symbol": "daf-16",
                                "taxon": "NCBITaxon:6239",
                                "data_provider": "WB",
                            }
                        ]
                    },
                },
            )
        ],
        live_evidence_records=[],
    )

    assert feedback.accepted_payload is None
    assert any(
        error.get("field") in {"resolved_values.primary_external_id", "gene_candidates[].gene_id"}
        for error in feedback.field_errors
    )


def test_gene_finalization_accepts_api_grounded_gene(
    _repo_package_curation_registry,
):
    tool_gene_record = {
        "primary_external_id": "WB:WBGene00000912",
        "gene_id": "WB:WBGene00000912",
        "symbol": "daf-16",
        "species": "Caenorhabditis elegans",
        "taxon": "NCBITaxon:6239",
        "data_provider": "WB",
    }
    gene_candidate = {
        key: value
        for key, value in tool_gene_record.items()
        if key != "primary_external_id"
    }
    payload = _validator_result_payload(
        agent_id="gene_validation",
        target_inputs={"gene_symbol": "daf-16"},
        expected_fields=["primary_external_id", "gene_symbol"],
        lookup_attempts=[
            {
                "provider": "agr_curation_query",
                "method": "search_genes",
                "query": {"gene_symbol": "daf-16", "data_provider": "WB"},
                "outcome": "success",
                "result_count": 1,
            }
        ],
        resolved_values={
            "primary_external_id": "WB:WBGene00000912",
            "gene_symbol": "daf-16",
        },
        gene_candidates=[gene_candidate],
    )

    feedback = streaming_tools._structured_specialist_finalization_feedback(
        payload,
        expected_output_type=_package_schema("GeneResultEnvelope"),
        finalization_config=_finalization_config("ask_gene_specialist"),
        tool_calls=[
            streaming_tools.SpecialistToolCall(
                tool_name="agr_curation_query",
                tool_args={
                    "method": "search_genes",
                    "gene_symbol": "daf-16",
                    "data_provider": "WB",
                },
                output_payload={
                    "status": "ok",
                    "status_code": 200,
                    "data": {
                        "results": [tool_gene_record]
                    },
                },
            )
        ],
        live_evidence_records=[],
    )

    assert feedback.accepted_payload is not None


def test_disease_finalization_rejects_ungrounded_unresolved_candidate(
    _repo_package_curation_registry,
):
    payload = _validator_result_payload(
        status="unresolved",
        agent_id="disease_validation",
        target_inputs={"disease_name": "invented disease"},
        expected_fields=["curie"],
        missing_expected_fields=["curie"],
        candidates=[
            {
                "value": "DOID:INVENTED",
                "label": "Invented disease",
                "object_type": "DOTerm",
                "matched_fields": {"name": "invented disease"},
                "details": {"curie": "DOID:INVENTED"},
            }
        ],
        lookup_attempts=[],
    )

    feedback = streaming_tools._structured_specialist_finalization_feedback(
        payload,
        expected_output_type=_package_schema("DiseaseValidationResult"),
        finalization_config=_finalization_config("ask_disease_specialist"),
        tool_calls=[],
        live_evidence_records=[],
    )

    assert feedback.accepted_payload is None
    assert any(error.get("field") == "tool_calls" for error in feedback.field_errors)


def test_go_annotations_finalization_accepts_unresolved_tool_error(
    _repo_package_curation_registry,
):
    url = "https://api.geneontology.org/api/bioentity/gene/WB:WBGene00000898/function"
    payload = _validator_result_payload(
        status="unresolved",
        agent_id="go_annotations_lookup",
        target_inputs={"gene_id": "WB:WBGene00000898"},
        expected_fields=["annotations"],
        missing_expected_fields=["annotations"],
        lookup_attempts=[
            _lookup_attempt(
                provider="go_api_call",
                url=url,
                outcome="error",
                result_count=0,
            )
        ],
        gene_id="WB:WBGene00000898",
        gene_symbol=None,
        annotations=[],
        manual_count=0,
        automatic_count=0,
    )

    feedback = streaming_tools._structured_specialist_finalization_feedback(
        payload,
        expected_output_type=_package_schema("GOAnnotationsResult"),
        finalization_config=_finalization_config("ask_go_annotations_specialist"),
        tool_calls=[
            _lookup_tool_call(
                tool_name="go_api_call",
                url=url,
                status="error",
                status_code=503,
                data={},
            )
        ],
        live_evidence_records=[],
    )

    assert feedback.accepted_payload is not None
    assert feedback.summary["failed_lookup_tool_call_count"] == 1


def test_go_term_finalization_does_not_count_finalizer_as_lookup_provenance(
    _repo_package_curation_registry,
):
    payload = _validator_result_payload(
        lookup_attempts=[_lookup_attempt()],
        resolved_values={"go_id": "GO:0003677"},
        results=[
            {
                "go_id": "GO:0003677",
                "name": "DNA binding",
                "aspect": "molecular_function",
            }
        ],
        query_summary="Resolved one GO term.",
        not_found=[],
    )

    feedback = streaming_tools._structured_specialist_finalization_feedback(
        payload,
        expected_output_type=_package_schema("GOTermResultEnvelope"),
        finalization_config=_finalization_config("ask_gene_ontology_specialist"),
        tool_calls=[
            streaming_tools.SpecialistToolCall(tool_name="finalize_go_term_lookup")
        ],
        live_evidence_records=[],
    )

    assert feedback.accepted_payload is None
    assert any(error.get("field") == "tool_calls" for error in feedback.field_errors)


def test_go_term_finalization_rejects_invented_resolved_fact(
    _repo_package_curation_registry,
):
    url = "https://www.ebi.ac.uk/QuickGO/services/ontology/go/terms/GO:FAKE0000"
    payload = _validator_result_payload(
        lookup_attempts=[_lookup_attempt(url=url)],
        resolved_values={"go_id": "GO:FAKE0000"},
        results=[
            {
                "go_id": "GO:FAKE0000",
                "name": "Invented term",
                "aspect": "molecular_function",
            }
        ],
        query_summary="Resolved an invented GO term.",
        not_found=[],
    )

    feedback = streaming_tools._structured_specialist_finalization_feedback(
        payload,
        expected_output_type=_package_schema("GOTermResultEnvelope"),
        finalization_config=_finalization_config("ask_gene_ontology_specialist"),
        tool_calls=[
            _lookup_tool_call(
                url=url,
                data={"results": [{"id": "GO:0003677", "name": "DNA binding"}]},
            )
        ],
        live_evidence_records=[],
    )

    assert feedback.accepted_payload is None
    assert any(
        error.get("field") == "results[].go_id"
        for error in feedback.field_errors
    )


def test_go_annotations_finalization_rejects_invented_annotation(
    _repo_package_curation_registry,
):
    url = "https://api.geneontology.org/api/bioentity/gene/WB:WBGene00000898/function"
    payload = _validator_result_payload(
        agent_id="go_annotations_lookup",
        target_inputs={"gene_id": "WB:WBGene00000898"},
        expected_fields=["annotations"],
        lookup_attempts=[
            _lookup_attempt(
                provider="go_api_call",
                url=url,
                outcome="success",
                result_count=1,
            )
        ],
        gene_id="WB:WBGene00000898",
        annotations=[
            {
                "go_id": "GO:FAKE0000",
                "go_name": "Invented annotation",
                "aspect": "MF",
                "evidence_code": "IDA",
            }
        ],
        manual_count=1,
        automatic_count=0,
    )

    feedback = streaming_tools._structured_specialist_finalization_feedback(
        payload,
        expected_output_type=_package_schema("GOAnnotationsResult"),
        finalization_config=_finalization_config("ask_go_annotations_specialist"),
        tool_calls=[
            _lookup_tool_call(
                tool_name="go_api_call",
                url=url,
                data={"associations": [{"object": {"id": "GO:0003677"}}]},
            )
        ],
        live_evidence_records=[],
    )

    assert feedback.accepted_payload is None
    assert any(
        error.get("field") == "annotations[].go_id"
        for error in feedback.field_errors
    )


def test_ortholog_finalization_rejects_invented_ortholog(
    _repo_package_curation_registry,
):
    url = "https://www.alliancegenome.org/api/gene/WB:WBGene00000898/orthologs"
    payload = _validator_result_payload(
        agent_id="orthologs_lookup",
        target_inputs={"gene_id": "WB:WBGene00000898"},
        expected_fields=["orthologs"],
        lookup_attempts=[
            _lookup_attempt(
                provider="alliance_api_call",
                url=url,
                outcome="success",
                result_count=1,
            )
        ],
        query_gene={"gene_id": "WB:WBGene00000898", "symbol": "lin-12"},
        orthologs=[
            {
                "ortholog": {
                    "gene_id": "HGNC:FAKE",
                    "symbol": "FAKE1",
                    "species": "Homo sapiens",
                },
                "confidence": "high",
            }
        ],
        high_confidence_count=1,
        species_represented=["Homo sapiens"],
    )

    feedback = streaming_tools._structured_specialist_finalization_feedback(
        payload,
        expected_output_type=_package_schema("OrthologsResult"),
        finalization_config=_finalization_config("ask_orthologs_specialist"),
        tool_calls=[
            _lookup_tool_call(
                tool_name="alliance_api_call",
                url=url,
                data={
                    "results": [
                        {
                            "geneToGeneOrthologyGenerated": {
                                "objectGene": {"primaryExternalId": "HGNC:11998"}
                            }
                        }
                    ]
                },
            )
        ],
        live_evidence_records=[],
    )

    assert feedback.accepted_payload is None
    assert any(
        error.get("field") == "orthologs[].ortholog.gene_id"
        for error in feedback.field_errors
    )


def test_go_term_finalization_rejects_missing_multi_query_coverage(
    _repo_package_curation_registry,
):
    url = (
        "https://www.ebi.ac.uk/QuickGO/services/ontology/go/terms/"
        "GO:0003677,GO:9999999"
    )
    payload = _validator_result_payload(
        target_inputs={"go_ids": ["GO:0003677", "GO:9999999"]},
        lookup_attempts=[_lookup_attempt(url=url)],
        resolved_values={"go_id": "GO:0003677"},
        results=[
            {
                "go_id": "GO:0003677",
                "name": "DNA binding",
                "aspect": "molecular_function",
            }
        ],
        query_summary="Resolved one GO term.",
        not_found=[],
    )

    feedback = streaming_tools._structured_specialist_finalization_feedback(
        payload,
        expected_output_type=_package_schema("GOTermResultEnvelope"),
        finalization_config=_finalization_config("ask_gene_ontology_specialist"),
        tool_calls=[
            _lookup_tool_call(
                url=url,
                data={"results": [{"id": "GO:0003677"}]},
            )
        ],
        live_evidence_records=[],
    )

    assert feedback.accepted_payload is None
    assert any(
        error.get("field") == "result_coverage"
        and error.get("missing_inputs") == ["GO:9999999"]
        for error in feedback.field_errors
    )


def test_go_term_finalization_accepts_repaired_multi_query_coverage(
    _repo_package_curation_registry,
):
    url = (
        "https://www.ebi.ac.uk/QuickGO/services/ontology/go/terms/"
        "GO:0003677,GO:9999999"
    )
    payload = _validator_result_payload(
        status="unresolved",
        target_inputs={"go_ids": ["GO:0003677", "GO:9999999"]},
        expected_fields=["results"],
        missing_expected_fields=["results"],
        lookup_attempts=[_lookup_attempt(url=url)],
        resolved_values={"go_id": "GO:0003677"},
        results=[
            {
                "go_id": "GO:0003677",
                "name": "DNA binding",
                "aspect": "molecular_function",
            }
        ],
        query_summary="Resolved GO:0003677; GO:9999999 was not found.",
        not_found=["GO:9999999"],
    )

    feedback = streaming_tools._structured_specialist_finalization_feedback(
        payload,
        expected_output_type=_package_schema("GOTermResultEnvelope"),
        finalization_config=_finalization_config("ask_gene_ontology_specialist"),
        tool_calls=[
            _lookup_tool_call(
                url=url,
                data={"results": [{"id": "GO:0003677"}]},
            )
        ],
        live_evidence_records=[],
    )

    assert feedback.accepted_payload is not None
    assert feedback.summary["requested_input_count"] == 2


def test_lookup_finalization_preserves_true_result_count_above_compact_limit(
    _repo_package_curation_registry,
):
    url = "https://api.geneontology.org/api/bioentity/gene/WB:WBGene00000898/function"
    associations = [
        {"object": {"id": f"GO:{index:07d}"}}
        for index in range(60)
    ]
    output_payload = streaming_tools._tool_output_payload_for_finalization(
        "go_api_call",
        {
            "status": "ok",
            "status_code": 200,
            "data": {"associations": associations},
        },
    )
    assert output_payload is not None
    payload = _validator_result_payload(
        agent_id="go_annotations_lookup",
        target_inputs={"gene_id": "WB:WBGene00000898"},
        expected_fields=["annotations"],
        lookup_attempts=[
            _lookup_attempt(
                provider="go_api_call",
                url=url,
                outcome="success",
                result_count=60,
            )
        ],
        gene_id="WB:WBGene00000898",
        annotations=[
            {
                "go_id": "GO:0000059",
                "go_name": "Annotation beyond compact preview",
                "aspect": "MF",
                "evidence_code": "IDA",
            }
        ],
        manual_count=60,
        automatic_count=0,
    )

    feedback = streaming_tools._structured_specialist_finalization_feedback(
        payload,
        expected_output_type=_package_schema("GOAnnotationsResult"),
        finalization_config=_finalization_config("ask_go_annotations_specialist"),
        tool_calls=[
            streaming_tools.SpecialistToolCall(
                tool_name="go_api_call",
                tool_args={"url": url, "method": "GET"},
                output_payload=output_payload,
            )
        ],
        live_evidence_records=[],
    )

    assert feedback.accepted_payload is not None
    assert output_payload["data"]["associations"]["__full_count"] == 60


def test_chemical_finalization_rejects_invented_resolved_identity(
    _repo_package_curation_registry,
):
    url = "https://www.ebi.ac.uk/chebi/backend/api/public/compound/CHEBI:FAKE/"
    payload = _validator_result_payload(
        agent_id="chemical_validation",
        target_inputs={"chemical_name": "glucose"},
        expected_fields=["chebi_id"],
        lookup_attempts=[
            _lookup_attempt(
                provider="chebi_api_call",
                url=url,
                outcome="success",
                result_count=1,
            )
        ],
        resolved_values={"chebi_id": "CHEBI:FAKE", "name": "Invented chemical"},
        resolved_objects=[{"chebi_id": "CHEBI:FAKE", "name": "Invented chemical"}],
    )

    feedback = streaming_tools._structured_specialist_finalization_feedback(
        payload,
        expected_output_type=_package_schema("ChemicalValidationResult"),
        finalization_config=_finalization_config("ask_chemical_specialist"),
        tool_calls=[
            _lookup_tool_call(
                tool_name="chebi_api_call",
                url=url,
                data={"id": "CHEBI:17234", "name": "D-glucose"},
            )
        ],
        live_evidence_records=[],
    )

    assert feedback.accepted_payload is None
    assert any(
        error.get("field") == "resolved_values.chebi_id"
        for error in feedback.field_errors
    )


def test_lookup_finalization_rejects_partial_scalar_identity_match(
    _repo_package_curation_registry,
):
    url = "https://www.ebi.ac.uk/chebi/backend/api/public/compound/CHEBI:17/"
    payload = _validator_result_payload(
        agent_id="chemical_validation",
        target_inputs={"chemical_name": "glucose"},
        expected_fields=["chebi_id"],
        lookup_attempts=[
            _lookup_attempt(
                provider="chebi_api_call",
                url=url,
                outcome="success",
                result_count=1,
            )
        ],
        resolved_values={"chebi_id": "CHEBI:17", "name": "Partial chemical"},
        resolved_objects=[{"chebi_id": "CHEBI:17", "name": "Partial chemical"}],
    )

    feedback = streaming_tools._structured_specialist_finalization_feedback(
        payload,
        expected_output_type=_package_schema("ChemicalValidationResult"),
        finalization_config=_finalization_config("ask_chemical_specialist"),
        tool_calls=[
            _lookup_tool_call(
                tool_name="chebi_api_call",
                url=url,
                data={"id": "CHEBI:17234", "name": "D-glucose"},
            )
        ],
        live_evidence_records=[],
    )

    assert feedback.accepted_payload is None
    assert any(
        error.get("field") == "resolved_values.chebi_id"
        for error in feedback.field_errors
    )


def test_lookup_tool_output_capture_uses_package_declared_paths(monkeypatch):
    monkeypatch.setattr(
        streaming_tools,
        "_lookup_finalization_config_for_tool",
        lambda tool_name: {
            "tool_name": tool_name,
            "tool_output_paths": ["resolved_widget", "result.items[]"],
        },
    )

    output_payload = streaming_tools._tool_output_payload_for_finalization(
        "org_widget_lookup",
        {
            "status": "ok",
            "resolved_widget": {"widget_id": "WIDGET:1"},
            "result": {"items": [{"widget_id": "WIDGET:2"}]},
            "ignored": {"widget_id": "WIDGET:IGNORED"},
        },
    )

    assert output_payload is not None
    assert "WIDGET:1".lower() in output_payload["scalar_tokens"]
    assert "WIDGET:2".lower() in output_payload["scalar_tokens"]
    assert "WIDGET:IGNORED".lower() not in output_payload["scalar_tokens"]


def test_reference_finalization_accepts_api_grounded_reference(
    _repo_package_curation_registry,
):
    reference = {
        "reference_id": 101000000924191,
        "curie": "AGRKB:101000000924191",
        "title": "A curated source paper",
        "short_citation": "Curator et al., 2026",
        "cross_references": ["PMID:123456"],
        "source": "literature_es",
    }
    payload = _validator_result_payload(
        agent_id="reference_validation",
        target_inputs={"pmid": "PMID:123456"},
        expected_fields=["curie"],
        lookup_attempts=[
            {
                "provider": "agr_literature_reference_lookup",
                "method": "get_literature_reference",
                "query": {"value": "PMID:123456"},
                "result_count": 1,
                "outcome": "success",
            }
        ],
        resolved_values={"curie": "AGRKB:101000000924191"},
        resolved_objects=[reference],
        reference_id=101000000924191,
        curie="AGRKB:101000000924191",
        title="A curated source paper",
        short_citation="Curator et al., 2026",
        cross_references=["PMID:123456"],
        source="literature_es",
        match_type="exact_identifier",
        confidence=1.0,
        candidate_references=[reference],
    )
    output_payload = streaming_tools._tool_output_payload_for_finalization(
        "agr_literature_reference_lookup",
        {
            "status": "ok",
            "source": "literature_es",
            "method": "get_literature_reference",
            "query": "PMID:123456",
            "count": 1,
            "lookup_status": "success",
            "resolved_reference": reference,
            "candidate_references": [reference],
        },
    )
    assert output_payload is not None

    feedback = streaming_tools._structured_specialist_finalization_feedback(
        payload,
        expected_output_type=_package_schema("ReferenceValidationResult"),
        finalization_config=_finalization_config("ask_reference_specialist"),
        tool_calls=[
            streaming_tools.SpecialistToolCall(
                tool_name="agr_literature_reference_lookup",
                tool_args={
                    "method": "get_literature_reference",
                    "identifier": "PMID:123456",
                },
                output_payload=output_payload,
            )
        ],
        live_evidence_records=[],
    )

    assert feedback.accepted_payload is not None


def test_reference_finalization_rejects_invented_curie(
    _repo_package_curation_registry,
):
    reference = {
        "reference_id": 101000000924191,
        "curie": "AGRKB:101000000924191",
        "title": "A curated source paper",
        "cross_references": ["PMID:123456"],
        "source": "literature_es",
    }
    payload = _validator_result_payload(
        agent_id="reference_validation",
        target_inputs={"pmid": "PMID:123456"},
        expected_fields=["curie"],
        lookup_attempts=[
            {
                "provider": "agr_literature_reference_lookup",
                "method": "get_literature_reference",
                "query": {"value": "PMID:123456"},
                "result_count": 1,
                "outcome": "success",
            }
        ],
        resolved_values={"curie": "AGRKB:INVENTED"},
        resolved_objects=[{**reference, "curie": "AGRKB:INVENTED"}],
        reference_id=101000000924191,
        curie="AGRKB:INVENTED",
        title="A curated source paper",
        cross_references=["PMID:123456"],
        source="literature_es",
        match_type="exact_identifier",
        confidence=1.0,
        candidate_references=[reference],
    )
    output_payload = streaming_tools._tool_output_payload_for_finalization(
        "agr_literature_reference_lookup",
        {
            "status": "ok",
            "source": "literature_es",
            "method": "get_literature_reference",
            "query": "PMID:123456",
            "count": 1,
            "lookup_status": "success",
            "resolved_reference": reference,
            "candidate_references": [reference],
        },
    )
    assert output_payload is not None

    feedback = streaming_tools._structured_specialist_finalization_feedback(
        payload,
        expected_output_type=_package_schema("ReferenceValidationResult"),
        finalization_config=_finalization_config("ask_reference_specialist"),
        tool_calls=[
            streaming_tools.SpecialistToolCall(
                tool_name="agr_literature_reference_lookup",
                tool_args={
                    "method": "get_literature_reference",
                    "identifier": "PMID:123456",
                },
                output_payload=output_payload,
            )
        ],
        live_evidence_records=[],
    )

    assert feedback.accepted_payload is None
    assert any(error.get("field") == "curie" for error in feedback.field_errors)


@pytest.mark.asyncio
async def test_builder_materializer_agent_rejects_structured_output_schema():
    agent = SimpleNamespace(
        name="Gene Expression",
        output_type=_Envelope,
        tools=[SimpleNamespace(name="finalize_gene_expression_extraction")],
    )

    with pytest.raises(
        streaming_tools.SpecialistOutputError,
        match="builder/materializer specialist",
    ) as exc_info:
        await streaming_tools.run_specialist_with_events(
            agent,
            "extract gene expression",
            "Gene Expression",
            tool_name="ask_gene_expression_specialist",
        )

    assert exc_info.value.details == [
        {
            "reason": "builder_materializer_output_schema_forbidden",
            "output_type": "_Envelope",
        }
    ]


@pytest.mark.asyncio
async def test_pdf_specialist_prefers_accepted_finalization_payload(monkeypatch):
    accepted_payload = _pdf_finalization_payload()
    captured_events = []
    captured = {}

    class _FakeFinalizePdfTool:
        name = "finalize_pdf_extraction"

        def __init__(self, state):
            self._state = state

        def accept(self):
            self._state.accepted_payload = accepted_payload

    def _fake_build_structured_finalization_tool(**kwargs):
        state = kwargs["finalization_state"]
        captured["state"] = state
        return _FakeFinalizePdfTool(state)

    def _run_streamed(runtime_agent, *args, **kwargs):
        captured["tools"] = list(getattr(runtime_agent, "tools", []) or [])
        captured["max_turns"] = kwargs.get("max_turns")
        finalizer = next(
            tool for tool in captured["tools"] if getattr(tool, "name", "") == "finalize_pdf_extraction"
        )
        finalizer.accept()
        return _FakeRunResult(
            events=_search_document_stream_events(),
            final_output={"answer": "Untrusted SDK output"},
            new_items=[],
        )

    monkeypatch.setattr(streaming_tools, "add_specialist_event", captured_events.append)
    monkeypatch.setattr(streaming_tools, "commit_pending_prompts", lambda _agent: None)
    monkeypatch.setattr(streaming_tools, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(
        streaming_tools,
        "_build_structured_specialist_finalization_tool",
        _fake_build_structured_finalization_tool,
    )
    monkeypatch.setattr(streaming_tools.Runner, "run_streamed", _run_streamed)

    agent = SimpleNamespace(
        name="General PDF Extraction Agent",
        tools=[SimpleNamespace(name="search_document")],
        output_type=PdfExtractionResultEnvelope,
        instructions="base instructions",
        model="gpt-4o",
    )

    result = await streaming_tools.run_specialist_with_events(
        agent,
        "summarize the paper",
        "General PDF Extraction Agent",
        max_turns=3,
        tool_name="ask_pdf_extraction_specialist",
    )

    assert result == accepted_payload["answer"]
    assert captured["max_turns"] == 5
    assert captured["state"].accepted_payload == accepted_payload
    assert any(
        event.get("type") == "evidence_summary"
        and event.get("evidence_records") == accepted_payload["evidence_records"]
        for event in captured_events
    )
    assert not any(event.get("type") == "SPECIALIST_RETRY" for event in captured_events)


@pytest.mark.asyncio
async def test_pdf_specialist_requires_accepted_finalization(monkeypatch):
    captured_events = []

    monkeypatch.setattr(streaming_tools, "add_specialist_event", captured_events.append)
    monkeypatch.setattr(streaming_tools, "commit_pending_prompts", lambda _agent: None)
    monkeypatch.setattr(streaming_tools, "RunConfig", lambda *args, **kwargs: SimpleNamespace(**kwargs))
    monkeypatch.setattr(
        streaming_tools.Runner,
        "run_streamed",
        lambda *args, **kwargs: _FakeRunResult(
            events=_search_document_stream_events(),
            final_output=_pdf_finalization_payload(),
            new_items=[],
        ),
    )

    agent = SimpleNamespace(
        name="General PDF Extraction Agent",
        tools=[SimpleNamespace(name="search_document")],
        output_type=PdfExtractionResultEnvelope,
        instructions="base instructions",
        model="gpt-4o",
    )

    with pytest.raises(
        streaming_tools.SpecialistOutputError,
        match="mandatory finalize_pdf_extraction",
    ):
        await streaming_tools.run_specialist_with_events(
            agent,
            "summarize the paper",
            "General PDF Extraction Agent",
            max_turns=3,
            tool_name="ask_pdf_extraction_specialist",
        )

    assert any(
        event.get("type") == "SPECIALIST_ERROR"
        and event.get("details", {}).get("reason") == "structured_finalization_missing"
        for event in captured_events
    )


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


def test_domain_envelope_reduction_includes_resolved_validator_values():
    envelope_output = json.dumps(
        {
            "envelope_id": "env-test-allele",
            "domain_pack_id": "agr.alliance.allele",
            "domain_pack_version": "0.1.0",
            "objects": [
                {
                    "object_type": "AllelePaperEvidenceAssociation",
                    "object_role": "curatable_unit",
                    "pending_ref_id": "allele-assoc-crb-11A22",
                    "status": "pending",
                    "payload": {"mention": "crb 11A22"},
                }
            ],
            "validation_findings": [
                {
                    "code": "domain_pack.validator_resolved",
                    "severity": "info",
                    "status": "resolved",
                    "message": "Resolved crb 11A22.",
                    "details": {
                        "validation_request": {
                            "selected_inputs": {"mention": "crb 11A22"}
                        },
                        "validation_result": {
                            "resolved_values": {
                                "curie": "FB:FBal0001817",
                                "symbol": "crb<sup>11A22</sup>",
                                "taxon": "NCBITaxon:7227",
                            }
                        },
                    },
                }
            ],
        }
    )

    result = streaming_tools._reduce_specialist_output_for_supervisor(
        envelope_output,
        expected_output_type=GeneExtractionResultEnvelope,
    )

    assert "Validation findings: resolved=1." in result
    assert "Resolved validator finding: crb 11A22" in result
    assert "curie=FB:FBal0001817" in result
    assert "symbol=crb<sup>11A22</sup>" in result
    assert "taxon=NCBITaxon:7227" in result


def test_builder_domain_envelope_reduction_without_output_type_stays_compact():
    huge_note = "x" * 200_000
    envelope_output = json.dumps(
        {
            "envelope_id": "env-test-builder",
            "domain_pack_id": "agr.alliance.gene_expression",
            "domain_pack_version": "0.1.0",
            "objects": [
                {
                    "object_type": "GeneExpressionAnnotation",
                    "object_role": "curatable_unit",
                    "pending_ref_id": "gene-expression-1",
                    "status": "validated",
                    "payload": {
                        "symbol": "rpm-1",
                        "taxon": "NCBITaxon:6239",
                        "where_expressed_statement": huge_note,
                    },
                }
            ],
            "validation_findings": [{"status": "resolved"}],
        }
    )

    result = streaming_tools._reduce_specialist_output_for_supervisor(
        envelope_output,
        expected_output_type=None,
        finalized_domain_envelope=True,
    )

    assert len(result) < 2000
    assert "Validated domain envelope result for agr.alliance.gene_expression" in result
    assert "symbol=rpm-1" in result
    assert "taxon=NCBITaxon:6239" in result
    assert huge_note not in result
    with pytest.raises(json.JSONDecodeError):
        json.loads(result)


def test_builder_domain_envelope_reduction_counts_curatable_objects():
    envelope_output = json.dumps(
        {
            "envelope_id": "env-test-builder-curatable",
            "domain_pack_id": "agr.alliance.gene_expression",
            "domain_pack_version": "0.1.0",
            "curatable_objects": [
                {
                    "object_type": "GeneExpressionAnnotation",
                    "object_role": "curatable_unit",
                    "pending_ref_id": "gene-expression-1",
                    "status": "validated",
                    "payload": {
                        "symbol": "rpm-1",
                        "taxon": "NCBITaxon:6239",
                    },
                }
            ],
            "validation_findings": [],
        }
    )

    result = streaming_tools._reduce_specialist_output_for_supervisor(
        envelope_output,
        expected_output_type=None,
        finalized_domain_envelope=True,
    )

    assert "Validated domain envelope result for agr.alliance.gene_expression" in result
    assert "GeneExpressionAnnotation gene-expression-1 (validated)" in result
    assert "symbol=rpm-1" in result
    assert "Object count: 0." not in result
    with pytest.raises(json.JSONDecodeError):
        json.loads(result)


def test_domain_envelope_reduction_uses_unusual_payload_scalars_not_raw_json():
    envelope_output = json.dumps(
        {
            "envelope_id": "env-test-unusual",
            "domain_pack_id": "agr.alliance.unusual",
            "objects": [
                {
                    "object_type": "UnusualObject",
                    "pending_ref_id": "unusual-1",
                    "status": "validated",
                    "payload": {
                        "nonstandard_curator_value": "kept compactly",
                        "numeric_observation": 7,
                    },
                }
            ],
            "validation_findings": [],
        }
    )

    result = streaming_tools._reduce_specialist_output_for_supervisor(
        envelope_output,
        expected_output_type=None,
        finalized_domain_envelope=True,
    )

    assert "Validated domain envelope result for agr.alliance.unusual" in result
    assert "nonstandard_curator_value=kept compactly" in result
    assert "numeric_observation=7" in result
    with pytest.raises(json.JSONDecodeError):
        json.loads(result)


def test_domain_envelope_reduction_empty_objects_never_returns_raw_json():
    envelope_output = json.dumps(
        {
            "envelope_id": "env-test-empty",
            "domain_pack_id": "agr.alliance.empty",
            "objects": [],
            "validation_findings": [],
        }
    )

    result = streaming_tools._reduce_specialist_output_for_supervisor(
        envelope_output,
        expected_output_type=None,
        finalized_domain_envelope=True,
    )

    assert "Validated domain envelope result for agr.alliance.empty" in result
    assert "Object count: 0." in result
    with pytest.raises(json.JSONDecodeError):
        json.loads(result)


def test_curatable_objects_shape_without_contract_does_not_return_raw_json():
    envelope_output = json.dumps(
        {
            "summary": "Model-authored extraction that should not be replayed.",
            "curatable_objects": [
                {
                    "object_type": "GeneExpressionAnnotation",
                    "pending_ref_id": "gene-expression-1",
                    "payload": {"symbol": "rpm-1"},
                }
            ],
        }
    )

    result = streaming_tools._reduce_specialist_output_for_supervisor(
        envelope_output,
        expected_output_type=None,
        finalized_domain_envelope=False,
    )

    assert "not passed to the supervisor" in result
    assert "curatable_objects" not in result
    with pytest.raises(json.JSONDecodeError):
        json.loads(result)


def test_domain_envelope_shape_without_contract_does_not_fallback_to_validated_summary():
    envelope_output = json.dumps(
        {
            "envelope_id": "env-test-unaccepted",
            "domain_pack_id": "agr.alliance.gene_expression",
            "objects": [
                {
                    "object_type": "GeneExpressionAnnotation",
                    "pending_ref_id": "gene-expression-1",
                    "status": "validated",
                    "payload": {"symbol": "rpm-1"},
                }
            ],
        }
    )

    result = streaming_tools._reduce_specialist_output_for_supervisor(
        envelope_output,
        expected_output_type=None,
    )

    assert "not accepted through a declared or finalized curation contract" in result
    assert "Validated domain envelope result" not in result
    assert "rpm-1" not in result
    with pytest.raises(json.JSONDecodeError):
        json.loads(result)


def test_domain_validator_reduction_never_returns_raw_json():
    from packages.alliance.agents.gene.schema import GeneResultEnvelope

    validator_output = json.dumps(
        {
            "status": "resolved",
            "request_id": "domain-validation:test",
            "validator_binding_id": "alliance_gene_reference_lookup",
            "validator_agent": {
                "package_id": "alliance",
                "agent_id": "gene_validation",
            },
            "target": {
                "domain_pack_id": "gene",
                "object_type": "gene_mention_evidence",
                "object_id": "gene-mention-1",
                "field_path": "primary_external_id",
                "expected_fields": ["primary_external_id", "gene_symbol"],
            },
            "resolved_values": {
                "primary_external_id": "FB:FBgn0259685",
                "gene_symbol": "crb",
                "taxon": "NCBITaxon:7227",
            },
            "resolved_objects": [],
            "missing_expected_fields": [],
            "candidates": [],
            "lookup_attempts": [
                {
                    "provider": "agr_curation_query",
                    "method": "gene_lookup",
                    "query": {"symbol": "crb"},
                    "result_count": 1,
                    "outcome": "success",
                }
            ],
            "curator_message": "Resolved crumbs to crb.",
            "explanation": "API lookup resolved the requested gene.",
            "gene_candidates": [],
        }
    )

    result = streaming_tools._reduce_specialist_output_for_supervisor(
        validator_output,
        expected_output_type=GeneResultEnvelope,
    )

    assert "GeneResultEnvelope validator result: status=resolved" in result
    assert "binding=alliance_gene_reference_lookup" in result
    assert "primary_external_id=FB:FBgn0259685" in result
    assert "gene_symbol=crb" in result
    assert "Full validated payload is retained" in result
    assert '"resolved_values"' not in result
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
                        "identity_resolution_notes": [
                            "The paper identifies crumbs as a Drosophila gene and the quote provides focal identity context."
                        ],
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
            0,
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
                # R4 added annotation_type (manually_curated CV lookup) to the disease pack.
                "disease_annotation_type_cv_lookup",
                "disease_condition_relation_lookup",
                "disease_data_provider_lookup",
            },
            # Conditions work adds context-only / selector-suppressed condition matches:
            # matchedBindingCount (9) == validatorResultCount (6) + 3 suppressed.
            3,
            id="disease",
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
            0,
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
                            "expression_annotation_subject": {
                                "gene_symbol": "flcn",
                            },
                            "single_reference": {
                                "pmid": "PMID:27528223",
                            },
                        },
                    )
                ],
            ),
            {
                "relation_vocabulary_validation",
                "data_provider_validation",
                "subject_gene_validation",
                "source_reference_validation",
            },
            7,
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
        metadata={"validated": True},
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
async def test_chat_domain_envelope_dispatch_uses_real_gene_binding(
    monkeypatch,
    _repo_package_curation_registry,
):
    emitted = []
    monkeypatch.setattr(streaming_tools, "add_specialist_event", emitted.append)

    from src.lib.curation_workspace import extraction_results
    from src.lib.domain_packs import validator_dispatch

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
        if event["details"]["toolName"] == "domain_validator_lookup"
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
    (
        "tool_name,agent_key,adapter_key,envelope,expected_binding_ids,"
        "expected_selector_suppressed_binding_count"
    ),
    _chat_dispatch_domain_cases(),
)
async def test_chat_domain_envelope_dispatch_covers_launchable_active_validator_domains(
    monkeypatch,
    _repo_package_curation_registry,
    tool_name,
    agent_key,
    adapter_key,
    envelope,
    expected_binding_ids,
    expected_selector_suppressed_binding_count,
):
    emitted = []
    monkeypatch.setattr(streaming_tools, "add_specialist_event", emitted.append)

    from src.lib.curation_workspace import (
        curation_prep_service,
        extraction_results,
    )
    from src.lib.domain_packs import validator_dispatch

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
        if event["details"].get("toolName") == "dispatch_active_validator_bindings"
        and event["type"] == "TOOL_COMPLETE"
    ][0]
    assert len(captured_requests) == len(expected_binding_ids)
    assert dispatch_complete["details"]["validatorAgentRunCount"] == len(
        captured_requests
    )
    assert dispatch_complete["details"]["matchedBindingCount"] == (
        dispatch_complete["details"]["validatorResultCount"]
        + expected_selector_suppressed_binding_count
    )
    assert dispatch_complete["details"]["validatorResultCount"] >= len(
        captured_requests
    )


@pytest.mark.asyncio
async def test_chat_domain_envelope_dispatch_surfaces_validator_lookup_errors(
    monkeypatch,
    _repo_package_curation_registry,
):
    emitted = []
    monkeypatch.setattr(streaming_tools, "add_specialist_event", emitted.append)

    from src.lib.curation_workspace import extraction_results
    from src.lib.domain_packs import validator_dispatch

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

    # A validator that could not RUN its lookup is NOT fatal: the dispatch records a
    # validator_error finding and the envelope still persists for curator review.
    serialized_envelope = (
        await streaming_tools._dispatch_domain_envelope_validators_for_chat(
            _gene_extractor_domain_output(),
            expected_output_type=GeneExtractionResultEnvelope,
            specialist_name="Gene Extraction",
            tool_name="ask_gene_extractor_specialist",
        )
    )

    envelope_payload = json.loads(serialized_envelope)
    finding_codes = [
        finding.get("code")
        for finding in envelope_payload.get("validation_findings", [])
    ]
    assert "domain_pack.validator_error" in finding_codes

    dispatch_complete = [
        event
        for event in emitted
        if event["details"].get("toolName") == "dispatch_active_validator_bindings"
        and event["type"] == "TOOL_COMPLETE"
    ][0]
    assert dispatch_complete["details"]["success"] is False

    lookup_complete = [
        event
        for event in emitted
        if event["details"].get("toolName") == "domain_validator_lookup"
        and event["type"] == "TOOL_COMPLETE"
    ][0]
    assert lookup_complete["details"]["success"] is False
    assert lookup_complete["details"]["outcome"] == "error"
    assert lookup_complete["details"]["error"].startswith(
        "Validator agent execution failed"
    )

    # The dispatch error is reported as a NON-FATAL specialist event, not a raised exception.
    specialist_error = [
        event
        for event in emitted
        if event["type"] == "SPECIALIST_ERROR"
    ][0]
    assert specialist_error["details"]["reason"] == "domain_validator_dispatch_error"
    assert specialist_error["details"]["fatal"] is False
    assert specialist_error["details"]["validatorDispatchErrors"][0]["reason"] == (
        "domain_validator_dispatch_failed"
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
