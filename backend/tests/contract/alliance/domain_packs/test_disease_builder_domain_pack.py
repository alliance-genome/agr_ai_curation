"""Contract tests for the disease builder-pattern migration (Phase 2, FULL LinkML alignment).

Mirrors ``test_phenotype_builder_domain_pack.py`` for the disease extractor's envelope -> builder
migration, but asserts the FULL-alignment posture (NOT preserve-existing-posture):

  * D1: the per-domain materializer (``materialize_disease_builder_state``) emits the CONCRETE
    GeneDiseaseAnnotation / AlleleDiseaseAnnotation / AGMDiseaseAnnotation subtype selected by the
    staged subject kind (abstract DiseaseAnnotation only on unknown subject).
  * D2: the subject is staged and carried in ``disease_annotation_subject``.
  * D3: ECO ``evidence_code_curies[]`` are staged and snapshotted.
  * D5: the relation rides on the concrete object payload.
  * RELATIVE metadata_refs, the golden fixture, and the ``builder_finalization`` /
    ``builder_run_state`` tool-binding detection flags.

The pre-existing ``test_disease_domain_pack.py`` covers the envelope-pattern conversion and is
intentionally left untouched (envelope legacy stays until Phase 6).
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

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
from agr_ai_curation_alliance.domain_packs.disease import (  # noqa: E402
    DISEASE_DOMAIN_PACK_ID,
    DISEASE_MATERIALIZER_ID,
    DISEASE_MODEL_ID,
    materialize_disease_builder_state,
)
from agr_ai_curation_alliance.domain_packs.disease.builder_conversion import (  # noqa: E402
    DiseaseBuilderExtractionOutput,
    validate_disease_builder_objects,
)
from agr_ai_curation_alliance.domain_packs.disease.constants import (  # noqa: E402
    DISEASE_AGM_OBJECT_TYPE,
    DISEASE_ALLELE_OBJECT_TYPE,
    DISEASE_ANNOTATION_OBJECT_ROLE,
    DISEASE_EVIDENCE_QUOTE_OBJECT_TYPE,
    DISEASE_GENE_OBJECT_TYPE,
    DISEASE_OBJECT_TYPE,
    DISEASE_REFERENCE_OBJECT_TYPE,
    DISEASE_SUBJECT_OBJECT_TYPE,
    DISEASE_TERM_OBJECT_TYPE,
)

DISEASE_PACK_DIR = ALLIANCE_PYTHON_SRC.parent.parent / "domain_packs" / "disease"
BUILDER_FIXTURE_PATH = DISEASE_PACK_DIR / "fixtures" / "alzheimers_builder_pending.yaml"
BINDINGS_PATH = REPO_ROOT / "packages" / "alliance" / "tools" / "bindings.yaml"


def _staged_fields(subject_type: str = "gene", subject_identifier: str = "FB:FBgn0000108") -> dict[str, Any]:
    return {
        "domain_pack_id": DISEASE_DOMAIN_PACK_ID,
        "object_type": DISEASE_OBJECT_TYPE,
        "pending_ref_id": "disease-annotation-1",
        "mention": "Alzheimer's disease",
        "disease_name": "Alzheimer's disease",
        "disease_curie": "DOID:10652",
        "role": "model_context",
        "confidence": "high",
        "data_provider": "FB",
        "subject_type": subject_type,
        "subject_identifier": subject_identifier,
        "subject_label": "Appl",
        "disease_relation_name": "is_implicated_in",
        "evidence_code_curies": ["ECO:0000315"],
        "source_mentions": ["a transgenic Drosophila model of Alzheimer's disease"],
        "negated": False,
    }


def _evidence_records() -> list[dict[str, Any]]:
    return [
        {
            "evidence_record_id": "evidence-ad-1",
            "entity": "Appl",
            "verified_quote": (
                "Over-expression of human APP and BACE in this transgenic Drosophila line "
                "recapitulated key features of Alzheimer's disease."
            ),
            "page": 4,
            "section": "Results",
            "subsection": "Alzheimer's model",
            "chunk_id": "chunk-ad-1",
        }
    ]


def _materialize_one_candidate(
    *, subject_type: str = "gene", subject_identifier: str = "FB:FBgn0000108"
) -> Any:
    workspace = ExtractionBuilderWorkspace(
        run_id="disease-builder-test-run",
        domain_pack_id=DISEASE_DOMAIN_PACK_ID,
        agent_id="disease_extractor",
    )
    workspace.upsert_candidate(
        candidate_id="disease-candidate-1",
        staged_fields=_staged_fields(subject_type=subject_type, subject_identifier=subject_identifier),
        pending_ref_ids=["disease-annotation-1"],
        evidence_record_ids=["evidence-ad-1"],
        resolver_selection_refs=[],
        status=CANDIDATE_STATUS_VALID,
    )
    return materialize_disease_builder_state(
        workspace=workspace,
        candidate_ids=["disease-candidate-1"],
        evidence_records=_evidence_records(),
        resolver_entry_lookup=None,
    )


def test_disease_pack_loads_with_builder_fixture():
    registry = load_alliance_domain_pack_registry()
    pack = registry.get_pack(DISEASE_DOMAIN_PACK_ID)
    assert pack is not None

    fixture_ref = registry.get_fixture_pack_ref(
        DISEASE_DOMAIN_PACK_ID, "alzheimers_builder_pending"
    )
    assert fixture_ref is not None
    assert fixture_ref.path == "fixtures/alzheimers_builder_pending.yaml"
    assert DISEASE_GENE_OBJECT_TYPE in fixture_ref.object_types


def test_disease_builder_materializes_concrete_gene_subtype():
    result = _materialize_one_candidate(subject_type="gene")
    assert result.ok, result.summary()
    payload = result.payload
    assert payload is not None

    objects = payload["curatable_objects"]
    by_type = {obj["object_type"] for obj in objects}
    # D1: the curatable_unit is the CONCRETE GeneDiseaseAnnotation, NOT the abstract DiseaseAnnotation.
    assert DISEASE_GENE_OBJECT_TYPE in by_type
    assert DISEASE_OBJECT_TYPE not in by_type
    assert {
        DISEASE_SUBJECT_OBJECT_TYPE,
        DISEASE_TERM_OBJECT_TYPE,
        DISEASE_REFERENCE_OBJECT_TYPE,
        DISEASE_EVIDENCE_QUOTE_OBJECT_TYPE,
    } <= by_type

    annotation = next(
        obj for obj in objects if obj["object_type"] == DISEASE_GENE_OBJECT_TYPE
    )
    assert annotation["object_role"] == DISEASE_ANNOTATION_OBJECT_ROLE
    assert annotation["model_ref"] == DISEASE_MODEL_ID
    assert annotation["pending_ref_id"] == "disease-annotation-1"
    assert annotation["evidence_record_ids"] == ["evidence-ad-1"]
    payload_obj = annotation["payload"]
    assert payload_obj["mention"] == "Alzheimer's disease"
    # D2: subject carried inline.
    assert payload_obj["disease_annotation_subject"]["subject_identifier"] == "FB:FBgn0000108"
    assert payload_obj["disease_annotation_subject"]["subject_type"] == "gene"
    # DOID staged.
    assert payload_obj["disease_annotation_object"]["curie"] == "DOID:10652"
    assert payload_obj["disease_annotation_object"]["name"] == "Alzheimer's disease"
    # D3: ECO codes staged + snapshotted.
    assert payload_obj["evidence_code_curies"] == ["ECO:0000315"]
    # D5: relation rides on the concrete object.
    assert payload_obj["disease_relation_name"] == "is_implicated_in"
    assert payload_obj["data_provider"]["abbreviation"] == "FB"
    # FULL alignment: NO blocked write/export posture on the concrete annotation metadata.
    assert "write_behavior" not in annotation["metadata"]
    assert "export_behavior" not in annotation["metadata"]
    assert payload["metadata"]["provenance"]["source"] == DISEASE_MATERIALIZER_ID
    assert result.evidence_record_ids == ("evidence-ad-1",)


def test_disease_builder_materializes_allele_and_agm_subtypes():
    allele_result = _materialize_one_candidate(
        subject_type="allele", subject_identifier="FB:FBal0000001"
    )
    assert allele_result.ok, allele_result.summary()
    allele_types = {obj["object_type"] for obj in allele_result.payload["curatable_objects"]}
    assert DISEASE_ALLELE_OBJECT_TYPE in allele_types
    assert DISEASE_GENE_OBJECT_TYPE not in allele_types

    agm_result = _materialize_one_candidate(
        subject_type="agm", subject_identifier="FB:FBst0000001"
    )
    assert agm_result.ok, agm_result.summary()
    agm_types = {obj["object_type"] for obj in agm_result.payload["curatable_objects"]}
    assert DISEASE_AGM_OBJECT_TYPE in agm_types
    assert DISEASE_GENE_OBJECT_TYPE not in agm_types


def test_disease_builder_unknown_subject_falls_back_to_abstract():
    # A genuinely unresolved subject yields the abstract DiseaseAnnotation -> validator_unresolved
    # (non-structural), NOT a structural failure.
    workspace = ExtractionBuilderWorkspace(
        run_id="disease-builder-no-subject",
        domain_pack_id=DISEASE_DOMAIN_PACK_ID,
        agent_id="disease_extractor",
    )
    staged = _staged_fields()
    staged.pop("subject_type")
    staged.pop("subject_identifier")
    workspace.upsert_candidate(
        candidate_id="disease-candidate-1",
        staged_fields=staged,
        pending_ref_ids=["disease-annotation-1"],
        evidence_record_ids=["evidence-ad-1"],
        resolver_selection_refs=[],
        status=CANDIDATE_STATUS_VALID,
    )
    result = materialize_disease_builder_state(
        workspace=workspace,
        candidate_ids=["disease-candidate-1"],
        evidence_records=_evidence_records(),
        resolver_entry_lookup=None,
    )
    assert result.ok, result.summary()
    by_type = {obj["object_type"] for obj in result.payload["curatable_objects"]}
    assert DISEASE_OBJECT_TYPE in by_type
    assert DISEASE_GENE_OBJECT_TYPE not in by_type


def test_disease_builder_metadata_refs_are_relative_and_resolve():
    result = _materialize_one_candidate()
    payload = result.payload
    assert payload is not None
    annotation = next(
        obj
        for obj in payload["curatable_objects"]
        if obj["object_type"] == DISEASE_GENE_OBJECT_TYPE
    )

    metadata_paths = {ref["metadata_path"] for ref in annotation["metadata_refs"]}
    assert metadata_paths == {"raw_mentions[0]", "evidence_records[0]"}
    metadata_root = payload["metadata"]
    for ref in annotation["metadata_refs"]:
        assert not ref["metadata_path"].startswith("extraction_metadata")
        assert field_path_exists(metadata_root, ref["metadata_path"])


def test_disease_builder_output_validates_against_object_contract():
    result = _materialize_one_candidate()
    assert result.payload is not None
    output = DiseaseBuilderExtractionOutput.model_validate(result.payload)
    assert validate_disease_builder_objects(output) == ()


def test_disease_builder_rejects_evidence_record_not_in_metadata():
    workspace = ExtractionBuilderWorkspace(
        run_id="disease-builder-bad-evidence",
        domain_pack_id=DISEASE_DOMAIN_PACK_ID,
        agent_id="disease_extractor",
    )
    workspace.upsert_candidate(
        candidate_id="disease-candidate-1",
        staged_fields=_staged_fields(),
        pending_ref_ids=["disease-annotation-1"],
        evidence_record_ids=["evidence-MISSING"],
        resolver_selection_refs=[],
        status=CANDIDATE_STATUS_VALID,
    )
    result = materialize_disease_builder_state(
        workspace=workspace,
        candidate_ids=["disease-candidate-1"],
        evidence_records=_evidence_records(),
        resolver_entry_lookup=None,
    )
    assert not result.ok
    assert any(
        issue["reason"] == "unknown_evidence_record_id" for issue in result.issues
    )


def test_disease_builder_rejects_missing_mention():
    staged = _staged_fields()
    staged["mention"] = "   "
    workspace = ExtractionBuilderWorkspace(
        run_id="disease-builder-no-mention",
        domain_pack_id=DISEASE_DOMAIN_PACK_ID,
        agent_id="disease_extractor",
    )
    workspace.upsert_candidate(
        candidate_id="disease-candidate-1",
        staged_fields=staged,
        pending_ref_ids=["disease-annotation-1"],
        evidence_record_ids=["evidence-ad-1"],
        resolver_selection_refs=[],
        status=CANDIDATE_STATUS_VALID,
    )
    result = materialize_disease_builder_state(
        workspace=workspace,
        candidate_ids=["disease-candidate-1"],
        evidence_records=_evidence_records(),
        resolver_entry_lookup=None,
    )
    assert not result.ok
    assert any(
        issue["reason"] in {"missing_disease_mention", "no_retained_candidates"}
        for issue in result.issues
    )


def test_disease_builder_golden_fixture_loads_with_relative_refs():
    fixture_pack = load_domain_fixture_pack(BUILDER_FIXTURE_PATH)
    envelope = fixture_pack.fixtures[0].envelope
    assert envelope.domain_pack_id == DISEASE_DOMAIN_PACK_ID

    annotation = next(
        obj for obj in envelope.objects if obj.object_type == DISEASE_GENE_OBJECT_TYPE
    )
    assert annotation.pending_ref_id == "disease-annotation-1"

    extraction_metadata = envelope.metadata.get("extraction_metadata")
    assert isinstance(extraction_metadata, Mapping)
    for obj in envelope.objects:
        for ref in obj.metadata_refs:
            assert not ref.metadata_path.startswith("extraction_metadata")
            assert field_path_exists(extraction_metadata, ref.metadata_path)


def test_disease_subject_and_evidence_code_bindings_are_active():
    registry = load_alliance_domain_pack_registry()
    pack = registry.get_pack(DISEASE_DOMAIN_PACK_ID)
    assert pack is not None
    metadata = yaml.safe_load(
        (DISEASE_PACK_DIR / "domain_pack.yaml").read_text(encoding="utf-8")
    )
    active_bindings = {
        binding["binding_id"]
        for binding in metadata["metadata"]["validator_bindings"]["active"]
    }
    # D2 + D3 activated.
    assert "disease_subject_materialization" in active_bindings
    assert "disease_evidence_code_lookup" in active_bindings
    # D4 stays under_development (blocked: no durable reference identity at extraction time).
    under_dev = {
        binding["binding_id"]
        for binding in metadata["metadata"]["validator_bindings"]["under_development"]
    }
    assert "disease_reference_materialization" in under_dev


def test_finalize_disease_extraction_tool_is_marked_builder_finalization():
    bindings = yaml.safe_load(BINDINGS_PATH.read_text(encoding="utf-8"))
    by_id = {
        entry["tool_id"]: entry
        for entry in bindings["tools"]
        if isinstance(entry, Mapping) and "tool_id" in entry
    }
    finalize = by_id["finalize_disease_extraction"]
    assert finalize["metadata"]["builder_finalization"] is True
    assert finalize["metadata"]["builder_run_state"] is True
    assert finalize["callable"] == (
        "agr_ai_curation_alliance.tools.disease_builder_tools:finalize_disease_extraction"
    )
    for tool_id in (
        "stage_disease_observation",
        "patch_disease_observation",
        "discard_disease_observation",
        "list_staged_disease_observations",
    ):
        assert by_id[tool_id]["metadata"]["builder_run_state"] is True


def test_disease_extractor_agent_has_no_output_schema_and_builder_tools():
    agent_path = (
        REPO_ROOT
        / "packages"
        / "alliance"
        / "agents"
        / "disease_extractor"
        / "agent.yaml"
    )
    agent = yaml.safe_load(agent_path.read_text(encoding="utf-8"))
    assert agent["output_schema"] is None
    tools = set(agent["tools"])
    assert "stage_disease_observation" in tools
    assert "finalize_disease_extraction" in tools
    assert "DiseaseExtractionResultEnvelope" not in str(agent.get("output_schema"))
