"""Contract tests for the allele builder-pattern migration (Phase 4).

Mirrors ``test_gene_domain_pack.py`` / ``test_phenotype_builder_domain_pack.py`` for the allele
extractor's envelope -> builder migration: the per-domain materializer
(``materialize_allele_builder_state``), RELATIVE metadata_refs, the golden pending fixture, and the
``builder_finalization`` / ``builder_run_state`` tool-binding detection flags.

POSTURE: the migration changes the EXTRACTION MECHANISM, not the curation target. The builder
materializer emits the same 4-object pending paper/evidence association graph (one shared Reference,
per-candidate AlleleMention + EvidenceQuote(s) + AllelePaperEvidenceAssociation curatable_unit) and
the same BLOCKED write/export posture the existing envelope pack declares. The extractor is
mention-only: it NEVER materializes an Allele object or an allele identifier; the active
allele_mention_reference_validation binding owns allele identity.

The pre-existing ``test_allele_domain_pack.py`` covers the envelope-pattern conversion and
export/submission adapters and is intentionally left untouched (envelope legacy stays until Phase 6).
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
import yaml

from src.lib.domain_packs.loader import load_domain_fixture_pack
from src.lib.openai_agents.extraction_builder_workspace import (
    CANDIDATE_STATUS_VALID,
    ExtractionBuilderWorkspace,
)
from src.schemas.domain_envelope import field_path_exists

REPO_ROOT = Path(__file__).resolve().parents[5]
ALLIANCE_PYTHON_SRC = REPO_ROOT / "packages" / "alliance" / "python" / "src"
if str(ALLIANCE_PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(ALLIANCE_PYTHON_SRC))

from agr_ai_curation_alliance.domain_packs import (  # noqa: E402
    load_alliance_domain_pack_registry,
)
from agr_ai_curation_alliance.domain_packs.allele import (  # noqa: E402
    ALLELE_ASSOCIATION_KIND,
    ALLELE_ASSOCIATION_OBJECT_ROLE,
    ALLELE_ASSOCIATION_OBJECT_TYPE,
    ALLELE_DOMAIN_PACK_ID,
    ALLELE_EVIDENCE_QUOTE_OBJECT_TYPE,
    ALLELE_MATERIALIZER_ID,
    ALLELE_MENTION_OBJECT_TYPE,
    ALLELE_REFERENCE_OBJECT_TYPE,
    materialize_allele_builder_state,
)
from agr_ai_curation_alliance.domain_packs.allele.conversion import (  # noqa: E402
    AlleleBuilderExtractionOutput,
    validate_allele_builder_objects,
)

ALLELE_PACK_DIR = ALLIANCE_PYTHON_SRC.parent.parent / "domain_packs" / "allele"
BUILDER_FIXTURE_PATH = ALLELE_PACK_DIR / "fixtures" / "allele_builder_pending.yaml"
BINDINGS_PATH = REPO_ROOT / "packages" / "alliance" / "tools" / "bindings.yaml"


def _staged_fields() -> dict[str, Any]:
    return {
        "domain_pack_id": ALLELE_DOMAIN_PACK_ID,
        "object_type": ALLELE_ASSOCIATION_OBJECT_TYPE,
        "pending_ref_id": "allele-mention-1",
        "mention": "unc-54(e190)",
        "source_mentions": ["unc-54(e190)"],
        "associated_gene": "unc-54",
        "taxon": "NCBITaxon:6239",
        "reference_title": "Myosin assembly in C. elegans body-wall muscle",
        "reference_filename": "unc54_paper.pdf",
    }


def _evidence_records() -> list[dict[str, Any]]:
    return [
        {
            "evidence_record_id": "evidence-unc54-1",
            "entity": "unc-54(e190)",
            "verified_quote": "unc-54(e190) animals were paralyzed and arrested at the L4 stage.",
            "page": 5,
            "section": "Results",
            "subsection": "Muscle phenotypes",
            "chunk_id": "chunk-unc54-1",
        }
    ]


def _materialize_one_candidate() -> Any:
    workspace = ExtractionBuilderWorkspace(
        run_id="allele-builder-test-run",
        domain_pack_id=ALLELE_DOMAIN_PACK_ID,
        agent_id="allele_extractor",
    )
    workspace.upsert_candidate(
        candidate_id="allele-candidate-1",
        staged_fields=_staged_fields(),
        pending_ref_ids=["allele-mention-1"],
        evidence_record_ids=["evidence-unc54-1"],
        resolver_selection_refs=[],
        status=CANDIDATE_STATUS_VALID,
    )
    return materialize_allele_builder_state(
        workspace=workspace,
        candidate_ids=["allele-candidate-1"],
        evidence_records=_evidence_records(),
        resolver_entry_lookup=None,
    )


def test_allele_pack_loads_with_builder_fixture():
    registry = load_alliance_domain_pack_registry()
    pack = registry.get_pack(ALLELE_DOMAIN_PACK_ID)
    assert pack is not None

    fixture_ref = registry.get_fixture_pack_ref(
        ALLELE_DOMAIN_PACK_ID, "allele_builder_pending"
    )
    assert fixture_ref is not None
    assert fixture_ref.path == "fixtures/allele_builder_pending.yaml"
    assert ALLELE_ASSOCIATION_OBJECT_TYPE in fixture_ref.object_types


def test_allele_builder_materializer_produces_clean_extraction_output():
    result = _materialize_one_candidate()
    assert result.ok, result.summary()
    payload = result.payload
    assert payload is not None

    objects = payload["curatable_objects"]
    by_type = {obj["object_type"] for obj in objects}
    assert by_type == {
        ALLELE_REFERENCE_OBJECT_TYPE,
        ALLELE_MENTION_OBJECT_TYPE,
        ALLELE_EVIDENCE_QUOTE_OBJECT_TYPE,
        ALLELE_ASSOCIATION_OBJECT_TYPE,
    }
    # Mention-only: the extractor never emits an Allele object.
    assert "Allele" not in by_type

    association = next(
        obj for obj in objects if obj["object_type"] == ALLELE_ASSOCIATION_OBJECT_TYPE
    )
    assert association["object_role"] == ALLELE_ASSOCIATION_OBJECT_ROLE
    assert association["pending_ref_id"] == "allele-paper-evidence-association-1"
    assert association["payload"]["association_kind"] == ALLELE_ASSOCIATION_KIND
    assert "allele_identifier" not in association["payload"]
    assert association["evidence_record_ids"] == ["evidence-unc54-1"]
    assert association["payload"]["evidence_record_ids"] == ["evidence-unc54-1"]
    # Existing-pack posture preserved: write/export remain blocked (the write_blocked BLOCKER is a
    # domain finding surfaced downstream, NOT a structural code).
    assert association["metadata"]["write_behavior"]["status"] == "blocked"
    assert association["metadata"]["export_behavior"]["status"] == "blocked"

    mention = next(
        obj for obj in objects if obj["object_type"] == ALLELE_MENTION_OBJECT_TYPE
    )
    # Exact paper notation preserved as the validator-binding selector anchor.
    assert mention["payload"]["mention"]["text"] == "unc-54(e190)"
    assert mention["payload"]["associated_gene"]["symbol"] == "unc-54"
    assert mention["payload"]["taxon"]["curie"] == "NCBITaxon:6239"

    assert payload["metadata"]["provenance"]["source"] == ALLELE_MATERIALIZER_ID
    # No resolver/helper machinery: the allele validator owns identity.
    assert "helper_selections" not in payload["metadata"]["provenance"]
    assert result.evidence_record_ids == ("evidence-unc54-1",)


def test_allele_builder_shares_one_reference_across_candidates():
    workspace = ExtractionBuilderWorkspace(
        run_id="allele-builder-two-candidate",
        domain_pack_id=ALLELE_DOMAIN_PACK_ID,
        agent_id="allele_extractor",
    )
    workspace.upsert_candidate(
        candidate_id="allele-candidate-1",
        staged_fields=_staged_fields(),
        pending_ref_ids=["allele-mention-1"],
        evidence_record_ids=["evidence-unc54-1"],
        resolver_selection_refs=[],
        status=CANDIDATE_STATUS_VALID,
    )
    second = {
        **_staged_fields(),
        "pending_ref_id": "allele-mention-2",
        "mention": "daf-2(m41)",
        "source_mentions": ["daf-2(m41)"],
        "associated_gene": "daf-2",
    }
    workspace.upsert_candidate(
        candidate_id="allele-candidate-2",
        staged_fields=second,
        pending_ref_ids=["allele-mention-2"],
        evidence_record_ids=["evidence-daf2-1"],
        resolver_selection_refs=[],
        status=CANDIDATE_STATUS_VALID,
    )
    evidence = _evidence_records() + [
        {
            "evidence_record_id": "evidence-daf2-1",
            "entity": "daf-2(m41)",
            "verified_quote": "Sequencing identified a G-to-A substitution in daf-2(m41).",
            "page": 7,
            "section": "Results",
            "subsection": "Molecular lesions",
            "chunk_id": "chunk-daf2-1",
        }
    ]
    result = materialize_allele_builder_state(
        workspace=workspace,
        candidate_ids=["allele-candidate-1", "allele-candidate-2"],
        evidence_records=evidence,
        resolver_entry_lookup=None,
    )
    assert result.ok, result.summary()
    objects = result.payload["curatable_objects"]
    references = [o for o in objects if o["object_type"] == ALLELE_REFERENCE_OBJECT_TYPE]
    associations = [o for o in objects if o["object_type"] == ALLELE_ASSOCIATION_OBJECT_TYPE]
    # Exactly one shared Reference; two associations both reference it.
    assert len(references) == 1
    assert len(associations) == 2
    shared_ref_id = references[0]["pending_ref_id"]
    for association in associations:
        ref_ids = {
            ref["pending_ref_id"]
            for ref in association["object_refs"]
            if ref["object_type"] == ALLELE_REFERENCE_OBJECT_TYPE
        }
        assert ref_ids == {shared_ref_id}


def test_allele_builder_metadata_refs_are_relative_and_resolve():
    result = _materialize_one_candidate()
    payload = result.payload
    assert payload is not None
    association = next(
        obj
        for obj in payload["curatable_objects"]
        if obj["object_type"] == ALLELE_ASSOCIATION_OBJECT_TYPE
    )

    metadata_paths = {ref["metadata_path"] for ref in association["metadata_refs"]}
    assert metadata_paths == {"raw_mentions[0]", "evidence_records[0]"}
    metadata_root = payload["metadata"]
    for ref in association["metadata_refs"]:
        assert not ref["metadata_path"].startswith("extraction_metadata")
        assert field_path_exists(metadata_root, ref["metadata_path"])


def test_allele_builder_output_validates_against_object_contract():
    result = _materialize_one_candidate()
    assert result.payload is not None
    output = AlleleBuilderExtractionOutput.model_validate(result.payload)
    assert validate_allele_builder_objects(output) == ()


def test_allele_builder_rejects_evidence_record_not_in_metadata():
    workspace = ExtractionBuilderWorkspace(
        run_id="allele-builder-bad-evidence",
        domain_pack_id=ALLELE_DOMAIN_PACK_ID,
        agent_id="allele_extractor",
    )
    workspace.upsert_candidate(
        candidate_id="allele-candidate-1",
        staged_fields=_staged_fields(),
        pending_ref_ids=["allele-mention-1"],
        evidence_record_ids=["evidence-MISSING"],
        resolver_selection_refs=[],
        status=CANDIDATE_STATUS_VALID,
    )
    result = materialize_allele_builder_state(
        workspace=workspace,
        candidate_ids=["allele-candidate-1"],
        evidence_records=_evidence_records(),
        resolver_entry_lookup=None,
    )
    assert not result.ok
    assert any(
        issue["reason"] == "unknown_evidence_record_id" for issue in result.issues
    )


def test_allele_builder_rejects_missing_mention():
    staged = _staged_fields()
    staged["mention"] = "   "
    workspace = ExtractionBuilderWorkspace(
        run_id="allele-builder-no-mention",
        domain_pack_id=ALLELE_DOMAIN_PACK_ID,
        agent_id="allele_extractor",
    )
    workspace.upsert_candidate(
        candidate_id="allele-candidate-1",
        staged_fields=staged,
        pending_ref_ids=["allele-mention-1"],
        evidence_record_ids=["evidence-unc54-1"],
        resolver_selection_refs=[],
        status=CANDIDATE_STATUS_VALID,
    )
    result = materialize_allele_builder_state(
        workspace=workspace,
        candidate_ids=["allele-candidate-1"],
        evidence_records=_evidence_records(),
        resolver_entry_lookup=None,
    )
    assert not result.ok
    assert any(
        issue["reason"] in {"missing_allele_mention", "no_retained_candidates"}
        for issue in result.issues
    )


def test_allele_builder_golden_fixture_loads_with_relative_refs():
    fixture_pack = load_domain_fixture_pack(BUILDER_FIXTURE_PATH)
    envelope = fixture_pack.fixtures[0].envelope
    assert envelope.domain_pack_id == ALLELE_DOMAIN_PACK_ID

    association = next(
        obj
        for obj in envelope.objects
        if obj.object_type == ALLELE_ASSOCIATION_OBJECT_TYPE
    )
    assert association.pending_ref_id == "allele-paper-evidence-association-1"

    extraction_metadata = envelope.metadata.get("extraction_metadata")
    assert isinstance(extraction_metadata, Mapping)
    for obj in envelope.objects:
        for ref in obj.metadata_refs:
            assert not ref.metadata_path.startswith("extraction_metadata")
            assert field_path_exists(extraction_metadata, ref.metadata_path)


def test_finalize_allele_extraction_tool_is_marked_builder_finalization():
    bindings = yaml.safe_load(BINDINGS_PATH.read_text(encoding="utf-8"))
    by_id = {
        entry["tool_id"]: entry
        for entry in bindings["tools"]
        if isinstance(entry, Mapping) and "tool_id" in entry
    }
    finalize = by_id["finalize_allele_extraction"]
    assert finalize["metadata"]["builder_finalization"] is True
    assert finalize["metadata"]["builder_run_state"] is True
    assert finalize["callable"] == (
        "agr_ai_curation_alliance.tools.allele_builder_tools:finalize_allele_extraction"
    )
    for tool_id in (
        "stage_allele_observation",
        "patch_allele_observation",
        "discard_allele_observation",
        "list_staged_allele_observations",
    ):
        assert by_id[tool_id]["metadata"]["builder_run_state"] is True


def test_allele_extractor_agent_has_no_output_schema_and_builder_tools():
    agent_path = (
        REPO_ROOT / "packages" / "alliance" / "agents" / "allele_extractor" / "agent.yaml"
    )
    agent = yaml.safe_load(agent_path.read_text(encoding="utf-8"))
    assert agent["output_schema"] is None
    tools = set(agent["tools"])
    assert "stage_allele_observation" in tools
    assert "finalize_allele_extraction" in tools
    assert "AlleleExtractionResultEnvelope" not in str(agent.get("output_schema"))
