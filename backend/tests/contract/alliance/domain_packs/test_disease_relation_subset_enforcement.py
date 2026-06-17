"""Contract: disease relation CV subset selection is wired per staged subject_type.

Proves the data-type-axis subset (Part A) flows through the real validator-dispatch
selector layer: the ``disease_relation_cv_lookup`` binding resolves a ``subset``
input from the staged ``subject_type`` via the ``payload_keyed_literal`` selector, so
the controlled_vocabulary validator restricts the Disease Relation lookup to the
subject-type subset (closing R2). The umbrella/full-vocabulary path is unchanged when
no subset is selected (unknown subject_type).
"""

from __future__ import annotations

from pathlib import Path

from src.lib.domain_packs.input_selectors import build_domain_validation_request
from src.lib.domain_packs.loader import load_domain_pack_metadata
from src.lib.domain_packs.registry import LoadedDomainPack
from src.lib.domain_packs.validation_registry import (
    DomainPackValidationRegistry,
    ValidationBindingState,
)
from src.schemas.domain_envelope import CuratableObjectEnvelope, DomainEnvelope

REPO_ROOT = Path(__file__).resolve().parents[5]
DISEASE_PACK_METADATA = (
    REPO_ROOT
    / "packages"
    / "alliance"
    / "domain_packs"
    / "disease"
    / "domain_pack.yaml"
)


def _disease_pack() -> LoadedDomainPack:
    metadata = load_domain_pack_metadata(DISEASE_PACK_METADATA)
    return LoadedDomainPack(
        pack_id=metadata.pack_id,
        display_name=metadata.display_name,
        version=metadata.version,
        pack_path=DISEASE_PACK_METADATA.parent,
        metadata_path=DISEASE_PACK_METADATA,
        metadata=metadata,
    )


def _annotation_envelope(*, object_type: str, subject_type: str | None, relation: str) -> DomainEnvelope:
    subject: dict = {
        "subject_identifier": "FB:FBgn0000108",
        "subject_label": "Appl",
    }
    if subject_type is not None:
        subject["subject_type"] = subject_type
    payload = {
        "mention": "Alzheimer's disease",
        "disease_relation_name": relation,
        "disease_annotation_subject": subject,
        "disease_annotation_object": {
            "curie": "DOID:10652",
            "name": "Alzheimer's disease",
        },
        "data_provider": {"abbreviation": "FB"},
    }
    return DomainEnvelope(
        envelope_id="disease-subset-env",
        domain_pack_id="agr.alliance.disease",
        extracted_objects=[
            CuratableObjectEnvelope(
                object_type=object_type,
                pending_ref_id="disease-annotation-1",
                payload=payload,
                metadata={},
                evidence_record_ids=[],
            )
        ],
        metadata={},
    )


def _relation_request_subset(envelope: DomainEnvelope):
    pack = _disease_pack()
    registry = DomainPackValidationRegistry.from_domain_pack(pack)
    matches = registry.match_bindings(envelope, states=[ValidationBindingState.ACTIVE])
    relation_matches = [
        match
        for match in matches
        if match.binding.binding_id == "disease_relation_cv_lookup"
    ]
    assert relation_matches, "disease_relation_cv_lookup did not match the envelope"
    result = build_domain_validation_request(relation_matches[0])
    assert result.findings == (), result.findings
    assert result.request is not None
    return result.request.selected_inputs


def test_agm_subject_selects_agm_disease_relation_subset():
    envelope = _annotation_envelope(
        object_type="AGMDiseaseAnnotation",
        subject_type="agm",
        relation="is_model_of",
    )
    selected = _relation_request_subset(envelope)
    assert selected["vocabulary"] == "Disease Relation"
    assert selected["subset"] == "AGM Disease Relation"
    assert selected["term_name"] == "is_model_of"


def test_gene_subject_selects_gene_union_subset():
    envelope = _annotation_envelope(
        object_type="GeneDiseaseAnnotation",
        subject_type="gene",
        relation="is_implicated_in",
    )
    selected = _relation_request_subset(envelope)
    assert selected["subset"] == [
        "Gene Disease Relation",
        "Via Orthology Disease Relation",
    ]


def test_allele_subject_selects_allele_disease_relation_subset():
    envelope = _annotation_envelope(
        object_type="AlleleDiseaseAnnotation",
        subject_type="allele",
        relation="is_implicated_in",
    )
    selected = _relation_request_subset(envelope)
    assert selected["subset"] == "Allele Disease Relation"


def test_unknown_subject_type_omits_subset_full_vocabulary():
    """Abstract DiseaseAnnotation with no subject_type -> no subset (umbrella unchanged)."""
    envelope = _annotation_envelope(
        object_type="DiseaseAnnotation",
        subject_type=None,
        relation="is_implicated_in",
    )
    selected = _relation_request_subset(envelope)
    assert "subset" not in selected
    assert selected["vocabulary"] == "Disease Relation"
    assert selected["term_name"] == "is_implicated_in"


def test_wrong_subtype_relation_still_dispatches_with_restrictive_subset():
    """A Gene subject carrying is_model_of (AGM-only) still selects the gene subset,
    so the subset-aware CV lookup will reject it (R2 enforcement at the source)."""
    envelope = _annotation_envelope(
        object_type="GeneDiseaseAnnotation",
        subject_type="gene",
        relation="is_model_of",
    )
    selected = _relation_request_subset(envelope)
    # The subset is the gene union, which does NOT contain is_model_of -> the CV
    # lookup restricted to this subset returns zero candidates (validator_unresolved),
    # where the umbrella vocabulary would have resolved it.
    assert selected["subset"] == [
        "Gene Disease Relation",
        "Via Orthology Disease Relation",
    ]
    assert selected["term_name"] == "is_model_of"
