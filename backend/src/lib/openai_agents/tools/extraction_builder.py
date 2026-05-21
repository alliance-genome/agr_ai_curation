"""Inline extraction-builder tools backed by run-scoped staging state."""

from __future__ import annotations

from typing import Any, Literal

from agents import function_tool

from src.lib.openai_agents.extraction_staging import (
    BuilderAmbiguityInput,
    BuilderExclusionInput,
    BuilderReferenceInput,
    finalize_allele_extraction_payload,
    finalize_extraction_builder_payload,
    stage_allele_paper_evidence_payload,
    stage_extraction_builder_payload,
)


@function_tool(
    name_override="stage_allele_paper_evidence",
    description_override=(
        "Stage one retained allele or variant finding after verifying supporting "
        "quotes with record_evidence. Submit exactly one allele mention per call "
        "with one or more verified evidence_record_ids. The backend validates the "
        "evidence IDs and later builds the AlleleExtractionResultEnvelope."
    ),
)
def stage_allele_paper_evidence(
    mention_text: str,
    evidence_record_ids: list[str],
    verified_quotes: list[str] | None = None,
    page: int | None = None,
    section: str | None = None,
    chunk_id: str | None = None,
    associated_gene_symbol: str | None = None,
    taxon_curie: str | None = None,
    normalized_hint: str | None = None,
    reference: BuilderReferenceInput | None = None,
    finding_notes: str | None = None,
    raw_mentions: list[str] | None = None,
) -> dict[str, Any]:
    """Stage one retained allele finding in current extraction builder state."""

    return stage_allele_paper_evidence_payload(
        {
            "mention_text": mention_text,
            "evidence_record_ids": evidence_record_ids,
            "verified_quotes": verified_quotes or [],
            "page": page,
            "section": section,
            "chunk_id": chunk_id,
            "associated_gene_symbol": associated_gene_symbol,
            "taxon_curie": taxon_curie,
            "normalized_hint": normalized_hint,
            "reference": reference.model_dump(mode="json") if reference else None,
            "finding_notes": finding_notes,
            "raw_mentions": raw_mentions or [],
        }
    )


@function_tool(
    name_override="finalize_allele_extraction",
    description_override=(
        "Finalize allele extraction exactly once after all retained findings have "
        "been staged. This means there are no more retained allele findings to "
        "stage. The backend builds the final AlleleExtractionResultEnvelope from "
        "staged state; the model should only return a small acknowledgment."
    ),
)
def finalize_allele_extraction(
    summary: str,
    candidate_count: int,
    kept_count: int,
    excluded_count: int,
    ambiguous_count: int,
    exclusions: list[BuilderExclusionInput] | None = None,
    ambiguities: list[BuilderAmbiguityInput] | None = None,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    """Finalize staged allele findings into backend curation output."""

    return finalize_allele_extraction_payload(
        {
            "summary": summary,
            "candidate_count": candidate_count,
            "kept_count": kept_count,
            "excluded_count": excluded_count,
            "ambiguous_count": ambiguous_count,
            "exclusions": [
                exclusion.model_dump(mode="json") for exclusion in (exclusions or [])
            ],
            "ambiguities": [
                ambiguity.model_dump(mode="json") for ambiguity in (ambiguities or [])
            ],
            "notes": notes or [],
        }
    )


@function_tool(
    name_override="stage_gene_mention_evidence",
    description_override=(
        "Stage one retained gene mention after verifying supporting quotes with "
        "record_evidence. Submit one paper mention and one or more verified "
        "evidence_record_ids with paper-supported species, taxon, provider, and "
        "identity-resolution context when available. The backend builds the "
        "GeneExtractionResultEnvelope."
    ),
)
def stage_gene_mention_evidence(
    mention: str,
    evidence_record_ids: list[str],
    identity_resolution_notes: list[str],
    confidence: Literal["high", "medium", "low"],
    species: str | None = None,
    taxon_hint: str | None = None,
    data_provider_hint: str | None = None,
    proposed_primary_external_id: str | None = None,
    proposed_gene_symbol: str | None = None,
    proposed_taxon: str | None = None,
    raw_mentions: list[str] | None = None,
) -> dict[str, Any]:
    """Stage one retained gene mention in current extraction builder state."""

    return stage_extraction_builder_payload(
        {
            "mention": mention,
            "evidence_record_ids": evidence_record_ids,
            "identity_resolution_notes": identity_resolution_notes,
            "confidence": confidence,
            "species": species,
            "taxon_hint": taxon_hint,
            "data_provider_hint": data_provider_hint,
            "proposed_primary_external_id": proposed_primary_external_id,
            "proposed_gene_symbol": proposed_gene_symbol,
            "proposed_taxon": proposed_taxon,
            "raw_mentions": raw_mentions or [],
        }
    )


@function_tool(
    name_override="finalize_gene_extraction",
    description_override=(
        "Finalize gene extraction exactly once after all retained gene mentions "
        "have been staged. The backend builds the final GeneExtractionResultEnvelope "
        "from staged state; the model should only return a small acknowledgment."
    ),
)
def finalize_gene_extraction(
    summary: str,
    candidate_count: int,
    kept_count: int,
    excluded_count: int,
    ambiguous_count: int,
    exclusions: list[BuilderExclusionInput] | None = None,
    ambiguities: list[BuilderAmbiguityInput] | None = None,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    """Finalize staged gene findings into backend curation output."""

    return finalize_extraction_builder_payload(
        {
            "summary": summary,
            "candidate_count": candidate_count,
            "kept_count": kept_count,
            "excluded_count": excluded_count,
            "ambiguous_count": ambiguous_count,
            "exclusions": [
                exclusion.model_dump(mode="json") for exclusion in (exclusions or [])
            ],
            "ambiguities": [
                ambiguity.model_dump(mode="json") for ambiguity in (ambiguities or [])
            ],
            "notes": notes or [],
        }
    )


@function_tool(
    name_override="stage_disease_assertion_evidence",
    description_override=(
        "Stage one retained disease assertion after verifying supporting quotes "
        "with record_evidence. Submit one disease mention with Disease Ontology "
        "label/optional CURIE hints, relation and data-provider selector context, "
        "and one or more verified evidence_record_ids. The backend builds the "
        "DiseaseExtractionResultEnvelope."
    ),
)
def stage_disease_assertion_evidence(
    mention: str,
    disease_name: str,
    disease_relation_name: str,
    data_provider_abbreviation: str,
    evidence_record_ids: list[str],
    role: Literal[
        "primary",
        "background",
        "comparative",
        "model_context",
        "unspecified",
    ],
    confidence: Literal["high", "medium", "low"],
    disease_curie: str | None = None,
    subject_type: Literal["gene", "allele", "agm", "unknown"] | None = None,
    subject_label: str | None = None,
    subject_identifier: str | None = None,
    condition_relation_type_name: str | None = None,
    condition_summary: str | None = None,
    condition_free_text: str | None = None,
    normalization_notes: list[str] | None = None,
    raw_mentions: list[str] | None = None,
) -> dict[str, Any]:
    """Stage one retained disease assertion in current extraction builder state."""

    return stage_extraction_builder_payload(
        {
            "mention": mention,
            "disease_name": disease_name,
            "disease_curie": disease_curie,
            "disease_relation_name": disease_relation_name,
            "data_provider_abbreviation": data_provider_abbreviation,
            "evidence_record_ids": evidence_record_ids,
            "role": role,
            "confidence": confidence,
            "subject_type": subject_type,
            "subject_label": subject_label,
            "subject_identifier": subject_identifier,
            "condition_relation_type_name": condition_relation_type_name,
            "condition_summary": condition_summary,
            "condition_free_text": condition_free_text,
            "normalization_notes": normalization_notes or [],
            "raw_mentions": raw_mentions or [],
        }
    )


@function_tool(
    name_override="finalize_disease_extraction",
    description_override=(
        "Finalize disease extraction exactly once after all retained disease "
        "assertions have been staged. The backend builds the final "
        "DiseaseExtractionResultEnvelope from staged state; the model should only "
        "return a small acknowledgment."
    ),
)
def finalize_disease_extraction(
    summary: str,
    candidate_count: int,
    kept_count: int,
    excluded_count: int,
    ambiguous_count: int,
    exclusions: list[BuilderExclusionInput] | None = None,
    ambiguities: list[BuilderAmbiguityInput] | None = None,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    """Finalize staged disease findings into backend curation output."""

    return finalize_extraction_builder_payload(
        {
            "summary": summary,
            "candidate_count": candidate_count,
            "kept_count": kept_count,
            "excluded_count": excluded_count,
            "ambiguous_count": ambiguous_count,
            "exclusions": [
                exclusion.model_dump(mode="json") for exclusion in (exclusions or [])
            ],
            "ambiguities": [
                ambiguity.model_dump(mode="json") for ambiguity in (ambiguities or [])
            ],
            "notes": notes or [],
        }
    )


@function_tool(
    name_override="stage_chemical_condition_evidence",
    description_override=(
        "Stage one retained chemical-condition finding after verifying "
        "supporting quotes with record_evidence. Submit one chemical mention "
        "with chemical label/optional CURIE hints, condition class/relation "
        "selector context, dose/timing text when available, and one or more "
        "verified evidence_record_ids. The backend builds the "
        "ChemicalExtractionResultEnvelope."
    ),
)
def stage_chemical_condition_evidence(
    source_chemical_mention: str,
    condition_chemical_name: str,
    evidence_record_ids: list[str],
    role: Literal[
        "treatment",
        "assay_reagent",
        "buffer",
        "control",
        "other",
        "unspecified",
    ],
    confidence: Literal["high", "medium", "low"],
    condition_relation_type_name: str = "has_condition",
    condition_class_name: str = "chemical treatment",
    condition_chemical_curie: str | None = None,
    condition_class_curie: str | None = None,
    condition_quantity: str | None = None,
    condition_free_text: str | None = None,
    condition_summary: str | None = None,
    timing: str | None = None,
    host_annotation_type: str | None = None,
    host_annotation_id: str | None = None,
    reference: BuilderReferenceInput | None = None,
    normalization_notes: list[str] | None = None,
    raw_mentions: list[str] | None = None,
) -> dict[str, Any]:
    """Stage one retained chemical-condition finding in builder state."""

    return stage_extraction_builder_payload(
        {
            "source_chemical_mention": source_chemical_mention,
            "condition_chemical_name": condition_chemical_name,
            "condition_chemical_curie": condition_chemical_curie,
            "condition_relation_type_name": condition_relation_type_name,
            "condition_class_name": condition_class_name,
            "condition_class_curie": condition_class_curie,
            "evidence_record_ids": evidence_record_ids,
            "role": role,
            "confidence": confidence,
            "condition_quantity": condition_quantity,
            "condition_free_text": condition_free_text,
            "condition_summary": condition_summary,
            "timing": timing,
            "host_annotation_type": host_annotation_type,
            "host_annotation_id": host_annotation_id,
            "reference": reference.model_dump(mode="json") if reference else None,
            "normalization_notes": normalization_notes or [],
            "raw_mentions": raw_mentions or [],
        }
    )


@function_tool(
    name_override="finalize_chemical_extraction",
    description_override=(
        "Finalize chemical extraction exactly once after all retained chemical "
        "conditions have been staged. The backend builds the final "
        "ChemicalExtractionResultEnvelope from staged state; the model should "
        "only return a small acknowledgment."
    ),
)
def finalize_chemical_extraction(
    summary: str,
    candidate_count: int,
    kept_count: int,
    excluded_count: int,
    ambiguous_count: int,
    exclusions: list[BuilderExclusionInput] | None = None,
    ambiguities: list[BuilderAmbiguityInput] | None = None,
    notes: list[str] | None = None,
) -> dict[str, Any]:
    """Finalize staged chemical-condition findings into curation output."""

    return finalize_extraction_builder_payload(
        {
            "summary": summary,
            "candidate_count": candidate_count,
            "kept_count": kept_count,
            "excluded_count": excluded_count,
            "ambiguous_count": ambiguous_count,
            "exclusions": [
                exclusion.model_dump(mode="json") for exclusion in (exclusions or [])
            ],
            "ambiguities": [
                ambiguity.model_dump(mode="json") for ambiguity in (ambiguities or [])
            ],
            "notes": notes or [],
        }
    )


__all__ = [
    "finalize_allele_extraction",
    "finalize_chemical_extraction",
    "finalize_disease_extraction",
    "finalize_gene_extraction",
    "stage_allele_paper_evidence",
    "stage_chemical_condition_evidence",
    "stage_disease_assertion_evidence",
    "stage_gene_mention_evidence",
]
