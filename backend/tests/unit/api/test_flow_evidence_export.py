"""Unit tests for flow evidence export service and endpoint behavior."""

import asyncio
import csv
import importlib
import io
import json
import sys
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException

from src.lib.flows import evidence_export
from src.schemas.curation_workspace import (
    CurationExtractionResultRecord,
    CurationExtractionSourceKind,
)


sys.modules.setdefault(
    "rapidfuzz",
    SimpleNamespace(
        fuzz=SimpleNamespace(
            partial_ratio_alignment=lambda *_args, **_kwargs: SimpleNamespace(
                dest_start=0,
                dest_end=0,
                score=0.0,
            )
        )
    ),
)

flows = importlib.import_module("src.api.flows")


def _evidence_record(
    *,
    evidence_record_id: str,
    entity: str,
    quote: str,
    page: int,
    section: str,
    chunk_id: str,
    subsection: str | None = None,
    figure_reference: str | None = None,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "evidence_record_id": evidence_record_id,
        "entity": entity,
        "verified_quote": quote,
        "page": page,
        "section": section,
        "chunk_id": chunk_id,
    }
    if subsection:
        payload["subsection"] = subsection
    if figure_reference:
        payload["figure_reference"] = figure_reference
    return payload


def _extraction_result(
    *,
    extraction_result_id: str,
    flow_run_id: str = "flow-run-123",
    user_id: str | None = "user-123",
    evidence_records: list[dict[str, object]] | None = None,
    agent_key: str = "gene_specialist",
    agent_name: str = "Gene Specialist",
    tool_name: str = "ask_gene_specialist",
    step: int | None = 1,
    flow_name: str = "Flow Evidence",
) -> CurationExtractionResultRecord:
    metadata: dict[str, object] = {
        "agent_name": agent_name,
        "flow_name": flow_name,
        "tool_name": tool_name,
    }
    if step is not None:
        metadata["step"] = step

    return CurationExtractionResultRecord.model_validate(
        {
            "extraction_result_id": extraction_result_id,
            "document_id": str(uuid4()),
            "adapter_key": "reference",
            "agent_key": agent_key,
            "source_kind": CurationExtractionSourceKind.FLOW,
            "origin_session_id": "session-123",
            "trace_id": "trace-123",
            "flow_run_id": flow_run_id,
            "user_id": user_id,
            "candidate_count": 1,
            "conversation_summary": "summary",
            "payload_json": {
                "items": [
                    {
                        "label": "entity",
                        "evidence_record_ids": [
                            record["evidence_record_id"] for record in (evidence_records or [])
                        ],
                    }
                ],
                "evidence_records": evidence_records or [],
                "run_summary": {"kept_count": 1},
            },
            "created_at": datetime.now(timezone.utc),
            "metadata": metadata,
        }
    )


def test_build_flow_evidence_export_artifact_dedupes_and_formats_csv():
    shared_record = _evidence_record(
        evidence_record_id="evidence-shared",
        entity="act-5c",
        quote="Shared evidence quote.",
        page=3,
        section="Results",
        subsection="Expression",
        chunk_id="chunk-1",
    )
    unique_record = _evidence_record(
        evidence_record_id="evidence-unique",
        entity="unc-54",
        quote="Unique evidence quote.",
        page=5,
        section="Discussion",
        chunk_id="chunk-2",
        figure_reference="Figure 2",
    )

    artifact = evidence_export.build_flow_evidence_export_artifact(
        flow_run_id="flow-run-123",
        extraction_results=[
            _extraction_result(
                extraction_result_id="result-1",
                evidence_records=[shared_record],
                agent_key="pdf_extraction",
                agent_name="PDF Specialist",
                tool_name="ask_pdf_extraction_specialist",
                step=1,
            ),
            _extraction_result(
                extraction_result_id="result-2",
                evidence_records=[shared_record, unique_record],
                agent_key="gene_specialist",
                agent_name="Gene Specialist",
                tool_name="ask_gene_specialist",
                step=2,
            ),
        ],
        export_format=evidence_export.FlowEvidenceExportFormat.CSV,
    )

    assert artifact.content_type == "text/csv"
    assert artifact.filename == "flow-flow-run-123-evidence.csv"
    assert artifact.record_count == 2

    rows = list(csv.DictReader(io.StringIO(artifact.payload_text)))
    assert rows == [
        {
            "entity": "act-5c",
            "verified_quote": "Shared evidence quote.",
            "page": "3",
            "section": "Results",
            "subsection": "Expression",
            "chunk_id": "chunk-1",
            "figure_reference": "",
            "agent_id": "pdf_extraction",
            "step_number": "1",
            "evidence_record_id": "evidence-shared",
        },
        {
            "entity": "act-5c",
            "verified_quote": "Shared evidence quote.",
            "page": "3",
            "section": "Results",
            "subsection": "Expression",
            "chunk_id": "chunk-1",
            "figure_reference": "",
            "agent_id": "gene_specialist",
            "step_number": "2",
            "evidence_record_id": "evidence-shared",
        },
        {
            "entity": "unc-54",
            "verified_quote": "Unique evidence quote.",
            "page": "5",
            "section": "Discussion",
            "subsection": "",
            "chunk_id": "chunk-2",
            "figure_reference": "Figure 2",
            "agent_id": "gene_specialist",
            "step_number": "2",
            "evidence_record_id": "evidence-unique",
        },
    ]


@pytest.mark.parametrize(
    ("export_format", "expected_content_type", "expected_delimiter"),
    [
        (evidence_export.FlowEvidenceExportFormat.TSV, "text/tab-separated-values", "\t"),
        (evidence_export.FlowEvidenceExportFormat.JSON, "application/json", None),
    ],
)
def test_build_flow_evidence_export_artifact_supports_tsv_and_json(
    export_format,
    expected_content_type,
    expected_delimiter,
):
    record = _evidence_record(
        evidence_record_id="evidence-a",
        entity="pax-6",
        quote="Evidence quote.",
        page=7,
        section="Methods",
        chunk_id="chunk-7",
    )

    artifact = evidence_export.build_flow_evidence_export_artifact(
        flow_run_id="flow-run-123",
        extraction_results=[_extraction_result(extraction_result_id="result-1", evidence_records=[record])],
        export_format=export_format,
    )

    assert artifact.content_type == expected_content_type
    assert artifact.record_count == 1

    if expected_delimiter is not None:
        assert expected_delimiter in artifact.payload_text.splitlines()[0]
        assert "pax-6" in artifact.payload_text
        return

    payload = json.loads(artifact.payload_text)
    assert payload == {
        "flow_name": "Flow Evidence",
        "flow_run_id": "flow-run-123",
        "steps": [
            {
                "agent_id": "gene_specialist",
                "agent_name": "Gene Specialist",
                "evidence_count": 1,
                "evidence_records": [
                    {
                        "chunk_id": "chunk-7",
                        "entity": "pax-6",
                        "evidence_record_id": "evidence-a",
                        "page": 7,
                        "section": "Methods",
                        "verified_quote": "Evidence quote.",
                    }
                ],
                "step": 1,
                "tool_name": "ask_gene_specialist",
            }
        ],
        "total_evidence_records": 1,
    }


def test_build_flow_evidence_export_artifact_rejects_empty_flow_run_id_in_filename():
    record = _extraction_result(extraction_result_id="result-1")

    with pytest.raises(ValueError, match="Flow run identifier cannot be empty"):
        evidence_export.build_flow_evidence_export_artifact(
            flow_run_id="   ",
            extraction_results=[record],
            export_format=evidence_export.FlowEvidenceExportFormat.CSV,
        )


def test_build_flow_evidence_export_artifact_ignores_non_step_results_from_mixed_flow_runs():
    first_step_record = _evidence_record(
        evidence_record_id="evidence-step-1",
        entity="act-5c",
        quote="Step one evidence quote.",
        page=3,
        section="Results",
        chunk_id="chunk-1",
    )
    ignored_prep_record = _evidence_record(
        evidence_record_id="evidence-curation-prep",
        entity="egl-1",
        quote="Prep-only evidence quote.",
        page=9,
        section="Appendix",
        chunk_id="chunk-prep",
    )
    second_step_record = _evidence_record(
        evidence_record_id="evidence-step-2",
        entity="unc-54",
        quote="Step two evidence quote.",
        page=5,
        section="Discussion",
        chunk_id="chunk-2",
    )

    artifact = evidence_export.build_flow_evidence_export_artifact(
        flow_run_id="flow-run-123",
        extraction_results=[
            _extraction_result(
                extraction_result_id="result-1",
                evidence_records=[first_step_record],
                step=1,
            ),
            _extraction_result(
                extraction_result_id="result-curation-prep",
                agent_key="curation_prep",
                agent_name="Curation Prep",
                tool_name="ask_curation_prep_specialist",
                evidence_records=[ignored_prep_record],
                step=None,
            ),
            _extraction_result(
                extraction_result_id="result-2",
                evidence_records=[second_step_record],
                step=2,
            ),
        ],
        export_format=evidence_export.FlowEvidenceExportFormat.CSV,
    )

    assert artifact.record_count == 2

    rows = list(csv.DictReader(io.StringIO(artifact.payload_text)))
    assert rows == [
        {
            "entity": "act-5c",
            "verified_quote": "Step one evidence quote.",
            "page": "3",
            "section": "Results",
            "subsection": "",
            "chunk_id": "chunk-1",
            "figure_reference": "",
            "agent_id": "gene_specialist",
            "step_number": "1",
            "evidence_record_id": "evidence-step-1",
        },
        {
            "entity": "unc-54",
            "verified_quote": "Step two evidence quote.",
            "page": "5",
            "section": "Discussion",
            "subsection": "",
            "chunk_id": "chunk-2",
            "figure_reference": "",
            "agent_id": "gene_specialist",
            "step_number": "2",
            "evidence_record_id": "evidence-step-2",
        },
    ]


@pytest.mark.parametrize(
    ("overrides", "expected_message"),
    [
        ({"agent_key": "", "metadata": {"agent_name": "Gene Specialist", "flow_name": "Flow Evidence", "step": 1, "tool_name": "ask_gene_specialist"}}, "missing required agent_key metadata"),
        ({"metadata": {"agent_name": "Gene Specialist", "step": 1, "tool_name": "ask_gene_specialist"}}, "missing required flow_name metadata"),
    ],
)
def test_build_flow_evidence_export_artifact_rejects_missing_required_metadata(
    overrides,
    expected_message,
):
    payload = _extraction_result(extraction_result_id="result-1").model_dump()
    payload.update(overrides)
    record = CurationExtractionResultRecord.model_validate(payload)

    with pytest.raises(evidence_export.FlowRunEvidenceExportDataError, match=expected_message):
        evidence_export.build_flow_evidence_export_artifact(
            flow_run_id="flow-run-123",
            extraction_results=[record],
            export_format=evidence_export.FlowEvidenceExportFormat.JSON,
        )


def test_resolve_authorized_flow_run_extraction_results_enforces_ownership(monkeypatch):
    owned_record = _extraction_result(extraction_result_id="result-1", user_id="user-123")

    monkeypatch.setattr(
        evidence_export,
        "list_extraction_results",
        lambda **_kwargs: [owned_record],
    )

    resolved = evidence_export.resolve_authorized_flow_run_extraction_results(
        db=object(),
        flow_run_id="flow-run-123",
        user_id="user-123",
    )
    assert resolved == [owned_record]


@pytest.mark.parametrize(
    ("records", "expected_exception"),
    [
        ([], evidence_export.FlowRunEvidenceExportNotFoundError),
        ([_extraction_result(extraction_result_id="result-1", user_id="other-user")], evidence_export.FlowRunEvidenceExportPermissionError),
        ([_extraction_result(extraction_result_id="result-1", user_id=None)], evidence_export.FlowRunEvidenceExportPermissionError),
    ],
)
def test_resolve_authorized_flow_run_extraction_results_rejects_missing_and_unauthorized_records(
    monkeypatch,
    records,
    expected_exception,
):
    monkeypatch.setattr(
        evidence_export,
        "list_extraction_results",
        lambda **_kwargs: records,
    )

    with pytest.raises(expected_exception):
        evidence_export.resolve_authorized_flow_run_extraction_results(
            db=object(),
            flow_run_id="flow-run-123",
            user_id="user-123",
        )


@pytest.mark.asyncio
async def test_export_flow_evidence_route_returns_attachment_response(monkeypatch):
    record = _extraction_result(extraction_result_id="result-1")

    monkeypatch.setattr(
        flows,
        "resolve_authorized_flow_run_extraction_results",
        lambda **_kwargs: [record],
    )

    response = await flows.export_flow_evidence(
        flow_run_id="flow-run-123",
        export_format=evidence_export.FlowEvidenceExportFormat.CSV,
        user={"sub": "user-123"},
        db=object(),
    )

    assert response.status_code == 200
    assert response.media_type == "text/csv"
    assert response.headers["content-disposition"] == (
        'attachment; filename="flow-flow-run-123-evidence.csv"'
    )
    assert b"entity,verified_quote,page,section,subsection,chunk_id,figure_reference,agent_id,step_number,evidence_record_id" in response.body


@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (evidence_export.FlowRunEvidenceExportNotFoundError("missing"), 404),
        (evidence_export.FlowRunEvidenceExportPermissionError("forbidden"), 403),
        (evidence_export.FlowRunEvidenceExportDataError("bad data"), 500),
    ],
)
def test_export_flow_evidence_route_maps_service_errors(monkeypatch, error, expected_status):
    if isinstance(error, evidence_export.FlowRunEvidenceExportDataError):
        monkeypatch.setattr(
            flows,
            "resolve_authorized_flow_run_extraction_results",
            lambda **_kwargs: [_extraction_result(extraction_result_id="result-1")],
        )
        monkeypatch.setattr(
            flows,
            "build_flow_evidence_export_artifact",
            lambda **_kwargs: (_ for _ in ()).throw(error),
        )
    else:
        monkeypatch.setattr(
            flows,
            "resolve_authorized_flow_run_extraction_results",
            lambda **_kwargs: (_ for _ in ()).throw(error),
        )

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            flows.export_flow_evidence(
                flow_run_id="flow-run-123",
                export_format=evidence_export.FlowEvidenceExportFormat.JSON,
                user={"sub": "user-123"},
                db=object(),
            )
        )

    assert exc.value.status_code == expected_status
