"""Alliance generic builder tool tests."""

from __future__ import annotations

from typing import Any

import pytest

from agr_ai_curation_alliance.domain_packs.generic import GENERIC_DOMAIN_PACK_ID
from agr_ai_curation_alliance.tools import generic_builder_tools
from src.lib.openai_agents import extraction_builder_workspace as builder


@pytest.fixture
def active_generic_builder_context(monkeypatch):
    events: list[dict[str, Any]] = []
    monkeypatch.setattr(
        generic_builder_tools,
        "write_extraction_trace_event",
        lambda **event: events.append(event) or event,
    )
    monkeypatch.setattr(
        builder,
        "write_extraction_trace_event",
        lambda **event: events.append(event) or event,
    )
    workspace = builder.ExtractionBuilderWorkspace(
        run_id="trace-generic",
        document_id="doc-1",
        domain_pack_id=GENERIC_DOMAIN_PACK_ID,
        agent_id="pdf_extraction",
    )
    builder_token = builder.set_active_extraction_builder_workspace(workspace)
    try:
        yield workspace, events
    finally:
        builder.reset_active_extraction_builder_workspace(builder_token)


def test_generic_object_stage_reports_attributes_and_soft_key_notice(
    active_generic_builder_context,
):
    workspace, _events = active_generic_builder_context

    first = generic_builder_tools._stage_generic_object_impl(
        class_key="generic:generic_object",
        label="B cell lymphoma",
        evidence_record_ids=["evidence-1"],
        classification_notes=["The paper reports this tumor classification."],
        pending_ref_id="generic-object-1",
        semantic_class="tumor_classification_occurrence",
        attributes={
            "Cell Type": "B cell",
            "Tumor Classification Term": "lymphoma",
            "Species": "Mouse",
        },
    )

    assert first.status == "ok"
    assert first.data["attribute_keys"] == [
        "cell_type",
        "tumor_classification_term",
        "species",
    ]
    assert first.data["notices"] == []
    assert workspace.candidates["generic-candidate-1"].staged_fields["attributes"] == {
        "cell_type": "B cell",
        "tumor_classification_term": "lymphoma",
        "species": "Mouse",
    }

    second = generic_builder_tools._stage_generic_object_impl(
        class_key="generic:generic_object",
        label="T cell lymphoma",
        evidence_record_ids=["evidence-2"],
        classification_notes=["The paper reports another tumor classification."],
        pending_ref_id="generic-object-2",
        semantic_class="tumor_classification_occurrence",
        attributes={
            "Cell Type": "T cell",
            "Tumor Classification Term": "lymphoma",
            "Section": "Results",
        },
    )

    assert second.status == "ok"
    assert second.data["notices"] == [
        {
            "code": "generic_attribute_key_drift",
            "severity": "info",
            "candidate_id": "generic-candidate-2",
            "semantic_class": "tumor_classification_occurrence",
            "missing_keys": ["species"],
            "additional_keys": ["section"],
            "comparison_candidate_ids": ["generic-candidate-1"],
            "message": (
                "Attribute keys differ from comparable staged generic objects. "
                "This can be intentional for mixed object shapes; review only if "
                "these objects are meant to represent the same kind of thing."
            ),
        }
    ]

    listed = generic_builder_tools._list_staged_generic_objects_impl(
        include_discarded=False
    )
    assert listed.status == "ok"
    assert [
        candidate.get("attribute_keys") for candidate in listed.data["candidates"]
    ] == [
        ["cell_type", "tumor_classification_term", "species"],
        ["cell_type", "tumor_classification_term", "section"],
    ]
    assert [candidate.get("semantic_class") for candidate in listed.data["candidates"]] == [
        "tumor_classification_occurrence",
        "tumor_classification_occurrence",
    ]
    assert listed.data["candidates"][1]["attribute_key_notices"][0]["missing_keys"] == [
        "species"
    ]
    assert listed.data["candidates"][1]["attribute_key_notices"][0]["additional_keys"] == [
        "section"
    ]

    found = generic_builder_tools._find_staged_generic_objects_impl(
        field_value_contains="T cell"
    )
    assert found.status == "ok"
    assert found.data["matched_candidate_count"] == 1
    assert found.data["candidates"][0]["candidate_id"] == "generic-candidate-2"
    assert found.data["candidates"][0]["attribute_keys"] == [
        "cell_type",
        "tumor_classification_term",
        "section",
    ]
    assert found.data["candidates"][0]["attribute_key_notices"][0]["comparison_candidate_ids"] == [
        "generic-candidate-1"
    ]

    patched = generic_builder_tools._patch_generic_object_impl(
        candidate_id="generic-candidate-2",
        updates=[
            {"field_path": "attributes.Section", "value": None},
            {"field_path": "attributes.Species", "value": "Mouse"},
        ],
    )
    assert patched.status == "ok"
    relisted = generic_builder_tools._list_staged_generic_objects_impl(
        include_discarded=False
    )
    assert relisted.data["candidates"][1]["attribute_keys"] == [
        "cell_type",
        "tumor_classification_term",
        "species",
    ]
    assert relisted.data["candidates"][1]["attribute_key_notices"] == []
    assert (
        workspace.candidates["generic-candidate-2"].status
        == builder.CANDIDATE_STATUS_VALID
    )


@pytest.mark.parametrize(
    ("attributes", "reason"),
    [
        (
            {"Cell Type": "B cell", "cell-type": "duplicate"},
            "duplicate_normalized_attribute_key",
        ),
        ({"payload.term": "lymphoma"}, "attribute_key_path_separator"),
        ({"object_label": "lymphoma"}, "reserved_attribute_key"),
        ({"structured_row": {"cell_type": "B cell"}}, "invalid_attribute_value"),
        ({"rows": [{"cell_type": "B cell"}]}, "invalid_attribute_value"),
    ],
)
def test_generic_object_stage_rejects_invalid_attributes(
    active_generic_builder_context,
    attributes,
    reason,
):
    result = generic_builder_tools._stage_generic_object_impl(
        class_key="generic:generic_object",
        label="Invalid attribute object",
        evidence_record_ids=["evidence-1"],
        classification_notes=["The paper reports this object."],
        pending_ref_id="generic-object-1",
        semantic_class="tumor_classification_occurrence",
        attributes=attributes,
    )

    assert result.status == "error"
    assert result.data["validation_issues"][0]["reason"] == reason


def test_generic_claim_stage_rejects_attributes(active_generic_builder_context):
    result = generic_builder_tools._stage_generic_object_impl(
        class_key="generic:generic_claim",
        label="Narrative claim",
        evidence_record_ids=["evidence-1"],
        classification_notes=["The paper states a narrative claim."],
        pending_ref_id="generic-claim-1",
        payload={"claim_text": "The paper reports lymphoma incidence."},
        attributes={"cell_type": "B cell"},
    )

    assert result.status == "error"
    assert result.data["validation_issues"][0]["reason"] == (
        "attributes_not_supported_for_class"
    )


def test_generic_claim_patch_rejects_attributes(active_generic_builder_context):
    workspace, _events = active_generic_builder_context
    workspace.upsert_candidate(
        candidate_id="generic-candidate-1",
        staged_fields={
            "domain_pack_id": GENERIC_DOMAIN_PACK_ID,
            "object_type": "generic_claim",
            "class_key": "generic:generic_claim",
            "label": "Narrative claim",
            "classification_notes": ["The paper states a narrative claim."],
            "payload": {"claim_text": "The paper reports lymphoma incidence."},
        },
        pending_ref_ids=["generic-claim-1"],
        evidence_record_ids=["evidence-1"],
        resolver_selection_refs=[],
        status=builder.CANDIDATE_STATUS_VALID,
    )

    result = generic_builder_tools._patch_generic_object_impl(
        candidate_id="generic-candidate-1",
        updates=[{"field_path": "attributes.cell_type", "value": "B cell"}],
    )

    assert result.status == "error"
    assert result.data["validation_issues"][0]["reason"] == (
        "attributes_not_supported_for_class"
    )
