"""Alliance package-owned curation adapter registrations."""

from __future__ import annotations

from src.lib.curation_adapters.reference import (
    REFERENCE_ADAPTER_KEY,
    ReferenceCandidateNormalizer,
)
from src.lib.curation_adapters.structured_payload import (
    StructuredPayloadCandidateNormalizer,
)
from src.lib.curation_workspace.export_adapters.json_bundle import JsonBundleExportAdapter


_STRUCTURED_ADAPTER_KEYS = (
    "allele",
    "chemical",
    "disease",
    "gene",
    "gene_expression",
    "phenotype",
)


def register_curation_adapters(registry) -> None:
    """Register the Alliance curation adapters needed by the shared workspace runtime."""

    structured_normalizer = StructuredPayloadCandidateNormalizer()
    for adapter_key in _STRUCTURED_ADAPTER_KEYS:
        registry.register_adapter(
            adapter_key=adapter_key,
            candidate_normalizer=structured_normalizer,
            export_adapter=JsonBundleExportAdapter(adapter_key=adapter_key),
        )

    registry.register_adapter(
        adapter_key=REFERENCE_ADAPTER_KEY,
        candidate_normalizer=ReferenceCandidateNormalizer(),
        export_adapter=JsonBundleExportAdapter(adapter_key=REFERENCE_ADAPTER_KEY),
    )
