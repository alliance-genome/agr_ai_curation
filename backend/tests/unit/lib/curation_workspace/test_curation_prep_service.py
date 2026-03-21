"""Unit tests for the curation prep service layer."""

from types import SimpleNamespace

import pytest

from src.lib.curation_workspace import curation_prep_service as module
from src.schemas.curation_prep import CurationPrepAgentInput, CurationPrepAgentOutput
from src.schemas.curation_workspace import CurationExtractionSourceKind


def _make_agent_input(*, second_document_id: str | None = None) -> CurationPrepAgentInput:
    payload = {
        "conversation_history": [
            {
                "role": "user",
                "content": "Prepare a disease curation candidate for APOE.",
                "message_id": "message-1",
                "created_at": "2026-03-20T21:50:00Z",
            }
        ],
        "extraction_results": [
            {
                "extraction_result_id": "extract-1",
                "document_id": "document-1",
                "adapter_key": "disease",
                "profile_key": "primary",
                "domain_key": "disease",
                "agent_key": "pdf_extraction",
                "source_kind": CurationExtractionSourceKind.CHAT,
                "origin_session_id": "chat-session-1",
                "trace_id": "trace-upstream",
                "flow_run_id": None,
                "user_id": "user-upstream",
                "candidate_count": 1,
                "conversation_summary": "Conversation focused on APOE disease relevance.",
                "payload_json": {
                    "items": [{"gene_symbol": "APOE"}],
                    "run_summary": {"candidate_count": 1},
                },
                "created_at": "2026-03-20T21:55:00Z",
                "metadata": {},
            }
        ],
        "evidence_records": [
            {
                "evidence_record_id": "evidence-1",
                "source": "extracted",
                "extraction_result_id": "extract-1",
                "field_paths": ["gene_symbol"],
                "anchor": {
                    "anchor_kind": "snippet",
                    "locator_quality": "exact_quote",
                    "supports_decision": "supports",
                    "snippet_text": "APOE was associated with the reported phenotype.",
                    "sentence_text": "APOE was associated with the reported phenotype.",
                    "viewer_search_text": "APOE was associated with the reported phenotype.",
                    "page_number": 3,
                    "section_title": "Results",
                    "subsection_title": "Disease association",
                    "figure_reference": "Fig. 2",
                    "chunk_ids": ["chunk-1"],
                },
                "notes": ["Exact quote from Results section."],
            }
        ],
        "scope_confirmation": {
            "confirmed": True,
            "adapter_keys": ["disease"],
            "profile_keys": ["primary"],
            "domain_keys": ["disease"],
            "notes": ["User confirmed the disease adapter scope."],
        },
        "adapter_metadata": [
            {
                "adapter_key": "disease",
                "profile_key": "primary",
                "required_field_keys": ["gene_symbol", "phenotype_label"],
                "field_hints": [
                    {
                        "field_key": "gene_symbol",
                        "required": True,
                        "label": "Gene symbol",
                        "value_type": "string",
                        "controlled_vocabulary": ["APOE"],
                        "normalization_hints": ["Prefer AGR gene symbols."],
                    }
                ],
                "notes": ["Populate only fields supported by the adapter-owned normalized shape."],
            }
        ],
    }

    if second_document_id is not None:
        second_result = dict(payload["extraction_results"][0])
        second_result["extraction_result_id"] = "extract-2"
        second_result["document_id"] = second_document_id
        payload["extraction_results"].append(second_result)

    return CurationPrepAgentInput.model_validate(payload)


def _make_agent_output() -> CurationPrepAgentOutput:
    return CurationPrepAgentOutput.model_validate(
        {
            "candidates": [
                {
                    "adapter_key": "disease",
                    "profile_key": "primary",
                    "extracted_fields": [
                        {
                            "field_path": "gene_symbol",
                            "value_type": "string",
                            "string_value": "APOE",
                            "number_value": None,
                            "boolean_value": None,
                            "json_value": None,
                        }
                    ],
                    "evidence_references": [
                        {
                            "field_path": "gene_symbol",
                            "evidence_record_id": "evidence-1",
                            "extraction_result_id": "extract-1",
                            "anchor": {
                                "anchor_kind": "snippet",
                                "locator_quality": "exact_quote",
                                "supports_decision": "supports",
                                "snippet_text": "APOE was associated with the reported phenotype.",
                                "sentence_text": "APOE was associated with the reported phenotype.",
                                "viewer_search_text": "APOE was associated with the reported phenotype.",
                                "page_number": 3,
                                "section_title": "Results",
                                "subsection_title": "Disease association",
                                "figure_reference": "Fig. 2",
                                "chunk_ids": ["chunk-1"],
                            },
                            "rationale": "The exact quote names APOE in direct association with the finding.",
                        }
                    ],
                    "conversation_context_summary": "Conversation narrowed to the APOE disease finding.",
                    "confidence": 0.91,
                    "unresolved_ambiguities": [],
                }
            ],
            "run_metadata": {
                "model_name": "service-populated",
                "token_usage": {
                    "input_tokens": 0,
                    "output_tokens": 0,
                    "total_tokens": 0,
                },
                "processing_notes": ["Candidate derived from the retained extraction envelope."],
                "warnings": [],
            },
        }
    )


@pytest.mark.asyncio
async def test_run_curation_prep_populates_usage_and_persists_raw_output(monkeypatch):
    """The service should enrich runtime metadata and persist the raw model payload."""

    captured: dict[str, object] = {}
    agent_input = _make_agent_input()
    raw_output = _make_agent_output()

    monkeypatch.setattr(
        module,
        "get_curation_prep_agent_definition",
        lambda: SimpleNamespace(model_config=SimpleNamespace(model="gpt-5-mini")),
    )
    monkeypatch.setattr(module, "create_curation_prep_agent", lambda: "prep-agent")
    monkeypatch.setattr(module, "RunConfig", lambda **kwargs: kwargs)

    async def _fake_runner_run(agent, input_text, run_config):
        captured["runner_args"] = {
            "agent": agent,
            "input_text": input_text,
            "run_config": run_config,
        }
        return SimpleNamespace(
            final_output=raw_output,
            context_wrapper=SimpleNamespace(
                usage=SimpleNamespace(
                    input_tokens=120,
                    output_tokens=35,
                    total_tokens=180,
                )
            ),
            raw_responses=[
                SimpleNamespace(id="resp-1"),
                SimpleNamespace(response_id="resp-2"),
            ],
        )

    monkeypatch.setattr(module.Runner, "run", _fake_runner_run)

    def _fake_persist_extraction_result(request, *, db=None):
        captured["persistence"] = {"request": request, "db": db}
        return SimpleNamespace(extraction_result=SimpleNamespace(extraction_result_id="stored-1"))

    monkeypatch.setattr(module, "persist_extraction_result", _fake_persist_extraction_result)

    result = await module.run_curation_prep(agent_input)

    assert captured["runner_args"]["agent"] == "prep-agent"
    assert "CurationPrepAgentInput JSON payload" in captured["runner_args"]["input_text"]
    assert captured["runner_args"]["run_config"]["workflow_name"] == "Curation prep"
    assert captured["runner_args"]["run_config"]["group_id"] == "chat-session-1"

    assert result.run_metadata.model_name == "gpt-5-mini"
    assert result.run_metadata.token_usage.input_tokens == 120
    assert result.run_metadata.token_usage.output_tokens == 35
    assert result.run_metadata.token_usage.total_tokens == 180
    assert result.run_metadata.processing_notes == [
        "Candidate derived from the retained extraction envelope."
    ]

    persisted_request = captured["persistence"]["request"]
    assert persisted_request.document_id == "document-1"
    assert persisted_request.agent_key == "curation_prep"
    assert persisted_request.source_kind is CurationExtractionSourceKind.CHAT
    assert persisted_request.adapter_key == "disease"
    assert persisted_request.profile_key == "primary"
    assert persisted_request.domain_key == "disease"
    assert persisted_request.origin_session_id == "chat-session-1"
    assert persisted_request.trace_id == "trace-upstream"
    assert persisted_request.user_id == "user-upstream"
    assert persisted_request.candidate_count == 1
    assert persisted_request.payload_json["run_metadata"]["model_name"] == "service-populated"
    assert persisted_request.metadata["final_run_metadata"]["model_name"] == "gpt-5-mini"
    assert persisted_request.metadata["raw_response_ids"] == ["resp-1", "resp-2"]


@pytest.mark.asyncio
async def test_run_curation_prep_resolves_document_id_once(monkeypatch):
    """Document id validation should be reused for persistence rather than recomputed."""

    agent_input = _make_agent_input()
    raw_output = _make_agent_output()
    original_resolve_document_id = module._resolve_document_id
    resolve_document_id_calls = 0

    def _spy_resolve_document_id(extraction_results, persistence_context):
        nonlocal resolve_document_id_calls
        resolve_document_id_calls += 1
        return original_resolve_document_id(extraction_results, persistence_context)

    monkeypatch.setattr(module, "_resolve_document_id", _spy_resolve_document_id)
    monkeypatch.setattr(
        module,
        "get_curation_prep_agent_definition",
        lambda: SimpleNamespace(model_config=SimpleNamespace(model="gpt-5-mini")),
    )
    monkeypatch.setattr(module, "create_curation_prep_agent", lambda: "prep-agent")
    monkeypatch.setattr(module, "RunConfig", lambda **kwargs: kwargs)

    async def _fake_runner_run(_agent, _input_text, run_config=None):
        return SimpleNamespace(final_output=raw_output, context_wrapper=None, raw_responses=[])

    monkeypatch.setattr(module.Runner, "run", _fake_runner_run)
    monkeypatch.setattr(module, "persist_extraction_result", lambda *_args, **_kwargs: None)

    await module.run_curation_prep(agent_input)

    assert resolve_document_id_calls == 1


def test_resolve_primary_extraction_result_requires_non_empty_input():
    """The explicit first-result helper should guard empty sequences clearly."""

    with pytest.raises(ValueError, match="at least one extraction result"):
        module._resolve_primary_extraction_result([])


@pytest.mark.asyncio
async def test_run_curation_prep_rejects_multiple_document_ids(monkeypatch):
    """Persisted prep output must target a single document id."""

    agent_input = _make_agent_input(second_document_id="document-2")
    raw_output = _make_agent_output()

    monkeypatch.setattr(
        module,
        "get_curation_prep_agent_definition",
        lambda: SimpleNamespace(model_config=SimpleNamespace(model="gpt-5-mini")),
    )
    monkeypatch.setattr(module, "create_curation_prep_agent", lambda: "prep-agent")
    monkeypatch.setattr(module, "RunConfig", lambda **kwargs: kwargs)

    async def _fake_runner_run(_agent, _input_text, _run_config):
        return SimpleNamespace(
            final_output=raw_output,
            context_wrapper=SimpleNamespace(
                usage=SimpleNamespace(input_tokens=10, output_tokens=5, total_tokens=15)
            ),
            raw_responses=[],
        )

    monkeypatch.setattr(module.Runner, "run", _fake_runner_run)
    monkeypatch.setattr(module, "persist_extraction_result", lambda *_args, **_kwargs: None)

    with pytest.raises(ValueError, match="exactly one document"):
        await module.run_curation_prep(agent_input)


@pytest.mark.asyncio
async def test_run_curation_prep_rejects_mismatched_document_id_override(monkeypatch):
    """Persistence context cannot retarget the run to a different document."""

    agent_input = _make_agent_input()

    monkeypatch.setattr(
        module,
        "get_curation_prep_agent_definition",
        lambda: SimpleNamespace(model_config=SimpleNamespace(model="gpt-5-mini")),
    )
    monkeypatch.setattr(module, "create_curation_prep_agent", lambda: "prep-agent")
    monkeypatch.setattr(module, "RunConfig", lambda **kwargs: kwargs)
    monkeypatch.setattr(module.Runner, "run", lambda *_args, **_kwargs: None)

    with pytest.raises(ValueError, match="must match"):
        await module.run_curation_prep(
            agent_input,
            persistence_context=module.CurationPrepPersistenceContext(
                document_id="document-2",
            ),
        )
