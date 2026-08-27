"""Typed GO builder materialization and canonical projection coverage."""

from __future__ import annotations

import copy
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

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
from src.schemas.curation_workspace import (
    CurationExtractionResultRecord,
    CurationExtractionSourceKind,
)
from src.lib.openai_agents import extraction_builder_workspace as builder
from src.lib.openai_agents import resolver_call_ledger
from src.lib.openai_agents.tools import evidence_workspace


REPO_ROOT = Path(__file__).resolve().parents[6]
ALLIANCE_PYTHON_SRC = REPO_ROOT / "packages" / "alliance" / "python" / "src"
if str(ALLIANCE_PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(ALLIANCE_PYTHON_SRC))

from agr_ai_curation_alliance.domain_packs.go import (  # noqa: E402
    materialize_go_builder_state,
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


def _candidate(*, resolution_state: str = "resolved", evidence_ids=None):
    retained_evidence_ids = (
        ["go-evidence-1"] if evidence_ids is None else list(evidence_ids)
    )
    gene_product = {
        "mention": "Cttn",
        "label": "Cttn",
        "entity_type": "protein_coding_gene",
        "taxon_curie": "NCBITaxon:10116",
    }
    blockers = []
    if resolution_state == "resolved":
        gene_product["curie"] = "RGD:619839"
    else:
        gene_product.update(
            {
                "mention": "miR-124-3p",
                "label": "miR-124-3p",
                "entity_type": "mature_miRNA",
            }
        )
        blockers = ["Mature product maps to more than one possible precursor locus."]
    payload = {
        "gene_product": gene_product,
        "go_term": {
            "curie": "GO:0005515",
            "label": "protein binding",
            "aspect": "molecular_function",
        },
        "evidence_code": "IPI",
        "evidence_eco_curie": "ECO:0000353",
        "reference_curie": "AGRKB:101000000400377",
        "with_from": ["RGD:621255"],
        "qualifiers": [],
        "annotation_extensions": [],
        "negated": False,
        "rationale": "The Results interaction assay directly supports protein binding.",
        "provider_context": {
            "provider_key": "RGD",
            "taxon_curie": "NCBITaxon:10116",
            "review_lane": "rgd_go_curator_review",
            "existing_annotation_context": {
                "status": "available",
                "annotations": [],
                "provenance": {
                    "source": "GO Consortium API",
                    "request_gene_id": "RGD:619839",
                },
            },
            "identity_resolution": {"status": resolution_state},
            "hierarchy_limitations": [],
            "section_limitations": [],
        },
        "resolution_state": resolution_state,
        "blocking_reasons": blockers,
    }
    return SimpleNamespace(
        candidate_id="go-candidate-1",
        staged_fields={
            "pending_ref_id": "go-recommendation-1",
            "payload": payload,
            "evidence_record_ids": retained_evidence_ids,
        },
        pending_ref_ids=["go-recommendation-1"],
        evidence_record_ids=retained_evidence_ids,
        resolver_selection_refs=[],
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


def _ground_candidate(candidate, ledger):
    payload = candidate.staged_fields["payload"]
    outputs = {
        "call-identity": (
            "resolve_gene_product",
            {
                "gene_product": {
                    "mention": payload["gene_product"]["mention"],
                    "curie": payload["gene_product"].get("curie"),
                },
                "resolution": payload["provider_context"]["identity_resolution"],
            },
        ),
        "call-term": (
            "quickgo_api_call",
            {
                "results": [payload["go_term"]],
                "evidence": {
                    "code": payload["evidence_code"],
                    "eco_curie": payload["evidence_eco_curie"],
                },
            },
        ),
        "call-annotations": (
            "go_api_call",
            {
                "query_gene_id": payload["gene_product"].get("curie"),
                "annotations": payload["provider_context"][
                    "existing_annotation_context"
                ]["annotations"],
                "provenance": payload["provider_context"][
                    "existing_annotation_context"
                ]["provenance"],
                "with_from": payload["with_from"],
            },
        ),
        "call-reference": (
            "agr_literature_reference_lookup",
            {"match": {"reference_curie": payload["reference_curie"]}},
        ),
    }
    for call_id, (tool_name, output) in outputs.items():
        ledger.record_tool_output(
            tool_call_id=call_id, tool_name=tool_name, output=output
        )
    requirements = go_builder_tools._grounding_requirements(payload)
    candidate.staged_fields["source_grounding"] = {
        "payload": copy.deepcopy(payload),
        "requirements": requirements,
    }
    candidate.resolver_selection_refs = list(outputs)
    return candidate


def _materialize(candidate, *, evidence_records=None, ground=True):
    ledger = resolver_call_ledger.ResolverCallLedger(trace_id="rgd-go-test")
    if ground:
        _ground_candidate(candidate, ledger)
    return materialize_go_builder_state(
        workspace=_Workspace({candidate.candidate_id: candidate}),
        candidate_ids=[candidate.candidate_id],
        evidence_records=[_evidence()]
        if evidence_records is None
        else evidence_records,
        resolver_entry_lookup=ledger.get_tool_output,
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
    seed_candidate = _candidate()
    ledger = resolver_call_ledger.ResolverCallLedger(trace_id=workspace.run_id)
    _ground_candidate(seed_candidate, ledger)
    builder_token = builder.set_active_extraction_builder_workspace(workspace)
    ledger_token = resolver_call_ledger.set_active_resolver_call_ledger(ledger)
    evidence_token = evidence_workspace.set_active_evidence_records([_evidence()])
    try:
        yield workspace, events
    finally:
        evidence_workspace.reset_active_evidence_records(evidence_token)
        resolver_call_ledger.reset_active_resolver_call_ledger(ledger_token)
        builder.reset_active_extraction_builder_workspace(builder_token)


def _materialized_payload(*, resolution_state: str = "resolved"):
    candidate = _candidate(resolution_state=resolution_state)
    result = _materialize(candidate)
    assert result.ok, result.issues
    assert result.payload is not None
    return result.payload


def test_go_builder_materializes_typed_evidence_backed_extraction_result():
    payload = _materialized_payload()

    obj = payload["curatable_objects"][0]
    assert obj["object_type"] == "GOCuratableObject"
    assert obj["model_ref"] == "GOCuratableObjectPayload"
    assert obj["payload"]["gene_product"]["curie"] == "RGD:619839"
    assert (
        obj["payload"]["provider_context"]["existing_annotation_context"]["status"]
        == "available"
    )
    assert obj["evidence_record_ids"] == ["go-evidence-1"]
    assert payload["metadata"]["evidence_records"][0]["figure_reference"] == "Figure 2A"


def test_go_stage_and_finalize_tools_emit_typed_extraction_result(
    active_go_builder_context,
):
    workspace, _events = active_go_builder_context
    staged = go_builder_tools._stage_go_recommendation_impl(
        pending_ref_id="go-recommendation-1",
        gene_product_mention="Cttn",
        gene_product_label="Cttn",
        gene_product_entity_type="protein_coding_gene",
        gene_product_taxon_curie="NCBITaxon:10116",
        gene_product_curie="RGD:619839",
        resolution_state="resolved",
        go_term_curie="GO:0005515",
        go_term_label="protein binding",
        go_term_aspect="molecular_function",
        evidence_code="IPI",
        evidence_eco_curie="ECO:0000353",
        reference_curie="AGRKB:101000000400377",
        rationale="The Results interaction assay directly supports protein binding.",
        evidence_record_ids=["go-evidence-1"],
        with_from=["RGD:621255"],
        existing_annotation_status="available",
        existing_annotations=[],
        existing_annotation_provenance={
            "source": "GO Consortium API",
            "request_gene_id": "RGD:619839",
        },
        identity_resolution={"status": "resolved"},
    )
    assert staged.status == "ok", staged.model_dump(mode="json")

    finalized = go_builder_tools._finalize_go_extraction_impl(
        [staged.data["candidate_id"]]
    )

    assert finalized.status == "ok"
    assert workspace.finalization is not None
    payload = workspace.finalization.payload
    assert payload["curatable_objects"][0]["object_type"] == "GOCuratableObject"
    assert payload["curatable_objects"][0]["evidence_record_ids"] == ["go-evidence-1"]
    assert payload["metadata"]["evidence_records"][0]["section"] == "Results"


def test_reference_curie_grounding_requires_literature_lookup():
    requirements = go_builder_tools._grounding_requirements(
        _candidate().staged_fields["payload"]
    )

    reference_requirements = [
        requirement
        for requirement in requirements
        if requirement["field_path"] == "reference_curie"
    ]
    assert reference_requirements == [
        {
            "field_path": "reference_curie",
            "tool_names": ["agr_literature_reference_lookup"],
            "value": "AGRKB:101000000400377",
        }
    ]


def test_go_builder_emits_complete_decision_lifecycle_events(
    active_go_builder_context,
):
    workspace, events = active_go_builder_context
    staged = go_builder_tools._stage_go_recommendation_impl(
        pending_ref_id="go-recommendation-1",
        gene_product_mention="Cttn",
        gene_product_label="Cttn",
        gene_product_entity_type="protein_coding_gene",
        gene_product_taxon_curie="NCBITaxon:10116",
        gene_product_curie="RGD:619839",
        resolution_state="resolved",
        go_term_curie="GO:0005515",
        go_term_label="protein binding",
        go_term_aspect="molecular_function",
        evidence_code="IPI",
        evidence_eco_curie="ECO:0000353",
        reference_curie="AGRKB:101000000400377",
        rationale="The Results interaction assay directly supports protein binding.",
        evidence_record_ids=["go-evidence-1"],
        with_from=["RGD:621255"],
        existing_annotation_status="available",
        existing_annotations=[],
        existing_annotation_provenance={
            "source": "GO Consortium API",
            "request_gene_id": "RGD:619839",
        },
        identity_resolution={"status": "resolved"},
    )
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
        resolver_selection_refs=list(candidate.resolver_selection_refs),
        status=candidate.status,
    )
    go_builder_tools._discard_go_recommendation_impl(
        "go-candidate-discard", "Not retained"
    )
    go_builder_tools._finalize_go_extraction_impl([candidate_id])

    event_types = {event["event_type"] for event in events}
    assert {
        "go_builder.patch_requested",
        "go_builder.patch_completed",
        "go_builder.discard_requested",
        "go_builder.discard_completed",
        "go_builder.list_requested",
        "go_builder.list_completed",
        "go_builder.find_requested",
        "go_builder.find_completed",
        "go_builder.finalize_requested",
        "go_builder.finalize_completed",
    } <= event_types


def test_go_builder_rejects_evidence_less_finalization():
    candidate = _candidate(evidence_ids=[])
    result = _materialize(candidate, evidence_records=[])

    assert not result.ok
    assert {issue["reason"] for issue in result.issues} == {
        "missing_evidence_record_ids"
    }


def test_go_builder_rejects_excluded_section_only_evidence():
    candidate = _candidate()
    evidence = {**_evidence(), "section": "Discussion"}
    result = _materialize(candidate, evidence_records=[evidence])

    assert not result.ok
    assert "out_of_scope_evidence" in {issue["reason"] for issue in result.issues}


@pytest.mark.parametrize(
    "section",
    [
        "Abstract",
        "1. Introduction",
        "6. Discussion and conclusions",
        "Results and Discussion",
    ],
)
def test_go_builder_rejects_non_positive_and_combined_section_headings(section):
    result = _materialize(
        _candidate(),
        evidence_records=[
            {**_evidence(), "section": section, "figure_reference": None}
        ],
    )

    assert not result.ok
    assert "out_of_scope_evidence" in {issue["reason"] for issue in result.issues}


@pytest.mark.parametrize("section", ["Results", "2. Materials and Methods"])
def test_go_builder_preserves_positive_results_and_methods_evidence(section):
    result = _materialize(
        _candidate(),
        evidence_records=[
            {**_evidence(), "section": section, "figure_reference": None}
        ],
    )

    assert result.ok, result.issues


def test_go_builder_rejects_cross_candidate_or_unrelated_evidence_attachment():
    evidence = {
        **_evidence(),
        "pending_ref_id": "other-recommendation",
        "field_paths": ["unrelated_field"],
    }
    result = _materialize(_candidate(), evidence_records=[evidence])

    assert not result.ok
    assert "unattached_candidate_evidence" in {
        issue["reason"] for issue in result.issues
    }


def test_go_builder_accepts_canonical_envelope_target_attachment():
    evidence = {
        **_evidence(),
        "pending_ref_id": None,
        "field_paths": None,
        "envelope_targets": [
            {
                "pending_ref_id": "go-recommendation-1",
                "field_path": "go_term.curie",
            }
        ],
    }

    result = _materialize(_candidate(), evidence_records=[evidence])

    assert result.ok, result.issues


def test_go_builder_rejects_invented_or_malformed_identifiers():
    candidate = _candidate()
    candidate.staged_fields["payload"]["gene_product"]["curie"] = "RGD-GUESSED"
    candidate.staged_fields["payload"]["go_term"]["curie"] = "GO:123"
    candidate.staged_fields["payload"]["evidence_eco_curie"] = "ECO:guess"
    candidate.staged_fields["payload"]["with_from"] = ["Ago2"]
    result = _materialize(candidate)

    reasons = {issue["reason"] for issue in result.issues}
    assert {
        "invalid_rgd_gene_product_curie",
        "invalid_go_curie",
        "invalid_eco_curie",
        "invalid_with_from_curie",
    } <= reasons


@pytest.mark.parametrize(
    ("field_path", "invented_value"),
    [
        ("gene_product.curie", "RGD:999999999"),
        ("go_term.curie", "GO:9999999"),
        ("evidence_eco_curie", "ECO:9999999"),
        ("reference_curie", "PMID:99999999"),
        ("with_from", ["RGD:999999998"]),
        (
            "provider_context.existing_annotation_context.provenance",
            {"source": "invented source", "request_gene_id": "RGD:619839"},
        ),
    ],
)
def test_go_builder_rejects_well_formed_values_not_present_in_tool_outputs(
    field_path, invented_value
):
    candidate = _candidate()
    ledger = resolver_call_ledger.ResolverCallLedger(trace_id="rgd-go-grounding")
    _ground_candidate(candidate, ledger)
    target = candidate.staged_fields["payload"]
    parts = field_path.split(".")
    for part in parts[:-1]:
        target = target[part]
    target[parts[-1]] = invented_value

    result = materialize_go_builder_state(
        workspace=_Workspace({candidate.candidate_id: candidate}),
        candidate_ids=[candidate.candidate_id],
        evidence_records=[_evidence()],
        resolver_entry_lookup=ledger.get_tool_output,
    )

    assert not result.ok
    assert "stale_or_missing_source_grounding" in {
        issue["reason"] for issue in result.issues
    }


def test_go_stage_rejects_well_formed_unobserved_gene_identity(
    active_go_builder_context,
):
    result = go_builder_tools._stage_go_recommendation_impl(
        pending_ref_id="go-recommendation-invented",
        gene_product_mention="Cttn",
        gene_product_label="Cttn",
        gene_product_entity_type="protein_coding_gene",
        gene_product_taxon_curie="NCBITaxon:10116",
        gene_product_curie="RGD:999999999",
        resolution_state="resolved",
        go_term_curie="GO:0005515",
        go_term_label="protein binding",
        go_term_aspect="molecular_function",
        evidence_code="IPI",
        evidence_eco_curie="ECO:0000353",
        reference_curie="AGRKB:101000000400377",
        rationale="The Results interaction assay directly supports protein binding.",
        evidence_record_ids=["go-evidence-1"],
        with_from=["RGD:621255"],
        existing_annotation_status="available",
        existing_annotation_provenance={
            "source": "GO Consortium API",
            "request_gene_id": "RGD:999999999",
        },
        identity_resolution={"status": "resolved"},
    )

    assert result.status == "error"
    assert "unobserved_tool_value" in {
        issue["reason"] for issue in result.data["validation_issues"]
    }


def test_go_builder_requires_explicit_blocker_for_unresolved_identity():
    candidate = _candidate(resolution_state="unresolved")
    candidate.staged_fields["payload"]["blocking_reasons"] = []
    result = _materialize(candidate)

    assert not result.ok
    assert "unresolved_identity_missing_blocker" in {
        issue["reason"] for issue in result.issues
    }


def test_mature_product_ambiguity_survives_typed_materialization():
    payload = _materialized_payload(resolution_state="unresolved")
    obj = payload["curatable_objects"][0]

    assert obj["status"] == "needs_review"
    assert "curie" not in obj["payload"]["gene_product"]
    assert obj["payload"]["resolution_state"] == "unresolved"
    assert obj["payload"]["blocking_reasons"]
    assert payload["metadata"]["ambiguities"][0]["mention"] == "miR-124-3p"


def test_unresolved_mature_product_does_not_require_existing_annotation_lookup():
    candidate = _candidate(resolution_state="unresolved")

    requirements = go_builder_tools._grounding_requirements(
        candidate.staged_fields["payload"]
    )

    assert "provider_context.existing_annotation_context" not in {
        requirement["field_path"] for requirement in requirements
    }


def test_evidence_anchor_survives_result_reference_envelope_and_workspace_projection():
    payload = _materialized_payload()
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
            "payload_json": payload,
            "created_at": datetime.now(timezone.utc),
            "metadata": {},
        }
    )

    envelope = domain_envelope_from_extraction_result(record)
    assert envelope.envelope_id == (
        "extraction-result:11111111-1111-4111-8111-111111111111"
    )
    assert envelope.metadata["source_extraction_result_id"] == (
        "11111111-1111-4111-8111-111111111111"
    )
    anchors = project_evidence_anchor_projections(envelope, envelope_revision=1)
    assert {anchor.field_path for anchor in anchors} == {
        "gene_product",
        "go_term",
        "rationale",
    }
    assert {anchor.quote for anchor in anchors} == {
        "Cttn bound Ago2 in the interaction assay."
    }
    assert {anchor.figure_reference for anchor in anchors} == {"Figure 2A"}

    metadata = load_domain_pack_metadata(
        REPO_ROOT / "packages" / "alliance" / "domain_packs" / "go" / "domain_pack.yaml"
    )
    rows = DomainPackMetadataReviewRowMaterializer(metadata).materialize(
        envelope, envelope_revision=1
    )
    fields = {
        field.field_key: field.value
        for field in workspace_pipeline._draft_fields_from_review_row(rows[0])
    }
    assert fields["rationale"] == (
        "The Results interaction assay directly supports protein binding."
    )
    assert rows[0].metadata["evidence_record_ids"] == ["go-evidence-1"]
