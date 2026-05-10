"""Domain envelope persistence services."""

from src.lib.domain_envelopes.migration import (
    LegacyCurationWorkspaceMigrationOptions,
    LegacyCurationWorkspaceMigrationSummary,
    LegacyMigrationBlocker,
    LegacySourceRef,
    migrate_legacy_curation_workspace_to_domain_envelopes,
)
from src.lib.domain_envelopes.persistence import (
    DomainEnvelopeCheckpointRequest,
    DomainEnvelopeCheckpointResult,
    DomainEnvelopeIndexCounts,
    DomainEnvelopePersistenceError,
    StaleDomainEnvelopeRevisionError,
    load_domain_envelope,
    regenerate_domain_envelope_indexes,
    write_domain_envelope_checkpoint,
)
from src.lib.domain_envelopes.patches import (
    EnvelopeFieldPatch,
    EnvelopeFieldPatchOperation,
    EnvelopeFieldPatchResult,
    EnvelopeFieldPatchStatus,
    apply_curator_field_patch,
)

__all__ = [
    "DomainEnvelopeCheckpointRequest",
    "DomainEnvelopeCheckpointResult",
    "DomainEnvelopeIndexCounts",
    "DomainEnvelopePersistenceError",
    "EnvelopeFieldPatch",
    "EnvelopeFieldPatchOperation",
    "EnvelopeFieldPatchResult",
    "EnvelopeFieldPatchStatus",
    "LegacyCurationWorkspaceMigrationOptions",
    "LegacyCurationWorkspaceMigrationSummary",
    "LegacyMigrationBlocker",
    "LegacySourceRef",
    "StaleDomainEnvelopeRevisionError",
    "apply_curator_field_patch",
    "load_domain_envelope",
    "migrate_legacy_curation_workspace_to_domain_envelopes",
    "regenerate_domain_envelope_indexes",
    "write_domain_envelope_checkpoint",
]
