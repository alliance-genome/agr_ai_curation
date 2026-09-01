"""Unit tests for the record_evidence document tool."""

import inspect
import logging
from copy import deepcopy
from typing import Any, cast
from uuid import NAMESPACE_URL, uuid5

import pytest

import src.lib.openai_agents.tools.record_evidence as record_evidence
from src.lib.openai_agents.tools import evidence_workspace
from src.lib.openai_agents.evidence_spans import build_evidence_spans
from src.lib.openai_agents.evidence_summary import build_evidence_record_id


class _Tracker:
    def __init__(self):
        self.calls = []

    def record_call(self, name: str):
        self.calls.append(name)


@pytest.fixture(autouse=True)
def identity_function_tool(monkeypatch):
    monkeypatch.setattr(record_evidence, "function_tool", lambda fn: fn)


@pytest.fixture
def runtime_exception_reports(monkeypatch):
    reports = []

    def _fake_report_runtime_exception(exc, **kwargs):
        reports.append((exc, kwargs))
        return True

    monkeypatch.setattr(
        record_evidence,
        "report_runtime_exception",
        _fake_report_runtime_exception,
    )
    return reports


def _chunk(
    *,
    chunk_id: str,
    text: str,
    page_number: int | None = 3,
    section: str = "Results",
    subsection: str | None = "Expression assays",
    doc_items: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "id": chunk_id,
        "text": text,
        "page_number": page_number,
        "parent_section": section,
        "subsection": subsection,
        "doc_items": doc_items or [],
        "metadata": {},
    }


def _span_ids(chunk_id: str, text: str) -> list[str]:
    return [
        span.span_id
        for span in build_evidence_spans(
            chunk_id=chunk_id,
            chunk_text=text,
            page_number=3,
            section_title="Results",
        )
    ]


def _chunk_uuid(label: str) -> str:
    return str(uuid5(NAMESPACE_URL, f"agr-ai-curation-test:{label}"))


def test_record_evidence_schema_accepts_span_ids_not_claimed_quote():
    tool = record_evidence.create_record_evidence_tool("doc-123", "user-1")
    signature = inspect.signature(tool)

    assert "span_ids" in signature.parameters
    assert "evidence_record_id" in signature.parameters
    assert "claimed_quote" not in signature.parameters
    assert "chunk_id" not in signature.parameters


@pytest.mark.asyncio
async def test_record_evidence_rejects_claimed_quote_argument_in_primary_path():
    tool = record_evidence.create_record_evidence_tool("doc-123", "user-1")

    with pytest.raises(TypeError, match="claimed_quote"):
        await tool(
            entity="wg",
            span_ids=[],
            claimed_quote="Wingless expression expanded in the mutant tissue.",
        )


def test_build_envelope_target_fields_normalizes_target_identity_and_field_path():
    assert record_evidence._build_envelope_target_fields(
        object_id="  expression:1  ",
        pending_ref_id="pending-1",
        object_type="  expression_assay  ",
        field_path="  gene.symbol  ",
        validation_finding_id=" validation:symbol ",
    ) == {
        "envelope_target": {
            "object_id": "expression:1",
            "object_type": "expression_assay",
            "field_path": "gene.symbol",
            "validation_finding_id": "validation:symbol",
        }
    }


def test_build_envelope_target_fields_uses_pending_ref_when_object_id_missing():
    assert record_evidence._build_envelope_target_fields(
        object_id=" ",
        pending_ref_id=" pending:gene:1 ",
        object_type="gene",
        field_path=" expression.field ",
    ) == {
        "envelope_target": {
            "pending_ref_id": "pending:gene:1",
            "object_type": "gene",
            "field_path": "expression.field",
        }
    }


def test_build_envelope_target_fields_rejects_target_without_field_path():
    with pytest.raises(ValueError, match="field_path is required"):
        record_evidence._build_envelope_target_fields(
            pending_ref_id="pending:gene:1",
            object_type="gene",
        )


def test_build_envelope_target_fields_omits_empty_targets():
    assert record_evidence._build_envelope_target_fields(
        object_id=None,
        pending_ref_id=" ",
        object_type="",
        field_path=None,
    ) == {}


def test_merge_extra_fields_returns_merged_fields_or_none():
    assert record_evidence._merge_extra_fields(
        {},
        {"envelope_target": {"object_id": "expression:1"}},
        {"retry_tool": "read_chunk"},
    ) == {
        "envelope_target": {"object_id": "expression:1"},
        "retry_tool": "read_chunk",
    }
    assert record_evidence._merge_extra_fields({}, {}) is None
    assert record_evidence._merge_extra_fields() is None


@pytest.mark.asyncio
async def test_record_evidence_copies_exact_span_text_and_tracks_call(monkeypatch):
    chunk_id = _chunk_uuid("chunk-expression-1")
    chunk_text = (
        "Wingless expression expanded in the mutant tissue. "
        "This sentence is exact evidence."
    )
    span_ids = _span_ids(chunk_id, chunk_text)
    captured = {}

    async def _fake_get_chunk_by_id(**kwargs):
        captured.update(kwargs)
        return _chunk(
            chunk_id=chunk_id,
            text=chunk_text,
            page_number=7,
            section="Results",
        )

    tracker = _Tracker()
    monkeypatch.setattr(record_evidence, "get_chunk_by_id", _fake_get_chunk_by_id)
    tool = record_evidence.create_record_evidence_tool(
        "doc-12345678",
        "user-1",
        tracker=cast(Any, tracker),
    )

    result = await tool(
        entity="wg",
        span_ids=[span_ids[1]],
        object_id=" expression:1 ",
        field_path=" assay.result ",
    )

    assert result == {
        "status": "verified",
        "entity": "wg",
        "span_ids": [span_ids[1]],
        "source_span_ids": [span_ids[1]],
        "verified_quote": "This sentence is exact evidence.",
        "document_id": "doc-12345678",
        "chunk_id": chunk_id,
        "chunk_ids": [chunk_id],
        "source_fragments": [
            {
                "span_id": span_ids[1],
                "chunk_id": chunk_id,
                "document_id": "doc-12345678",
                "text": "This sentence is exact evidence.",
                "char_start": 51,
                "char_end": 83,
                "text_hash": span_ids[1].rsplit(":", 1)[1],
                "span_index": 1,
                "span_type": "sentence",
                "spanizer_version": "pdf_sentence_v1",
                "page": 7,
                "section": "Results",
                "subsection": "Expression assays",
            }
        ],
        "envelope_target": {
            "object_id": "expression:1",
            "field_path": "assay.result",
        },
        "page": 7,
        "section": "Results",
        "subsection": "Expression assays",
        "evidence_record_id": build_evidence_record_id(
            evidence_record={
                "entity": "wg",
                "verified_quote": "This sentence is exact evidence.",
                "page": 7,
                "section": "Results",
                "chunk_id": chunk_id,
                "subsection": "Expression assays",
                "figure_reference": None,
                "source_span_ids": [span_ids[1]],
            }
        ),
    }
    assert captured == {
        "chunk_id": chunk_id,
        "user_id": "user-1",
        "document_id": "doc-12345678",
    }
    assert tracker.calls == ["record_evidence"]


@pytest.mark.asyncio
async def test_record_evidence_rejects_target_identity_without_field_path(monkeypatch):
    async def _unexpected_get_chunk_by_id(**_kwargs):
        raise AssertionError("target validation should run before span resolution")

    monkeypatch.setattr(record_evidence, "get_chunk_by_id", _unexpected_get_chunk_by_id)
    tool = record_evidence.create_record_evidence_tool("doc-123", "user-1")

    result = await tool(
        entity="wg",
        span_ids=["chunk:s0000:c0000-c0001:aaaabbbb"],
        pending_ref_id="expression-wg",
    )

    assert result == {
        "status": "forbidden",
        "entity": "wg",
        "message": (
            "record_evidence target arguments require field_path. "
            "Omit object/pending target arguments to create unattached source "
            "evidence, or provide field_path to attach the evidence to a "
            "concrete curatable field."
        ),
        "target_requires_field_path": True,
        "supplied_target": {"pending_ref_id": "expression-wg"},
    }


@pytest.mark.asyncio
async def test_record_evidence_rejects_malformed_span_without_lookup_or_error_log(
    monkeypatch,
    caplog,
    runtime_exception_reports,
):
    async def _unexpected_get_chunk_by_id(**_kwargs):
        raise AssertionError("malformed span IDs must not reach chunk lookup")

    monkeypatch.setattr(record_evidence, "get_chunk_by_id", _unexpected_get_chunk_by_id)
    tool = record_evidence.create_record_evidence_tool("doc-123", "user-1")

    with caplog.at_level(logging.INFO, logger=record_evidence.__name__):
        result = await tool(entity="wg", span_ids=["truncated-span-id"])

    assert result["status"] == "not_found"
    assert result["resolution_failure"] == "invalid_span_id"
    assert result["span_resolution_errors"][0]["resolution_failure"] == "invalid_span_id"
    assert not any(
        record.name == record_evidence.__name__ and record.levelno >= logging.ERROR
        for record in caplog.records
    )
    assert runtime_exception_reports == []


@pytest.mark.asyncio
async def test_record_evidence_rejects_non_uuid_chunk_without_lookup_or_error_log(
    monkeypatch,
    caplog,
):
    span_id = _span_ids("truncated-chunk-identity", "Exact sentence.")[0]

    async def _unexpected_get_chunk_by_id(**_kwargs):
        raise AssertionError("non-UUID chunk IDs must not reach chunk lookup")

    monkeypatch.setattr(record_evidence, "get_chunk_by_id", _unexpected_get_chunk_by_id)
    tool = record_evidence.create_record_evidence_tool("doc-123", "user-1")

    with caplog.at_level(logging.INFO, logger=record_evidence.__name__):
        result = await tool(entity="wg", span_ids=[span_id])

    assert result["status"] == "not_found"
    assert result["resolution_failure"] == "invalid_chunk_id"
    assert "UUID" in result["failed_span_error"]
    assert not any(
        record.name == record_evidence.__name__ and record.levelno >= logging.ERROR
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_record_evidence_rejects_unknown_span_chunk_without_record(
    monkeypatch,
    caplog,
    runtime_exception_reports,
):
    chunk_id = _chunk_uuid("chunk-missing")
    span_id = _span_ids(chunk_id, "The selected sentence exists.")[0]

    async def _fake_get_chunk_by_id(**kwargs):
        assert kwargs["chunk_id"] == chunk_id
        return None

    monkeypatch.setattr(record_evidence, "get_chunk_by_id", _fake_get_chunk_by_id)
    tool = record_evidence.create_record_evidence_tool("doc-123", "user-1")

    with caplog.at_level(logging.INFO, logger=record_evidence.__name__):
        result = await tool(entity="wg", span_ids=[span_id])

    assert result["status"] == "not_found"
    assert result["resolution_failure"] == "missing_chunk"
    assert result["failed_span_id"] == span_id
    assert result["chunk_id"] == chunk_id
    assert "not found in the active document" in result["failed_span_error"]
    assert "read_chunk" in result["retry_instructions"]
    assert "evidence_record_id" not in result
    assert "verified_quote" not in result
    assert not any(
        record.name == record_evidence.__name__ and record.levelno >= logging.ERROR
        for record in caplog.records
    )
    assert runtime_exception_reports == []


@pytest.mark.asyncio
async def test_record_evidence_reports_lookup_runtime_failure_at_error_level(
    monkeypatch,
    caplog,
    runtime_exception_reports,
):
    chunk_id = _chunk_uuid("chunk-runtime-failure")
    span_id = _span_ids(chunk_id, "Exact sentence.")[0]

    async def _failing_get_chunk_by_id(**_kwargs):
        raise RuntimeError("sensitive chunk text from Weaviate")

    monkeypatch.setattr(record_evidence, "get_chunk_by_id", _failing_get_chunk_by_id)
    tool = record_evidence.create_record_evidence_tool("doc-123", "user-1")

    with caplog.at_level(logging.INFO, logger=record_evidence.__name__):
        result = await tool(entity="wg", span_ids=[span_id])

    assert result["status"] == "not_found"
    assert result["resolution_failure"] == "chunk_load_error"
    assert "retry_instructions" not in result
    assert any(
        record.name == record_evidence.__name__ and record.levelno == logging.ERROR
        for record in caplog.records
    )
    assert len(runtime_exception_reports) == 1
    reported_exc, report_kwargs = runtime_exception_reports[0]
    assert isinstance(reported_exc, record_evidence._ChunkLookupRuntimeError)
    assert str(reported_exc) == "Evidence chunk lookup failed (RuntimeError)"
    assert reported_exc.__traceback__ is not None
    assert reported_exc.__context__ is None
    assert reported_exc.__cause__ is None
    assert report_kwargs == {
        "component": "record_evidence",
        "operation": "evidence_chunk_lookup_failed",
        "context": {"document_id": "doc-123"},
    }
    assert "sensitive chunk text" not in caplog.text


@pytest.mark.asyncio
async def test_record_evidence_updates_existing_id_and_preserves_attachments(monkeypatch):
    chunk_id = _chunk_uuid("chunk-update")
    chunk_text = "Old context sentence. New exact replacement sentence."
    span_ids = _span_ids(chunk_id, chunk_text)
    workspace_records = [
        {
            "evidence_record_id": "ev-active",
            "entity": "wg",
            "verified_quote": "Old exact source sentence.",
            "page": 2,
            "section": "Results",
            "chunk_id": "chunk-old",
            "document_id": "doc-123",
            "source_span_ids": ["chunk-old:s0000:c0000-c0026:aaaabbbb"],
            "source_fragments": [
                {
                    "span_id": "chunk-old:s0000:c0000-c0026:aaaabbbb",
                    "chunk_id": "chunk-old",
                    "document_id": "doc-123",
                    "text": "Old exact source sentence.",
                    "char_start": 0,
                    "char_end": 26,
                    "text_hash": "aaaabbbb",
                }
            ],
            "envelope_targets": [
                {
                    "pending_ref_id": "expression-wg",
                    "field_path": "expression_assay.used_in",
                }
            ],
            "pending_ref_id": "expression-wg",
            "field_path": "expression_assay.used_in",
            "field_paths": ["expression_assay.used_in"],
            "agent_note": "Use for assay support.",
        }
    ]

    async def _fake_get_chunk_by_id(**kwargs):
        assert kwargs["chunk_id"] == chunk_id
        return _chunk(chunk_id=chunk_id, text=chunk_text, page_number=5)

    monkeypatch.setattr(record_evidence, "get_chunk_by_id", _fake_get_chunk_by_id)
    token = evidence_workspace.set_active_evidence_records(workspace_records)
    try:
        tool = record_evidence.create_record_evidence_tool("doc-123", "user-1")
        result = await tool(
            entity="wg",
            span_ids=[span_ids[1]],
            evidence_record_id=" ev-active ",
        )
    finally:
        evidence_workspace.reset_active_evidence_records(token)

    assert result["status"] == "verified"
    assert result["evidence_record_id"] == "ev-active"
    assert result["verified_quote"] == "New exact replacement sentence."
    assert result["source_span_ids"] == [span_ids[1]]
    assert result["pending_ref_id"] == "expression-wg"
    assert result["field_paths"] == ["expression_assay.used_in"]
    assert "evidence_revision_history" not in result
    assert len(workspace_records) == 1

    updated_record = workspace_records[0]
    assert updated_record["evidence_record_id"] == "ev-active"
    assert updated_record["verified_quote"] == "New exact replacement sentence."
    assert updated_record["agent_note"] == "Use for assay support."
    assert updated_record["envelope_targets"] == [
        {
            "pending_ref_id": "expression-wg",
            "field_path": "expression_assay.used_in",
        }
    ]
    assert updated_record["updated_at"]
    assert updated_record["evidence_revision_history"] == [
        {
            "revision": 1,
            "replaced_at": updated_record["evidence_revision_history"][0]["replaced_at"],
            "previous_source": {
                "verified_quote": "Old exact source sentence.",
                "source_span_ids": ["chunk-old:s0000:c0000-c0026:aaaabbbb"],
                "source_fragments": [
                    {
                        "span_id": "chunk-old:s0000:c0000-c0026:aaaabbbb",
                        "chunk_id": "chunk-old",
                        "document_id": "doc-123",
                        "text": "Old exact source sentence.",
                        "char_start": 0,
                        "char_end": 26,
                        "text_hash": "aaaabbbb",
                    }
                ],
                "document_id": "doc-123",
                "chunk_id": "chunk-old",
                "page": 2,
                "section": "Results",
            },
        }
    ]


@pytest.mark.asyncio
async def test_record_evidence_update_rejects_object_only_retarget_without_mutation(monkeypatch):
    async def _unexpected_get_chunk_by_id(**_kwargs):
        raise AssertionError("target validation should run before span resolution")

    workspace_records = [
        {
            "evidence_record_id": "ev-active",
            "entity": "wg",
            "verified_quote": "Existing quote remains live.",
            "page": 2,
            "section": "Results",
            "chunk_id": "chunk-old",
            "document_id": "doc-123",
            "source_span_ids": ["chunk-old:s0000:c0000-c0027:aaaabbbb"],
            "envelope_targets": [
                {
                    "pending_ref_id": "expression-wg",
                    "field_path": "expression_assay.used_in",
                }
            ],
            "pending_ref_id": "expression-wg",
            "field_path": "expression_assay.used_in",
            "field_paths": ["expression_assay.used_in"],
        }
    ]
    original_records = deepcopy(workspace_records)

    monkeypatch.setattr(record_evidence, "get_chunk_by_id", _unexpected_get_chunk_by_id)
    token = evidence_workspace.set_active_evidence_records(workspace_records)
    try:
        tool = record_evidence.create_record_evidence_tool("doc-123", "user-1")
        result = await tool(
            entity="wg",
            span_ids=["chunk-new:s0000:c0000-c0001:bbbbcccc"],
            evidence_record_id="ev-active",
            pending_ref_id="expression-wg",
        )
    finally:
        evidence_workspace.reset_active_evidence_records(token)

    assert result["status"] == "forbidden"
    assert result["evidence_record_id"] == "ev-active"
    assert result["target_requires_field_path"] is True
    assert result["supplied_target"] == {"pending_ref_id": "expression-wg"}
    assert workspace_records == original_records


@pytest.mark.asyncio
async def test_record_evidence_update_failure_does_not_mutate_existing_record(monkeypatch):
    chunk_id = _chunk_uuid("chunk-update-stale")
    original_text = "The selected source sentence exists."
    stale_span_id = _span_ids(chunk_id, original_text)[0]
    existing_record = {
        "evidence_record_id": "ev-active",
        "entity": "wg",
        "verified_quote": "Existing quote remains live.",
        "page": 2,
        "section": "Results",
        "chunk_id": "chunk-old",
        "document_id": "doc-123",
        "source_span_ids": ["chunk-old:s0000:c0000-c0027:aaaabbbb"],
    }
    workspace_records = [dict(existing_record)]

    async def _fake_get_chunk_by_id(**kwargs):
        assert kwargs["chunk_id"] == chunk_id
        return _chunk(
            chunk_id=chunk_id,
            text="The selected source sentence changed.",
        )

    monkeypatch.setattr(record_evidence, "get_chunk_by_id", _fake_get_chunk_by_id)
    token = evidence_workspace.set_active_evidence_records(workspace_records)
    try:
        tool = record_evidence.create_record_evidence_tool("doc-123", "user-1")
        result = await tool(
            entity="wg",
            span_ids=[stale_span_id],
            evidence_record_id="ev-active",
        )
    finally:
        evidence_workspace.reset_active_evidence_records(token)

    assert result["status"] == "not_found"
    assert result["evidence_record_id"] == "ev-active"
    assert result["failed_span_id"] == stale_span_id
    assert workspace_records == [existing_record]


@pytest.mark.asyncio
async def test_record_evidence_update_requires_active_workspace():
    tool = record_evidence.create_record_evidence_tool("doc-123", "user-1")

    result = await tool(
        entity="wg",
        span_ids=["chunk:s0000:c0000-c0001:aaaabbbb"],
        evidence_record_id="ev-missing-workspace",
    )

    assert result == {
        "status": "forbidden",
        "entity": "wg",
        "evidence_record_id": "ev-missing-workspace",
        "message": (
            "Existing evidence updates require an active evidence workspace. "
            "The evidence record was not updated."
        ),
    }


@pytest.mark.asyncio
async def test_record_evidence_scoped_validator_tool_refuses_create_and_wrong_ids():
    workspace_records = [
        {
            "evidence_record_id": "ev-allowed",
            "entity": "wg",
            "verified_quote": "Existing quote.",
            "page": 2,
            "section": "Results",
            "chunk_id": "chunk-old",
            "document_id": "doc-123",
        }
    ]
    tool = record_evidence.create_record_evidence_tool(
        "doc-123",
        "user-1",
        workspace_records=workspace_records,
        allowed_evidence_record_ids={"ev-allowed"},
        allow_create=False,
        required_pending_ref_id="expression-wg",
        required_field_path="expression_assay.used_in",
    )

    create_attempt = await tool(entity="wg", span_ids=["chunk:s0000:c0000-c0001:aaaabbbb"])
    wrong_id = await tool(
        entity="wg",
        span_ids=["chunk:s0000:c0000-c0001:aaaabbbb"],
        evidence_record_id="ev-other",
    )
    wrong_target = await tool(
        entity="wg",
        span_ids=["chunk:s0000:c0000-c0001:aaaabbbb"],
        evidence_record_id="ev-allowed",
        pending_ref_id="other-expression",
        field_path="expression_assay.used_in",
    )
    wrong_field = await tool(
        entity="wg",
        span_ids=["chunk:s0000:c0000-c0001:aaaabbbb"],
        evidence_record_id="ev-allowed",
        pending_ref_id="expression-wg",
        field_path="other.field",
    )

    assert create_attempt["status"] == "forbidden"
    assert "provide evidence_record_id" in create_attempt["message"]
    assert wrong_id["status"] == "forbidden"
    assert wrong_id["allowed_evidence_record_ids"] == ["ev-allowed"]
    assert wrong_target["status"] == "forbidden"
    assert "another object or pending ref" in wrong_target["message"]
    assert wrong_field["status"] == "forbidden"
    assert wrong_field["target_field_path"] == "expression_assay.used_in"
    assert workspace_records[0]["verified_quote"] == "Existing quote."


@pytest.mark.asyncio
async def test_record_evidence_scoped_validator_tool_updates_allowed_record(monkeypatch):
    chunk_id = _chunk_uuid("chunk-scoped-update")
    chunk_text = "Old context sentence. Better scoped support sentence."
    span_ids = _span_ids(chunk_id, chunk_text)
    workspace_records = [
        {
            "evidence_record_id": "ev-allowed",
            "entity": "wg",
            "verified_quote": "Previous scoped quote.",
            "page": 2,
            "section": "Results",
            "chunk_id": "chunk-old",
            "document_id": "doc-123",
            "envelope_targets": [
                {
                    "pending_ref_id": "expression-wg",
                    "field_path": "expression_assay.used_in",
                }
            ],
        }
    ]

    async def _fake_get_chunk_by_id(**kwargs):
        assert kwargs["chunk_id"] == chunk_id
        return _chunk(chunk_id=chunk_id, text=chunk_text, page_number=6)

    monkeypatch.setattr(record_evidence, "get_chunk_by_id", _fake_get_chunk_by_id)
    tool = record_evidence.create_record_evidence_tool(
        "doc-123",
        "user-1",
        workspace_records=workspace_records,
        allowed_evidence_record_ids={"ev-allowed"},
        allow_create=False,
        required_pending_ref_id="expression-wg",
        required_field_path="expression_assay.used_in",
    )

    result = await tool(
        entity="wg",
        span_ids=[span_ids[1]],
        evidence_record_id="ev-allowed",
    )

    assert result["status"] == "verified"
    assert result["evidence_record_id"] == "ev-allowed"
    assert result["verified_quote"] == "Better scoped support sentence."
    assert "evidence_revision_history" not in result
    assert workspace_records[0]["verified_quote"] == "Better scoped support sentence."
    assert workspace_records[0]["evidence_revision_history"][0]["previous_source"][
        "verified_quote"
    ] == "Previous scoped quote."


@pytest.mark.asyncio
async def test_record_evidence_rejects_stale_hash_mismatched_span_without_fallback(
    monkeypatch,
    runtime_exception_reports,
):
    chunk_id = _chunk_uuid("chunk-stale")
    original_text = "The selected sentence exists. Another exact sentence."
    stale_span_id = _span_ids(chunk_id, original_text)[0]

    async def _fake_get_chunk_by_id(**kwargs):
        assert kwargs["chunk_id"] == chunk_id
        return _chunk(
            chunk_id=chunk_id,
            text="The selected sentence changed. Another exact sentence.",
        )

    monkeypatch.setattr(record_evidence, "get_chunk_by_id", _fake_get_chunk_by_id)
    tool = record_evidence.create_record_evidence_tool("doc-123", "user-1")

    result = await tool(entity="wg", span_ids=[stale_span_id])

    assert result["status"] == "not_found"
    assert result["resolution_failure"] == "stale_span_id"
    assert result["failed_span_id"] == stale_span_id
    assert "hash" in result["failed_span_error"]
    assert "Call read_chunk again" in result["failed_span_error"]
    assert "evidence_record_id" not in result
    assert runtime_exception_reports == []
    assert "verified_quote" not in result


@pytest.mark.asyncio
async def test_record_evidence_rejects_stale_offsets_without_fallback(
    monkeypatch,
    caplog,
):
    chunk_id = _chunk_uuid("chunk-stale-offsets")
    span_id = f"{chunk_id}:s0000:c0000-c9999:aaaabbbb"

    async def _fake_get_chunk_by_id(**kwargs):
        assert kwargs["chunk_id"] == chunk_id
        return _chunk(chunk_id=chunk_id, text="Exact current sentence.")

    monkeypatch.setattr(record_evidence, "get_chunk_by_id", _fake_get_chunk_by_id)
    tool = record_evidence.create_record_evidence_tool("doc-123", "user-1")

    with caplog.at_level(logging.INFO, logger=record_evidence.__name__):
        result = await tool(entity="wg", span_ids=[span_id])

    assert result["status"] == "not_found"
    assert result["resolution_failure"] == "stale_span_id"
    assert "offsets" in result["failed_span_error"]
    assert not any(
        record.name == record_evidence.__name__ and record.levelno >= logging.ERROR
        for record in caplog.records
    )


@pytest.mark.asyncio
async def test_record_evidence_multi_span_call_creates_one_conjoined_record(monkeypatch):
    chunk_id = _chunk_uuid("chunk-multispan")
    chunk_text = (
        "First exact support sentence. "
        "Second exact support sentence. "
        "A third unrelated sentence."
    )
    span_ids = _span_ids(chunk_id, chunk_text)

    async def _fake_get_chunk_by_id(**kwargs):
        assert kwargs["chunk_id"] == chunk_id
        return _chunk(chunk_id=chunk_id, text=chunk_text)

    monkeypatch.setattr(record_evidence, "get_chunk_by_id", _fake_get_chunk_by_id)
    tool = record_evidence.create_record_evidence_tool("doc-123", "user-1")

    result = await tool(entity="wg", span_ids=[span_ids[0], span_ids[1]])

    assert result["status"] == "verified"
    assert result["span_ids"] == [span_ids[0], span_ids[1]]
    assert result["source_span_ids"] == [span_ids[0], span_ids[1]]
    assert result["document_id"] == "doc-123"
    assert result["verified_quote"] == (
        "First exact support sentence.\n\nSecond exact support sentence."
    )
    assert result["chunk_ids"] == [chunk_id]
    assert len(result["source_fragments"]) == 2
    assert result["source_fragments"][0]["text"] == "First exact support sentence."
    assert result["source_fragments"][0]["document_id"] == "doc-123"
    assert result["source_fragments"][0]["text_hash"] == span_ids[0].rsplit(":", 1)[1]
    assert result["source_fragments"][1]["text"] == "Second exact support sentence."
    assert result["source_fragments"][1]["document_id"] == "doc-123"
    assert result["source_fragments"][1]["text_hash"] == span_ids[1].rsplit(":", 1)[1]
    assert result["evidence_record_id"] == build_evidence_record_id(
        evidence_record={
            "entity": "wg",
            "verified_quote": result["verified_quote"],
            "page": 3,
            "section": "Results",
            "chunk_id": chunk_id,
            "subsection": "Expression assays",
            "figure_reference": None,
            "source_span_ids": [span_ids[0], span_ids[1]],
        }
    )


@pytest.mark.asyncio
async def test_record_evidence_multi_span_failure_is_all_or_nothing(monkeypatch):
    chunk_id = _chunk_uuid("chunk-all-or-nothing")
    original_text = "First exact support sentence. Second exact support sentence."
    span_ids = _span_ids(chunk_id, original_text)

    async def _fake_get_chunk_by_id(**kwargs):
        assert kwargs["chunk_id"] == chunk_id
        return _chunk(
            chunk_id=chunk_id,
            text="First exact support sentence. Second exact support changed.",
        )

    monkeypatch.setattr(record_evidence, "get_chunk_by_id", _fake_get_chunk_by_id)
    tool = record_evidence.create_record_evidence_tool("doc-123", "user-1")

    result = await tool(entity="wg", span_ids=[span_ids[0], span_ids[1]])

    assert result["status"] == "not_found"
    assert result["failed_span_id"] == span_ids[1]
    assert result["failed_span_index"] == 1
    assert result["span_ids"] == [span_ids[0], span_ids[1]]
    assert "evidence_record_id" not in result
    assert "verified_quote" not in result


@pytest.mark.asyncio
async def test_record_evidence_prefers_pdf_provenance_page_when_chunk_page_is_stale(monkeypatch):
    chunk_id = _chunk_uuid("chunk-live-repro")
    chunk_text = "Actin 87E accumulated to a higher molar abundance in mutant fly eyes."
    span_id = _span_ids(chunk_id, chunk_text)[0]

    async def _fake_get_chunk_by_id(**_kwargs):
        return _chunk(
            chunk_id=chunk_id,
            text=chunk_text,
            page_number=1,
            section="Results and Discussion",
            subsection="2.3. The molar abundance of actins, optins, and crumbs in fly eyes",
            doc_items=[
                {"page": 6},
                {"page": 6},
            ],
        )

    monkeypatch.setattr(record_evidence, "get_chunk_by_id", _fake_get_chunk_by_id)
    tool = record_evidence.create_record_evidence_tool("doc-123", "user-1")

    result = await tool(entity="Act 87E", span_ids=[span_id])

    assert result["status"] == "verified"
    assert result["verified_quote"] == chunk_text
    assert result["page"] == 6
    assert result["source_fragments"][0]["page"] == 6
    assert result["section"] == "Results and Discussion"


@pytest.mark.asyncio
async def test_record_evidence_does_not_invent_missing_chunk_page(monkeypatch):
    chunk_id = _chunk_uuid("chunk-without-page")
    chunk_text = "Provider metadata has no usable page provenance."
    span_id = _span_ids(chunk_id, chunk_text)[0]

    async def _fake_get_chunk_by_id(**_kwargs):
        return _chunk(
            chunk_id=chunk_id,
            text=chunk_text,
            page_number=None,
        )

    monkeypatch.setattr(record_evidence, "get_chunk_by_id", _fake_get_chunk_by_id)
    tool = record_evidence.create_record_evidence_tool("doc-123", "user-1")

    result = await tool(entity="figure", span_ids=[span_id])

    assert result["status"] == "verified"
    assert "page" not in result
    assert "page" not in result["source_fragments"][0]
