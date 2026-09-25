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

import pytest
import yaml

from src.lib.domain_packs.loader import load_domain_fixture_pack
from src.lib.openai_agents.extraction_builder_workspace import (
    CANDIDATE_STATUS_VALID,
    ExtractionBuilderWorkspace,
)
from src.schemas.domain_envelope import field_path_exists

REPO_ROOT = Path(__file__).resolve().parents[5]
# Identity lookups belong to validators; extraction never searches a database (2026-09-24).
_IDENTITY_LOOKUP_TOOLS = {"search_domain_field_terms", "inspect_ontology_term", "resolve_domain_field_term"}
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
        "term_mention": "truncated sensory cilia",
        "data_provider": "WB",
        "term_taxon_id": "NCBITaxon:6239",
        "rationale": "Amphid cilia were truncated in che-2 mutants, a morphology defect rather than a behaviour.",
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


def _materialize_one_candidate(
    *,
    staged_fields: Mapping[str, Any] | None = None,
) -> Any:
    workspace = ExtractionBuilderWorkspace(
        run_id="phenotype-builder-test-run",
        domain_pack_id=PHENOTYPE_DOMAIN_PACK_ID,
        agent_id="phenotype_extractor",
    )
    candidate_fields = dict(staged_fields) if staged_fields is not None else _staged_fields()
    workspace.upsert_candidate(
        candidate_id="phenotype-candidate-1",
        staged_fields=candidate_fields,
        pending_ref_ids=["phenotype-annotation-1"],
        evidence_record_ids=["evidence-cilia-1"],
        status=CANDIDATE_STATUS_VALID,
    )
    return materialize_phenotype_builder_state(
        workspace=workspace,
        candidate_ids=["phenotype-candidate-1"],
        evidence_records=_evidence_records(),
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


def test_phenotype_annotation_declares_protected_data_provider_fields():
    registry = load_alliance_domain_pack_registry()
    pack = registry.get_pack(PHENOTYPE_DOMAIN_PACK_ID)
    assert pack is not None
    annotation_definition = next(
        definition
        for definition in pack.metadata.object_definitions
        if definition.object_type == PHENOTYPE_OBJECT_TYPE
    )
    fields_by_path = {field.field_path: field for field in annotation_definition.fields}

    data_provider = fields_by_path["data_provider"]
    assert data_provider.field_type == "object"
    assert data_provider.metadata["protected"] is True
    assert "validator_binding_id" not in data_provider.metadata
    assert data_provider.metadata["provider_refs"]["alliance_linkml"] == {
        "schema_ref": "alliance.linkml",
        "commit": "1b11d0888f19eba4ca72022200bb7d96b30d4a52",
        "source_file": "model/schema/core.yaml",
        "class": "PhenotypeAnnotation",
        "slot": "data_provider",
        "range": "Organization",
        "db_table": "phenotypeannotation",
        "db_column": "dataprovider_id",
    }

    abbreviation = fields_by_path["data_provider.abbreviation"]
    assert abbreviation.field_type == "string"
    # Workflow context: a curator override here would submit under another group's provider.
    assert abbreviation.metadata["protected"] is True
    assert "validator_binding_id" not in abbreviation.metadata
    assert abbreviation.metadata["provider_refs"]["alliance_linkml"] == {
        "schema_ref": "alliance.linkml",
        "commit": "1b11d0888f19eba4ca72022200bb7d96b30d4a52",
        "source_file": "model/schema/core.yaml",
        "class": "Organization",
        "slot": "abbreviation",
        "range": "string",
        "db_table": "organization",
        "db_column": "abbreviation",
    }


@pytest.mark.parametrize("has_subject", [True, False])
def test_fresh_phenotype_builder_output_passes_normalization_and_persistence(has_subject):
    from tests.fixtures.fresh_extraction_output import assert_fresh_output_records_every_state

    staged = _staged_fields()
    if not has_subject:
        for key in ("subject_identifier", "subject_label", "subject_type", "subject_taxon"):
            staged.pop(key)
    result = _materialize_one_candidate(staged_fields=staged)
    assert result.ok, result.summary()
    objects = {item["object_type"]: item["payload"] for item in result.payload["curatable_objects"]}
    assert objects["Reference"] == {}
    if not has_subject:
        assert set(objects[PHENOTYPE_SUBJECT_OBJECT_TYPE]) == {"resolution_note"}
    assert_fresh_output_records_every_state(
        result.payload, adapter_key="phenotype", agent_key="phenotype_extractor",
    )


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
    assert annotation["payload"]["data_provider"] == {
        "abbreviation": None,
        "mention": "WB",
        "resolution_state": "unresolved",
        "lookup_outcome": "not_validated",
        "validator_explanation": "Not validated yet.",
    }
    # Existing-pack posture preserved: export/write remain blocked.
    assert annotation["metadata"]["export_behavior"]["status"] == "blocked"
    assert annotation["metadata"]["write_behavior"]["status"] == "blocked"
    assert payload["metadata"]["provenance"]["source"] == PHENOTYPE_MATERIALIZER_ID
    # No resolver/helper machinery (the ontology validator resolves the staged term inline).
    assert "helper_selections" not in payload["metadata"]["provenance"]
    assert result.evidence_record_ids == ("evidence-cilia-1",)


def test_phenotype_builder_leaves_data_provider_unset_when_not_staged():
    staged_fields = _staged_fields()
    del staged_fields["data_provider"]

    result = _materialize_one_candidate(staged_fields=staged_fields)
    assert result.payload is not None
    annotation = next(
        obj
        for obj in result.payload["curatable_objects"]
        if obj["object_type"] == PHENOTYPE_OBJECT_TYPE
    )
    assert "data_provider" not in annotation["payload"]


def test_phenotype_builder_stages_term_as_unresolved_paper_wording():
    result = _materialize_one_candidate()
    payload = result.payload
    assert payload is not None
    annotation = next(
        obj for obj in payload["curatable_objects"] if obj["object_type"] == PHENOTYPE_OBJECT_TYPE
    )
    term_value = annotation["payload"]["phenotype_terms"][0]
    # The paper wording is the mention; extraction never proposes a term name.
    assert term_value["mention"] == "truncated sensory cilia"
    assert "proposed_label" not in term_value
    assert term_value["curie"] is None
    assert term_value["label"] is None
    assert term_value["resolution_state"] == "unresolved"
    assert term_value["lookup_outcome"] == "not_validated"
    assert term_value["validator_explanation"] == "Not validated yet."
    assert term_value["ontology_lookup_hint"] == {
        "data_provider": "WB",
        "taxon_id": "NCBITaxon:6239",
        "evidence_record_id": "evidence-cilia-1",
    }
    # The support object is a structural copy the validator no longer targets.
    support = next(
        obj for obj in payload["curatable_objects"] if obj["object_type"] == PHENOTYPE_TERM_OBJECT_TYPE
    )
    assert support["payload"] == term_value
    assert "validator_binding_id" not in support["metadata"]
    assert support["metadata"]["validation_state"] == "pending_ontology_resolution"


def test_phenotype_builder_never_marks_an_extractor_curie_resolved():
    # Regression (ALL-1283, conversion.py :422-424): a CURIE the extractor supplied was stored
    # as resolved without any validator. It is now a proposal on an unresolved value.
    staged_fields = _staged_fields()
    staged_fields["term_curie"] = "WBPhenotype:0000886"
    result = _materialize_one_candidate(staged_fields=staged_fields)
    assert result.ok, result.summary()
    for obj in result.payload["curatable_objects"]:
        if obj["object_type"] == PHENOTYPE_OBJECT_TYPE:
            term_value = obj["payload"]["phenotype_terms"][0]
        elif obj["object_type"] == PHENOTYPE_TERM_OBJECT_TYPE:
            term_value = obj["payload"]
        else:
            continue
        assert term_value["proposed_curie"] == "WBPhenotype:0000886"
        assert term_value["curie"] is None
        assert term_value["resolution_state"] == "unresolved"
        assert term_value["lookup_outcome"] == "not_validated"


def test_phenotype_builder_term_label_never_falls_back_to_the_statement():
    # Regression (conversion.py :397 `term_label or statement`): the validated label stays
    # empty; neither the statement nor any proposal fills it.
    staged_fields = _staged_fields()
    result = _materialize_one_candidate(staged_fields=staged_fields)
    annotation = next(
        obj for obj in result.payload["curatable_objects"] if obj["object_type"] == PHENOTYPE_OBJECT_TYPE
    )
    term_value = annotation["payload"]["phenotype_terms"][0]
    assert "proposed_label" not in term_value
    assert term_value["label"] is None
    assert term_value["mention"] == "truncated sensory cilia"


def test_phenotype_builder_requires_term_mention_and_source_mentions():
    # Regression (conversion.py ~:886 `source_mentions or [statement]`): nothing fills in for
    # missing paper wording.
    without_term = _staged_fields()
    del without_term["term_mention"]
    result = _materialize_one_candidate(staged_fields=without_term)
    assert not result.ok
    assert [issue["reason"] for issue in result.issues] == ["missing_term_mention"]

    without_mentions = _staged_fields()
    del without_mentions["source_mentions"]
    result = _materialize_one_candidate(staged_fields=without_mentions)
    assert not result.ok
    assert [issue["reason"] for issue in result.issues] == ["missing_source_mentions"]


def test_phenotype_builder_taxon_hints_read_one_staged_key_each():
    # Regression (conversion.py :340 subject_taxon-or-taxon and :370-374 taxon chains).
    staged_fields = _staged_fields()
    del staged_fields["term_taxon_id"]
    del staged_fields["subject_taxon"]
    staged_fields["taxon"] = "NCBITaxon:10090"
    result = _materialize_one_candidate(staged_fields=staged_fields)
    annotation = next(
        obj for obj in result.payload["curatable_objects"] if obj["object_type"] == PHENOTYPE_OBJECT_TYPE
    )
    assert "taxon_id" not in annotation["payload"]["phenotype_terms"][0]["ontology_lookup_hint"]
    subject = annotation["payload"]["phenotype_annotation_subject"]
    assert (subject["taxon"], "proposed_taxon" in subject) == (None, False)

    staged_fields = _staged_fields()
    del staged_fields["term_taxon_id"]
    result = _materialize_one_candidate(staged_fields=staged_fields)
    annotation = next(
        obj for obj in result.payload["curatable_objects"] if obj["object_type"] == PHENOTYPE_OBJECT_TYPE
    )
    # The subject's taxon never stands in for the term lookup taxon.
    assert "taxon_id" not in annotation["payload"]["phenotype_terms"][0]["ontology_lookup_hint"]
    # The extractor's species is a validator input; the validated taxon stays empty (ALL-1283).
    subject = annotation["payload"]["phenotype_annotation_subject"]
    assert (subject["proposed_taxon"], subject["taxon"]) == ("NCBITaxon:6239", None)


def test_phenotype_builder_stages_subject_with_proposed_identifier():
    result = _materialize_one_candidate()
    annotation = next(
        obj for obj in result.payload["curatable_objects"] if obj["object_type"] == PHENOTYPE_OBJECT_TYPE
    )
    subject = annotation["payload"]["phenotype_annotation_subject"]
    assert subject == {
        "subject_type": "gene",
        "proposed_taxon": "NCBITaxon:6239",
        "taxon": None,
        "proposed_subject_identifier": "WB:WBGene00000111",
        "subject_identifier": None,
        "subject_label": None,
        "mention": "che-2",
        "resolution_state": "unresolved",
        "lookup_outcome": "not_validated",
        "validator_explanation": "Not validated yet.",
    }
    assert annotation["metadata"]["validation_state"] == "pending_entity_resolution"


def test_phenotype_builder_without_subject_leaves_the_subject_absent():
    staged_fields = _staged_fields()
    for key in ("subject_identifier", "subject_label", "subject_type", "subject_taxon"):
        del staged_fields[key]
    result = _materialize_one_candidate(staged_fields=staged_fields)
    assert result.ok, result.summary()
    by_type = {obj["object_type"]: obj for obj in result.payload["curatable_objects"]}
    assert "phenotype_annotation_subject" not in by_type[PHENOTYPE_OBJECT_TYPE]["payload"]
    assert by_type[PHENOTYPE_OBJECT_TYPE]["metadata"]["validation_state"] == "blocked_missing_subject"
    assert set(by_type[PHENOTYPE_SUBJECT_OBJECT_TYPE]["payload"]) == {"resolution_note"}


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
        status=CANDIDATE_STATUS_VALID,
    )
    result = materialize_phenotype_builder_state(
        workspace=workspace,
        candidate_ids=["phenotype-candidate-1"],
        evidence_records=_evidence_records(),
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
        status=CANDIDATE_STATUS_VALID,
    )
    result = materialize_phenotype_builder_state(
        workspace=workspace,
        candidate_ids=["phenotype-candidate-1"],
        evidence_records=_evidence_records(),
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
        obj for obj in envelope.extracted_objects if obj.object_type == PHENOTYPE_OBJECT_TYPE
    )
    assert annotation.pending_ref_id == "phenotype-annotation-1"
    assert annotation.payload["data_provider"]["mention"] == "WB"
    assert annotation.payload["data_provider"]["abbreviation"] is None

    extraction_metadata = envelope.metadata.get("extraction_metadata")
    assert isinstance(extraction_metadata, Mapping)
    for obj in envelope.extracted_objects:
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
    # Extraction never searches a database for an identity (2026-09-24); only species
    # context lookup stays.
    assert _IDENTITY_LOOKUP_TOOLS.isdisjoint(tools)
    assert "agr_species_context_lookup" in tools
    assert "PhenotypeResultEnvelope" not in str(agent.get("output_schema"))


def _staged_fields_with_conditions(**overrides: Any) -> dict[str, Any]:
    staged = _staged_fields()
    staged["condition_relations"] = [
        {
            "condition_relation_type": "has_condition",
            "conditions": [
                {
                    "condition_class_mention": "chemical treatment",
                    "condition_class_curie": "ZECO:0000111",
                    "condition_chemical_mention": "rapamycin",
                    "condition_chemical_curie": "CHEBI:9168",
                    "condition_summary": "treated with 3 pM rapamycin",
                },
                {
                    "condition_class_mention": "temperature exposure",
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
        staged_fields=_staged_fields_with_conditions(validation_guidance="Check the explicitly described experimental conditions."),
        pending_ref_ids=["phenotype-annotation-1"],
        evidence_record_ids=["evidence-cilia-1"],
        status=CANDIDATE_STATUS_VALID,
    )
    result = materialize_phenotype_builder_state(
        workspace=workspace,
        candidate_ids=["phenotype-candidate-1"],
        evidence_records=_evidence_records(),
    )
    assert result.ok, result.summary()

    annotation = next(
        obj
        for obj in result.payload["curatable_objects"]
        if obj["object_type"] == PHENOTYPE_OBJECT_TYPE
    )
    relations = annotation["payload"]["condition_relations"]
    from src.schemas.domain_envelope import DomainEnvelope
    from src.lib.domain_packs.input_selectors import build_domain_validation_request
    from src.lib.domain_packs.validation_registry import DomainPackValidationRegistry, ValidationBindingState
    envelope = DomainEnvelope(envelope_id="phenotype-guidance", domain_pack_id=PHENOTYPE_DOMAIN_PACK_ID,
        extracted_objects=result.payload["curatable_objects"], metadata=result.payload["metadata"])
    pack = load_alliance_domain_pack_registry().get_pack(PHENOTYPE_DOMAIN_PACK_ID)
    matches = DomainPackValidationRegistry.from_domain_pack(pack).match_bindings(envelope, states=[ValidationBindingState.ACTIVE])
    requests = [build_domain_validation_request(match).request for match in matches
                if match.object_envelope.object_type == PHENOTYPE_OBJECT_TYPE]
    requests = [request for request in requests if request is not None]
    assert requests
    assert all(request.validation_guidance == "Check the explicitly described experimental conditions." for request in requests)
    assert all(obj.get("validation_guidance") is None for obj in result.payload["curatable_objects"]
               if obj["object_type"].endswith(("Reference", "EvidenceQuote")))
    assert len(relations) == 1
    relation = relations[0]
    # Every relation type and component is a staged resolvable value: paper wording plus any
    # proposed CURIE, with no validated identity yet.
    unresolved = {
        "resolution_state": "unresolved",
        "lookup_outcome": "not_validated",
        "validator_explanation": "Not validated yet.",
    }
    assert relation["condition_relation_type"] == {
        "name": None, "mention": "has_condition", **unresolved,
    }
    conditions = relation["conditions"]
    assert len(conditions) == 2
    assert conditions[0]["condition_class"] == {
        "proposed_curie": "ZECO:0000111", "curie": None, "name": None, "mention": "chemical treatment",
        **unresolved,
    }
    assert conditions[0]["condition_chemical"] == {
        "proposed_curie": "CHEBI:9168", "curie": None, "name": None, "mention": "rapamycin", **unresolved,
    }
    assert conditions[0]["condition_summary"] == "treated with 3 pM rapamycin"
    assert conditions[1]["condition_class"]["proposed_curie"] == "ZECO:0000160"
    assert conditions[1]["condition_free_text"] == "28 degrees C"
    condition_request = next(
        request for request in requests
        if request.validator_binding_id == "experimental_condition_validation"
    )
    assert condition_request.selected_inputs["condition_class_curie"] == "ZECO:0000111"
    assert condition_request.selected_inputs["condition_class_name"] == "chemical treatment"
    assert condition_request.selected_inputs["condition_chemical_name"] == "rapamycin"
    relation_request = next(
        request for request in requests
        if request.validator_binding_id == "phenotype_condition_relation_lookup"
    )
    assert relation_request.selected_inputs["term_name"] == "has_condition"
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


# --- ALL-1298: per-item rationale -------------------------------------------------------------


def _stage_kwargs(**overrides: Any) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "pending_ref_id": "phenotype-annotation-1",
        "phenotype_annotation_object": "abnormal sensory cilia morphology",
        "evidence_record_ids": ["evidence-cilia-1"],
        "source_mentions": ["sensory cilia were truncated in mutant animals"],
        "rationale": "  Amphid cilia were truncated in che-2 mutants, a morphology defect.  ",
        "term_mention": "truncated sensory cilia",
    }
    kwargs.update(overrides)
    return kwargs


def _builder_tools_with_workspace(monkeypatch: Any) -> tuple[Any, ExtractionBuilderWorkspace]:
    from agr_ai_curation_alliance.tools import phenotype_builder_tools as tools

    workspace = ExtractionBuilderWorkspace(
        run_id="phenotype-rationale-run",
        domain_pack_id=PHENOTYPE_DOMAIN_PACK_ID,
        agent_id="phenotype_extractor",
    )
    monkeypatch.setattr(tools, "get_active_extraction_builder_workspace", lambda: workspace)
    monkeypatch.setattr(tools, "write_extraction_trace_event", lambda **_: None)
    return tools, workspace


def test_stage_phenotype_tool_requires_rationale_with_shared_description():
    from agr_ai_curation_alliance.tools.builder_rationale import RATIONALE_ARG_DESCRIPTION
    from agr_ai_curation_alliance.tools.phenotype_builder_tools import (
        patch_phenotype_observation,
        stage_phenotype_observation,
    )

    schema = stage_phenotype_observation.params_json_schema
    assert "rationale" in schema["required"]
    assert schema["properties"]["rationale"]["description"] == RATIONALE_ARG_DESCRIPTION
    patch_updates = patch_phenotype_observation.params_json_schema["properties"]["updates"]
    assert "A `rationale` update must be non-empty; it cannot be cleared." in (
        " ".join(patch_updates["description"].split())
    )


def test_stage_phenotype_observation_stores_stripped_rationale(monkeypatch):
    tools, workspace = _builder_tools_with_workspace(monkeypatch)

    result = tools._stage_phenotype_observation_impl(**_stage_kwargs())

    assert result.status == "ok"
    staged = workspace.candidates[result.data["candidate_id"]].staged_fields
    assert staged["rationale"] == "Amphid cilia were truncated in che-2 mutants, a morphology defect."


def test_stage_phenotype_observation_rejects_blank_rationale(monkeypatch):
    tools, workspace = _builder_tools_with_workspace(monkeypatch)

    blank = tools._stage_phenotype_observation_impl(**_stage_kwargs(rationale="   "))

    assert blank.status == "error"
    assert blank.data["validation_issues"][0]["field_path"] == "rationale"
    assert workspace.candidates == {}


def test_patch_phenotype_observation_rewrites_but_never_clears_rationale(monkeypatch):
    tools, workspace = _builder_tools_with_workspace(monkeypatch)
    candidate_id = tools._stage_phenotype_observation_impl(**_stage_kwargs()).data["candidate_id"]

    rewritten = tools._patch_phenotype_observation_impl(
        candidate_id=candidate_id,
        pending_ref_id="phenotype-annotation-1",
        updates=[{"field_path": "rationale", "string_value": "Cilia were shortened, not absent."}],
    )
    assert rewritten.status == "ok"
    assert workspace.candidates[candidate_id].staged_fields["rationale"] == (
        "Cilia were shortened, not absent."
    )

    for value in ("  ", None):
        cleared = tools._patch_phenotype_observation_impl(
            candidate_id=candidate_id,
            pending_ref_id="phenotype-annotation-1",
            updates=[{"field_path": "rationale", "string_value": value}],
        )
        assert cleared.status == "error"
        assert cleared.data["validation_issues"][0]["reason"] == "invalid_rationale"
    assert workspace.candidates[candidate_id].staged_fields["rationale"] == (
        "Cilia were shortened, not absent."
    )


def test_phenotype_builder_carries_rationale_onto_annotation_only():
    result = _materialize_one_candidate()
    assert result.ok, result.summary()

    for obj in result.payload["curatable_objects"]:
        if obj["object_type"] == PHENOTYPE_OBJECT_TYPE:
            assert obj["payload"]["rationale"] == _staged_fields()["rationale"]
        else:
            assert "rationale" not in obj["payload"]


def test_phenotype_builder_rejects_new_candidate_without_rationale():
    staged_fields = _staged_fields()
    del staged_fields["rationale"]

    result = _materialize_one_candidate(staged_fields=staged_fields)

    assert not result.ok
    assert any(
        issue["reason"] == "missing_rationale" and issue["message"].endswith("patch the candidate with a rationale saying why you selected it.")
        for issue in result.issues
    )


def test_phenotype_annotation_declares_optional_rationale_in_rationale_group():
    registry = load_alliance_domain_pack_registry()
    pack = registry.get_pack(PHENOTYPE_DOMAIN_PACK_ID)
    assert pack is not None
    definition = next(
        definition
        for definition in pack.metadata.object_definitions
        if definition.object_type == PHENOTYPE_OBJECT_TYPE
    )
    rationale = next(field for field in definition.fields if field.field_path == "rationale")
    assert rationale.field_type == "string"
    assert rationale.required is False
    groups = definition.metadata["workspace_display"]["groups"]
    rationale_group = next(group for group in groups if group["id"] == "rationale")
    assert rationale_group["label"] == "Rationale"
    assert rationale_group["fields"] == ["rationale"]


def test_stored_phenotype_annotation_without_rationale_validates_without_new_findings():
    from src.lib.domain_packs.structural_checks import run_domain_envelope_structural_checks

    registry = load_alliance_domain_pack_registry()
    pack = registry.get_pack(PHENOTYPE_DOMAIN_PACK_ID)
    assert pack is not None
    current = load_domain_fixture_pack(BUILDER_FIXTURE_PATH).fixtures[0].envelope
    stored_before_rationale = current.model_copy(deep=True)
    for obj in stored_before_rationale.extracted_objects:
        obj.payload.pop("rationale", None)
    assert any("rationale" in obj.payload for obj in current.extracted_objects)

    baseline = run_domain_envelope_structural_checks(current, pack)
    legacy = run_domain_envelope_structural_checks(stored_before_rationale, pack)

    assert [finding.code for finding in legacy.appended_findings] == [
        finding.code for finding in baseline.appended_findings
    ]


# --- ALL-1283: extracted vs validated values ----------------------------------------------------


def test_stage_phenotype_observation_requires_term_mention(monkeypatch):
    tools, workspace = _builder_tools_with_workspace(monkeypatch)
    schema = tools.stage_phenotype_observation.params_json_schema
    assert "term_mention" in schema["required"]

    blank = tools._stage_phenotype_observation_impl(**_stage_kwargs(term_mention="  "))

    assert blank.status == "error"
    assert blank.data["validation_issues"][0]["field_path"] == "term_mention"
    assert workspace.candidates == {}


def test_stage_phenotype_observation_rejects_subject_details_without_paper_wording(monkeypatch):
    tools, workspace = _builder_tools_with_workspace(monkeypatch)

    result = tools._stage_phenotype_observation_impl(
        **_stage_kwargs(subject_identifier="WB:WBGene00000111", subject_type="gene")
    )

    assert result.status == "error"
    assert "need subject_label" in result.data["validation_issues"][0]["message"]
    assert workspace.candidates == {}

    staged = tools._stage_phenotype_observation_impl(
        **_stage_kwargs(subject_identifier="WB:WBGene00000111", subject_label="che-2")
    )
    assert staged.status == "ok"
    cleared = tools._patch_phenotype_observation_impl(
        candidate_id=staged.data["candidate_id"],
        pending_ref_id="phenotype-annotation-1",
        updates=[{"field_path": "subject_label", "string_value": None}],
    )
    assert cleared.status == "error"
    assert cleared.data["validation_issues"][0]["reason"] == "missing_subject_wording"


def test_stage_phenotype_observation_keeps_an_unmatched_term_as_paper_wording(monkeypatch):
    tools, workspace = _builder_tools_with_workspace(monkeypatch)

    result = tools._stage_phenotype_observation_impl(
        **_stage_kwargs(term_mention="wobbly cilia of no known ontology class")
    )

    assert result.status == "ok"
    staged = workspace.candidates[result.data["candidate_id"]].staged_fields
    assert staged["term_mention"] == "wobbly cilia of no known ontology class"


def test_stage_phenotype_condition_curie_needs_its_paper_wording(monkeypatch):
    tools, workspace = _builder_tools_with_workspace(monkeypatch)

    result = tools._stage_phenotype_observation_impl(
        **_stage_kwargs(
            condition_relations=[
                {
                    "condition_relation_type": "has_condition",
                    "conditions": [{"condition_class_curie": "ZECO:0000111"}],
                }
            ]
        )
    )

    assert result.status == "error"
    assert "condition_class_curie needs condition_class_mention" in (
        result.data["validation_issues"][0]["message"]
    )
    assert workspace.candidates == {}


def _builder_envelope(staged_fields: Mapping[str, Any] | None = None) -> Any:
    from src.schemas.domain_envelope import DomainEnvelope

    result = _materialize_one_candidate(staged_fields=staged_fields)
    assert result.ok, result.summary()
    return DomainEnvelope(
        envelope_id="phenotype-term-write-back",
        domain_pack_id=PHENOTYPE_DOMAIN_PACK_ID,
        extracted_objects=result.payload["curatable_objects"],
        metadata={"extraction_metadata": result.payload["metadata"]},
    )


def _term_materialization_inputs(envelope: Any, *, status: str, lookup_outcome: str) -> list[Any]:
    from src.lib.domain_packs.input_selectors import build_domain_validation_request
    from src.lib.domain_packs.materialization import ValidatorResultMaterializationInput
    from src.lib.domain_packs.validation_registry import (
        DomainPackValidationRegistry,
        ValidationBindingState,
    )
    from src.schemas.domain_validator import DomainValidatorResultBase

    pack = load_alliance_domain_pack_registry().get_pack(PHENOTYPE_DOMAIN_PACK_ID)
    matches = [
        match
        for match in DomainPackValidationRegistry.from_domain_pack(pack).match_bindings(
            envelope, states=[ValidationBindingState.ACTIVE]
        )
        if match.binding.binding_id == "phenotype_term_ontology_validator"
    ]
    items = []
    for match in matches:
        request = build_domain_validation_request(match).request
        assert request is not None
        resolved = status == "resolved"
        result = DomainValidatorResultBase.model_validate(
            {
                "status": status,
                "request_id": request.request_id,
                "validator_binding_id": request.validator_binding_id,
                "validator_agent": request.validator_agent,
                "target": request.target,
                "resolved_values": (
                    {"curie": "WBPhenotype:0001174", "label": "cilium morphology variant"}
                    if resolved
                    else {}
                ),
                "resolved_objects": [],
                "missing_expected_fields": [],
                "candidates": [],
                "curator_message": None,
                "lookup_attempts": [
                    {
                        "provider": "fixture_lookup",
                        "method": "search_ontology_terms",
                        "query": {"term": request.selected_inputs["label"]},
                        "result_count": 1 if resolved else 0,
                        "outcome": lookup_outcome,
                    }
                ],
                "explanation": "Fixture ontology decision.",
            }
        )
        items.append(
            ValidatorResultMaterializationInput(match=match, request=request, result=result)
        )
    return items


def test_phenotype_term_validator_fans_out_over_the_annotation_terms():
    envelope = _builder_envelope()
    items = _term_materialization_inputs(envelope, status="resolved", lookup_outcome="success")

    # One request per annotation term; the PhenotypeTerm support object is not a target.
    assert len(items) == 1
    item = items[0]
    assert item.match.object_envelope.object_type == PHENOTYPE_OBJECT_TYPE
    assert item.match.field_path == "phenotype_terms[0]"
    assert item.request.selected_inputs["label"] == "truncated sensory cilia"
    assert "curie" not in item.request.selected_inputs
    assert item.request.expected_result_fields == {
        "curie": "phenotype_terms[0].curie",
        "label": "phenotype_terms[0].label",
    }


def test_phenotype_term_validator_writes_resolution_into_the_annotation_term():
    from src.lib.domain_packs.materialization import materialize_validator_results_into_envelope

    pack = load_alliance_domain_pack_registry().get_pack(PHENOTYPE_DOMAIN_PACK_ID)
    envelope = _builder_envelope()

    resolved = materialize_validator_results_into_envelope(
        envelope,
        pack.metadata,
        _term_materialization_inputs(envelope, status="resolved", lookup_outcome="success"),
    ).envelope
    annotation = next(
        obj for obj in resolved.extracted_objects if obj.object_type == PHENOTYPE_OBJECT_TYPE
    )
    term_value = annotation.payload["phenotype_terms"][0]
    assert term_value["curie"] == "WBPhenotype:0001174"
    assert term_value["label"] == "cilium morphology variant"
    assert term_value["mention"] == "truncated sensory cilia"
    assert term_value["resolution_state"] == "resolved"
    assert term_value["lookup_outcome"] == "matched"
    assert term_value["validator_explanation"] == "Fixture ontology decision."

    unresolved = materialize_validator_results_into_envelope(
        envelope,
        pack.metadata,
        _term_materialization_inputs(envelope, status="unresolved", lookup_outcome="not_found"),
    ).envelope
    annotation = next(
        obj for obj in unresolved.extracted_objects if obj.object_type == PHENOTYPE_OBJECT_TYPE
    )
    term_value = annotation.payload["phenotype_terms"][0]
    assert term_value["curie"] is None
    assert term_value["label"] is None
    assert term_value["mention"] == "truncated sensory cilia"
    assert term_value["resolution_state"] == "unresolved"
    assert term_value["lookup_outcome"] == "not_found"
    assert term_value["validator_explanation"] == "Fixture ontology decision."


def test_nested_term_normalizer_never_invents_wording_or_resolution():
    # Regression (conversion.py :418 source_mentions=[label] and :422-424 CURIE -> "resolved"):
    # a nested term without paper wording is not a term value, and a term's stored state is
    # kept exactly as written.
    from agr_ai_curation_alliance.domain_packs.phenotype import (
        normalize_phenotype_extraction_payload,
    )

    def _payload(term: dict[str, Any]) -> dict[str, Any]:
        return {
            "curatable_objects": [
                {
                    "object_type": PHENOTYPE_OBJECT_TYPE,
                    "pending_ref_id": "phenotype-annotation-1",
                    "evidence_record_ids": ["evidence-cilia-1"],
                    "payload": {"phenotype_terms": [term]},
                }
            ]
        }

    no_wording = {"curie": "WBPhenotype:0000886", "label": "reduced brood size"}
    assert normalize_phenotype_extraction_payload(_payload(no_wording)) == _payload(no_wording)

    staged = {
        "proposed_curie": "WBPhenotype:0000886",
        "curie": None,
        "label": None,
        "mention": "fewer progeny",
        "resolution_state": "unresolved",
        "lookup_outcome": "not_validated",
        "validator_explanation": "Not validated yet.",
    }
    normalized = normalize_phenotype_extraction_payload(_payload(staged))
    support = next(
        obj for obj in normalized["curatable_objects"] if obj["object_type"] == PHENOTYPE_TERM_OBJECT_TYPE
    )
    assert support["payload"] == staged
    assert "source_mentions" not in support["payload"]


def _staged_contract_value_paths(node: Any, path: str = "") -> list[str]:
    """Payload paths (indexes dropped) of every value stored with the contract state."""

    from src.lib.domain_packs.resolvable_values import has_resolution_state

    found = [path] if has_resolution_state(node) else []
    if isinstance(node, Mapping):
        for key, child in node.items():
            found += _staged_contract_value_paths(child, f"{path}.{key}" if path else str(key))
    elif isinstance(node, list):
        for child in node:
            found += _staged_contract_value_paths(child, path)
    return found


def test_every_staged_phenotype_value_is_declared_resolvable():
    """Guard (ALL-1283 H1/F2): a validator write into an undeclared contract value raises, so
    every value the builder stages with contract state is declared by the pack."""

    from src.lib.domain_packs.resolvable_values import declared_resolvable_fields

    metadata = load_alliance_domain_pack_registry().get_pack(PHENOTYPE_DOMAIN_PACK_ID).metadata
    staged = _staged_fields_with_conditions()
    staged["condition_relations"][0]["conditions"].append({
        f"{part}_{suffix}": f"{part} {suffix}"
        for part in ("condition_class", "condition_id", "condition_chemical", "condition_taxon")
        for suffix in ("mention", "curie")
    })
    result = _materialize_one_candidate(staged_fields=staged)
    assert result.ok, result.summary()
    for obj in result.payload["curatable_objects"]:
        declared = set(declared_resolvable_fields(metadata, obj["object_type"]))
        staged_paths = set(_staged_contract_value_paths(obj["payload"]))
        assert staged_paths <= declared, (obj["object_type"], sorted(staged_paths - declared))


# --- Extraction never searches a database (2026-09-24) -------------------------------------------


def test_phenotype_staging_takes_no_proposed_term_name_or_identity(monkeypatch):
    """Extraction records paper wording plus IDs the paper prints; it never stages a proposed
    term name or a validated identity key."""

    tools, workspace = _builder_tools_with_workspace(monkeypatch)
    for extra in ({"term_label": "abnormal sensory cilium morphology"}, {"curie": "WBPhenotype:0000180"}):
        with pytest.raises(TypeError):
            tools._stage_phenotype_observation_impl(**_stage_kwargs(**extra))
    candidate_id = tools._stage_phenotype_observation_impl(
        **_stage_kwargs(term_curie="WBPhenotype:0000180")
    ).data["candidate_id"]

    patched = tools._patch_phenotype_observation_impl(
        candidate_id=candidate_id,
        pending_ref_id="phenotype-annotation-1",
        updates=[{"field_path": "term_label", "string_value": "abnormal sensory cilium morphology"}],
    )
    assert patched.status == "error"
    assert "term_label" not in workspace.candidates[candidate_id].staged_fields

    properties = tools.stage_phenotype_observation.params_json_schema["properties"]
    assert "term_label" not in properties
    assert "paper itself prints" in properties["term_curie"]["description"]
    assert "paper itself prints" in properties["subject_identifier"]["description"]


# --- Species context comes from the species tool (2026-09-24, option A) --------------------------

_WORM_CONTEXT = {"data_provider": "WB", "term_taxon_id": "NCBITaxon:6239"}
_SPECIES_TOOL_HINT = "Call agr_species_context_lookup with the species"


@pytest.mark.parametrize(
    "species_context",
    [
        {"data_provider": "WB", "term_taxon_id": "Caenorhabditis elegans"},
        {**_WORM_CONTEXT, "subject_label": "mus-81", "subject_taxon": "C. elegans"},
    ],
    ids=["term_taxon_id species name", "subject_taxon species name"],
)
def test_stage_phenotype_rejects_a_species_name_as_the_taxon(monkeypatch, species_context):
    """The term check needs an NCBITaxon ID; a species name read from the paper blocks it."""

    tools, workspace = _builder_tools_with_workspace(monkeypatch)

    result = tools._stage_phenotype_observation_impl(**_stage_kwargs(**species_context))

    assert result.status == "error"
    message = result.data["validation_issues"][0]["message"]
    assert "is not an NCBITaxon ID" in message and _SPECIES_TOOL_HINT in message
    assert workspace.candidates == {}


@pytest.mark.parametrize(
    ("species_context", "missing"),
    [
        ({"data_provider": "WB"}, "term_taxon_id"),
        ({"term_taxon_id": "NCBITaxon:6239"}, "data_provider"),
        ({"subject_label": "mus-81", "subject_taxon": "NCBITaxon:6239"}, "data_provider, term_taxon_id"),
        ({**_WORM_CONTEXT, "subject_label": "mus-81"}, "subject_taxon"),
    ],
)
def test_stage_phenotype_rejects_a_partial_species_context(monkeypatch, species_context, missing):
    tools, workspace = _builder_tools_with_workspace(monkeypatch)

    result = tools._stage_phenotype_observation_impl(**_stage_kwargs(**species_context))

    assert result.status == "error"
    message = result.data["validation_issues"][0]["message"]
    assert f"Species context is incomplete: {missing} missing." in message
    assert _SPECIES_TOOL_HINT in message
    assert workspace.candidates == {}


@pytest.mark.parametrize(
    "species_context",
    [
        {},
        dict(_WORM_CONTEXT),
        {**_WORM_CONTEXT, "subject_label": "mus-81", "subject_taxon": "NCBITaxon:6239"},
    ],
    ids=["no species", "term context", "term and subject context"],
)
def test_stage_phenotype_accepts_no_species_or_the_full_species_tool_context(monkeypatch, species_context):
    tools, workspace = _builder_tools_with_workspace(monkeypatch)

    result = tools._stage_phenotype_observation_impl(**_stage_kwargs(**species_context))

    assert result.status == "ok", result.data
    staged = workspace.candidates[result.data["candidate_id"]].staged_fields
    for name, value in species_context.items():
        assert staged[name] == value


def test_patch_phenotype_keeps_the_species_context_complete(monkeypatch):
    tools, workspace = _builder_tools_with_workspace(monkeypatch)
    candidate_id = tools._stage_phenotype_observation_impl(
        **_stage_kwargs(**_WORM_CONTEXT)
    ).data["candidate_id"]

    def patch(field_path, value):
        return tools._patch_phenotype_observation_impl(
            candidate_id=candidate_id,
            pending_ref_id="phenotype-annotation-1",
            updates=[{"field_path": field_path, "string_value": value}],
        )

    species_name = patch("term_taxon_id", "Caenorhabditis elegans")
    assert species_name.status == "error"
    assert species_name.data["validation_issues"][0]["reason"] == "invalid_species_context"
    partial = patch("data_provider", None)
    assert partial.status == "error"
    assert _SPECIES_TOOL_HINT in partial.data["validation_issues"][0]["message"]
    assert workspace.candidates[candidate_id].staged_fields["term_taxon_id"] == "NCBITaxon:6239"

    mouse = tools._patch_phenotype_observation_impl(
        candidate_id=candidate_id,
        pending_ref_id="phenotype-annotation-1",
        updates=[
            {"field_path": "data_provider", "string_value": "MGI"},
            {"field_path": "term_taxon_id", "string_value": "NCBITaxon:10090"},
        ],
    )
    assert mouse.status == "ok"


# --- Review S1 (integration-2): the subject kind is exactly what the subject check routes on ------


@pytest.mark.parametrize("subject_type", ["AGM", "Gene", "model"])
def test_stage_phenotype_rejects_a_subject_type_the_subject_check_cannot_route(monkeypatch, subject_type):
    tools, workspace = _builder_tools_with_workspace(monkeypatch)

    result = tools._stage_phenotype_observation_impl(
        **_stage_kwargs(subject_label="mus-81", subject_type=subject_type)
    )

    assert result.status == "error"
    assert workspace.candidates == {}


@pytest.mark.parametrize("subject_type", ["gene", "allele", "agm"])
def test_stage_and_patch_phenotype_accept_each_routable_subject_type(monkeypatch, subject_type):
    tools, workspace = _builder_tools_with_workspace(monkeypatch)

    result = tools._stage_phenotype_observation_impl(
        **_stage_kwargs(subject_label="mus-81", subject_type=subject_type)
    )

    assert result.status == "ok", result.data
    candidate_id = result.data["candidate_id"]
    assert workspace.candidates[candidate_id].staged_fields["subject_type"] == subject_type

    def patch(value):
        return tools._patch_phenotype_observation_impl(
            candidate_id=candidate_id,
            pending_ref_id="phenotype-annotation-1",
            updates=[{"field_path": "subject_type", "string_value": value}],
        )

    assert patch("Allele").status == "error"
    assert patch("allele").status == "ok"
