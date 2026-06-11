"""Unit tests for active-run recorded-evidence workspace tools."""

from __future__ import annotations

import pytest

import src.lib.openai_agents.tools.evidence_workspace as evidence_workspace
from src.lib.openai_agents.evidence_summary import canonicalize_structured_result_payload
from src.schemas.models.domain_envelope_extraction import DomainEnvelopeExtractionResult


@pytest.fixture(autouse=True)
def identity_function_tool(monkeypatch):
    monkeypatch.setattr(evidence_workspace, "function_tool", lambda fn: fn)


@pytest.fixture
def workspace_records():
    records = [
        {
            "evidence_record_id": "ev-active",
            "entity": "flcn",
            "verified_quote": "flcn was detected in embryonic brain.",
            "page": 6,
            "section": "Results",
            "chunk_id": "chunk-1",
            "document_id": "doc-1",
            "source_span_ids": ["chunk-1:s0000:c0000-c0037:aaaabbbb"],
            "source_fragments": [
                {
                    "span_id": "chunk-1:s0000:c0000-c0037:aaaabbbb",
                    "chunk_id": "chunk-1",
                    "document_id": "doc-1",
                    "text": "flcn was detected in embryonic brain.",
                    "char_start": 0,
                    "char_end": 37,
                    "text_hash": "aaaabbbb",
                    "page": 6,
                    "section": "Results",
                }
            ],
        },
        {
            "evidence_record_id": "ev-other-document",
            "entity": "other",
            "verified_quote": "Other document quote.",
            "page": 1,
            "section": "Results",
            "chunk_id": "chunk-other",
            "document_id": "doc-2",
        },
    ]
    token = evidence_workspace.set_active_evidence_records(records)
    try:
        yield records
    finally:
        evidence_workspace.reset_active_evidence_records(token)


@pytest.mark.asyncio
async def test_list_and_get_recorded_evidence_are_current_document_scoped(workspace_records):
    list_tool = evidence_workspace.create_list_recorded_evidence_tool("doc-1", "user-1")
    get_tool = evidence_workspace.create_get_recorded_evidence_tool("doc-1", "user-1")
    workspace_records[0]["evidence_revision_history"] = [
        {"revision": 1, "previous_source": {"verified_quote": "Previous quote."}}
    ]

    listed = await list_tool()
    fetched = await get_tool("ev-active")
    missing_other_doc = await get_tool("ev-other-document")

    assert listed["count"] == 1
    assert listed["evidence_records"][0]["evidence_record_id"] == "ev-active"
    assert listed["evidence_records"][0]["source_span_count"] == 1
    assert "source_span_ids" not in listed["evidence_records"][0]
    assert "evidence_revision_history" not in listed["evidence_records"][0]
    assert fetched["record"]["verified_quote"] == "flcn was detected in embryonic brain."
    assert "evidence_revision_history" not in fetched["record"]
    assert workspace_records[0]["evidence_revision_history"] == [
        {"revision": 1, "previous_source": {"verified_quote": "Previous quote."}}
    ]
    assert missing_other_doc["status"] == "not_found"


@pytest.mark.asyncio
async def test_attach_and_detach_evidence_to_object_or_pending_ref(workspace_records):
    attach_tool = evidence_workspace.create_attach_evidence_to_object_tool("doc-1", "user-1")
    detach_tool = evidence_workspace.create_detach_evidence_from_object_tool("doc-1", "user-1")
    list_tool = evidence_workspace.create_list_recorded_evidence_tool("doc-1", "user-1")

    attached = await attach_tool(
        "ev-active",
        pending_ref_id="expression-flcn-brain",
        field_path="expression_annotation_subject.gene_symbol",
    )
    filtered = await list_tool(pending_ref_id="expression-flcn-brain")

    assert attached["record"]["envelope_targets"] == [
        {
            "pending_ref_id": "expression-flcn-brain",
            "field_path": "expression_annotation_subject.gene_symbol",
        }
    ]
    assert workspace_records[0]["pending_ref_id"] == "expression-flcn-brain"
    assert workspace_records[0]["field_paths"] == [
        "expression_annotation_subject.gene_symbol"
    ]
    assert filtered["count"] == 1

    detached = await detach_tool("ev-active", pending_ref_id="expression-flcn-brain")

    assert detached["record"]["envelope_targets"] == []
    assert "pending_ref_id" not in workspace_records[0]
    assert "object_id" not in workspace_records[0]


@pytest.mark.asyncio
async def test_attach_requires_field_path_for_new_extraction_targets(workspace_records):
    attach_tool = evidence_workspace.create_attach_evidence_to_object_tool("doc-1", "user-1")

    missing_field = await attach_tool(
        "ev-active",
        pending_ref_id="expression-flcn-brain",
        field_path="",
    )
    missing_object = await attach_tool(
        "ev-active",
        field_path="expression_annotation_subject.gene_symbol",
    )

    assert missing_field == {
        "status": "invalid_request",
        "message": (
            "field_path is required when attaching evidence to a new extraction target."
        ),
    }
    assert missing_object == {
        "status": "invalid_request",
        "message": "Provide object_id or pending_ref_id plus field_path.",
    }
    assert "envelope_targets" not in workspace_records[0]


@pytest.mark.asyncio
async def test_scoped_workspace_tools_limit_ids_and_target_changes(workspace_records):
    workspace_records.append(
        {
            "evidence_record_id": "ev-unscoped",
            "entity": "other",
            "verified_quote": "Other quote.",
            "page": 3,
            "section": "Results",
            "chunk_id": "chunk-other",
            "document_id": "doc-1",
        }
    )
    list_tool = evidence_workspace.create_list_recorded_evidence_tool(
        "doc-1",
        "user-1",
        workspace_records=workspace_records,
        allowed_evidence_record_ids={"ev-active"},
    )
    get_tool = evidence_workspace.create_get_recorded_evidence_tool(
        "doc-1",
        "user-1",
        workspace_records=workspace_records,
        allowed_evidence_record_ids={"ev-active"},
    )
    attach_tool = evidence_workspace.create_attach_evidence_to_object_tool(
        "doc-1",
        "user-1",
        workspace_records=workspace_records,
        allowed_evidence_record_ids={"ev-active"},
        required_pending_ref_id="expression-flcn-brain",
        required_field_path="expression_annotation_subject.gene_symbol",
    )
    detach_tool = evidence_workspace.create_detach_evidence_from_object_tool(
        "doc-1",
        "user-1",
        workspace_records=workspace_records,
        allowed_evidence_record_ids={"ev-active"},
        allow_detach=False,
    )
    update_tool = evidence_workspace.create_update_recorded_evidence_metadata_tool(
        "doc-1",
        "user-1",
        workspace_records=workspace_records,
        allowed_evidence_record_ids={"ev-active"},
        required_field_path="expression_annotation_subject.gene_symbol",
    )

    listed = await list_tool(include_discarded=True)
    forbidden_get = await get_tool("ev-unscoped")
    forbidden_attach = await attach_tool(
        "ev-active",
        pending_ref_id="other-target",
        field_path="expression_annotation_subject.gene_symbol",
    )
    attached = await attach_tool(
        "ev-active",
        pending_ref_id="expression-flcn-brain",
        field_path="expression_annotation_subject.gene_symbol",
    )
    forbidden_detach = await detach_tool("ev-active", pending_ref_id="expression-flcn-brain")
    forbidden_update = await update_tool("ev-active", field_path="other.field")

    assert listed["count"] == 1
    assert listed["evidence_records"][0]["evidence_record_id"] == "ev-active"
    assert forbidden_get["status"] == "forbidden"
    assert forbidden_get["allowed_evidence_record_ids"] == ["ev-active"]
    assert forbidden_attach["status"] == "forbidden"
    assert "retarget" in forbidden_attach["message"]
    assert attached["status"] == "ok"
    assert attached["record"]["envelope_targets"] == [
        {
            "pending_ref_id": "expression-flcn-brain",
            "field_path": "expression_annotation_subject.gene_symbol",
        }
    ]
    assert forbidden_detach["status"] == "forbidden"
    assert "cannot detach" in forbidden_detach["message"]
    assert forbidden_update["status"] == "forbidden"
    assert forbidden_update["target_field_path"] == (
        "expression_annotation_subject.gene_symbol"
    )


@pytest.mark.asyncio
async def test_detach_removes_field_path_for_only_detached_target(workspace_records):
    attach_tool = evidence_workspace.create_attach_evidence_to_object_tool("doc-1", "user-1")
    detach_tool = evidence_workspace.create_detach_evidence_from_object_tool("doc-1", "user-1")
    update_tool = evidence_workspace.create_update_recorded_evidence_metadata_tool(
        "doc-1",
        "user-1",
    )

    await attach_tool("ev-active", pending_ref_id="obj-a", field_path="field.a")
    await attach_tool("ev-active", pending_ref_id="obj-b", field_path="field.b")

    detached = await detach_tool("ev-active", pending_ref_id="obj-a")

    assert detached["record"]["envelope_targets"] == [
        {"pending_ref_id": "obj-b", "field_path": "field.b"}
    ]
    assert workspace_records[0]["field_path"] == "field.b"
    assert workspace_records[0]["field_paths"] == ["field.b"]

    await update_tool("ev-active", field_path="agent.selected_field")
    await attach_tool("ev-active", pending_ref_id="obj-a", field_path="field.a")
    detached_after_metadata_update = await detach_tool("ev-active", pending_ref_id="obj-a")

    assert detached_after_metadata_update["record"]["envelope_targets"] == [
        {"pending_ref_id": "obj-b", "field_path": "field.b"}
    ]
    assert workspace_records[0]["field_path"] == "agent.selected_field"
    assert workspace_records[0]["field_paths"] == ["agent.selected_field", "field.b"]


@pytest.mark.asyncio
async def test_discard_recorded_evidence_is_status_change_not_delete(workspace_records):
    discard_tool = evidence_workspace.create_discard_recorded_evidence_tool("doc-1", "user-1")
    list_tool = evidence_workspace.create_list_recorded_evidence_tool("doc-1", "user-1")

    discarded = await discard_tool("ev-active", reason="Wrong anatomy context")
    active_only = await list_tool()
    with_discarded = await list_tool(include_discarded=True)

    assert discarded["record"]["status"] == "discarded"
    assert workspace_records[0]["status"] == "discarded"
    assert workspace_records[0]["discard_reason"] == "Wrong anatomy context"
    assert len(workspace_records) == 2
    assert active_only["count"] == 0
    assert with_discarded["count"] == 1
    assert with_discarded["evidence_records"][0]["discard_reason"] == "Wrong anatomy context"


@pytest.mark.asyncio
async def test_update_metadata_cannot_mutate_quote_or_provenance(workspace_records):
    update_tool = evidence_workspace.create_update_recorded_evidence_metadata_tool(
        "doc-1",
        "user-1",
    )
    original_quote = workspace_records[0]["verified_quote"]
    original_spans = list(workspace_records[0]["source_span_ids"])

    updated = await update_tool(
        "ev-active",
        entity="flcn expression",
        field_path="anatomical_structure",
        agent_note="Use for expression location only.",
    )

    assert updated["record"]["entity"] == "flcn expression"
    assert workspace_records[0]["field_path"] == "anatomical_structure"
    assert workspace_records[0]["agent_note"] == "Use for expression location only."
    assert workspace_records[0]["verified_quote"] == original_quote
    assert workspace_records[0]["source_span_ids"] == original_spans

    with pytest.raises(TypeError, match="verified_quote"):
        await update_tool("ev-active", verified_quote="mutated")

    with pytest.raises(TypeError, match="source_span_ids"):
        await update_tool("ev-active", source_span_ids=["mutated"])


@pytest.mark.asyncio
async def test_record_list_attach_discard_finalize_flow_omits_discarded(workspace_records):
    attach_tool = evidence_workspace.create_attach_evidence_to_object_tool("doc-1", "user-1")
    discard_tool = evidence_workspace.create_discard_recorded_evidence_tool("doc-1", "user-1")
    list_tool = evidence_workspace.create_list_recorded_evidence_tool("doc-1", "user-1")

    workspace_records.append(
        {
            "evidence_record_id": "ev-weak",
            "entity": "weak",
            "verified_quote": "Weak quote.",
            "page": 7,
            "section": "Results",
            "chunk_id": "chunk-weak",
            "document_id": "doc-1",
        }
    )

    await attach_tool(
        "ev-active",
        pending_ref_id="expression-flcn-brain",
        field_path="expression_annotation_subject.gene_symbol",
    )
    await discard_tool("ev-weak", reason="Not specific enough")
    listed = await list_tool()

    payload = {
        "curatable_objects": [
            {
                "object_type": "GeneExpressionAnnotation",
                "pending_ref_id": "expression-flcn-brain",
                "payload": {"gene": {"symbol": "flcn"}},
                "evidence_record_ids": ["ev-active"],
            }
        ],
        "run_summary": {"kept_count": 1},
    }
    canonical = canonicalize_structured_result_payload(
        payload,
        preferred_evidence_records=workspace_records,
    )

    assert listed["count"] == 1
    assert canonical["metadata"]["evidence_records"] == [
        {
            "entity": "flcn",
            "verified_quote": "flcn was detected in embryonic brain.",
            "page": 6,
            "section": "Results",
            "chunk_id": "chunk-1",
            "document_id": "doc-1",
            "source_span_ids": ["chunk-1:s0000:c0000-c0037:aaaabbbb"],
            "source_fragments": [
                {
                    "span_id": "chunk-1:s0000:c0000-c0037:aaaabbbb",
                    "chunk_id": "chunk-1",
                    "document_id": "doc-1",
                    "text": "flcn was detected in embryonic brain.",
                    "char_start": 0,
                    "char_end": 37,
                    "text_hash": "aaaabbbb",
                    "page": 6,
                    "section": "Results",
                }
            ],
            "pending_ref_id": "expression-flcn-brain",
            "object_ref": {"pending_ref_id": "expression-flcn-brain"},
            "envelope_target": {
                "pending_ref_id": "expression-flcn-brain",
                "field_path": "expression_annotation_subject.gene_symbol",
            },
            "envelope_targets": [
                {
                    "pending_ref_id": "expression-flcn-brain",
                    "field_path": "expression_annotation_subject.gene_symbol",
                }
            ],
            "field_path": "expression_annotation_subject.gene_symbol",
            "field_paths": ["expression_annotation_subject.gene_symbol"],
            "evidence_record_id": "ev-active",
        }
    ]
    validated = DomainEnvelopeExtractionResult.model_validate(canonical)
    validated_record = validated.metadata.evidence_records[0]
    assert validated_record.pending_ref_id == "expression-flcn-brain"
    assert validated_record.object_ref is not None
    assert validated_record.object_ref.pending_ref_id == "expression-flcn-brain"
    assert validated_record.envelope_targets is not None
    assert validated_record.envelope_targets[0].field_path == (
        "expression_annotation_subject.gene_symbol"
    )


@pytest.fixture
def many_workspace_records():
    records = [
        {
            "evidence_record_id": f"ev-{index}",
            "entity": f"gene-{index}",
            "verified_quote": (
                "GFP signal" if index % 2 == 0 else "mCherry signal"
            )
            + f" in sample {index}.",
            "page": index,
            "section": "Results",
            "chunk_id": f"chunk-{index}",
            "document_id": "doc-1",
        }
        for index in range(5)
    ]
    token = evidence_workspace.set_active_evidence_records(records)
    try:
        yield records
    finally:
        evidence_workspace.reset_active_evidence_records(token)


@pytest.mark.asyncio
async def test_list_recorded_evidence_pages_with_offset_and_next_offset(many_workspace_records):
    list_tool = evidence_workspace.create_list_recorded_evidence_tool("doc-1", "user-1")

    first = await list_tool(limit=2, offset=0)
    assert first["count"] == 5
    assert first["returned_count"] == 2
    assert first["offset"] == 0
    assert first["next_offset"] == 2
    assert first["truncated"] is True
    assert [r["evidence_record_id"] for r in first["evidence_records"]] == ["ev-0", "ev-1"]

    last = await list_tool(limit=2, offset=4)
    assert last["returned_count"] == 1
    assert last["offset"] == 4
    assert last["next_offset"] is None
    assert last["truncated"] is False
    assert [r["evidence_record_id"] for r in last["evidence_records"]] == ["ev-4"]


@pytest.mark.asyncio
async def test_list_recorded_evidence_filters_by_text_contains(many_workspace_records):
    list_tool = evidence_workspace.create_list_recorded_evidence_tool("doc-1", "user-1")

    matched = await list_tool(text_contains="mcherry")

    assert matched["count"] == 2
    assert {r["evidence_record_id"] for r in matched["evidence_records"]} == {"ev-1", "ev-3"}
    # The compact list summary never echoes the full quote text back.
    assert "verified_quote" not in matched["evidence_records"][0]


@pytest.mark.asyncio
async def test_list_recorded_evidence_text_contains_pages(many_workspace_records):
    list_tool = evidence_workspace.create_list_recorded_evidence_tool("doc-1", "user-1")

    page = await list_tool(text_contains="gfp", limit=2, offset=0)

    assert page["count"] == 3
    assert page["returned_count"] == 2
    assert page["next_offset"] == 2
    assert page["truncated"] is True
    assert {r["evidence_record_id"] for r in page["evidence_records"]} == {"ev-0", "ev-2"}
