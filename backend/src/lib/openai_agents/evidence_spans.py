"""Backend import path for deterministic PDF evidence spans."""

from __future__ import annotations

from agr_ai_curation_runtime.evidence_spans import (
    EVIDENCE_SPAN_HASH_ALGORITHM,
    EVIDENCE_SPAN_HASH_LENGTH,
    EVIDENCE_SPAN_HASH_POLICY,
    EVIDENCE_SPANIZER_VERSION,
    EvidenceSpan,
    EvidenceSpanResolutionError,
    ParsedEvidenceSpanId,
    build_evidence_spans,
    parse_evidence_span_id,
    resolve_evidence_span_id,
)

__all__ = [
    "EVIDENCE_SPAN_HASH_ALGORITHM",
    "EVIDENCE_SPAN_HASH_LENGTH",
    "EVIDENCE_SPAN_HASH_POLICY",
    "EVIDENCE_SPANIZER_VERSION",
    "EvidenceSpan",
    "EvidenceSpanResolutionError",
    "ParsedEvidenceSpanId",
    "build_evidence_spans",
    "parse_evidence_span_id",
    "resolve_evidence_span_id",
]
