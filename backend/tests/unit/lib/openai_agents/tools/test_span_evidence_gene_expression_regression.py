"""Regression coverage for gene-expression span evidence workflows."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

import src.lib.openai_agents.tools.evidence_workspace as evidence_workspace
import src.lib.openai_agents.tools.record_evidence as record_evidence
import src.lib.openai_agents.tools.weaviate_search as weaviate_search
from src.lib.domain_packs.loader import load_domain_pack_metadata
from src.lib.domain_packs.registry import LoadedDomainPack
from src.lib.domain_packs.validator_dispatch import dispatch_active_validator_bindings
from src.lib.openai_agents.evidence_summary import (
    build_record_evidence_summary_record,
    canonicalize_structured_result_payload,
)
from src.schemas.domain_envelope import CuratableObjectEnvelope, DomainEnvelope


@pytest.fixture(autouse=True)
def identity_function_tool(monkeypatch):
    monkeypatch.setattr(weaviate_search, "function_tool", lambda fn: fn)
    monkeypatch.setattr(record_evidence, "function_tool", lambda fn: fn)
    monkeypatch.setattr(evidence_workspace, "function_tool", lambda fn: fn)


@pytest.fixture
def sandbox_fixture() -> dict[str, Any]:
    fixture_path = (
        Path(__file__).resolve().parents[4]
        / "fixtures"
        / "evidence"
        / "span_gene_expression_sandbox_cases.json"
    )
    return json.loads(fixture_path.read_text(encoding="utf-8"))


@pytest.fixture
def sandbox_chunks(sandbox_fixture: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(chunk["chunk_id"]): _runtime_chunk(chunk)
        for chunk in sandbox_fixture["chunks"]
    }


def _runtime_chunk(chunk: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": chunk["chunk_id"],
        "text": chunk["text"],
        "chunk_index": chunk["chunk_index"],
        "page_number": chunk["page_number"],
        "section_title": chunk["section_title"],
        "parent_section": chunk["section_title"],
        "subsection": chunk["subsection"],
        "metadata": {
            "section_title": chunk["section_title"],
            "page_number": chunk["page_number"],
        },
        "doc_items": [{"id": f"{chunk['chunk_id']}-bbox"}],
    }


def _case(fixture: dict[str, Any], case_id: str) -> dict[str, Any]:
    return next(
        chunk
        for chunk in fixture["chunks"]
        if chunk["case_id"] == case_id
    )


def _install_chunk_tools(
    monkeypatch: pytest.MonkeyPatch,
    chunks: dict[str, dict[str, Any]],
) -> None:
    async def _fake_get_chunk_by_id(**kwargs):
        return copy.deepcopy(chunks.get(kwargs["chunk_id"]))

    async def _fake_get_chunk_neighbor_ids(**kwargs):
        chunk_index = kwargs["chunk_index"]
        previous_chunk_id = None
        next_chunk_id = None
        for chunk in chunks.values():
            if chunk.get("chunk_index") == chunk_index - 1:
                previous_chunk_id = str(chunk["id"])
            if chunk.get("chunk_index") == chunk_index + 1:
                next_chunk_id = str(chunk["id"])
        return {
            "previous_chunk_id": previous_chunk_id,
            "next_chunk_id": next_chunk_id,
        }

    monkeypatch.setattr(weaviate_search, "get_chunk_by_id", _fake_get_chunk_by_id)
    monkeypatch.setattr(record_evidence, "get_chunk_by_id", _fake_get_chunk_by_id)
    monkeypatch.setattr(
        weaviate_search,
        "get_chunk_neighbor_ids",
        _fake_get_chunk_neighbor_ids,
    )


def _record_summary(
    *,
    entity: str,
    span_ids: list[str],
    tool_output: dict[str, Any],
) -> dict[str, Any]:
    record = build_record_evidence_summary_record(
        tool_name="record_evidence",
        tool_input={"entity": entity, "span_ids": span_ids},
        tool_output=tool_output,
    )
    assert record is not None
    return record


def _alliance_gene_expression_pack() -> LoadedDomainPack:
    repo_root = Path(__file__).resolve().parents[6]
    pack_path = repo_root / "packages" / "alliance" / "domain_packs" / "gene_expression"
    metadata_path = pack_path / "domain_pack.yaml"
    metadata = load_domain_pack_metadata(metadata_path)
    return LoadedDomainPack(
        pack_id=metadata.pack_id,
        display_name=metadata.display_name,
        version=metadata.version,
        pack_path=pack_path,
        metadata_path=metadata_path,
        metadata=metadata,
    )


@pytest.mark.asyncio
async def test_pat_unc_tln_expression_records_backend_span_text_not_model_quote(
    monkeypatch: pytest.MonkeyPatch,
    sandbox_fixture: dict[str, Any],
    sandbox_chunks: dict[str, dict[str, Any]],
):
    document_id = sandbox_fixture["document_id"]
    expression_case = _case(sandbox_fixture, "pat_unc_tln_expression")
    expression_chunk = sandbox_chunks[expression_case["chunk_id"]]
    _install_chunk_tools(monkeypatch, sandbox_chunks)

    async def _fake_hybrid_search(**kwargs):
        assert kwargs["query"] == "PAT-3 UNC-112 TLN-1 expression"
        return [{**copy.deepcopy(expression_chunk), "score": 0.97}]

    monkeypatch.setattr(weaviate_search, "hybrid_search_chunks", _fake_hybrid_search)
    search_tool = weaviate_search.create_search_tool(document_id, "user-1")
    read_tool = weaviate_search.create_read_chunk_tool(document_id, "user-1")
    record_tool = record_evidence.create_record_evidence_tool(document_id, "user-1")

    search_result = await search_tool("PAT-3 UNC-112 TLN-1 expression")
    read_result = await read_tool(search_result.hits[0].chunk_id)
    assert read_result.chunk is not None

    expression_span = next(
        span
        for span in read_result.chunk.evidence_spans
        if "PAT-3, UNC-112, and TLN-1 reporters" in span.text
    )
    result = await record_tool(
        entity="PAT-3/UNC-112/TLN-1 expression",
        span_ids=[expression_span.span_id],
    )

    assert result["status"] == "verified"
    assert result["verified_quote"] == expression_span.text
    assert result["verified_quote"] == (
        "PAT-3, UNC-112, and TLN-1 reporters were detected in the PLM "
        "mechanosensory neurons and their ventral branches."
    )
    assert result["verified_quote"] != (
        "PAT-3, UNC-112, and TLN-1 are expressed in mechanosensory neurons "
        "and colocalize with RPM-1."
    )
    assert result["source_span_ids"] == [expression_span.span_id]
    assert result["source_fragments"][0]["text"] == expression_span.text
    assert result["source_fragments"][0]["char_start"] == expression_span.char_start
    assert result["source_fragments"][0]["char_end"] == expression_span.char_end

    evidence_record = _record_summary(
        entity="PAT-3/UNC-112/TLN-1 expression",
        span_ids=[expression_span.span_id],
        tool_output=result,
    )
    payload = {
        "curatable_objects": [
            {
                "object_type": "GeneExpressionAnnotation",
                "pending_ref_id": "expression-pat-unc-tln-plm",
                "payload": {
                    "expression_annotation_subject": {"gene_symbol": "pat-3"},
                    "expression_pattern": {
                        "where_expressed": {
                            "anatomical_structure": {
                                "name": "PLM mechanosensory neurons"
                            }
                        }
                    },
                },
                "evidence_record_ids": [evidence_record["evidence_record_id"]],
            }
        ],
        "run_summary": {"kept_count": 1},
    }

    canonical = canonicalize_structured_result_payload(
        payload,
        preferred_evidence_records=[evidence_record],
    )

    assert len(canonical["curatable_objects"]) == 1
    assert canonical["metadata"]["evidence_records"] == [evidence_record]
    assert canonical["run_summary"]["kept_count"] == 1


@pytest.mark.asyncio
async def test_tln_rpm_colocalization_records_figure_title_and_result_summary_spans(
    monkeypatch: pytest.MonkeyPatch,
    sandbox_fixture: dict[str, Any],
    sandbox_chunks: dict[str, dict[str, Any]],
):
    document_id = sandbox_fixture["document_id"]
    colocalization_case = _case(sandbox_fixture, "tln_rpm_colocalization")
    _install_chunk_tools(monkeypatch, sandbox_chunks)
    read_tool = weaviate_search.create_read_chunk_tool(document_id, "user-1")
    record_tool = record_evidence.create_record_evidence_tool(document_id, "user-1")

    read_result = await read_tool(colocalization_case["chunk_id"])
    assert read_result.chunk is not None
    title_span = next(
        span
        for span in read_result.chunk.evidence_spans
        if span.text == "TLN-1 and RPM-1 colocalize in mechanosensory neurons."
    )
    summary_span = next(
        span
        for span in read_result.chunk.evidence_spans
        if span.text.startswith("TLN-1::GFP overlapped with RPM-1 puncta")
    )

    result = await record_tool(
        entity="TLN-1/RPM-1 colocalization",
        span_ids=[title_span.span_id, summary_span.span_id],
    )

    assert result["status"] == "verified"
    assert result["verified_quote"] == (
        "TLN-1 and RPM-1 colocalize in mechanosensory neurons.\n\n"
        "TLN-1::GFP overlapped with RPM-1 puncta at presynaptic regions in "
        "the PLM mechanosensory neurons."
    )
    assert result["figure_reference"] == "Figure 6"
    assert result["source_span_ids"] == [title_span.span_id, summary_span.span_id]
    assert [fragment["text"] for fragment in result["source_fragments"]] == [
        title_span.text,
        summary_span.text,
    ]


@pytest.mark.asyncio
async def test_span_evidence_workspace_finalize_and_validator_dispatch_flow(
    monkeypatch: pytest.MonkeyPatch,
    sandbox_fixture: dict[str, Any],
    sandbox_chunks: dict[str, dict[str, Any]],
):
    document_id = sandbox_fixture["document_id"]
    _install_chunk_tools(monkeypatch, sandbox_chunks)
    read_tool = weaviate_search.create_read_chunk_tool(document_id, "user-1")
    record_tool = record_evidence.create_record_evidence_tool(document_id, "user-1")

    workspace_records: list[dict[str, Any]] = []
    token = evidence_workspace.set_active_evidence_records(workspace_records)
    try:
        list_tool = evidence_workspace.create_list_recorded_evidence_tool(
            document_id,
            "user-1",
        )
        attach_tool = evidence_workspace.create_attach_evidence_to_object_tool(
            document_id,
            "user-1",
        )
        discard_tool = evidence_workspace.create_discard_recorded_evidence_tool(
            document_id,
            "user-1",
        )

        weak_read = await read_tool("sandbox-weak-marker-context")
        assert weak_read.chunk is not None
        weak_span = weak_read.chunk.evidence_spans[0]
        weak_result = await record_tool(
            entity="weak marker context",
            span_ids=[weak_span.span_id],
        )
        weak_record = _record_summary(
            entity="weak marker context",
            span_ids=[weak_span.span_id],
            tool_output=weak_result,
        )
        workspace_records.append(weak_record)

        queued = await list_tool()
        assert queued["count"] == 1

        discarded = await discard_tool(
            weak_record["evidence_record_id"],
            reason="Methods-only marker context is too weak for final annotation",
        )
        assert discarded["record"]["status"] == "discarded"

        expression_read = await read_tool("sandbox-pat-unc-tln-expression")
        assert expression_read.chunk is not None
        expression_span = next(
            span
            for span in expression_read.chunk.evidence_spans
            if "PAT-3, UNC-112, and TLN-1 reporters" in span.text
        )
        active_result = await record_tool(
            entity="PAT-3/UNC-112/TLN-1 expression",
            span_ids=[expression_span.span_id],
        )
        active_record = _record_summary(
            entity="PAT-3/UNC-112/TLN-1 expression",
            span_ids=[expression_span.span_id],
            tool_output=active_result,
        )
        workspace_records.append(active_record)

        await attach_tool(
            active_record["evidence_record_id"],
            pending_ref_id="expression-pat-unc-tln-plm",
            field_path="expression_pattern.where_expressed.anatomical_structure.name",
        )
        active_only = await list_tool()
        with_discarded = await list_tool(include_discarded=True)

        assert active_only["count"] == 1
        assert active_only["evidence_records"][0]["evidence_record_id"] == (
            active_record["evidence_record_id"]
        )
        assert with_discarded["count"] == 2

        payload = {
            "curatable_objects": [
                {
                    "object_type": "GeneExpressionAnnotation",
                    "object_role": "curatable_unit",
                    "pending_ref_id": "expression-pat-unc-tln-plm",
                    "payload": {
                        "data_provider": {"abbreviation": "WB"},
                        "expression_annotation_subject": {
                            "primary_external_id": "WBGene00003975",
                            "gene_symbol": "pat-3",
                        },
                        "relation": {"name": "is_expressed_in"},
                        "single_reference": {"pmid": "PMID:000000"},
                        "expression_pattern": {
                            "where_expressed": {
                                "anatomical_structure": {
                                    "name": "PLM mechanosensory neurons"
                                }
                            }
                        },
                    },
                    "evidence_record_ids": [active_record["evidence_record_id"]],
                }
            ],
            "run_summary": {"kept_count": 1},
        }
        canonical = canonicalize_structured_result_payload(
            payload,
            preferred_evidence_records=workspace_records,
        )

        retained_records = canonical["metadata"]["evidence_records"]
        assert [
            record["evidence_record_id"]
            for record in retained_records
        ] == [active_record["evidence_record_id"]]
        assert retained_records[0]["verified_quote"] == active_record["verified_quote"]
        assert retained_records[0]["source_span_ids"] == active_record["source_span_ids"]
        assert retained_records[0]["pending_ref_id"] == "expression-pat-unc-tln-plm"
        assert "discard_reason" not in retained_records[0]

        envelope = DomainEnvelope(
            envelope_id="span-gene-expression-regression",
            domain_pack_id="agr.alliance.gene_expression",
            objects=[
                CuratableObjectEnvelope(**canonical["curatable_objects"][0])
            ],
            metadata={"evidence_records": retained_records},
        )
        captured_requests = []

        def _validator_runner(request, *, binding):
            captured_requests.append(request)
            resolved_values = {
                field_name: (
                    request.selected_inputs.get(field_name)
                    or request.selected_inputs.get("gene_id")
                    or request.selected_inputs.get("pmid")
                    or f"resolved-{field_name}"
                )
                for field_name in request.expected_result_fields
            }
            return {
                "status": "resolved",
                "request_id": request.request_id,
                "validator_binding_id": request.validator_binding_id,
                "validator_agent": request.validator_agent.model_dump(mode="json"),
                "target": request.target.model_dump(mode="json"),
                "resolved_values": resolved_values,
                "resolved_objects": [
                    {
                        "object_type": "ValidatorResolvedValue",
                        "canonical_id": request.request_id,
                        "payload": resolved_values,
                    }
                ],
                "missing_expected_fields": [],
                "candidates": [],
                "lookup_attempts": [
                    {
                        "provider": "fixture_lookup",
                        "method": "span_evidence_regression",
                        "query": request.selected_inputs,
                        "result_count": 1,
                        "outcome": "success",
                    }
                ],
                "curator_message": None,
                "explanation": "Fixture validator result.",
            }

        dispatch_result = dispatch_active_validator_bindings(
            envelope,
            _alliance_gene_expression_pack(),
            runner=_validator_runner,
            max_parallel_validators=1,
        )

        assert captured_requests
        assert all(
            request.target.object_type == "GeneExpressionAnnotation"
            for request in captured_requests
        )
        assert all(request.evidence for request in captured_requests)
        assert dispatch_result.validator_results
    finally:
        evidence_workspace.reset_active_evidence_records(token)
