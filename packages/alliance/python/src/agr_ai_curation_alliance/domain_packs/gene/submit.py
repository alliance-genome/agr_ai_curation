"""Submission planning for non-mutating gene mention evidence payloads."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from src.schemas.domain_envelope import DomainEnvelope

from .export import build_gene_mention_evidence_export


def build_gene_mention_evidence_submission_plan(
    envelope: DomainEnvelope,
    *,
    selected_object_ids: Sequence[str] | None = None,
) -> dict[str, Any]:
    """Return the submission plan for verified gene mention evidence.

    The plan intentionally has no curation DB write operations. It carries
    verified reference/evidence data downstream and records that no base
    ``gene`` row or paper-gene association is mutated by this pack.
    """

    export_payload = build_gene_mention_evidence_export(
        envelope,
        selected_object_ids=selected_object_ids,
    )
    return {
        "status": "ready",
        "submission_kind": "validated_reference_evidence",
        "record_count": len(export_payload["records"]),
        "write_targets": [],
        "blocked_targets": [],
        "mutations": {
            "public.gene": False,
            "paper_gene_association": False,
        },
        "export": export_payload,
    }


__all__ = ["build_gene_mention_evidence_submission_plan"]
