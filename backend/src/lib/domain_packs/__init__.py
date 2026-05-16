"""Domain-pack metadata loading and registry helpers."""

from .loader import (
    DomainPackContractError,
    DomainFixturePackError,
    DomainPackMetadataError,
    load_domain_fixture_pack,
    load_domain_pack_metadata,
)
from .materialization import (
    DomainEnvelopeMaterializationError,
    DomainEnvelopeRevisionUnavailableError,
    DomainEnvelopeReviewRowMaterializer,
    DomainPackMetadataReviewRowMaterializer,
    REVIEW_ROW_PROJECTION_TYPE,
    ValidatorResultMaterializationInput,
    ValidatorResultMaterializationResult,
    materialize_persisted_envelope_review_rows,
    materialize_validator_results_into_envelope,
    stable_object_id,
)
from .registry import (
    DomainPackDiscoveryFailure,
    DomainPackRegistry,
    DomainPackRegistryValidationError,
    LoadedDomainPack,
    load_package_domain_pack_registry,
    load_domain_pack_registry,
)
from .validation_registry import (
    DomainPackValidationRegistry,
    FieldValidationPolicy,
    ValidationAttachmentOption,
    ValidationBindingState,
    ValidationRegistryError,
    ValidatorAgentRef,
    ValidatorBinding,
    ValidatorBindingMatch,
    ValidatorMetadataEntry,
    validate_active_validator_agent_references,
)
from .validation_supervisor import (
    ValidationSupervisorResult,
    append_validation_findings_to_envelope,
    run_validation_supervisor,
)
from .validator_dispatch import (
    ActiveValidatorDispatchResult,
    dispatch_active_validator_bindings,
)
from .materialization import (
    project_evidence_anchor_projections,
    project_validation_summary_projections,
)

__all__ = [
    "DomainFixturePackError",
    "DomainPackContractError",
    "DomainPackDiscoveryFailure",
    "DomainEnvelopeMaterializationError",
    "DomainEnvelopeRevisionUnavailableError",
    "DomainEnvelopeReviewRowMaterializer",
    "ActiveValidatorDispatchResult",
    "DomainPackMetadataReviewRowMaterializer",
    "DomainPackMetadataError",
    "DomainPackRegistry",
    "DomainPackRegistryValidationError",
    "DomainPackValidationRegistry",
    "FieldValidationPolicy",
    "LoadedDomainPack",
    "REVIEW_ROW_PROJECTION_TYPE",
    "ValidationAttachmentOption",
    "ValidationBindingState",
    "ValidationRegistryError",
    "ValidationSupervisorResult",
    "ValidatorAgentRef",
    "ValidatorBinding",
    "ValidatorBindingMatch",
    "ValidatorMetadataEntry",
    "ValidatorResultMaterializationInput",
    "ValidatorResultMaterializationResult",
    "append_validation_findings_to_envelope",
    "dispatch_active_validator_bindings",
    "load_domain_fixture_pack",
    "load_domain_pack_metadata",
    "load_package_domain_pack_registry",
    "load_domain_pack_registry",
    "materialize_persisted_envelope_review_rows",
    "materialize_validator_results_into_envelope",
    "project_evidence_anchor_projections",
    "project_validation_summary_projections",
    "run_validation_supervisor",
    "stable_object_id",
    "validate_active_validator_agent_references",
]
