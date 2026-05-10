"""Shared domain-envelope response helpers for TraceReview APIs."""

from typing import Any, Mapping

from ..analyzers.domain_envelopes import DomainEnvelopeTraceAnalyzer


def domain_envelope_response_views(
    trace_summary: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return full and compact domain-envelope views from TraceSummaryAnalyzer output."""
    domain_envelope = trace_summary["domain_envelope"]
    compact_domain_envelope = DomainEnvelopeTraceAnalyzer.compact(domain_envelope)
    return domain_envelope, compact_domain_envelope
