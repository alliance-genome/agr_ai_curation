"""Alliance package-owned curation adapter registrations."""

from __future__ import annotations

from agr_ai_curation_alliance.domain_packs.allele import AllelePaperEvidenceExportAdapter
from agr_ai_curation_alliance.domain_packs.chemical_condition import (
    ChemicalConditionExportAdapter,
    ChemicalConditionSubmissionBlockerAdapter,
)
from agr_ai_curation_alliance.domain_packs.disease import (
    DiseaseAnnotationExportAdapter,
    DiseaseAnnotationSubmissionBlockerAdapter,
)
from agr_ai_curation_alliance.domain_packs.gene import GeneMentionEvidenceExportAdapter
from agr_ai_curation_alliance.domain_packs.loader import get_alliance_domain_pack
from agr_ai_curation_alliance.domain_packs.phenotype import (
    PhenotypeAnnotationExportAdapter,
    PhenotypeAnnotationSubmissionBlockerAdapter,
)
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
_DOMAIN_EXPORT_ADAPTERS = {
    "allele": AllelePaperEvidenceExportAdapter,
    "chemical": ChemicalConditionExportAdapter,
    "disease": DiseaseAnnotationExportAdapter,
    "gene": GeneMentionEvidenceExportAdapter,
    "gene_expression": JsonBundleExportAdapter,
    "phenotype": PhenotypeAnnotationExportAdapter,
}
_DOMAIN_SUBMISSION_BLOCKERS = {
    "chemical": ChemicalConditionSubmissionBlockerAdapter,
    "disease": DiseaseAnnotationSubmissionBlockerAdapter,
    "phenotype": PhenotypeAnnotationSubmissionBlockerAdapter,
}


def register_curation_adapters(registry) -> None:
    """Register the Alliance curation adapters needed by the shared workspace runtime."""

    structured_normalizer = StructuredPayloadCandidateNormalizer()
    for adapter_key, domain_pack_id in _STRUCTURED_ADAPTER_DOMAIN_PACKS.items():
        domain_pack = get_alliance_domain_pack(domain_pack_id)
        export_adapter_factory = _DOMAIN_EXPORT_ADAPTERS[adapter_key]
        submission_blocker_factory = _DOMAIN_SUBMISSION_BLOCKERS.get(adapter_key)
        registry.register_adapter(
            adapter_key=adapter_key,
            candidate_normalizer=structured_normalizer,
            export_adapter=export_adapter_factory(adapter_key=adapter_key),
            submission_transport_adapters=(
                (submission_blocker_factory(),)
                if submission_blocker_factory is not None
                else ()
            ),
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
