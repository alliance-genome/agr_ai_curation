"""Inline extraction-builder tools backed by run-scoped staging state."""

from __future__ import annotations

from typing import Any

from agents import function_tool

from src.lib.openai_agents.extraction_staging import (
    BuilderAmbiguityInput,
    BuilderExclusionInput,
    BuilderReferenceInput,
    finalize_allele_extraction_payload,
    stage_allele_paper_evidence_payload,
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


__all__ = [
    "finalize_allele_extraction",
    "stage_allele_paper_evidence",
]
