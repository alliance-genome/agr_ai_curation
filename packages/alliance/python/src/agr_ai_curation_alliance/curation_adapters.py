"""Alliance package-owned curation adapter registrations."""

from __future__ import annotations

from agr_ai_curation_alliance.domain_packs.loader import get_alliance_domain_pack
from src.lib.curation_adapters.reference import (
    REFERENCE_ADAPTER_KEY,
    ReferenceCandidateNormalizer,
)
from src.lib.curation_adapters.structured_payload import (
    StructuredPayloadCandidateNormalizer,
)
from src.lib.domain_packs.materialization import (
    DomainPackMetadataReviewRowMaterializer,
)
from src.lib.curation_workspace.export_adapters.json_bundle import JsonBundleExportAdapter


_STRUCTURED_ADAPTER_DOMAIN_PACKS = {
    "allele": "agr.alliance.allele",
    "chemical": "agr.alliance.chemical_condition",
    "disease": "agr.alliance.disease",
    "gene": "gene",
    "gene_expression": "agr.alliance.gene_expression",
    "phenotype": "agr.alliance.phenotype",
}


def register_curation_adapters(registry) -> None:
    """Register the Alliance curation adapters needed by the shared workspace runtime."""

    structured_normalizer = StructuredPayloadCandidateNormalizer()
    for adapter_key, domain_pack_id in _STRUCTURED_ADAPTER_DOMAIN_PACKS.items():
        domain_pack = get_alliance_domain_pack(domain_pack_id)
        registry.register_adapter(
            adapter_key=adapter_key,
            candidate_normalizer=structured_normalizer,
            export_adapter=JsonBundleExportAdapter(adapter_key=adapter_key),
            domain_pack=domain_pack,
            review_row_materializer=DomainPackMetadataReviewRowMaterializer(
                metadata=domain_pack.metadata,
            ),
        )

    registry.register_adapter(
        adapter_key=REFERENCE_ADAPTER_KEY,
        candidate_normalizer=ReferenceCandidateNormalizer(),
        export_adapter=JsonBundleExportAdapter(adapter_key=REFERENCE_ADAPTER_KEY),
    )
