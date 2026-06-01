"""Contract tests for the phenotype builder-pattern migration (Phase 3).

Mirrors ``test_gene_domain_pack.py`` / ``test_gene_expression_domain_pack.py`` for the phenotype
extractor's envelope -> builder migration: the per-domain materializer
(``materialize_phenotype_builder_state``), RELATIVE metadata_refs, the golden pending fixture, and
the ``builder_finalization`` / ``builder_run_state`` tool-binding detection flags.

POSTURE: the migration changes the EXTRACTION MECHANISM, not the curation target. The builder
materializer emits the same object graph (one PhenotypeAnnotation curatable_unit plus pending
PhenotypeSubject / PhenotypeTerm / Reference / EvidenceQuote objects) and the same blocked
export/write posture the existing envelope pack declares.

The pre-existing ``test_phenotype_domain_pack.py`` covers the envelope-pattern conversion and
export/submission adapters and is intentionally left untouched (envelope legacy stays until Phase 6).
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
from agr_ai_curation_alliance.domain_packs.phenotype import (  # noqa: E402
    PHENOTYPE_ANNOTATION_MODEL_ID,
    PHENOTYPE_ANNOTATION_OBJECT_ROLE,
    PHENOTYPE_DOMAIN_PACK_ID,
    PHENOTYPE_MATERIALIZER_ID,
    PHENOTYPE_OBJECT_TYPE,
    PHENOTYPE_SUBJECT_OBJECT_TYPE,
    PHENOTYPE_TERM_OBJECT_TYPE,
    materialize_phenotype_builder_state,
)
from agr_ai_curation_alliance.domain_packs.phenotype.conversion import (  # noqa: E402
    PhenotypeBuilderExtractionOutput,
    validate_phenotype_builder_objects,
)

PHENOTYPE_PACK_DIR = (
    ALLIANCE_PYTHON_SRC.parent.parent / "domain_packs" / "phenotype"
)
BUILDER_FIXTURE_PATH = PHENOTYPE_PACK_DIR / "fixtures" / "cilia_builder_pending.yaml"
BINDINGS_PATH = REPO_ROOT / "packages" / "alliance" / "tools" / "bindings.yaml"


def _staged_fields() -> dict[str, Any]:
    return {
        "domain_pack_id": PHENOTYPE_DOMAIN_PACK_ID,
        "object_type": PHENOTYPE_OBJECT_TYPE,
        "pending_ref_id": "phenotype-annotation-1",
        "phenotype_annotation_object": "abnormal sensory cilia morphology",
        "source_mentions": ["sensory cilia were truncated in mutant animals"],
        "subject_identifier": "WB:WBGene00000111",
        "subject_label": "che-2",
        "subject_type": "gene",
        "subject_taxon": "NCBITaxon:6239",
        "term_label": "abnormal sensory cilium morphology",
        "data_provider": "WB",
        "term_taxon_id": "NCBITaxon:6239",
        "negated": False,
    }


def _evidence_records() -> list[dict[str, Any]]:
    return [
        {
            "evidence_record_id": "evidence-cilia-1",
            "entity": "che-2",
            "verified_quote": "Sensory cilia were severely truncated in che-2 mutant amphid neurons.",
            "page": 6,
            "section": "Results",
            "subsection": "Cilia morphology",
            "chunk_id": "chunk-cilia-1",
        }
    ]


def _materialize_one_candidate() -> Any:
    workspace = ExtractionBuilderWorkspace(
        run_id="phenotype-builder-test-run",
        domain_pack_id=PHENOTYPE_DOMAIN_PACK_ID,
        agent_id="phenotype_extractor",
    )
    workspace.upsert_candidate(
        candidate_id="phenotype-candidate-1",
        staged_fields=_staged_fields(),
        pending_ref_ids=["phenotype-annotation-1"],
        evidence_record_ids=["evidence-cilia-1"],
        resolver_selection_refs=[],
        status=CANDIDATE_STATUS_VALID,
    )
    return materialize_phenotype_builder_state(
        workspace=workspace,
        candidate_ids=["phenotype-candidate-1"],
        evidence_records=_evidence_records(),
        resolver_entry_lookup=None,
    )


def test_phenotype_pack_loads_with_builder_fixture():
    registry = load_alliance_domain_pack_registry()
    pack = registry.get_pack(PHENOTYPE_DOMAIN_PACK_ID)
    assert pack is not None

    fixture_ref = registry.get_fixture_pack_ref(
        PHENOTYPE_DOMAIN_PACK_ID, "cilia_builder_pending"
    )
    assert fixture_ref is not None
    assert fixture_ref.path == "fixtures/cilia_builder_pending.yaml"
    assert PHENOTYPE_OBJECT_TYPE in fixture_ref.object_types


def test_phenotype_builder_materializer_produces_clean_extraction_output():
    result = _materialize_one_candidate()
    assert result.ok, result.summary()
    payload = result.payload
    assert payload is not None

    objects = payload["curatable_objects"]
    by_type = {obj["object_type"] for obj in objects}
    assert by_type == {
        PHENOTYPE_SUBJECT_OBJECT_TYPE,
        PHENOTYPE_TERM_OBJECT_TYPE,
        "Reference",
        "EvidenceQuote",
        PHENOTYPE_OBJECT_TYPE,
    }

    annotation = next(
        obj for obj in objects if obj["object_type"] == PHENOTYPE_OBJECT_TYPE
    )
    assert annotation["object_role"] == PHENOTYPE_ANNOTATION_OBJECT_ROLE
    assert annotation["model_ref"] == PHENOTYPE_ANNOTATION_MODEL_ID
    assert annotation["pending_ref_id"] == "phenotype-annotation-1"
    assert annotation["evidence_record_ids"] == ["evidence-cilia-1"]
    assert (
        annotation["payload"]["phenotype_annotation_object"]
        == "abnormal sensory cilia morphology"
    )
    # Existing-pack posture preserved: export/write remain blocked.
    assert annotation["metadata"]["export_behavior"]["status"] == "blocked"
    assert annotation["metadata"]["write_behavior"]["status"] == "blocked"
    assert payload["metadata"]["provenance"]["source"] == PHENOTYPE_MATERIALIZER_ID
    # No resolver/helper machinery (the ontology validator resolves the staged term inline).
    assert "helper_selections" not in payload["metadata"]["provenance"]
    assert result.evidence_record_ids == ("evidence-cilia-1",)


def test_phenotype_builder_pending_term_preserves_resolution_state():
    result = _materialize_one_candidate()
    payload = result.payload
    assert payload is not None
    term = next(
        obj
        for obj in payload["curatable_objects"]
        if obj["object_type"] == PHENOTYPE_TERM_OBJECT_TYPE
    )
    # The staged term stays a pending label-backed candidate (no invented CURIE) so the active
    # ontology validator resolves it. CURIE was not supplied, so it must remain unset.
    assert term["payload"]["resolution_state"] == "pending_ontology_resolution"
    assert term["payload"]["label"] == "abnormal sensory cilium morphology"
    assert "curie" not in term["payload"] or term["payload"]["curie"] is None
    assert term["payload"]["export_state"] == "blocked_pending_ontology_resolution"
    # The validator binding id is preserved exactly as the existing pack declares it.
    assert term["metadata"]["validator_binding_id"] == "phenotype_term_ontology_validator"
    assert (
        term["payload"]["ontology_lookup_hint"]["evidence_record_id"]
        == "evidence-cilia-1"
    )


def test_phenotype_builder_metadata_refs_are_relative_and_resolve():
    result = _materialize_one_candidate()
    payload = result.payload
    assert payload is not None
    annotation = next(
        obj
        for obj in payload["curatable_objects"]
        if obj["object_type"] == PHENOTYPE_OBJECT_TYPE
    )

    metadata_paths = {ref["metadata_path"] for ref in annotation["metadata_refs"]}
    assert metadata_paths == {"raw_mentions[0]", "evidence_records[0]"}
    metadata_root = payload["metadata"]
    for ref in annotation["metadata_refs"]:
        assert not ref["metadata_path"].startswith("extraction_metadata")
        assert field_path_exists(metadata_root, ref["metadata_path"])


def test_phenotype_builder_output_validates_against_object_contract():
    result = _materialize_one_candidate()
    assert result.payload is not None
    output = PhenotypeBuilderExtractionOutput.model_validate(result.payload)
    assert validate_phenotype_builder_objects(output) == ()


def test_phenotype_builder_rejects_evidence_record_not_in_metadata():
    workspace = ExtractionBuilderWorkspace(
        run_id="phenotype-builder-bad-evidence",
        domain_pack_id=PHENOTYPE_DOMAIN_PACK_ID,
        agent_id="phenotype_extractor",
    )
    workspace.upsert_candidate(
        candidate_id="phenotype-candidate-1",
        staged_fields=_staged_fields(),
        pending_ref_ids=["phenotype-annotation-1"],
        evidence_record_ids=["evidence-MISSING"],
        resolver_selection_refs=[],
        status=CANDIDATE_STATUS_VALID,
    )
    result = materialize_phenotype_builder_state(
        workspace=workspace,
        candidate_ids=["phenotype-candidate-1"],
        evidence_records=_evidence_records(),
        resolver_entry_lookup=None,
    )
    assert not result.ok
    assert any(
        issue["reason"] == "unknown_evidence_record_id" for issue in result.issues
    )


def test_phenotype_builder_rejects_missing_phenotype_statement():
    staged = _staged_fields()
    staged["phenotype_annotation_object"] = "   "
    workspace = ExtractionBuilderWorkspace(
        run_id="phenotype-builder-no-statement",
        domain_pack_id=PHENOTYPE_DOMAIN_PACK_ID,
        agent_id="phenotype_extractor",
    )
    workspace.upsert_candidate(
        candidate_id="phenotype-candidate-1",
        staged_fields=staged,
        pending_ref_ids=["phenotype-annotation-1"],
        evidence_record_ids=["evidence-cilia-1"],
        resolver_selection_refs=[],
        status=CANDIDATE_STATUS_VALID,
    )
    result = materialize_phenotype_builder_state(
        workspace=workspace,
        candidate_ids=["phenotype-candidate-1"],
        evidence_records=_evidence_records(),
        resolver_entry_lookup=None,
    )
    assert not result.ok
    assert any(
        issue["reason"] in {"missing_phenotype_statement", "no_retained_candidates"}
        for issue in result.issues
    )


def test_phenotype_builder_golden_fixture_loads_with_relative_refs():
    fixture_pack = load_domain_fixture_pack(BUILDER_FIXTURE_PATH)
    envelope = fixture_pack.fixtures[0].envelope
    assert envelope.domain_pack_id == PHENOTYPE_DOMAIN_PACK_ID

    annotation = next(
        obj for obj in envelope.objects if obj.object_type == PHENOTYPE_OBJECT_TYPE
    )
    assert annotation.pending_ref_id == "phenotype-annotation-1"

    extraction_metadata = envelope.metadata.get("extraction_metadata")
    assert isinstance(extraction_metadata, Mapping)
    for obj in envelope.objects:
        for ref in obj.metadata_refs:
            assert not ref.metadata_path.startswith("extraction_metadata")
            assert field_path_exists(extraction_metadata, ref.metadata_path)


def test_finalize_phenotype_extraction_tool_is_marked_builder_finalization():
    bindings = yaml.safe_load(BINDINGS_PATH.read_text(encoding="utf-8"))
    by_id = {
        entry["tool_id"]: entry
        for entry in bindings["tools"]
        if isinstance(entry, Mapping) and "tool_id" in entry
    }
    finalize = by_id["finalize_phenotype_extraction"]
    assert finalize["metadata"]["builder_finalization"] is True
    assert finalize["metadata"]["builder_run_state"] is True
    assert finalize["callable"] == (
        "agr_ai_curation_alliance.tools.phenotype_builder_tools:finalize_phenotype_extraction"
    )
    for tool_id in (
        "stage_phenotype_observation",
        "patch_phenotype_observation",
        "discard_phenotype_observation",
        "list_staged_phenotype_observations",
    ):
        assert by_id[tool_id]["metadata"]["builder_run_state"] is True


def test_phenotype_extractor_agent_has_no_output_schema_and_builder_tools():
    agent_path = (
        REPO_ROOT
        / "packages"
        / "alliance"
        / "agents"
        / "phenotype_extractor"
        / "agent.yaml"
    )
    agent = yaml.safe_load(agent_path.read_text(encoding="utf-8"))
    assert agent["output_schema"] is None
    tools = set(agent["tools"])
    assert "stage_phenotype_observation" in tools
    assert "finalize_phenotype_extraction" in tools
    # Experimental-condition grounding tools (same as gene_expression's extractor).
    assert {
        "search_domain_field_terms",
        "inspect_ontology_term",
        "resolve_domain_field_term",
    } <= tools
    assert "PhenotypeResultEnvelope" not in str(agent.get("output_schema"))


def _staged_fields_with_conditions(**overrides: Any) -> dict[str, Any]:
    staged = _staged_fields()
    staged["condition_relations"] = [
        {
            "condition_relation_type": "has_condition",
            "conditions": [
                {
                    "condition_class_curie": "ZECO:0000111",
                    "condition_chemical_curie": "CHEBI:9168",
                    "condition_summary": "treated with 3 pM rapamycin",
                },
                {
                    "condition_class_curie": "ZECO:0000160",
                    "condition_free_text": "28 degrees C",
                },
            ],
        }
    ]
    staged.update(overrides)
    return staged


def test_phenotype_builder_materializes_staged_condition_relations():
    """Staged nested condition_relations land on the PhenotypeAnnotation in validator shape."""

    workspace = ExtractionBuilderWorkspace(
        run_id="phenotype-builder-conditions-run",
        domain_pack_id=PHENOTYPE_DOMAIN_PACK_ID,
        agent_id="phenotype_extractor",
    )
    workspace.upsert_candidate(
        candidate_id="phenotype-candidate-1",
        staged_fields=_staged_fields_with_conditions(),
        pending_ref_ids=["phenotype-annotation-1"],
        evidence_record_ids=["evidence-cilia-1"],
        resolver_selection_refs=[],
        status=CANDIDATE_STATUS_VALID,
    )
    result = materialize_phenotype_builder_state(
        workspace=workspace,
        candidate_ids=["phenotype-candidate-1"],
        evidence_records=_evidence_records(),
        resolver_entry_lookup=None,
    )
    assert result.ok, result.summary()

    annotation = next(
        obj
        for obj in result.payload["curatable_objects"]
        if obj["object_type"] == PHENOTYPE_OBJECT_TYPE
    )
    relations = annotation["payload"]["condition_relations"]
    assert len(relations) == 1
    relation = relations[0]
    # Materialized in the exact target shape the bindings read.
    assert relation["condition_relation_type"] == {"name": "has_condition"}
    conditions = relation["conditions"]
    assert len(conditions) == 2
    assert conditions[0]["condition_class"] == {"curie": "ZECO:0000111"}
    assert conditions[0]["condition_chemical"] == {"curie": "CHEBI:9168"}
    assert conditions[0]["condition_summary"] == "treated with 3 pM rapamycin"
    assert conditions[1]["condition_class"] == {"curie": "ZECO:0000160"}
    assert conditions[1]["condition_free_text"] == "28 degrees C"
    # Empty leaves are dropped (condition 2 had no chemical).
    assert "condition_chemical" not in conditions[1]


def test_phenotype_builder_omits_condition_relations_when_unstaged():
    """No conditions staged -> the annotation payload carries no condition_relations key."""

    result = _materialize_one_candidate()
    annotation = next(
        obj
        for obj in result.payload["curatable_objects"]
        if obj["object_type"] == PHENOTYPE_OBJECT_TYPE
    )
    assert "condition_relations" not in annotation["payload"]
