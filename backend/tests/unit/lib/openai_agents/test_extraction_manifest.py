"""Tests for supervisor extraction result manifests."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

import json

import pytest
from src.lib.curation_workspace.extraction_results import InlineExtractionPersistenceResult
from src.lib.openai_agents import streaming_tools
from src.lib.openai_agents.extraction_manifest import (
    build_extraction_manifest_page,
    render_extraction_manifest_page,
)
from src.schemas.domain_pack_metadata import (
    DomainPackFieldDefinition,
    DomainPackFieldType,
    DomainPackMetadata,
    DomainPackObjectDefinition,
)


def _metadata() -> DomainPackMetadata:
    return DomainPackMetadata(
        pack_id="fixture.manifest",
        display_name="Fixture Manifest Pack",
        version="0.1.0",
        metadata_api_version="1.0.0",
        object_definitions=[
            DomainPackObjectDefinition(
                object_type="Assertion",
                display_name="Assertion",
                metadata={
                    "object_role": "curatable_unit",
                    "supervisor_manifest": {
                        "primary_label_field": "label",
                        "secondary_label_field": "symbol",
                        "summary_fields": ["curie", "taxon"],
                    },
                },
                fields=[
                    DomainPackFieldDefinition(
                        field_path="label",
                        field_type=DomainPackFieldType.STRING,
                        display_name="Label",
                    ),
                    DomainPackFieldDefinition(
                        field_path="symbol",
                        field_type=DomainPackFieldType.STRING,
                        display_name="Symbol",
                    ),
                    DomainPackFieldDefinition(
                        field_path="curie",
                        field_type=DomainPackFieldType.STRING,
                        display_name="Validated CURIE",
                    ),
                    DomainPackFieldDefinition(
                        field_path="taxon",
                        field_type=DomainPackFieldType.STRING,
                        display_name="Taxon",
                    ),
                ],
            ),
            DomainPackObjectDefinition(
                object_type="EvidenceQuote",
                display_name="Evidence quote",
                metadata={"object_role": "metadata_only"},
                fields=[
                    DomainPackFieldDefinition(
                        field_path="verified_quote",
                        field_type=DomainPackFieldType.STRING,
                    )
                ],
            ),
        ],
    )


@pytest.fixture(autouse=True)
def _manifest_pack(monkeypatch):
    monkeypatch.setattr(
        "src.lib.curation_workspace.adapter_registry.resolve_curation_domain_pack_by_id",
        lambda domain_pack_id: _metadata()
        if domain_pack_id == "fixture.manifest"
        else None,
    )


def _payload(object_count: int) -> dict:
    objects = []
    for index in range(object_count):
        objects.append(
            {
                "object_type": "Assertion",
                "object_role": "curatable_unit",
                "pending_ref_id": f"assertion-{index + 1}",
                "status": "validated",
                "payload": {
                    "label": f"Assertion {index + 1}",
                    "symbol": f"sym-{index + 1}",
                    "curie": f"TEST:{index + 1:04d}",
                    "taxon": "NCBITaxon:6239",
                    "verified_quote": "This quote must not leak into the manifest.",
                },
                "evidence_record_ids": [f"evidence-{index + 1}"],
            }
        )
    objects.append(
        {
            "object_type": "EvidenceQuote",
            "object_role": "metadata_only",
            "pending_ref_id": "evidence-object-1",
            "payload": {
                "verified_quote": "Hidden evidence quote.",
            },
        }
    )
    validation_findings = []
    if object_count >= 2:
        validation_findings.append(
            {
                "severity": "warning",
                "status": "open",
                "message": "Review assertion 2.",
                "object_ref": {
                    "pending_ref_id": "assertion-2",
                    "object_type": "Assertion",
                },
            }
        )
    return {
        "envelope_id": "env-manifest",
        "domain_pack_id": "fixture.manifest",
        "status": "validated",
        "extracted_objects": objects,
        "validation_findings": validation_findings,
        "metadata": {
            "evidence_records": [
                {"verified_quote": "Envelope metadata quote must not leak."}
            ]
        },
    }


def _inline_persistence_ref() -> InlineExtractionPersistenceResult:
    return cast(
        InlineExtractionPersistenceResult,
        SimpleNamespace(
            extraction_result_id="00000000-0000-4000-8000-000000000123",
            result_ref="extraction-result:00000000-0000-4000-8000-000000000123",
            created_new=False,
        ),
    )


def test_manifest_lists_all_objects_up_to_page_size_and_paginates():
    page = build_extraction_manifest_page(
        _payload(5),
        extraction_result_id="result-1",
        cursor=None,
        limit=3,
    )

    assert page["result_ref"] == "extraction-result:result-1"
    assert page["object_count"] == 5
    assert page["page"]["next_cursor"] == "3"
    assert [item["object_ref"] for item in page["objects"]] == [
        "assertion-1",
        "assertion-2",
        "assertion-3",
    ]
    assert page["objects"][1]["validation"] == {
        "total": 1,
        "error_count": 0,
        "warning_count": 1,
        "unresolved_count": 1,
    }


def test_manifest_uses_yaml_fields_and_excludes_evidence_text():
    page = build_extraction_manifest_page(
        _payload(1),
        result_ref="extraction-result:abc",
    )
    rendered = render_extraction_manifest_page(page)

    assert "Extraction result ready: fixture.manifest" in rendered
    assert "Result ref: extraction-result:abc" in rendered
    assert "Assertion assertion-1: Assertion 1" in rendered
    assert "sym-1" in rendered
    assert "Validated CURIE=TEST:0001" in rendered
    assert "NCBITaxon:6239" in rendered
    assert "evidence_count=1" in rendered
    assert "quote" not in rendered.lower()
    assert "Hidden evidence" not in rendered
    assert "Envelope metadata" not in rendered


def test_empty_manifest_has_report_empty_guidance():
    page = build_extraction_manifest_page(
        _payload(0),
        result_ref="extraction-result:empty",
    )
    rendered = render_extraction_manifest_page(page)

    assert page["result_status"] == "empty_extraction"
    assert "Objects found: 0" in rendered
    assert "Recommended supervisor action: report_empty_result" in rendered
    assert "Answer from this manifest" not in rendered


def test_supervisor_extraction_handoff_uses_manifest_visible_object_count():
    handoff = streaming_tools._build_supervisor_extraction_handoff(
        tool_name="ask_assertion_specialist",
        specialist_name="Assertion Extraction",
        payload=_payload(2),
        inline_persistence=_inline_persistence_ref(),
        adapter_key="ASSERTION",
        agent_key="assertion_extraction",
    )

    assert handoff is not None
    assert handoff.result_ref == "extraction-result:00000000-0000-4000-8000-000000000123"
    assert handoff.result_status == "non_empty_extraction_ready"
    assert handoff.object_count == 2
    assert handoff.domain_pack_id == "fixture.manifest"
    assert handoff.created_new is False


def test_supervisor_extraction_handoff_treats_metadata_only_manifest_as_empty():
    handoff = streaming_tools._build_supervisor_extraction_handoff(
        tool_name="ask_assertion_specialist",
        specialist_name="Assertion Extraction",
        payload=_payload(0),
        inline_persistence=_inline_persistence_ref(),
        adapter_key="ASSERTION",
        agent_key="assertion_extraction",
    )

    assert handoff is not None
    assert handoff.result_status == "empty_extraction"
    assert handoff.object_count == 0


@pytest.mark.parametrize("object_type,curie", [("Allele", "FIXTURE:allele-1"), ("PhenotypeTerm", "FIXTURE:phenotype-1")])
def test_completed_validation_reaches_supervisor_without_exposing_reference_objects(object_type, curie):
    payload = _payload(1)
    result = {
        "status": "resolved", "request_id": "validation-request-1",
        "validator_binding_id": "identity",
        "target": {"object_type": object_type, "object_id": "hidden-reference", "field_path": "curie", "input_values": {"quote": "PRIVATE QUOTE"}},
        "resolved_values": {"curie": curie}, "missing_expected_fields": [],
    }
    finding = {"severity": "info", "status": "resolved", "message": "Resolved", "details": {"validation_result": result}}
    payload["validation_findings"] = [finding, finding.copy()]
    page = build_extraction_manifest_page(payload)
    assert page["object_count"] == 1
    summary = page["validator_results"]
    assert summary["total"] == 1
    assert summary["status_counts"] == {"resolved": 1}
    assert summary["results"][0]["resolved_values"]["curie"] == curie
    rendered = streaming_tools._reduce_specialist_output_for_supervisor(
        json.dumps(payload), expected_output_type=None, finalized_domain_envelope=True,
    )
    assert curie in rendered
    assert object_type in rendered
    assert "PRIVATE QUOTE" not in rendered
    assert "not submission readiness" in rendered


def test_validation_summary_distinguishes_absence_and_unresolved_and_bounds_details(monkeypatch):
    payload = _payload(1)
    assert build_extraction_manifest_page(payload)["validator_results"]["status_counts"] == {}
    payload["validation_findings"] = [
        {"severity": "warning", "status": "open", "message": "Not resolved", "details": {"validation_result": {
            "status": "unresolved", "request_id": str(index), "resolved_values": {}, "missing_expected_fields": ["curie"],
        }}} for index in range(3)
    ]
    monkeypatch.setenv("SUPERVISOR_MANIFEST_PAGE_SIZE", "1")
    monkeypatch.setenv("SUPERVISOR_FIELD_TEXT_LIMIT", "20")
    summary = build_extraction_manifest_page(payload)["validator_results"]
    assert summary["total"] == 3
    assert summary["status_counts"] == {"unresolved": 3}
    assert summary["details_complete"] is False
    assert summary["results"] == [{"status": "unresolved", "open_finding": True, "details_omitted": True}]


def test_duplicate_validator_findings_keep_rejected_writeback():
    payload = _payload(1)
    result = {"status": "resolved", "request_id": "same-request", "resolved_values": {"curie": "FIXTURE:allele-1"}}
    payload["validation_findings"] = [
        {"severity": "info", "status": "resolved", "message": "Proposed identity", "details": {"validation_result": result}},
        {"severity": "blocker", "status": "open", "message": "Rejected", "code": "domain_pack.validator_materialization_invalid",
         "details": {"validation_result": result, "materialization": "rejected"}},
    ]
    page = build_extraction_manifest_page(payload)
    assert page["validator_results"]["total"] == 1
    decision = page["validator_results"]["results"][0]
    assert decision["writeback_rejected"] is True
    assert decision["open_finding"] is True
    assert page["validation"]["error_count"] == 1
    assert "must not be described as an accepted identity" in render_extraction_manifest_page(page)
