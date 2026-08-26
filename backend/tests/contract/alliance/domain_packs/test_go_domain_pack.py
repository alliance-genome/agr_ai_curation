"""Contract tests for the review-only Alliance GO domain pack."""

from __future__ import annotations

import sys
from pathlib import Path

from src.lib.curation_workspace import pipeline as workspace_pipeline
from src.lib.curation_workspace.adapter_registry import CurationAdapterRegistry
from src.lib.domain_packs.loader import (
    load_domain_fixture_pack,
    load_domain_pack_metadata,
)
from src.lib.domain_packs.materialization import (
    DomainPackMetadataReviewRowMaterializer,
    project_evidence_anchor_projections,
)


REPO_ROOT = Path(__file__).resolve().parents[5]
ALLIANCE_PYTHON_SRC = REPO_ROOT / "packages" / "alliance" / "python" / "src"
if str(ALLIANCE_PYTHON_SRC) not in sys.path:
    sys.path.insert(0, str(ALLIANCE_PYTHON_SRC))

from agr_ai_curation_alliance.curation_adapters import register_curation_adapters  # noqa: E402
from agr_ai_curation_alliance.domain_packs import (  # noqa: E402
    load_alliance_domain_pack_registry,
)


GO_PACK_DIR = REPO_ROOT / "packages" / "alliance" / "domain_packs" / "go"
GO_PACK_PATH = GO_PACK_DIR / "domain_pack.yaml"
GO_FIXTURE_PATH = GO_PACK_DIR / "fixtures" / "rgd_curator_review.yaml"


def _contracts():
    metadata = load_domain_pack_metadata(GO_PACK_PATH)
    fixtures = load_domain_fixture_pack(GO_FIXTURE_PATH)
    return metadata, fixtures


def test_go_pack_is_auto_discovered_and_review_only():
    registry = load_alliance_domain_pack_registry()
    pack = registry.get_pack("agr.alliance.go")

    assert pack is not None
    assert pack.metadata_path == GO_PACK_PATH
    assert pack.metadata.metadata["provider_neutral_payload"] is True

    object_definition = pack.metadata.object_definitions[0]
    assert object_definition.object_type == "GOCuratableObject"
    assert object_definition.metadata["confidence_policy"]["status"] == "omitted"
    assert "confidence" not in {
        field.field_path for field in object_definition.fields
    }
    assert object_definition.metadata["export_behavior"]["status"] == "unsupported"
    assert object_definition.metadata["submission_behavior"]["status"] == "unsupported"


def test_go_adapter_materializes_review_rows_without_export_or_submission():
    registry = CurationAdapterRegistry()
    register_curation_adapters(registry)

    pack = registry.get_domain_pack_by_id("agr.alliance.go")
    assert pack is not None
    assert registry.get_review_row_materializer_for_domain_pack("agr.alliance.go") is not None
    assert registry.get_candidate_normalizer("go") is not None
    assert all(adapter.adapter_key != "go" for adapter in registry.export_adapters())
    assert all(
        getattr(adapter, "adapter_key", None) != "go"
        for adapter in registry.submission_transport_adapters()
    )


def test_protein_fixture_survives_review_row_candidate_and_evidence_projection():
    metadata, fixtures = _contracts()
    envelope = fixtures.fixtures[0].envelope
    payload = envelope.extracted_objects[0].payload

    rows = DomainPackMetadataReviewRowMaterializer(metadata).materialize(
        envelope,
        envelope_revision=3,
    )
    assert len(rows) == 1
    row = rows[0]
    assert row.projection_type == "go_curator_review"
    assert row.display_label == "Lta"
    assert row.secondary_label == "extracellular space"
    assert row.validation_state == "clear"
    assert payload["resolution_state"] == "resolved"

    candidate_fields = {
        field.field_key: field.value
        for field in workspace_pipeline._draft_fields_from_review_row(row)
    }
    assert candidate_fields["gene_product.curie"] == "RGD:3020"
    assert candidate_fields["go_term.curie"] == "GO:0005615"
    assert candidate_fields["evidence_code"] == "IDA"
    assert candidate_fields["evidence_eco_curie"] == "ECO:0000314"
    assert candidate_fields["reference_curie"] == "AGRKB:101000000400377"
    assert candidate_fields["with_from"] == []
    assert candidate_fields["qualifiers"] == []
    assert candidate_fields["annotation_extensions"] == []
    assert candidate_fields["negated"] is False
    provider_context = candidate_fields["provider_context"]
    assert isinstance(provider_context, dict)
    assert provider_context["provider_key"] == "RGD"
    assert "confidence" not in payload

    anchors = project_evidence_anchor_projections(envelope, envelope_revision=3)
    assert len(anchors) == 3
    assert {anchor.field_path for anchor in anchors} == {
        "gene_product",
        "go_term",
        "evidence_code",
    }
    assert {anchor.quote for anchor in anchors} == {
        "Lta protein was detected in the extracellular fraction."
    }


def test_ambiguous_mature_mirna_remains_unresolved_and_blocking_in_projection():
    metadata, fixtures = _contracts()
    envelope = fixtures.fixtures[1].envelope
    payload = envelope.extracted_objects[0].payload

    assert payload["gene_product"]["mention"] == "rno-miR-21-5p"
    assert "curie" not in payload["gene_product"]
    assert payload["resolution_state"] == "unresolved"
    assert len(payload["blocking_reasons"]) == 2
    assert envelope.validation_findings[0].severity.value == "blocker"

    rows = DomainPackMetadataReviewRowMaterializer(metadata).materialize(
        envelope,
        envelope_revision=2,
    )
    row = rows[0]
    assert row.validation_state == "blocked"
    candidate_fields = {
        field.field_key: field.value
        for field in workspace_pipeline._draft_fields_from_review_row(row)
    }
    assert candidate_fields["gene_product.mention"] == "rno-miR-21-5p"
    assert candidate_fields["resolution_state"] == "unresolved"
    assert "gene_product.curie" in candidate_fields
    assert candidate_fields["gene_product.curie"] is None
    assert candidate_fields["blocking_reasons"] == payload["blocking_reasons"]
    provider_context = candidate_fields["provider_context"]
    assert isinstance(provider_context, dict)
    assert provider_context["provider_key"] == "RGD"

    anchors = project_evidence_anchor_projections(envelope, envelope_revision=2)
    assert len(anchors) == 3
    assert {anchor.field_path for anchor in anchors} == {
        "gene_product.mention",
        "go_term",
        "rationale",
    }
