"""Unit tests for Alliance allele domain-pack fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

from src.lib.domain_packs.loader import load_domain_fixture_pack


REPO_ROOT = Path(__file__).resolve().parents[5]
ALLIANCE_PYTHON_SRC = REPO_ROOT / "packages" / "alliance" / "python" / "src"
if str(ALLIANCE_PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(ALLIANCE_PYTHON_SRC))

from agr_ai_curation_alliance.domain_packs import load_alliance_domain_pack_registry  # noqa: E402
from agr_ai_curation_alliance.domain_packs.allele import (  # noqa: E402
    ALLELE_DOMAIN_PACK_ID,
    validate_pending_allele_envelope,
)


LEGACY_SEMANTIC_KEYS = {
    "items",
    "annotations",
    "genes",
    "alleles",
    "diseases",
    "chemicals",
    "phenotypes",
}


def test_allele_domain_pack_loads_tool_verified_pending_fixture():
    registry = load_alliance_domain_pack_registry()
    pack = registry.get_pack(ALLELE_DOMAIN_PACK_ID)
    assert pack is not None

    fixture_ref = registry.get_fixture_pack_ref(ALLELE_DOMAIN_PACK_ID, "tool_verified")
    assert fixture_ref is not None
    fixture_pack = load_domain_fixture_pack(pack.pack_path / fixture_ref.path)
    fixture = fixture_pack.fixtures[0]
    envelope = fixture.envelope

    assert validate_pending_allele_envelope(envelope) == ()
    assert envelope.metadata["semantic_source"] == "domain_envelope.objects"
    assert LEGACY_SEMANTIC_KEYS.isdisjoint(envelope.metadata)
    assert envelope.metadata["raw_mentions"][0]["mention"] == "daf-2(m41)"
    assert envelope.metadata["exclusions"][0]["reason_code"] == "background_genotype_only"
    assert envelope.metadata["ambiguities"][0]["mention"] == "daf-2(mx)"

    association = next(
        obj for obj in envelope.objects if obj.object_type == "AllelePaperEvidenceAssociation"
    )
    assert association.object_role == "curatable_unit"
    assert association.payload["allele_identifier"] == "WB:WBVar00000001"
    assert association.evidence_record_ids == ["daf-2-m41-evidence-1"]
    assert association.metadata["write_behavior"]["status"] == "blocked"


def test_allele_domain_pack_validator_rejects_legacy_semantic_keys():
    registry = load_alliance_domain_pack_registry()
    pack = registry.get_pack(ALLELE_DOMAIN_PACK_ID)
    assert pack is not None
    fixture_ref = registry.get_fixture_pack_ref(ALLELE_DOMAIN_PACK_ID, "tool_verified")
    assert fixture_ref is not None
    fixture_pack = load_domain_fixture_pack(pack.pack_path / fixture_ref.path)
    envelope = fixture_pack.fixtures[0].envelope

    envelope_with_legacy_key = envelope.model_copy(
        update={"metadata": {**envelope.metadata, "alleles": []}}
    )

    findings = validate_pending_allele_envelope(envelope_with_legacy_key)

    assert [finding.code for finding in findings] == [
        "alliance.allele.legacy_semantic_store_present"
    ]


def test_allele_domain_pack_validator_allows_nested_payload_keys_named_like_legacy_lists():
    registry = load_alliance_domain_pack_registry()
    pack = registry.get_pack(ALLELE_DOMAIN_PACK_ID)
    assert pack is not None
    fixture_ref = registry.get_fixture_pack_ref(ALLELE_DOMAIN_PACK_ID, "tool_verified")
    assert fixture_ref is not None
    fixture_pack = load_domain_fixture_pack(pack.pack_path / fixture_ref.path)
    envelope = fixture_pack.fixtures[0].envelope
    objects = list(envelope.objects)
    objects[0] = objects[0].model_copy(
        update={
            "payload": {
                **objects[0].payload,
                "notes": {"items": ["paper supplemental table label"]},
            }
        }
    )

    envelope_with_nested_payload_key = envelope.model_copy(update={"objects": objects})

    assert validate_pending_allele_envelope(envelope_with_nested_payload_key) == ()
