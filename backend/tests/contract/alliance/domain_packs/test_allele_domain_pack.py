"""Contract tests for the Alliance allele domain pack."""

from __future__ import annotations

import copy
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from src.schemas.curation_workspace import SubmissionMode
from src.schemas.domain_envelope import CuratableObjectStatus, DefinitionState
from src.schemas.domain_pack_metadata import DomainPackFieldType

REPO_ROOT = Path(__file__).resolve().parents[5]
ALLIANCE_PYTHON_SRC = REPO_ROOT / "packages" / "alliance" / "python" / "src"
if str(ALLIANCE_PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(ALLIANCE_PYTHON_SRC))

from agr_ai_curation_alliance.domain_packs import (  # noqa: E402
    ALLIANCE_LINKML_COMMIT,
    ALLIANCE_LINKML_PROVIDER_KEY,
    load_alliance_domain_pack_registry,
)
from agr_ai_curation_alliance.domain_packs.allele import (  # noqa: E402
    ALLELE_ASSOCIATION_SUBMISSION_TARGET_KEY,
    ALLELE_DOMAIN_PACK_ID,
    AllelePaperEvidenceExportAdapter,
    VERIFIED_ALLELE_ASSOCIATION_TARGETS,
    build_allele_association_export,
    build_allele_association_submission_plan,
    build_pending_allele_envelope_from_tool_verified_fixture,
    validate_pending_allele_envelope,
)
from tests.fixtures.evidence.harness import load_evidence_fixture  # noqa: E402

from .test_alliance_domain_pack_scaffold import (  # noqa: E402
    _assert_range_exists,
    _assert_source_file_matches,
    _cache_schema,
    _iter_linkml_provider_refs,
    _load_linkml_index,
)


def _allele_pack():
    registry = load_alliance_domain_pack_registry()
    pack = registry.get_pack(ALLELE_DOMAIN_PACK_ID)
    assert pack is not None
    return pack


def _resolved_allele_association_envelope():
    fixture = load_evidence_fixture("tool_verified_allele_paper")
    envelope = build_pending_allele_envelope_from_tool_verified_fixture(fixture)

    resolved_objects = []
    for obj in envelope.objects:
        payload = dict(obj.payload)
        metadata = dict(obj.metadata)
        definition_state = obj.definition_state
        if obj.object_type == "Allele":
            payload["allele_id"] = 4749192
        elif obj.object_type == "Reference":
            payload["reference_id"] = 247320
        elif obj.object_type == "EvidenceQuote":
            payload["information_content_entity_id"] = 269867
        elif obj.object_type == "AllelePaperEvidenceAssociation":
            payload["association_id"] = 210252399
            metadata.pop("write_behavior", None)
            metadata.pop("export_behavior", None)
            definition_state = DefinitionState.STABLE
        resolved_objects.append(
            obj.model_copy(
                update={
                    "payload": payload,
                    "metadata": metadata,
                    "definition_state": definition_state,
                }
            )
        )
    return envelope.model_copy(
        update={"objects": resolved_objects, "validation_findings": []}
    )


def _stable_object_id(domain_object) -> str:
    if domain_object.object_id is not None:
        return domain_object.object_id
    if domain_object.pending_ref_id is not None:
        return domain_object.pending_ref_id
    raise AssertionError("test object is missing object_id and pending_ref_id")


def test_allele_pack_declares_object_roles_and_validator_bindings():
    metadata = _allele_pack().metadata

    roles_by_object_type = {
        object_definition.object_type: object_definition.metadata["object_role"]
        for object_definition in metadata.object_definitions
    }
    assert roles_by_object_type == {
        "AllelePaperEvidenceAssociation": "curatable_unit",
        "Allele": "validated_reference",
        "Reference": "validated_reference",
        "AlleleMention": "metadata_only",
        "EvidenceQuote": "metadata_only",
    }

    association = next(
        item
        for item in metadata.object_definitions
        if item.object_type == "AllelePaperEvidenceAssociation"
    )
    object_ref_fields = {
        field.field_path: field.object_type_ref
        for field in association.fields
        if field.field_type is DomainPackFieldType.OBJECT_REF
    }
    assert object_ref_fields == {
        "allele": "Allele",
        "reference": "Reference",
        "evidence_quote": "EvidenceQuote",
        "mention": "AlleleMention",
    }

    validator_bindings = metadata.metadata["validator_bindings"]
    under_development_bindings = {
        binding["binding_id"]: binding
        for binding in validator_bindings["under_development"]
    }
    assert set(under_development_bindings) == {"source_reference_validation"}
    reference_binding = under_development_bindings["source_reference_validation"]
    assert reference_binding["validator_agent"] == {
        "package_id": "agr.alliance",
        "agent_id": "reference_validation",
    }
    assert reference_binding["expected_result_fields"] == {
        "reference_id": "Reference.reference_id",
        "curie": "Reference.curie",
        "title": "Reference.title",
    }
    active_binding = validator_bindings["active"][0]
    assert active_binding["binding_id"] == "allele_pending_envelope_validator"
    assert active_binding["display_name"] == "Data check"
    assert active_binding["validator_agent"] == {
        "package_id": "agr.alliance",
        "agent_id": "allele_validation",
    }
    assert active_binding["applies_to"]["domain_pack_id"] == ALLELE_DOMAIN_PACK_ID
    assert active_binding["applies_to"]["object_types"] == [
        "AllelePaperEvidenceAssociation",
        "Allele",
        "Reference",
        "AlleleMention",
        "EvidenceQuote",
    ]
    assert active_binding["required"] is True
    assert active_binding["blocking"] is False
    assert active_binding["allow_opt_out"] is True
    assert active_binding["curator_override"] == {"allowed": False}
    assert active_binding["definition_state"] == "in_development"

    allele_lookup = {
        binding["binding_id"]: binding for binding in validator_bindings["active"]
    }["alliance_allele_reference_lookup"]
    assert allele_lookup["validator_agent"] == {
        "package_id": "agr.alliance",
        "agent_id": "allele_validation",
    }
    assert allele_lookup["applies_to"]["object_types"] == ["Allele"]
    assert allele_lookup["applies_to"]["field_paths"] == [
        "primary_external_id",
        "allele_symbol",
    ]
    assert allele_lookup["input_fields"] == {
        "allele_id": {
            "source": "payload",
            "path": "primary_external_id",
            "required": False,
        },
        "allele_symbol": {
            "source": "payload",
            "path": "allele_symbol",
            "required": False,
        },
    }
    assert allele_lookup["expected_result_fields"] == {
        "allele_id": "primary_external_id",
        "symbol": "allele_symbol",
    }
    assert allele_lookup["required"] is True
    assert allele_lookup["blocking"] is False
    assert allele_lookup["allow_opt_out"] is True
    assert allele_lookup["curator_override"] == {"allowed": False}


def test_allele_pack_records_grounded_metadata_and_blocks_writes():
    metadata = _allele_pack().metadata
    association_metadata = metadata.metadata["association_metadata"]

    assert association_metadata["linkml_grounding"]["allele_reference_slot"] == {
        "provider": ALLIANCE_LINKML_PROVIDER_KEY,
        "class": "Allele",
        "slot": "references",
        "range": "Reference",
        "source_file": "model/schema/core.yaml",
    }

    curation_db_grounding = association_metadata["curation_db_grounding"]
    tables = {table["table"]: table for table in curation_db_grounding["tables"]}
    assert set(tables) >= {
        "public.allele",
        "public.reference",
        "public.allele_reference",
        "public.allelegeneassociation",
        "public.allelegeneassociation_informationcontententity",
    }
    assert tables["public.allele_reference"]["verified_constraints"] == [
        "allele_reference_allele_id_fk references public.allele(id)",
        "allele_reference_references_id_fk references public.reference(id)",
    ]
    assert curation_db_grounding["verified_fixture_alleles"] == [
        {"primary_external_id": "WB:WBVar00000001"},
        {"primary_external_id": "WB:WBVar00000002"},
    ]

    write_behavior = association_metadata["write_behavior"]
    assert write_behavior["status"] == "blocked"
    assert write_behavior["verified_targets"] == [
        "public.allele_reference",
        "public.allelegeneassociation",
        "public.allelegeneassociation_informationcontententity",
    ]
    assert "update public.allele" in write_behavior["blocked_operations"]
    assert (
        "Resolve source papers to durable public.reference.id values."
        in write_behavior["required_before_write"]
    )


def test_allele_pack_linkml_class_slot_attribute_and_range_refs_exist(tmp_path: Path):
    schema_cache_dir, _env_values = _cache_schema(tmp_path)
    index = _load_linkml_index(schema_cache_dir)
    metadata = _allele_pack().metadata

    provider_refs = tuple(_iter_linkml_provider_refs(metadata))
    assert provider_refs

    for provider_ref in provider_refs:
        assert provider_ref["commit"] == ALLIANCE_LINKML_COMMIT
        assert provider_ref["schema_ref"] == "alliance.linkml"

        class_name = provider_ref.get("class")
        if class_name is not None:
            assert (
                class_name in index["classes"]
            ), f"LinkML class {class_name} is missing from pinned schema"
            actual_file, class_definition = index["classes"][class_name]
            if "slot" not in provider_ref:
                _assert_source_file_matches(
                    provider_ref=provider_ref,
                    actual_file=actual_file,
                    ref_kind="class",
                    ref_name=class_name,
                )

            attribute_name = provider_ref.get("attribute")
            if attribute_name is not None:
                attributes = class_definition.get("attributes") or {}
                assert attribute_name in attributes
                if "range" in provider_ref:
                    assert attributes[attribute_name]["range"] == provider_ref["range"]

        slot_name = provider_ref.get("slot")
        if slot_name is not None:
            assert (
                slot_name in index["slots"]
            ), f"LinkML slot {slot_name} is missing from pinned schema"
            actual_file, _definition = index["slots"][slot_name]
            _assert_source_file_matches(
                provider_ref=provider_ref,
                actual_file=actual_file,
                ref_kind="slot",
                ref_name=slot_name,
            )

        _assert_range_exists(index, provider_ref)


def test_tool_verified_allele_fixture_converts_to_pending_envelope():
    fixture = load_evidence_fixture("tool_verified_allele_paper")
    envelope = build_pending_allele_envelope_from_tool_verified_fixture(
        fixture,
        envelope_id="allele-tool-verified-envelope",
        created_at=datetime(2026, 5, 9, tzinfo=timezone.utc),
    )

    assert validate_pending_allele_envelope(envelope) == ()
    assert envelope.domain_pack_id == ALLELE_DOMAIN_PACK_ID
    assert {obj.status for obj in envelope.objects} == {CuratableObjectStatus.PENDING}

    counts = Counter(obj.object_type for obj in envelope.objects)
    assert counts == {
        "Reference": 1,
        "AlleleMention": 1,
        "Allele": 1,
        "EvidenceQuote": 2,
        "AllelePaperEvidenceAssociation": 1,
    }

    association = next(
        obj
        for obj in envelope.objects
        if obj.object_type == "AllelePaperEvidenceAssociation"
    )
    assert association.payload["allele_identifier"] == "WB:WBVar00000001"
    assert association.payload["evidence_record_ids"] == [
        "daf-2-m41-evidence-1",
        "daf-2-m41-evidence-2",
    ]
    assert association.metadata["export_behavior"]["status"] == "blocked"
    assert association.metadata["export_behavior"]["mode"] == (
        "verified_association_targets_only"
    )
    assert association.metadata["write_behavior"]["status"] == "blocked"

    allele_payloads = [
        obj.payload for obj in envelope.objects if obj.object_type == "Allele"
    ]
    assert allele_payloads == [
        {
            "primary_external_id": "WB:WBVar00000001",
            "allele_symbol": "daf-2(m41)",
            "source_mentions": ["daf-2(m41)"],
        }
    ]
    assert all(
        obj.payload.get("primary_external_id") != "WB:WBVar00000002"
        for obj in envelope.objects
    )

    finding_codes = [finding.code for finding in envelope.validation_findings]
    assert finding_codes == [
        "alliance.allele.write_blocked",
        "alliance.allele.skipped_without_verified_evidence",
    ]
    assert envelope.validation_findings[0].details["verified_targets"] == [
        "public.allele_reference",
        "public.allelegeneassociation",
        "public.allelegeneassociation_informationcontententity",
    ]
    assert envelope.validation_findings[0].details["mutates_base_rows"] == {
        "public.allele": False,
        "public.gene": False,
    }

    expected_path = (
        REPO_ROOT
        / "backend"
        / "tests"
        / "fixtures"
        / "domain_packs"
        / "allele"
        / "tool_verified_pending_envelope.yaml"
    )
    expected = yaml.safe_load(expected_path.read_text(encoding="utf-8"))
    assert (
        envelope.model_dump(
            mode="json",
            exclude_defaults=True,
            exclude_none=True,
        )
        == expected["envelope"]
    )


def test_tool_verified_allele_fixture_rejects_malformed_required_data():
    fixture = load_evidence_fixture("tool_verified_allele_paper")

    missing_extraction = copy.deepcopy(fixture)
    missing_extraction.pop("extraction")
    with pytest.raises(ValueError, match="extraction must be an object"):
        build_pending_allele_envelope_from_tool_verified_fixture(missing_extraction)

    legacy_items_only = copy.deepcopy(fixture)
    legacy_items_only["extraction"].pop("alleles")
    with pytest.raises(ValueError, match="extraction.alleles must be a list"):
        build_pending_allele_envelope_from_tool_verified_fixture(legacy_items_only)

    missing_evidence_id = copy.deepcopy(fixture)
    missing_evidence_id["tool_cases"][0]["expected_tool_result"].pop(
        "evidence_record_id"
    )
    with pytest.raises(ValueError, match="evidence_record_id"):
        build_pending_allele_envelope_from_tool_verified_fixture(missing_evidence_id)

    malformed_normalized_id = copy.deepcopy(fixture)
    malformed_normalized_id["extraction"]["alleles"][0]["normalized_id"] = 42
    with pytest.raises(ValueError, match="normalized_id must be a string"):
        build_pending_allele_envelope_from_tool_verified_fixture(
            malformed_normalized_id
        )


def test_allele_submission_plan_blocks_until_durable_targets_resolve():
    fixture = load_evidence_fixture("tool_verified_allele_paper")
    envelope = build_pending_allele_envelope_from_tool_verified_fixture(
        fixture,
        envelope_id="allele-tool-verified-envelope",
        created_at=datetime(2026, 5, 9, tzinfo=timezone.utc),
    )

    plan = build_allele_association_submission_plan(envelope)

    assert plan["status"] == "blocked"
    assert plan["target_key"] == ALLELE_ASSOCIATION_SUBMISSION_TARGET_KEY
    assert plan["verified_targets"] == VERIFIED_ALLELE_ASSOCIATION_TARGETS
    assert plan["operations"] == []
    assert plan["mutates_base_rows"] == {
        "public.allele": False,
        "public.gene": False,
    }
    blocker_codes = {blocker["code"] for blocker in plan["blockers"]}
    assert {
        "alliance.allele.definition_state_blocked",
        "alliance.allele.write_behavior_blocked",
        "alliance.allele.allele_db_id_unresolved",
        "alliance.allele.reference_id_unresolved",
        "alliance.allele.evidence_target_unresolved",
    }.issubset(blocker_codes)


def test_allele_submission_plan_blocks_unknown_write_target_loudly():
    fixture = load_evidence_fixture("tool_verified_allele_paper")
    envelope = build_pending_allele_envelope_from_tool_verified_fixture(fixture)

    plan = build_allele_association_submission_plan(
        envelope,
        target_key="public.allele",
    )

    assert plan["status"] == "blocked"
    assert plan["operations"] == []
    assert plan["blockers"] == [
        {
            "object_id": None,
            "severity": "blocker",
            "status": "blocked",
            "code": "alliance.allele.unknown_write_target",
            "message": "Allele submission target is not verified: public.allele.",
            "details": {
                "requested_target_key": "public.allele",
                "supported_target_key": ALLELE_ASSOCIATION_SUBMISSION_TARGET_KEY,
                "verified_targets": sorted(VERIFIED_ALLELE_ASSOCIATION_TARGETS),
            },
        }
    ]


def test_allele_submission_plan_emits_only_verified_non_mutating_operations_when_resolved():
    resolved_envelope = _resolved_allele_association_envelope()

    plan = build_allele_association_submission_plan(resolved_envelope)

    assert plan["status"] == "ready"
    assert plan["blockers"] == []
    assert [operation["target_table"] for operation in plan["operations"]] == [
        "public.allele_reference",
        "public.allelegeneassociation_informationcontententity",
        "public.allelegeneassociation_informationcontententity",
    ]
    assert all(
        operation["mutates_base_rows"] is False for operation in plan["operations"]
    )
    assert plan["mutates_base_rows"] == {
        "public.allele": False,
        "public.gene": False,
    }


def test_allele_export_adapter_preserves_verified_operations_from_workspace_snapshot():
    resolved_envelope = _resolved_allele_association_envelope()
    association = next(
        obj
        for obj in resolved_envelope.objects
        if obj.object_type == "AllelePaperEvidenceAssociation"
    )
    selected_object_id = _stable_object_id(association)
    referenced_keys = {ref.ref_key() for ref in association.object_refs}
    snapshot_objects = [
        obj.model_dump(mode="json")
        for obj in resolved_envelope.objects
        if _stable_object_id(obj) == selected_object_id
        or any(ref_key in referenced_keys for ref_key in obj.ref_keys())
    ]
    adapter = AllelePaperEvidenceExportAdapter()

    payload = adapter.build_submission_payload(
        mode=SubmissionMode.EXPORT,
        target_key=ALLELE_ASSOCIATION_SUBMISSION_TARGET_KEY,
        payload_context={
            "session_id": "session-1",
            "candidate_ids": ["candidate-1"],
            "candidate_count": 1,
            "domain_envelope_candidates": [{"candidate_id": "candidate-1"}],
            "domain_envelopes": [
                {
                    "envelope_id": resolved_envelope.envelope_id,
                    "domain_pack_id": resolved_envelope.domain_pack_id,
                    "domain_pack_version": resolved_envelope.domain_pack_version,
                    "status": resolved_envelope.status.value,
                    "schema_ref": (
                        resolved_envelope.schema_ref.model_dump(mode="json")
                        if resolved_envelope.schema_ref is not None
                        else None
                    ),
                    "selected_object_ids": [selected_object_id],
                    "objects": snapshot_objects,
                    "validation_findings": [],
                    "metadata": {},
                }
            ],
        },
    )

    assert payload.payload_json is not None
    plan = payload.payload_json["plans"][0]["submission_plan"]
    assert plan["status"] == "ready"
    assert plan["blockers"] == []
    assert [operation["target_table"] for operation in plan["operations"]] == [
        "public.allele_reference",
        "public.allelegeneassociation_informationcontententity",
        "public.allelegeneassociation_informationcontententity",
    ]


def test_allele_export_carries_submission_plan_and_never_base_row_mutations():
    fixture = load_evidence_fixture("tool_verified_allele_paper")
    envelope = build_pending_allele_envelope_from_tool_verified_fixture(fixture)

    export_payload = build_allele_association_export(envelope)

    assert export_payload["export_type"] == (
        "alliance_allele_paper_evidence_association"
    )
    plan = export_payload["submission_plan"]
    assert plan["status"] == "blocked"
    assert plan["operations"] == []
    assert plan["mutates_base_rows"] == {
        "public.allele": False,
        "public.gene": False,
    }
