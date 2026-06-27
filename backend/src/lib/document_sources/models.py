"""Provider-neutral document source model contracts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol


class DocumentSourceError(RuntimeError):
    """Base error for document-source provider failures."""


class DocumentSourceConfigError(DocumentSourceError):
    """Raised when document-source provider configuration is invalid."""


class SourceAccessScope(str, Enum):
    UNKNOWN = "unknown"
    GLOBAL = "global"
    RESTRICTED = "restricted"


class SourceArtifactRole(str, Enum):
    UNKNOWN = "unknown"
    SOURCE_PDF = "source_pdf"
    CONVERTED_TEXT = "converted_text"
    PROVIDER_METADATA = "provider_metadata"


class SourceArtifactFormat(str, Enum):
    UNKNOWN = "unknown"
    PDF = "pdf"
    MARKDOWN = "markdown"
    XML = "xml"
    TEXT = "text"
    JSON = "json"


class SourceArtifactStatus(str, Enum):
    UNKNOWN = "unknown"
    AVAILABLE = "available"
    RUNNING = "running"
    FAILED = "failed"


class SourceConversionStatus(str, Enum):
    UNKNOWN = "unknown"
    CONVERTED = "converted"
    RUNNING = "running"
    FAILED = "failed"
    NO_SOURCES = "no_sources"


class ViewerMode(str, Enum):
    LOCAL_PDF = "local_pdf"
    TEXT_ONLY = "text_only"
    PROVIDER_PDF_PROXY = "provider_pdf_proxy"


@dataclass(frozen=True, slots=True)
class SourceAccessPolicy:
    """Access policy normalized from the provider's source artifact metadata."""

    scope: SourceAccessScope = SourceAccessScope.UNKNOWN
    mods: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SourceReference:
    """Provider-normalized source reference, independent of provider payload shape."""

    provider: str
    reference_id: str | None = None
    reference_curie: str | None = None
    title: str | None = None
    external_ids: Mapping[str, str | Sequence[str]] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class NormalizedSourceIdentifier:
    """A curator-supplied identifier after provider-aware normalization."""

    original: str
    normalized: str | None
    error: str | None = None

    @property
    def is_valid(self) -> bool:
        return self.normalized is not None and self.error is None


@dataclass(frozen=True, slots=True)
class SourceArtifact:
    """Provider-normalized downloadable/listable source artifact."""

    provider: str
    artifact_id: str
    role: SourceArtifactRole = SourceArtifactRole.UNKNOWN
    artifact_format: SourceArtifactFormat = SourceArtifactFormat.UNKNOWN
    status: SourceArtifactStatus = SourceArtifactStatus.UNKNOWN
    reference_id: str | None = None
    reference_curie: str | None = None
    display_name: str | None = None
    md5sum: str | None = None
    parent_artifact_id: str | None = None
    access_policy: SourceAccessPolicy = field(default_factory=SourceAccessPolicy)
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class SourceConversionResult:
    """Provider-normalized conversion request/poll response."""

    provider: str
    status: SourceConversionStatus
    reference_id: str | None = None
    reference_curie: str | None = None
    job_id: str | None = None
    error_message: str | None = None
    converted_classes: tuple[str, ...] = ()
    per_file_progress: tuple[Mapping[str, Any], ...] = ()
    per_mod_status: tuple[Mapping[str, Any], ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class DocumentSourceHealth:
    """Provider health/configuration result for admin/readiness surfaces."""

    provider: str
    ok: bool
    message: str
    metadata: Mapping[str, Any] = field(default_factory=dict)


class DocumentSourceProvider(Protocol):
    """Provider boundary consumed by import code instead of provider REST clients."""

    provider_id: str

    async def __aenter__(self) -> "DocumentSourceProvider":
        """Enter a provider context."""
        raise NotImplementedError

    async def __aexit__(self, *_args: object) -> None:
        """Close provider resources when leaving a context."""
        raise NotImplementedError

    async def aclose(self) -> None:
        """Close provider-owned resources."""
        raise NotImplementedError

    async def resolve_reference(
        self,
        identifier: str,
        *,
        request_bearer_token: str | None = None,
    ) -> SourceReference:
        """Resolve a user/provider identifier to a source reference."""
        raise NotImplementedError

    def normalize_identifier(self, identifier: str) -> NormalizedSourceIdentifier:
        """Normalize provider-specific identifier syntax without network I/O."""
        raise NotImplementedError

    def is_main_text_artifact(self, artifact: SourceArtifact) -> bool:
        """Return whether a converted text artifact is provider-designated main text."""
        raise NotImplementedError

    def main_text_artifact_sort_key(self, artifact: SourceArtifact) -> tuple[int, ...]:
        """Rank converted text artifacts when more than one main-text candidate exists."""
        raise NotImplementedError

    def conversion_exposes_main_text(self, result: SourceConversionResult) -> bool:
        """Return whether a conversion response says provider main text exists or is pending."""
        raise NotImplementedError

    async def list_artifacts(
        self,
        reference: SourceReference | str,
        *,
        request_bearer_token: str | None = None,
    ) -> list[SourceArtifact]:
        """List artifacts for a source reference."""
        raise NotImplementedError

    async def find_artifacts_by_checksum(
        self,
        checksum: str,
        *,
        request_bearer_token: str | None = None,
    ) -> list[SourceArtifact]:
        """Find source artifacts by checksum when the provider supports it."""
        raise NotImplementedError

    async def download_artifact(
        self,
        artifact_id: str,
        *,
        request_bearer_token: str | None = None,
    ) -> bytes:
        """Download artifact bytes using provider-specific authorization."""
        raise NotImplementedError

    async def request_conversion(
        self,
        reference: SourceReference | SourceArtifact | str,
        *,
        wait: bool = False,
        request_bearer_token: str | None = None,
    ) -> SourceConversionResult:
        """Request or poll provider-side conversion when the provider supports it."""
        raise NotImplementedError

    async def health(self) -> DocumentSourceHealth:
        """Return sanitized provider readiness information."""
        raise NotImplementedError
