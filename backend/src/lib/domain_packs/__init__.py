"""Domain-pack metadata loading and registry helpers."""

from .loader import (
    DomainPackContractError,
    DomainFixturePackError,
    DomainPackMetadataError,
    load_domain_fixture_pack,
    load_domain_pack_metadata,
)
from .registry import (
    DomainPackDiscoveryFailure,
    DomainPackRegistry,
    DomainPackRegistryValidationError,
    LoadedDomainPack,
    load_domain_pack_registry,
)
from .validation_registry import (
    DomainPackValidationRegistry,
    FieldValidationPolicy,
    ValidationAttachmentOption,
    ValidationBindingState,
    ValidationRegistryError,
    ValidatorBinding,
    ValidatorBindingMatch,
    ValidatorMetadataEntry,
)
from .validation_supervisor import (
    ValidationSupervisorResult,
    append_validation_findings_to_envelope,
    run_validation_supervisor,
)

__all__ = [
    "DomainFixturePackError",
    "DomainPackContractError",
    "DomainPackDiscoveryFailure",
    "DomainPackMetadataError",
    "DomainPackRegistry",
    "DomainPackRegistryValidationError",
    "DomainPackValidationRegistry",
    "FieldValidationPolicy",
    "LoadedDomainPack",
    "ValidationAttachmentOption",
    "ValidationBindingState",
    "ValidationRegistryError",
    "ValidationSupervisorResult",
    "ValidatorBinding",
    "ValidatorBindingMatch",
    "ValidatorMetadataEntry",
    "append_validation_findings_to_envelope",
    "load_domain_fixture_pack",
    "load_domain_pack_metadata",
    "load_domain_pack_registry",
    "run_validation_supervisor",
]
