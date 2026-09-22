"""Disease validation agent schema."""

from src.schemas.domain_validator import DomainValidatorResultBase


COMPACT_VALIDATOR_RUNTIME = ("agr.alliance", "agr_ai_curation_alliance.compact_adapter:build_compact_validator_runtime")


class DiseaseValidationResult(DomainValidatorResultBase):
    """Canonical result schema for Alliance disease validator agents."""
