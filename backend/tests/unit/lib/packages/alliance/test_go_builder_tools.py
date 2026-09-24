"""Typed GO builder materialization and canonical projection coverage.

Extraction never searches a database (Chris, 2026-09-24): the GO builder stages
the paper's wording and paper-stated identifiers only, and the gene product, GO
term, reference and with/from stay unresolved and not yet validated. The
evidence code and qualifiers are mapped with the builder's own local tables.
"""

from __future__ import annotations

import copy
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import pytest

from src.lib.curation_workspace import pipeline as workspace_pipeline
from src.lib.curation_workspace.domain_envelope_normalization import (
    domain_envelope_from_extraction_result,
)
from src.lib.domain_packs.materialization import (
    DomainPackMetadataReviewRowMaterializer,
    project_evidence_anchor_projections,
)
from src.lib.domain_packs.loader import load_domain_pack_metadata
from src.lib.domain_packs.resolvable_values import resolved_value
from src.schemas.curation_workspace import (
    CurationExtractionResultRecord,
    CurationExtractionSourceKind,
)
from src.lib.openai_agents import extraction_builder_workspace as builder
from src.lib.openai_agents.tools import evidence_workspace


REPO_ROOT = Path(__file__).resolve().parents[6]
ALLIANCE_PYTHON_SRC = REPO_ROOT / "packages" / "alliance" / "python" / "src"
if str(ALLIANCE_PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(ALLIANCE_PYTHON_SRC))

from agr_ai_curation_alliance.domain_packs.go import (  # noqa: E402
    materialize_go_builder_state,
)
from agr_ai_curation_alliance.domain_packs.go.values import (  # noqa: E402
    evidence_code_value,
    gene_product_value,
    go_term_value,
    qualifier_value,
    reference_value,
    with_from_value,
)
from agr_ai_curation_alliance.tools import go_builder_tools  # noqa: E402


class _Workspace:
    run_id = "rgd-go-run-1"

    def __init__(self, candidates):
        self._candidates = candidates

    def get_candidate(self, candidate_id):
        if candidate_id not in self._candidates:
            raise KeyError(candidate_id)
        return self._candidates[candidate_id]


def _payload(*, mature_rna: bool = False):
    gene_product = (
        gene_product_value(
            "miR-124-3p", proposed_curie=None, entity_type="mature_miRNA",
            taxon_curie="NCBITaxon:10116",
        )
        if mature_rna
        else gene_product_value(
            "Cttn", proposed_curie="RGD:619839", entity_type="protein_coding_gene",
            taxon_curie="NCBITaxon:10116",
        )
    )
    return {
        "gene_product": gene_product,
        "go_term": go_term_value("binds", proposed_curie=None, aspect="molecular_function"),
        "evidence_code": evidence_code_value("IPI"),
        "reference_curie": reference_value("PMID:12345678", proposed_curie="PMID:12345678"),
        "with_from": [with_from_value("Ago2", proposed_curie=None)],
        "qualifiers": [qualifier_value("enables", aspect="molecular_function")],
        "annotation_extensions": [],
        "negated": False,
        "rationale": "The Results interaction assay directly supports protein binding.",
        "provider_context": {
            "provider_key": "RGD",
            "taxon_curie": "NCBITaxon:10116",
            "review_lane": "rgd_go_curator_review",
            "hierarchy_limitations": [],
            "section_limitations": [],
        },
        "blocking_reasons": (
            ["Mature product maps to more than one possible precursor locus."] if mature_rna else []
        ),
    }


def _candidate(*, mature_rna: bool = False, evidence_ids=None):
    retained_evidence_ids = ["go-evidence-1"] if evidence_ids is None else list(evidence_ids)
    return SimpleNamespace(
        candidate_id="go-candidate-1",
        staged_fields={
            "pending_ref_id": "go-recommendation-1",
            "payload": _payload(mature_rna=mature_rna),
            "evidence_record_ids": retained_evidence_ids,
        },
        pending_ref_ids=["go-recommendation-1"],
        evidence_record_ids=retained_evidence_ids,
    )


def _evidence():
    return {
        "evidence_record_id": "go-evidence-1",
        "entity": "Cttn",
        "verified_quote": "Cttn bound Ago2 in the interaction assay.",
        "page": 6,
        "section": "Results",
        "subsection": "Protein interaction assay",
        "chunk_id": "chunk-go-1",
        "document_id": "paper-go-1",
        "figure_reference": "Figure 2A",
        "pending_ref_id": "go-recommendation-1",
        "field_paths": ["gene_product", "go_term", "rationale"],
    }


def _materialize(candidate, *, evidence_records=None):
    return materialize_go_builder_state(
        workspace=_Workspace({candidate.candidate_id: candidate}),
        candidate_ids=[candidate.candidate_id],
        evidence_records=[_evidence()] if evidence_records is None else evidence_records,
    )


@pytest.fixture
def active_go_builder_context(monkeypatch):
    events = []
    monkeypatch.setattr(
        go_builder_tools,
        "write_extraction_trace_event",
        lambda **event: events.append(event) or event,
    )
    monkeypatch.setattr(
        builder,
        "write_extraction_trace_event",
        lambda **event: events.append(event) or event,
    )
    workspace = builder.ExtractionBuilderWorkspace(
        run_id="rgd-go-tool-run-1",
        document_id="paper-go-1",
        domain_pack_id="agr.alliance.go",
        agent_id="rgd_go_paper_curator",
    )
    builder_token = builder.set_active_extraction_builder_workspace(workspace)
    evidence_token = evidence_workspace.set_active_evidence_records([_evidence()])
    try:
        yield workspace, events
    finally:
        evidence_workspace.reset_active_evidence_records(evidence_token)
        builder.reset_active_extraction_builder_workspace(builder_token)


def _stage(**overrides):
    kwargs = {
        "pending_ref_id": "go-recommendation-1",
        "gene_product_mention": "Cttn",
        "gene_product_entity_type": "protein_coding_gene",
        "gene_product_taxon_curie": "NCBITaxon:10116",
        "gene_product_proposed_curie": "RGD:619839",
        "go_term_mention": "binds",
        "go_term_aspect": "molecular_function",
        "evidence_code": "IPI",
        "reference_mention": "Cortactin binds Argonaute in rat neurons",
        "reference_proposed_curie": "PMID:12345678",
        "rationale": "The Results interaction assay directly supports protein binding.",
        "evidence_record_ids": ["go-evidence-1"],
        "with_from": [{"mention": "Ago2"}],
        "qualifiers": ["enables"],
    }
    kwargs.update(overrides)
    return go_builder_tools._stage_go_recommendation_impl(**kwargs)


def _staged_payload(workspace, staged):
    return workspace.get_candidate(staged.data["candidate_id"]).staged_fields["payload"]


# --- The rule: extraction reads the paper and never searches ------------------


def test_go_builder_stages_searchable_values_unresolved_with_paper_wording_and_proposals(
    active_go_builder_context,
):
    workspace, _events = active_go_builder_context

    staged = _stage(with_from=[{"mention": "Ago2", "proposed_curie": "RGD:621255"}])

    assert staged.status == "ok", staged.model_dump(mode="json")
    payload = _staged_payload(workspace, staged)
    assert payload["gene_product"] == {
        "mention": "Cttn",
        "curie": None,
        "label": None,
        "proposed_curie": "RGD:619839",
        "entity_type": "protein_coding_gene",
        "taxon_curie": "NCBITaxon:10116",
        "resolution_state": "unresolved",
        "lookup_outcome": "not_validated",
        "validator_explanation": "Not validated yet.",
    }
    searchable = [
        payload["gene_product"], payload["go_term"], payload["reference_curie"], *payload["with_from"],
    ]
    assert {(value["resolution_state"], value["lookup_outcome"]) for value in searchable} == {
        ("unresolved", "not_validated")
    }
    assert payload["reference_curie"]["proposed_curie"] == "PMID:12345678"
    assert payload["with_from"][0]["proposed_curie"] == "RGD:621255"
    assert "existing_annotation_context" not in payload["provider_context"]
    assert "source_grounding" not in workspace.get_candidate(staged.data["candidate_id"]).staged_fields


def test_go_builder_maps_the_chosen_evidence_code_and_qualifiers_with_its_tables(
    active_go_builder_context,
):
    """Mapping from a fixed local table is not a search, so these stay resolved."""

    workspace, _events = active_go_builder_context

    payload = _staged_payload(workspace, _stage())

    assert (payload["evidence_code"]["code"], payload["evidence_code"]["eco_curie"]) == (
        "IPI", "ECO:0000353")
    assert payload["evidence_code"]["resolution_state"] == "resolved"
    assert (payload["qualifiers"][0]["name"], payload["qualifiers"][0]["resolution_state"]) == (
        "enables", "resolved")


def test_go_builder_never_accepts_an_identity_from_extraction():
    """The stage tool has no identity parameters: only paper wording and paper-stated proposals."""

    properties = go_builder_tools.stage_go_recommendation.params_json_schema["properties"]
    for removed in (
        "gene_product_curie", "gene_product_label", "go_term_curie", "go_term_label",
        "reference_curie", "existing_annotation_status", "existing_annotations",
        "existing_annotation_provenance", "identity_resolution", "resolution_state",
    ):
        assert removed not in properties
    assert {"gene_product_proposed_curie", "go_term_proposed_curie", "reference_proposed_curie"} <= set(properties)


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"gene_product_proposed_curie": "RGD-guess"}, "gene_product"),
        ({"go_term_proposed_curie": "GO:123"}, "go_term"),
        ({"with_from": [{"mention": "Ago2", "proposed_curie": "Ago2"}]}, "with_from"),
    ],
)
def test_a_paper_stated_identifier_must_be_written_as_the_paper_prints_it(
    active_go_builder_context, overrides, field,
):
    result = _stage(**overrides)

    assert result.status == "error"
    assert any(field in issue["field_path"] for issue in result.data["validation_issues"])


def test_an_unknown_code_or_relation_is_staged_unresolved_not_rejected(active_go_builder_context):
    workspace, _events = active_go_builder_context

    staged = _stage(evidence_code="TAS", qualifiers=["strongly required for", "involved_in"])

    assert staged.status == "ok"
    payload = _staged_payload(workspace, staged)
    assert (payload["evidence_code"]["mention"], payload["evidence_code"]["lookup_outcome"]) == (
        "TAS", "not_found")
    assert [(value["mention"], value["lookup_outcome"]) for value in payload["qualifiers"]] == [
        ("strongly required for", "not_found"),
        ("involved_in", "conflict"),
    ]


def test_a_mature_product_without_a_blocker_still_stages(active_go_builder_context):
    """Every gene product is unresolved at extraction, so a blocker is no longer required."""

    workspace, _events = active_go_builder_context

    staged = _stage(
        gene_product_mention="miR-124-3p", gene_product_entity_type="mature_miRNA",
        gene_product_proposed_curie=None,
    )

    assert staged.status == "ok"
    assert _staged_payload(workspace, staged)["gene_product"]["mention"] == "miR-124-3p"


@pytest.mark.parametrize(
    ("field_path", "value"),
    [
        ("gene_product", {"mention": "Cttn", "entity_type": "protein_coding_gene",
                          "taxon_curie": "NCBITaxon:10116", "curie": "RGD:619839"}),
        ("go_term", {"mention": "binds", "aspect": "molecular_function", "label": "protein binding"}),
        ("go_term", {"mention": "binds", "aspect": "molecular_function", "resolution_state": "resolved"}),
        ("with_from", [{"mention": "Ago2", "curie": "RGD:621255"}]),
    ],
)
def test_a_patch_never_carries_an_identity_or_a_state(active_go_builder_context, field_path, value):
    workspace, _events = active_go_builder_context
    staged = _stage()
    candidate_id = staged.data["candidate_id"]
    before = copy.deepcopy(workspace.get_candidate(candidate_id).staged_fields)

    result = go_builder_tools._patch_go_recommendation_impl(
        candidate_id, [{"field_path": field_path, "value": value}]
    )

    assert result.status == "error"
    assert result.data["validation_issues"][0]["reason"] == "validation_owned_field"
    assert workspace.get_candidate(candidate_id).staged_fields == before


def test_a_patch_restages_paper_wording_and_proposals_unresolved(active_go_builder_context):
    workspace, _events = active_go_builder_context
    staged = _stage()
    candidate_id = staged.data["candidate_id"]

    patched = go_builder_tools._patch_go_recommendation_impl(
        candidate_id,
        [
            {"field_path": "go_term", "value": {"mention": "nuclear localization",
                                                "aspect": "cellular_component",
                                                "proposed_curie": "GO:0005634"}},
            {"field_path": "qualifiers", "value": ["located_in"]},
            {"field_path": "evidence_code", "value": "IDA"},
        ],
    )

    assert patched.status == "ok", patched.model_dump(mode="json")
    payload = workspace.get_candidate(candidate_id).staged_fields["payload"]
    assert (payload["go_term"]["mention"], payload["go_term"]["proposed_curie"], payload["go_term"]["curie"]) == (
        "nuclear localization", "GO:0005634", None)
    assert (payload["qualifiers"][0]["mention"], payload["qualifiers"][0]["name"]) == (
        "located_in", "located_in")
    assert payload["evidence_code"]["eco_curie"] == "ECO:0000314"


def test_changing_the_aspect_maps_the_qualifiers_again(active_go_builder_context):
    workspace, _events = active_go_builder_context
    staged = _stage()
    candidate_id = staged.data["candidate_id"]

    go_builder_tools._patch_go_recommendation_impl(
        candidate_id,
        [{"field_path": "go_term", "value": {"mention": "neurite outgrowth",
                                             "aspect": "biological_process"}}],
    )

    qualifier = workspace.get_candidate(candidate_id).staged_fields["payload"]["qualifiers"][0]
    assert (qualifier["mention"], qualifier["lookup_outcome"]) == ("enables", "conflict")


# --- Finalization and materialization -----------------------------------------


def test_go_stage_and_finalize_tools_emit_typed_extraction_result(active_go_builder_context):
    workspace, _events = active_go_builder_context
    staged = _stage()
    assert staged.status == "ok", staged.model_dump(mode="json")

    finalized = go_builder_tools._finalize_go_extraction_impl([staged.data["candidate_id"]])

    assert finalized.status == "ok", finalized.model_dump(mode="json")
    payload = workspace.finalization.payload
    obj = payload["curatable_objects"][0]
    assert obj["object_type"] == "GOCuratableObject"
    assert obj["status"] == "needs_review"
    assert obj["evidence_record_ids"] == ["go-evidence-1"]
    assert obj["payload"]["gene_product"]["lookup_outcome"] == "not_validated"
    assert payload["metadata"]["evidence_records"][0]["section"] == "Results"


def test_go_builder_emits_complete_decision_lifecycle_events(active_go_builder_context):
    workspace, events = active_go_builder_context
    staged = _stage()
    candidate_id = staged.data["candidate_id"]

    go_builder_tools._patch_go_recommendation_impl(
        candidate_id, [{"field_path": "rationale", "value": "Updated rationale"}]
    )
    go_builder_tools._list_staged_go_recommendations_impl(False)
    go_builder_tools._find_staged_go_recommendations_impl(candidate_id=candidate_id)
    candidate = workspace.get_candidate(candidate_id)
    workspace.upsert_candidate(
        candidate_id="go-candidate-discard",
        staged_fields=copy.deepcopy(candidate.staged_fields),
        pending_ref_ids=["go-recommendation-discard"],
        evidence_record_ids=list(candidate.evidence_record_ids),
        status=candidate.status,
    )
    go_builder_tools._discard_go_recommendation_impl("go-candidate-discard", "Not retained")
    go_builder_tools._finalize_go_extraction_impl([candidate_id])

    event_types = {event["event_type"] for event in events}
    assert {
        "go_builder.patch_requested", "go_builder.patch_completed",
        "go_builder.discard_requested", "go_builder.discard_completed",
        "go_builder.list_requested", "go_builder.list_completed",
        "go_builder.find_requested", "go_builder.find_completed",
        "go_builder.finalize_requested", "go_builder.finalize_completed",
    } <= event_types


def test_go_builder_materializes_typed_evidence_backed_extraction_result():
    result = _materialize(_candidate())

    assert result.ok, result.issues
    obj = result.payload["curatable_objects"][0]
    assert obj["model_ref"] == "GOCuratableObjectPayload"
    assert obj["payload"]["gene_product"]["proposed_curie"] == "RGD:619839"
    assert obj["payload"]["gene_product"]["curie"] is None
    assert result.payload["metadata"]["evidence_records"][0]["figure_reference"] == "Figure 2A"
    assert result.payload["metadata"]["ambiguities"] == []


def test_mature_product_blocker_survives_typed_materialization():
    result = _materialize(_candidate(mature_rna=True))

    assert result.ok, result.issues
    obj = result.payload["curatable_objects"][0]
    assert obj["status"] == "needs_review"
    assert obj["payload"]["gene_product"]["lookup_outcome"] == "not_validated"
    assert result.payload["metadata"]["ambiguities"][0]["mention"] == "miR-124-3p"
    assert result.payload["run_summary"]["ambiguous_count"] == 1


@pytest.mark.parametrize(
    "value_path", ["gene_product", "go_term", "reference_curie", "with_from"]
)
def test_finalization_rejects_a_searchable_value_that_is_not_staged_for_validation(value_path):
    """Regression: a builder never marks a searched value resolved, and conversion enforces it."""

    identity = {
        "gene_product": ("Cttn", {"curie": "RGD:619839", "label": "Cttn"}),
        "go_term": ("binds", {"curie": "GO:0005515", "label": "protein binding"}),
        "reference_curie": ("PMID:12345678", {"curie": "AGRKB:101000000400377"}),
        "with_from": ("Ago2", {"curie": "RGD:621255"}),
    }[value_path]
    candidate = _candidate()
    payload = candidate.staged_fields["payload"]
    resolved = resolved_value(identity[0], identity[1])
    if isinstance(payload[value_path], list):
        payload[value_path] = [resolved]
    else:
        payload[value_path] = {**payload[value_path], **resolved}

    result = _materialize(candidate)

    assert not result.ok
    assert "extraction_value_not_staged_for_validation" in {issue["reason"] for issue in result.issues}


@pytest.mark.parametrize(
    ("value_path", "value"),
    [
        ("evidence_code", resolved_value("IPI", {"code": "IDA", "eco_curie": "ECO:0000314"})),
        ("qualifiers", [resolved_value("enables", {"name": "located_in"})]),
        ("evidence_code", resolved_value("TAS", {"code": "TAS", "eco_curie": "ECO:0000304"})),
    ],
)
def test_finalization_rejects_a_mapped_value_that_is_not_the_table_mapping(value_path, value):
    candidate = _candidate()
    candidate.staged_fields["payload"][value_path] = value

    result = _materialize(candidate)

    assert not result.ok
    assert "mapped_value_mismatch" in {issue["reason"] for issue in result.issues}


def test_finalization_rejects_a_malformed_paper_stated_identifier():
    candidate = _candidate()
    candidate.staged_fields["payload"]["go_term"]["proposed_curie"] = "GO:123"

    result = _materialize(candidate)

    assert ("payload.go_term.proposed_curie", "invalid_proposed_curie") in {
        (issue["field_path"], issue["reason"]) for issue in result.issues
    }


def test_go_builder_rejects_evidence_less_finalization():
    result = _materialize(_candidate(evidence_ids=[]), evidence_records=[])

    assert not result.ok
    assert {issue["reason"] for issue in result.issues} == {"missing_evidence_record_ids"}


def test_go_builder_rejects_finalization_without_rationale():
    candidate = _candidate()
    del candidate.staged_fields["payload"]["rationale"]

    result = _materialize(candidate)

    assert not result.ok
    assert {(issue["field_path"], issue["reason"]) for issue in result.issues} == {
        ("payload.rationale", "missing_rationale")
    }
    assert result.issues[0]["message"] == (
        "GO candidate is missing a rationale; patch the candidate with a rationale "
        "saying why you selected it."
    )


def test_go_builder_rejects_excluded_section_only_evidence():
    result = _materialize(_candidate(), evidence_records=[{**_evidence(), "section": "Discussion"}])

    assert not result.ok
    assert "out_of_scope_evidence" in {issue["reason"] for issue in result.issues}


@pytest.mark.parametrize(
    "section",
    ["Abstract", "1. Introduction", "6. Discussion and conclusions", "Results and Discussion"],
)
def test_go_builder_rejects_non_positive_and_combined_section_headings(section):
    result = _materialize(
        _candidate(),
        evidence_records=[{**_evidence(), "section": section, "figure_reference": None}],
    )

    assert not result.ok
    assert "out_of_scope_evidence" in {issue["reason"] for issue in result.issues}


@pytest.mark.parametrize("section", ["Results", "2. Materials and Methods"])
def test_go_builder_preserves_positive_results_and_methods_evidence(section):
    result = _materialize(
        _candidate(),
        evidence_records=[{**_evidence(), "section": section, "figure_reference": None}],
    )

    assert result.ok, result.issues


def test_go_builder_rejects_cross_candidate_or_unrelated_evidence_attachment():
    evidence = {**_evidence(), "pending_ref_id": "other-recommendation", "field_paths": ["unrelated_field"]}

    result = _materialize(_candidate(), evidence_records=[evidence])

    assert not result.ok
    assert "unattached_candidate_evidence" in {issue["reason"] for issue in result.issues}


def test_go_builder_accepts_canonical_envelope_target_attachment():
    evidence = {
        **_evidence(),
        "pending_ref_id": None,
        "field_paths": None,
        "envelope_targets": [{"pending_ref_id": "go-recommendation-1", "field_path": "go_term.mention"}],
    }

    result = _materialize(_candidate(), evidence_records=[evidence])

    assert result.ok, result.issues


# --- Rationale ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rationale", "message"),
    [("   ", "rationale must be non-empty"), ("", "rationale must be non-empty")],
)
def test_go_stage_rejects_blank_rationale(active_go_builder_context, rationale, message):
    result = _stage(rationale=rationale)

    assert result.status == "error"
    assert any(
        issue["field_path"] == "rationale" and message in issue["message"]
        for issue in result.data["validation_issues"]
    )


def test_go_stage_stores_stripped_rationale_without_a_length_cap(active_go_builder_context):
    workspace, _events = active_go_builder_context
    rationale = "y" * 2000

    staged = _stage(rationale=f"  {rationale}  ")

    assert staged.status == "ok"
    assert _staged_payload(workspace, staged)["rationale"] == rationale


@pytest.mark.parametrize(
    ("value", "message"),
    [
        (None, "rationale must be a non-empty string"),
        ("", "rationale must be non-empty"),
        ("  ", "rationale must be non-empty"),
    ],
)
def test_go_patch_cannot_clear_rationale(active_go_builder_context, value, message):
    workspace, _events = active_go_builder_context
    staged = _stage(rationale="The IPI assay binds Cttn directly, not a complex partner.")
    candidate_id = staged.data["candidate_id"]

    result = go_builder_tools._patch_go_recommendation_impl(
        candidate_id, [{"field_path": "rationale", "value": value}]
    )

    assert result.status == "error"
    assert result.data["validation_issues"] == [
        {"field_path": "rationale", "reason": "invalid_rationale", "message": mock.ANY}
    ]
    assert message in result.data["validation_issues"][0]["message"]
    payload = workspace.get_candidate(candidate_id).staged_fields["payload"]
    assert payload["rationale"] == "The IPI assay binds Cttn directly, not a complex partner."


def test_go_stage_tool_schema_carries_the_shared_rationale_description():
    from agr_ai_curation_alliance.tools.builder_rationale import RATIONALE_ARG_DESCRIPTION

    properties = go_builder_tools.stage_go_recommendation.params_json_schema["properties"]
    assert properties["rationale"]["description"] == RATIONALE_ARG_DESCRIPTION
    imp_rule = (
        "For IMP annotations, the rationale must name the perturbation and the phenotype "
        "in the paper's exact wording."
    )
    assert imp_rule in " ".join(go_builder_tools.stage_go_recommendation.description.split())
    assert RATIONALE_ARG_DESCRIPTION not in go_builder_tools.stage_go_recommendation.description
    patch_updates = go_builder_tools.patch_go_recommendation.params_json_schema["properties"]["updates"]
    assert "A `rationale` update must be non-empty; it cannot be cleared." in " ".join(
        patch_updates["description"].split()
    )


# --- Projection -------------------------------------------------------------------


def test_evidence_anchor_survives_result_reference_envelope_and_workspace_projection():
    result = _materialize(_candidate())
    assert result.ok, result.issues
    record = CurationExtractionResultRecord.model_validate(
        {
            "extraction_result_id": "11111111-1111-4111-8111-111111111111",
            "document_id": "22222222-2222-4222-8222-222222222222",
            "adapter_key": "go",
            "agent_key": "rgd_go_paper_curator",
            "source_kind": CurationExtractionSourceKind.CHAT,
            "origin_session_id": "session-rgd-go",
            "user_id": "rgd-curator",
            "candidate_count": 1,
            "payload_json": result.payload,
            "created_at": datetime.now(timezone.utc),
            "metadata": {},
        }
    )

    envelope = domain_envelope_from_extraction_result(record)
    anchors = project_evidence_anchor_projections(envelope, envelope_revision=1)
    assert {anchor.field_path for anchor in anchors} == {"gene_product", "go_term", "rationale"}
    assert {anchor.figure_reference for anchor in anchors} == {"Figure 2A"}

    metadata = load_domain_pack_metadata(
        REPO_ROOT / "packages" / "alliance" / "domain_packs" / "go" / "domain_pack.yaml"
    )
    rows = DomainPackMetadataReviewRowMaterializer(metadata).materialize(envelope, envelope_revision=1)
    fields = {
        field.field_key: field.value
        for field in workspace_pipeline._draft_fields_from_review_row(rows[0])
    }
    assert fields["rationale"] == "The Results interaction assay directly supports protein binding."
    assert fields["gene_product.mention"] == "Cttn"
    assert fields["gene_product.curie"] is None
    assert rows[0].metadata["evidence_record_ids"] == ["go-evidence-1"]
