"""Tests for the persisted extraction result inspector."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from src.lib.openai_agents import inspect_results as inspect_results_module
from src.schemas.curation_workspace import CurationExtractionSourceKind
from src.schemas.domain_pack_metadata import (
    DomainPackFieldDefinition,
    DomainPackFieldType,
    DomainPackMetadata,
    DomainPackObjectDefinition,
)


RESULT_ID = "11111111-1111-1111-1111-111111111111"
DOCUMENT_ID = "22222222-2222-2222-2222-222222222222"


class _InspectRecord:
    def __init__(self, **overrides):
        payload = {
            "extraction_result_id": RESULT_ID,
            "document_id": DOCUMENT_ID,
            "adapter_key": "fixture.inspect",
            "agent_key": "fixture_agent",
            "source_kind": CurationExtractionSourceKind.CHAT,
            "origin_session_id": "session-1",
            "trace_id": "trace-1",
            "flow_run_id": None,
            "user_id": "user-1",
            "candidate_count": 1,
            "conversation_summary": "Fixture extraction result.",
            "payload_json": _payload(),
            "created_at": datetime(2026, 6, 13, 12, 0, tzinfo=timezone.utc),
        }
        payload.update(overrides)
        for key, value in payload.items():
            setattr(self, key, value)


def _metadata() -> DomainPackMetadata:
    return DomainPackMetadata(
        pack_id="fixture.inspect",
        display_name="Fixture Inspect Pack",
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
                    DomainPackFieldDefinition(
                        field_path="verified_quote",
                        field_type=DomainPackFieldType.STRING,
                    ),
                ],
            )
        ],
    )


def _payload() -> dict:
    return {
        "envelope_id": "env-inspect",
        "domain_pack_id": "fixture.inspect",
        "status": "validated",
        "extracted_objects": [
            {
                "object_type": "Assertion",
                "object_role": "curatable_unit",
                "pending_ref_id": "assertion-1",
                "status": "validated",
                "payload": {
                    "label": "APOE association",
                    "symbol": "APOE",
                    "curie": "TEST:0001",
                    "taxon": "NCBITaxon:9606",
                    "verified_quote": "This evidence quote must not appear in manifests.",
                },
                "evidence_record_ids": ["evidence-1"],
            }
        ],
        "validation_findings": [
            {
                "severity": "warning",
                "status": "open",
                "message": "Review the mapped identifier.",
                "field_ref": {
                    "object_ref": {
                        "pending_ref_id": "assertion-1",
                        "object_type": "Assertion",
                    },
                    "field_path": "curie",
                },
            }
        ],
        "metadata": {
            "evidence_records": [
                {
                    "evidence_record_id": "evidence-1",
                    "verified_quote": "The paper states APOE is associated with disease.",
                    "page": 4,
                    "section": "Results",
                }
            ]
        },
    }


def _payload_with_objects(object_count: int) -> dict:
    payload = _payload()
    payload["extracted_objects"] = [
        {
            "object_type": "Assertion",
            "object_role": "curatable_unit",
            "pending_ref_id": f"assertion-{index + 1}",
            "status": "validated",
            "payload": {
                "label": f"Assertion {index + 1}",
                "symbol": f"SYM{index + 1}",
                "curie": f"TEST:{index + 1:04d}",
                "taxon": "NCBITaxon:9606",
                "verified_quote": "Hidden quote.",
            },
            "evidence_record_ids": [f"evidence-{index + 1}"],
        }
        for index in range(object_count)
    ]
    payload["validation_findings"] = []
    payload["metadata"] = {"evidence_records": []}
    return payload


@pytest.fixture(autouse=True)
def _patch_context(monkeypatch):
    monkeypatch.setattr(inspect_results_module, "get_current_session_id", lambda: "session-1")
    monkeypatch.setattr(inspect_results_module, "get_current_user_id", lambda: "user-1")
    monkeypatch.setattr(
        inspect_results_module.document_state,
        "get_document",
        lambda _user_id: {"id": DOCUMENT_ID},
    )
    monkeypatch.setattr(
        "src.lib.curation_workspace.adapter_registry.resolve_curation_domain_pack_by_id",
        lambda domain_pack_id: _metadata()
        if domain_pack_id == "fixture.inspect"
        else None,
    )


def _patch_records(monkeypatch, records):
    monkeypatch.setattr(
        inspect_results_module,
        "list_extraction_results",
        lambda **_kwargs: list(records),
    )


@pytest.mark.asyncio
async def test_inspect_results_help_uses_canonical_result_refs_only():
    payload = json.loads(await inspect_results_module.inspect_results(action="help"))

    assert payload["status"] == "ok"
    assert payload["result_ref_format"] == "extraction-result:<uuid>"
    assert "current-turn" not in json.dumps(payload)
    assert "current_chat" not in json.dumps(payload)


@pytest.mark.asyncio
async def test_inspect_results_summary_uses_manifest_without_evidence_text(monkeypatch):
    _patch_records(monkeypatch, [_InspectRecord()])

    response = await inspect_results_module.inspect_results(action="summary")

    payload = json.loads(response)
    serialized_manifest = json.dumps(payload["manifest"])
    assert payload["status"] == "ok"
    assert payload["result_ref"] == f"extraction-result:{RESULT_ID}"
    assert payload["manifest"]["objects"][0]["display_label"] == "APOE association"
    assert payload["manifest"]["objects"][0]["fields"] == [
        {"path": "curie", "label": "Validated CURIE", "value": "TEST:0001"},
        {"path": "taxon", "label": "Taxon", "value": "NCBITaxon:9606"},
    ]
    assert "This evidence quote must not appear" not in serialized_manifest
    assert "paper states APOE" not in serialized_manifest


@pytest.mark.asyncio
async def test_inspect_results_rejects_raw_uuid_and_transient_refs(monkeypatch):
    def _unexpected_list(**_kwargs):
        raise AssertionError("result lookup should not run for invalid refs")

    monkeypatch.setattr(inspect_results_module, "list_extraction_results", _unexpected_list)

    raw_uuid = json.loads(
        await inspect_results_module.inspect_results(
            action="summary",
            result_ref=RESULT_ID,
        )
    )
    transient = json.loads(
        await inspect_results_module.inspect_results(
            action="summary",
            result_ref="current-turn:1",
        )
    )

    assert raw_uuid["error_code"] == "raw_uuid_result_ref"
    assert transient["error_code"] == "invalid_result_ref"


@pytest.mark.asyncio
async def test_inspect_results_result_ref_defaults_to_current_chat(monkeypatch):
    cross_session_record = _InspectRecord(origin_session_id="other-session")

    def _list_records(**kwargs):
        if kwargs == {"user_id": "user-1"}:
            return [cross_session_record]
        return []

    monkeypatch.setattr(inspect_results_module, "list_extraction_results", _list_records)

    default_lookup = json.loads(
        await inspect_results_module.inspect_results(
            action="summary",
            result_ref=f"extraction-result:{RESULT_ID}",
        )
    )
    explicit_all_authorized = json.loads(
        await inspect_results_module.inspect_results(
            action="summary",
            target="all_authorized",
            result_ref=f"extraction-result:{RESULT_ID}",
        )
    )

    assert default_lookup["status"] == "no_context"
    assert explicit_all_authorized["status"] == "ok"


@pytest.mark.asyncio
async def test_inspect_results_flow_run_target_requires_flow_run_id(monkeypatch):
    def _unexpected_list(**_kwargs):
        raise AssertionError("flow_run lookup should not run without flow_run_id")

    monkeypatch.setattr(inspect_results_module, "list_extraction_results", _unexpected_list)

    payload = json.loads(
        await inspect_results_module.inspect_results(
            action="list",
            target="flow_run",
        )
    )

    assert payload["error_code"] == "flow_run_required"


@pytest.mark.asyncio
async def test_inspect_results_field_requires_yaml_manifest_field(monkeypatch):
    _patch_records(monkeypatch, [_InspectRecord()])

    visible = json.loads(
        await inspect_results_module.inspect_results(
            action="field",
            result_ref=f"extraction-result:{RESULT_ID}",
            object_ref="assertion-1",
            field_path="curie",
        )
    )
    evidence_path = json.loads(
        await inspect_results_module.inspect_results(
            action="field",
            result_ref=f"extraction-result:{RESULT_ID}",
            object_ref="assertion-1",
            field_path="verified_quote",
        )
    )
    hidden = json.loads(
        await inspect_results_module.inspect_results(
            action="field",
            result_ref=f"extraction-result:{RESULT_ID}",
            object_ref="assertion-1",
            field_path="not_declared",
        )
    )

    assert visible["status"] == "ok"
    assert visible["value"] == "TEST:0001"
    assert evidence_path["error_code"] == "evidence_path_requires_evidence_action"
    assert hidden["error_code"] == "field_not_supervisor_visible"


@pytest.mark.asyncio
async def test_inspect_results_objects_preserves_manifest_pages_above_list_limit(monkeypatch):
    _patch_records(
        monkeypatch,
        [_InspectRecord(payload_json=_payload_with_objects(25))],
    )

    response = await inspect_results_module.inspect_results(
        action="objects",
        result_ref=f"extraction-result:{RESULT_ID}",
        limit=25,
    )

    payload = json.loads(response)
    assert payload["status"] == "ok"
    assert len(payload["objects"]) == 25
    assert payload["next_cursor"] is None
    assert "truncated_count" not in json.dumps(payload["objects"])
    assert "Hidden quote" not in json.dumps(payload["objects"])


@pytest.mark.asyncio
async def test_inspect_results_evidence_inventory_hides_text_until_object_ref(monkeypatch):
    _patch_records(monkeypatch, [_InspectRecord()])

    inventory = json.loads(
        await inspect_results_module.inspect_results(
            action="evidence",
            result_ref=f"extraction-result:{RESULT_ID}",
        )
    )
    evidence = json.loads(
        await inspect_results_module.inspect_results(
            action="evidence",
            result_ref=f"extraction-result:{RESULT_ID}",
            object_ref="assertion-1",
        )
    )

    assert inventory["status"] == "ok"
    assert inventory["evidence_inventory"] == [
        {
            "object_ref": "assertion-1",
            "object_type": "Assertion",
            "status": "validated",
            "evidence_count": 1,
        }
    ]
    assert "paper states APOE" not in json.dumps(inventory)
    assert evidence["status"] == "ok"
    assert evidence["evidence"] == [
        {
            "evidence_record_id": "evidence-1",
            "page": "4",
            "section": "Results",
            "verified_quote": "The paper states APOE is associated with disease.",
        }
    ]


def _payload_with_mixed_findings() -> dict:
    payload = _payload()
    payload["validation_findings"] = [
        {
            "severity": "warning",
            "status": "open",
            "message": "Review the mapped identifier.",
            "field_ref": {
                "object_ref": {
                    "pending_ref_id": "assertion-1",
                    "object_type": "Assertion",
                },
                "field_path": "curie",
            },
        },
        {
            "severity": "error",
            "status": "open",
            "message": "Identifier did not resolve.",
            "object_ref": {
                "pending_ref_id": "assertion-1",
                "object_type": "Assertion",
            },
        },
        {
            "severity": "blocker",
            "status": "open",
            "message": "Required field is missing.",
            "object_ref": {
                "pending_ref_id": "assertion-1",
                "object_type": "Assertion",
            },
        },
        {
            "severity": "info",
            "status": "resolved",
            "message": "Identifier resolved cleanly.",
            "object_ref": {
                "pending_ref_id": "assertion-1",
                "object_type": "Assertion",
            },
        },
    ]
    return payload


@pytest.mark.asyncio
async def test_inspect_results_list_splits_validation_warning_and_error_counts(monkeypatch):
    _patch_records(
        monkeypatch,
        [_InspectRecord(payload_json=_payload_with_mixed_findings())],
    )

    response = await inspect_results_module.inspect_results(action="list")

    payload = json.loads(response)
    assert payload["status"] == "ok"
    assert len(payload["results"]) == 1
    summary = payload["results"][0]
    # warning severity -> warnings; error + blocker -> errors; info is ignored.
    assert summary["validation_warning_count"] == 1
    assert summary["validation_error_count"] == 2
    # The old single combined count is no longer emitted.
    assert "validation_finding_count" not in summary


@pytest.mark.asyncio
async def test_inspect_results_validation_filters_by_object_and_field(monkeypatch):
    _patch_records(monkeypatch, [_InspectRecord()])

    response = await inspect_results_module.inspect_results(
        action="validation",
        result_ref=f"extraction-result:{RESULT_ID}",
        object_ref="assertion-1",
        field_path="curie",
    )

    payload = json.loads(response)
    assert payload["status"] == "ok"
    assert payload["finding_count"] == 1
    assert payload["validation_findings"][0]["field_path"] == "curie"
    assert payload["validation_findings"][0]["message"] == "Review the mapped identifier."
