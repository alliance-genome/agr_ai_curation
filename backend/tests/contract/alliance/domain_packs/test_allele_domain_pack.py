"""Contract tests for the Alliance allele domain pack."""

from __future__ import annotations

import copy
import importlib
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import pytest
import yaml

from src.schemas.domain_envelope import CuratableObjectStatus
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
    ALLELE_DOMAIN_PACK_ID,
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
    assert validator_bindings == [
        {
            "binding_id": "allele_pending_envelope_validator",
            "validator": (
                "agr_ai_curation_alliance.domain_packs.allele."
                "validate_pending_allele_envelope"
            ),
            "applies_to": {
                "domain_pack_id": ALLELE_DOMAIN_PACK_ID,
                "object_types": [
                    "AllelePaperEvidenceAssociation",
                    "Allele",
                    "Reference",
                    "AlleleMention",
                    "EvidenceQuote",
                ],
            },
            "definition_state": "in_development",
        }
    ]
    module_name, _, function_name = validator_bindings[0]["validator"].rpartition(".")
    assert callable(getattr(importlib.import_module(module_name), function_name))


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
    assert "update public.allele" in write_behavior["blocked_operations"]
    assert "Resolve source papers to durable public.reference.id values." in write_behavior[
        "required_before_write"
    ]


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
            assert class_name in index["classes"], (
                f"LinkML class {class_name} is missing from pinned schema"
            )
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
            assert slot_name in index["slots"], (
                f"LinkML slot {slot_name} is missing from pinned schema"
            )
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
        obj for obj in envelope.objects if obj.object_type == "AllelePaperEvidenceAssociation"
    )
    assert association.payload["allele_identifier"] == "WB:WBVar00000001"
    assert association.payload["evidence_record_ids"] == [
        "daf-2-m41-evidence-1",
        "daf-2-m41-evidence-2",
    ]
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
    assert envelope.model_dump(
        mode="json",
        exclude_defaults=True,
        exclude_none=True,
    ) == expected["envelope"]


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
    missing_evidence_id["tool_cases"][0]["expected_tool_result"].pop("evidence_record_id")
    with pytest.raises(ValueError, match="evidence_record_id"):
        build_pending_allele_envelope_from_tool_verified_fixture(missing_evidence_id)
