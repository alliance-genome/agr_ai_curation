"""Unit tests for Alliance allele domain-pack fixtures."""

from __future__ import annotations

import sys
from copy import deepcopy
from pathlib import Path

from src.lib.domain_packs.input_selectors import build_domain_validation_request
from src.lib.domain_packs.loader import load_domain_fixture_pack
from src.lib.domain_packs.validator_dispatch import dispatch_active_validator_bindings
from src.lib.domain_packs.validation_registry import (
    DomainPackValidationRegistry,
    ValidationBindingState,
)


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
    assert envelope.metadata["semantic_source"] == "domain_envelope.extracted_objects"
    assert LEGACY_SEMANTIC_KEYS.isdisjoint(envelope.metadata)
    assert envelope.metadata["raw_mentions"][0]["mention"] == "daf-2(m41)"
    assert envelope.metadata["exclusions"][0]["reason_code"] == "background_genotype_only"
    assert envelope.metadata["ambiguities"][0]["mention"] == "daf-2(mx)"

    association = next(
        obj for obj in envelope.extracted_objects if obj.object_type == "AllelePaperEvidenceAssociation"
    )
    assert association.object_role == "curatable_unit"
    assert "allele_identifier" not in association.payload
    assert association.evidence_record_ids == ["daf-2-m41-evidence-1"]
    assert association.metadata["write_behavior"]["status"] == "blocked"
    mention = next(obj for obj in envelope.extracted_objects if obj.object_type == "AlleleMention")
    assert mention.payload["taxon"] == {"curie": "NCBITaxon:6239"}
    assert mention.evidence_record_ids == ["daf-2-m41-evidence-1"]


def test_tool_verified_allele_fixture_builds_active_mention_validation_request():
    registry = load_alliance_domain_pack_registry()
    pack = registry.get_pack(ALLELE_DOMAIN_PACK_ID)
    assert pack is not None
    fixture_ref = registry.get_fixture_pack_ref(ALLELE_DOMAIN_PACK_ID, "tool_verified")
    assert fixture_ref is not None
    fixture_pack = load_domain_fixture_pack(pack.pack_path / fixture_ref.path)
    envelope = fixture_pack.fixtures[0].envelope
    validation_registry = DomainPackValidationRegistry.from_domain_pack(pack)

    matches = [
        match
        for match in validation_registry.match_bindings(
            envelope,
            states=[ValidationBindingState.ACTIVE],
        )
        if match.binding.binding_id == "allele_mention_reference_validation"
    ]

    assert len(matches) == 1
    selector_result = build_domain_validation_request(matches[0])
    assert selector_result.findings == ()
    assert selector_result.request is not None
    assert selector_result.selected_inputs == {
        "mention": "daf-2(m41)",
        "normalized_hint": "WB:WBVar00000001",
        "associated_gene": "daf-2",
        "taxon": "NCBITaxon:6239",
        "source_mentions": ["daf-2(m41)"],
        "evidence_quotes": [{
            "evidence_record_id": "daf-2-m41-evidence-1",
            "verified_quote": "daf-2(m41) animals formed dauer larvae at 25 C.",
            "page": 3,
            "section": "Results",
            "subsection": "Dauer phenotype",
            "chunk_id": "chunk-allele-phenotype",
            "figure_reference": "Figure 3B",
        }],
    }


def test_multiple_allele_quotes_reach_validator_without_resolving_conflicting_identity():
    registry = load_alliance_domain_pack_registry()
    pack = registry.get_pack(ALLELE_DOMAIN_PACK_ID)
    fixture_ref = registry.get_fixture_pack_ref(ALLELE_DOMAIN_PACK_ID, "tool_verified")
    envelope = load_domain_fixture_pack(pack.pack_path / fixture_ref.path).fixtures[0].envelope
    first = envelope.metadata["evidence_records"][0]
    second = {**deepcopy(first), "evidence_record_id": "conflicting-evidence",
              "verified_quote": "The allele identity was not established.",
              "chunk_id": "chunk-conflicting", "section": "Discussion"}
    envelope.metadata["evidence_records"].append(second)
    mention = next(obj for obj in envelope.extracted_objects if obj.object_type == "AlleleMention")
    mention.evidence_record_ids.append(second["evidence_record_id"])
    seen = []

    def runner(request, *, binding):
        seen.append(request)
        assert [quote["evidence_record_id"] for quote in request.selected_inputs["evidence_quotes"]] == mention.evidence_record_ids
        return {
            "request_id": request.request_id,
            "validator_binding_id": request.validator_binding_id,
            "validator_agent": request.validator_agent.model_dump(mode="json"),
            "target": request.target.model_dump(mode="json"),
            "status": "unresolved", "resolved_values": {}, "resolved_objects": [],
            "missing_expected_fields": list(request.expected_result_fields),
            "candidates": [], "lookup_attempts": [],
            "explanation": "Conflicting source context; no confirmed database identity.",
        }

    result = dispatch_active_validator_bindings(envelope, pack, runner=runner,
                                               max_parallel_validators=1)
    assert len(seen) == 1
    assert [item.status for item in result.validator_results] == ["unresolved"]
    assert result.validator_results[0].resolved_values == {}


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
    objects = list(envelope.extracted_objects)
    objects[0] = objects[0].model_copy(
        update={
            "payload": {
                **objects[0].payload,
                "notes": {"items": ["paper supplemental table label"]},
            }
        }
    )

    envelope_with_nested_payload_key = envelope.model_copy(update={"extracted_objects": objects})

    assert validate_pending_allele_envelope(envelope_with_nested_payload_key) == ()
