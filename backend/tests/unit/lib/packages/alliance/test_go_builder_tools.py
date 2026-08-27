"""Typed GO builder materialization and canonical projection coverage."""

from __future__ import annotations

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


def _materialized_payload(*, resolution_state: str = "resolved"):
    candidate = _candidate(resolution_state=resolution_state)
    result = materialize_go_builder_state(
        workspace=_Workspace({candidate.candidate_id: candidate}),
        candidate_ids=[candidate.candidate_id],
        evidence_records=[_evidence()],
    )
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
        identity_resolution={"status": "resolved", "candidate_count": 1},
    )
    assert staged.status == "ok"

    finalized = go_builder_tools._finalize_go_extraction_impl(
        [staged.data["candidate_id"]]
    )

    assert finalized.status == "ok"
    assert workspace.finalization is not None
    payload = workspace.finalization.payload
    assert payload["curatable_objects"][0]["object_type"] == "GOCuratableObject"
    assert payload["curatable_objects"][0]["evidence_record_ids"] == ["go-evidence-1"]
    assert payload["metadata"]["evidence_records"][0]["section"] == "Results"


def test_go_builder_rejects_evidence_less_finalization():
    candidate = _candidate(evidence_ids=[])
    result = materialize_go_builder_state(
        workspace=_Workspace({candidate.candidate_id: candidate}),
        candidate_ids=[candidate.candidate_id],
        evidence_records=[],
    )

    assert not result.ok
    assert {issue["reason"] for issue in result.issues} == {
        "missing_evidence_record_ids"
    }


def test_go_builder_rejects_excluded_section_only_evidence():
    candidate = _candidate()
    evidence = {**_evidence(), "section": "Discussion"}
    result = materialize_go_builder_state(
        workspace=_Workspace({candidate.candidate_id: candidate}),
        candidate_ids=[candidate.candidate_id],
        evidence_records=[evidence],
    )

    assert not result.ok
    assert "excluded_section_only_evidence" in {
        issue["reason"] for issue in result.issues
    }


def test_go_builder_rejects_invented_or_malformed_identifiers():
    candidate = _candidate()
    candidate.staged_fields["payload"]["gene_product"]["curie"] = "RGD-GUESSED"
    candidate.staged_fields["payload"]["go_term"]["curie"] = "GO:123"
    candidate.staged_fields["payload"]["evidence_eco_curie"] = "ECO:guess"
    candidate.staged_fields["payload"]["with_from"] = ["Ago2"]
    result = materialize_go_builder_state(
        workspace=_Workspace({candidate.candidate_id: candidate}),
        candidate_ids=[candidate.candidate_id],
        evidence_records=[_evidence()],
    )

    reasons = {issue["reason"] for issue in result.issues}
    assert {
        "invalid_rgd_gene_product_curie",
        "invalid_go_curie",
        "invalid_eco_curie",
        "invalid_with_from_curie",
    } <= reasons


def test_go_builder_requires_explicit_blocker_for_unresolved_identity():
    candidate = _candidate(resolution_state="unresolved")
    candidate.staged_fields["payload"]["blocking_reasons"] = []
    result = materialize_go_builder_state(
        workspace=_Workspace({candidate.candidate_id: candidate}),
        candidate_ids=[candidate.candidate_id],
        evidence_records=[_evidence()],
    )

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
