"""Provider-neutral document-source contracts."""

from importlib import import_module
from typing import TYPE_CHECKING, Any

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
from .registration import (
    DevelopmentTokenResolver,
    DocumentSourceProviderFactory,
    DocumentSourceProviderPresentation,
    DocumentSourceProviderRegistration,
)

if TYPE_CHECKING:
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


_LAZY_EXPORTS = {
    "DocumentSourceRequestContext": (".access", "DocumentSourceRequestContext"),
    "build_document_source_request_context": (
        ".access",
        "build_document_source_request_context",
    ),
    "check_configured_document_source_health": (
        ".health",
        "check_configured_document_source_health",
    ),
    "build_document_source_provenance": (
        ".provenance",
        "build_document_source_provenance",
    ),
    "find_existing_document_by_source": (
        ".provenance",
        "find_existing_document_by_source",
    ),
    "sanitize_document_source_provenance": (
        ".provenance",
        "sanitize_document_source_provenance",
    ),
    "get_configured_document_source_provider": (
        ".registry",
        "get_configured_document_source_provider",
    ),
}


def __getattr__(name: str) -> Any:
    """Resolve service helpers lazily so contract imports stay cycle-free."""

    target = _LAZY_EXPORTS.get(name)
    if target is None:
        raise AttributeError(name)
    module_name, attribute_name = target
    value = getattr(import_module(module_name, __name__), attribute_name)
    globals()[name] = value
    return value

__all__ = [
    "DocumentSourceRequestContext",
    "DocumentSourceConfigError",
    "DocumentSourceError",
    "DocumentSourceHealth",
    "DocumentSourceProvider",
    "SourceAccessPolicy",
    "SourceAccessScope",
    "SourceArtifact",
    "SourceArtifactFormat",
    "SourceArtifactRole",
    "SourceArtifactStatus",
    "SourceReference",
    "ViewerMode",
    "DevelopmentTokenResolver",
    "DocumentSourceProviderFactory",
    "DocumentSourceProviderPresentation",
    "DocumentSourceProviderRegistration",
    "build_document_source_request_context",
    "check_configured_document_source_health",
    "build_document_source_provenance",
    "find_existing_document_by_source",
    "sanitize_document_source_provenance",
    "get_configured_document_source_provider",
]
