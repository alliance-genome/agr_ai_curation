"""Tests for supervisor extraction result manifests."""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast

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
