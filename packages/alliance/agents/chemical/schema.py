"""Chemical validation agent schema."""

from src.schemas.domain_validator import DomainValidatorResultBase


class ChemicalValidationResult(DomainValidatorResultBase):
    """Canonical result schema for Alliance chemical validator agents."""
