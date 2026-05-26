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
from agr_ai_curation_alliance.domain_packs.gene_expression import (
    GeneExpressionExportAdapter,
    GeneExpressionSubmissionAdapter,
    validate_pending_gene_expression_envelope,
)
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
    "gene_expression": GeneExpressionExportAdapter,
    "phenotype": PhenotypeAnnotationExportAdapter,
}
_DOMAIN_SUBMISSION_TRANSPORTS = {
    "chemical": ChemicalConditionSubmissionBlockerAdapter,
    "disease": DiseaseAnnotationSubmissionBlockerAdapter,
    "gene_expression": GeneExpressionSubmissionAdapter,
    "phenotype": PhenotypeAnnotationSubmissionBlockerAdapter,
}
_DOMAIN_ENVELOPE_VALIDATORS = {
    "gene_expression": validate_pending_gene_expression_envelope,
}


def register_curation_adapters(registry) -> None:
    """Register the Alliance curation adapters needed by the shared workspace runtime."""

    structured_normalizer = StructuredPayloadCandidateNormalizer()
    for adapter_key, domain_pack_id in _STRUCTURED_ADAPTER_DOMAIN_PACKS.items():
        domain_pack = get_alliance_domain_pack(domain_pack_id)
        export_adapter = _export_adapter_for(adapter_key)
        submission_transport_adapters = _submission_transport_adapters_for(adapter_key)
        registry.register_adapter(
            adapter_key=adapter_key,
            candidate_normalizer=structured_normalizer,
            export_adapter=export_adapter,
            submission_transport_adapters=submission_transport_adapters,
            domain_pack=domain_pack,
            domain_envelope_validator=_domain_envelope_validator_for(adapter_key),
            review_row_materializer=DomainPackMetadataReviewRowMaterializer(
                metadata=domain_pack.metadata,
            ),
        )

    registry.register_adapter(
        adapter_key=REFERENCE_ADAPTER_KEY,
        candidate_normalizer=ReferenceCandidateNormalizer(),
        export_adapter=JsonBundleExportAdapter(adapter_key=REFERENCE_ADAPTER_KEY),
    )


def _export_adapter_for(adapter_key: str):
    export_adapter_factory = _DOMAIN_EXPORT_ADAPTERS[adapter_key]
    if adapter_key == "gene_expression":
        return export_adapter_factory()
    return export_adapter_factory(adapter_key=adapter_key)


def _submission_transport_adapters_for(adapter_key: str):
    submission_adapter_factory = _DOMAIN_SUBMISSION_TRANSPORTS.get(adapter_key)
    if submission_adapter_factory is None:
        return ()
    return (submission_adapter_factory(),)


def _domain_envelope_validator_for(adapter_key: str):
    return _DOMAIN_ENVELOPE_VALIDATORS.get(adapter_key)
