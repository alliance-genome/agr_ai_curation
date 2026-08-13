from pydantic import Field
from src.schemas.domain_validator import (
    DomainValidatorBaseModel,
    DomainValidatorResultBase,
)


class DemoProjectedRecord(DomainValidatorBaseModel):
    """One neutral fixture row exposed through package projection metadata."""

    record_key: str
    label: str


class DemoValidationEnvelope(DomainValidatorResultBase):
    """Neutral demo validation result envelope."""

    source_name: str
    projected_records: list[DemoProjectedRecord] = Field(default_factory=list)
