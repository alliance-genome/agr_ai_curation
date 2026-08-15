"""Provider-neutral document source contracts."""

from .models import (
    DocumentSourceConfigError,
    DocumentSourceError,
    DocumentSourceHealth,
    DocumentSourceProvider,
    SourceAccessPolicy,
    SourceAccessScope,
    SourceArtifact,
    SourceArtifactFormat,
    SourceArtifactRole,
    SourceArtifactStatus,
    SourceReference,
    ViewerMode,
)
from src.lib.packages.document_source_provider_models import (
    DocumentSourceProviderPresentation,
    DocumentSourceProviderRegistration,
)
from .access import (
    DocumentSourceRequestContext,
    build_document_source_request_context,
)
from .health import check_configured_document_source_health
from .provenance import (
    build_document_source_provenance,
    find_existing_document_by_source,
    sanitize_document_source_provenance,
)
from .registry import get_configured_document_source_provider

__all__ = [
    "DocumentSourceRequestContext",
    "DocumentSourceConfigError",
    "DocumentSourceError",
    "DocumentSourceHealth",
    "DocumentSourceProvider",
    "DocumentSourceProviderPresentation",
    "DocumentSourceProviderRegistration",
    "SourceAccessPolicy",
    "SourceAccessScope",
    "SourceArtifact",
    "SourceArtifactFormat",
    "SourceArtifactRole",
    "SourceArtifactStatus",
    "SourceReference",
    "ViewerMode",
    "build_document_source_request_context",
    "check_configured_document_source_health",
    "build_document_source_provenance",
    "find_existing_document_by_source",
    "sanitize_document_source_provenance",
    "get_configured_document_source_provider",
]
