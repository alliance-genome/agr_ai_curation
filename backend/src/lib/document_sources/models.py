"""Provider-neutral document source model contracts."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol, Self


class DocumentSourceError(RuntimeError):
    """Base error for document-source provider failures."""


class DocumentSourceReferenceNotFound(DocumentSourceError):
    """The provider's reference lookup found no matching publication."""


class DocumentSourceConfigError(DocumentSourceError):
    """Raised when document-source provider configuration is invalid."""


class DocumentSourceAccessDenied(DocumentSourceError):
    """Raised when the provider denies access to a requested source artifact."""


# Curator-facing; proposed wording awaiting the owner's sign-off (KANBAN-1976 / ALL-1460, P7b).
LOGIN_EXPIRED_MESSAGE = (
    "Your sign-in expired before this import finished. "
    "Sign in again, then import the paper again."
)


class DocumentSourceLoginExpired(DocumentSourceError):
    """The provider refused the curator's login (it expired during a long import).

    Raised by a provider for its "not authenticated" answer, with the provider's
    own failure text, so an ordinary import shows what it showed before. Only an
    import with chosen supplements, refused before it starts, carries the
    curator-facing ``LOGIN_EXPIRED_MESSAGE``.
    """


class DocumentSourceArtifactNotFound(DocumentSourceError):
    """The provider has no such artifact, or cannot find its stored file.

    Covers an unknown or deleted artifact id as well as a listed artifact whose
    stored copy is missing. Permanent: asking again returns the same answer.
    """


class DocumentSourceUnavailable(DocumentSourceError):
    """The provider, or the storage behind it, could not be reached.

    Temporary: the same request may succeed when tried again later.
    """


class DocumentSourceArtifactTooLarge(DocumentSourceError):
    """A download was stopped because the artifact is larger than the caller's limit."""

    def __init__(self, message: str, *, max_bytes: int):
        super().__init__(message)
        self.max_bytes = max_bytes


class SourceAccessScope(str, Enum):
    """Who may import a provider artifact.

    ``GLOBAL``: any caller, including one with no authorized group.
    ``ANY_GROUP``: any caller with at least one authorized group.
    ``RESTRICTED``: a caller with one of the policy's ``group_ids``.
    ``UNKNOWN``: nobody.
    """

    UNKNOWN = "unknown"
    GLOBAL = "global"
    ANY_GROUP = "any_group"
    RESTRICTED = "restricted"


class SourceArtifactRole(str, Enum):
    """What a provider artifact is, independent of the provider's own file classes.

    ``SOURCE_SUPPLEMENT``: an original supplementary file that is not a PDF,
    such as a spreadsheet or a word-processor document. Its text is read from
    a ``CONVERTED_TEXT`` artifact whose ``parent_artifact_id`` names it.
    Supplementary PDFs stay ``SOURCE_PDF``.
    """

    UNKNOWN = "unknown"
    SOURCE_PDF = "source_pdf"
    SOURCE_SUPPLEMENT = "source_supplement"
    CONVERTED_TEXT = "converted_text"
    PROVIDER_METADATA = "provider_metadata"


class SupplementFileCategory(str, Enum):
    """How a provider classifies one supplementary original for the file listing.

    ``CONVERTIBLE``: its text is read from a converted text artifact whose
    ``parent_artifact_id`` names it (the listing says whether one exists).
    ``LARGE_DATA_FILE``: a data file the provider keeps but does not convert.
    ``UNSUPPORTED_TYPE``: any other supplementary file.
    """

    CONVERTIBLE = "convertible"
    LARGE_DATA_FILE = "large_data_file"
    UNSUPPORTED_TYPE = "unsupported_type"


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


class SourceConversionFileStatus(str, Enum):
    """What a conversion answer says about one original file the provider worked on."""

    PENDING = "pending"
    CONVERTED = "converted"
    FAILED = "failed"


class ProviderBearerKind(str, Enum):
    """Whose credential a provider call carries, which decides derived-artifact trust.

    ``CURATOR``: the logged-in curator's own bearer. The provider enforces that
    curator's file access on every download, so reference-level derived
    artifacts are safe to select.

    ``SERVICE``: a machine reader that the provider lets read every file. AI
    Curation is then the only access gate, so a derived artifact (converted
    text, figure metadata) is used only when the provider binds it to the
    selected, authorized source PDF; otherwise that PDF is parsed locally.
    """

    CURATOR = "curator"
    SERVICE = "service"


class ViewerMode(str, Enum):
    """How a file is shown; the values are ``pdf_document_files.display_mode``."""

    LOCAL_PDF = "local_pdf"
    TEXT = "text"
    PROVIDER_PDF_PROXY = "provider_pdf_proxy"


@dataclass(frozen=True, slots=True)
class SourceAccessPolicy:
    """Access policy normalized from the provider's source artifact metadata."""

    scope: SourceAccessScope = SourceAccessScope.UNKNOWN
    group_ids: tuple[str, ...] = ()


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

    async def __aenter__(self) -> Self:
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

    def is_reference_source_artifact(self, artifact: SourceArtifact) -> bool:
        """Return whether a source PDF is a candidate main document for its reference.

        Identifier imports consider only these PDFs as the paper; other source
        PDFs (for example supplements or superseded files) are never imported
        as the paper, even when they are the only accessible one.
        """
        raise NotImplementedError

    def supplement_file_category(self, artifact: SourceArtifact) -> SupplementFileCategory | None:
        """Classify a listed artifact as a supplementary original, or ``None``.

        ``None`` for everything the file listing does not show as its own file:
        the paper's main files, derived artifacts (converted text, figures,
        metadata) and files the provider has not published.
        """
        raise NotImplementedError

    def is_listed_converted_supplement_text(self, artifact: SourceArtifact) -> bool:
        """Whether an artifact is the published converted text of a supplementary original.

        Only these are read for a supplement; other conversions are never used.
        Such an artifact names its original in ``parent_artifact_id``.
        """
        raise NotImplementedError

    def supplement_text_artifact_sort_key(self, artifact: SourceArtifact) -> tuple[int, ...]:
        """Rank a supplement's listed converted texts when it has more than one (lowest first).

        Among texts of the same rank the newest (highest provider file id) is read.
        """
        raise NotImplementedError

    def converted_text_has_single_title(self, original: SourceArtifact) -> bool:
        """Whether this original's converted text starts with exactly one top-level title.

        When true the file checks require exactly one top-level title.
        """
        raise NotImplementedError

    def checksum_match_uses_local_pdf(self, source_artifact: SourceArtifact) -> bool:
        """Return whether a checksum match must use the exact local PDF bytes."""
        raise NotImplementedError

    def main_text_artifact_sort_key(self, artifact: SourceArtifact) -> tuple[int, ...]:
        """Rank converted text artifacts when more than one main-text candidate exists."""
        raise NotImplementedError

    def conversion_exposes_main_text(self, result: SourceConversionResult) -> bool:
        """Return whether a conversion response says provider main text exists or is pending."""
        raise NotImplementedError

    def conversion_progress_percentage(self, result: SourceConversionResult) -> int:
        """Map provider conversion state to local job progress."""
        raise NotImplementedError

    def conversion_progress_message(self, result: SourceConversionResult) -> str:
        """Return provider-owned curator-facing conversion progress wording."""
        raise NotImplementedError

    def conversion_failure_message(self, result: SourceConversionResult) -> str:
        """Return provider-owned sanitized conversion failure wording."""
        raise NotImplementedError

    def conversion_file_statuses(
        self, result: SourceConversionResult
    ) -> Mapping[str, SourceConversionFileStatus]:
        """Per original file the answer names, by its provider file id, the provider's status.

        Only files the answer identifies by id; the provider's free-text errors
        are never read. A file the answer does not name is absent.
        """
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
        max_bytes: int | None = None,
    ) -> bytes:
        """Download artifact bytes using provider-specific authorization.

        With ``max_bytes`` the download stops as soon as more than that many
        bytes have arrived and raises ``DocumentSourceArtifactTooLarge``; the
        artifact is never read into memory beyond the limit. A stored file the
        provider cannot find raises ``DocumentSourceArtifactNotFound``, and an
        unreachable provider or provider storage raises
        ``DocumentSourceUnavailable``.
        """
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
