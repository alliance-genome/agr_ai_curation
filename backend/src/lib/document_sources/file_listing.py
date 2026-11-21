"""List a paper's provider files with a status and a check result per file (P6).

Backend only: the screens that show this list come later. The listing returns
codes and measured values, never wording.

- The main paper is one entry, chosen exactly as an import chooses it
  (``decide_reference_import``); its converted text is never listed separately
  and is not checked here.
- Each supplementary original is an entry. Its text is the provider's listed
  converted text whose ``parent_artifact_id`` names it.
- A supplement is shown only when the curator passes both the original's
  access rule and its converted text's own rule, checked separately. Files the
  curator cannot open are only counted.
- Copies with the same MD5 collapse to one entry: the copy the curator can
  open, and among several, the lowest provider file id.
- Converted text not checked before (provider file id, MD5, check version) is
  downloaded, at most ``DOCUMENT_FILE_DOWNLOAD_CONCURRENCY`` at a time and
  never past ``DOCUMENT_FILE_MAX_CONVERTED_TEXT_BYTES``, checked and stored.
  A stored result is reused only after the access checks. No Sentry event is
  sent for a refusal found while listing.

The listing never cleans or parses text: the checks read the downloaded bytes.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from src.lib.document_sources.file_assessment import (
    FileAssessment,
    FileCheckLimits,
    FileCheckReason,
    FileProfile,
    assess_converted_file,
)
from src.lib.document_sources.identifier_import import (
    ReferenceImportDecisionStatus,
    decide_reference_import,
)
from src.lib.document_sources.import_selection import source_artifact_is_authorized
from src.lib.document_sources.models import (
    DocumentSourceAccessDenied,
    DocumentSourceArtifactNotFound,
    DocumentSourceArtifactTooLarge,
    DocumentSourceProvider,
    DocumentSourceUnavailable,
    ProviderBearerKind,
    SourceArtifact,
    SourceArtifactRole,
    SourceReference,
    SupplementFileCategory,
)
from src.models.sql.pdf_document import FILE_ROLE_MAIN, FILE_ROLE_SUPPLEMENT


class FileListingError(RuntimeError):
    """The listing cannot be built as asked (a structural problem, never a curator's access)."""


class FileRoleMappingError(FileListingError):
    """A provider artifact role has no file role; nothing falls through to a default."""


class ListedFileStatus(str, Enum):
    READY = "ready"
    NO_CONVERTED_TEXT = "no_converted_text"
    LARGE_DATA_FILE = "large_data_file"
    TYPE_NOT_SUPPORTED = "type_not_supported"


class ListedFileCheckStatus(str, Enum):
    PASSED = "passed"
    REFUSED = "refused"
    # The provider could not serve the converted text now (try again) or at all.
    TEXT_UNAVAILABLE = "text_unavailable"
    TEXT_MISSING = "text_missing"
    # The provider refused the download although both access rules allowed it.
    TEXT_ACCESS_DENIED = "text_access_denied"
    NOT_CHECKED = "not_checked"


@dataclass(frozen=True, slots=True)
class ListedFileCheck:
    status: ListedFileCheckStatus
    reason: FileCheckReason | None = None
    measured_value: int | None = None
    limit_value: int | None = None
    few_characters_count: int | None = None


@dataclass(frozen=True, slots=True)
class ListedFile:
    # Names the file: the ORIGINAL's provider file id. An import writes it to
    # ``source_provider_source_file_id``, and "in this paper" matches it.
    provider_file_id: str
    converted_text_id: str | None
    display_name: str | None
    # The ``pdf_document_files.role`` an import gives this file; ``None`` when
    # the file cannot be imported (no converted text, or a type that is not read).
    file_role: str | None
    status: ListedFileStatus
    check: ListedFileCheck
    in_this_paper: bool
    md5: str | None
    # The converted text's MD5: what an import sends back to detect a change since listing.
    converted_md5: str | None
    artifact_format: str
    # The provider's category for a supplement; ``None`` for the main paper.
    category: SupplementFileCategory | None
    # The provider's profile: the converted text's first top-level title is the file's title.
    single_title_required: bool


@dataclass(frozen=True, slots=True)
class FileListing:
    provider: str
    reference_id: str | None
    reference_curie: str | None
    # The import decision for the main paper (``ReferenceImportDecisionStatus``).
    main_status: str
    files: tuple[ListedFile, ...]
    # Supplementary files the curator cannot open: counted, never named.
    not_shown_count: int
    # Further copies of a listed file (same MD5) that are not listed again.
    duplicate_copies_count: int
    check_version: str


def file_role_for_artifact_role(role: SourceArtifactRole, *, is_main: bool) -> str:
    """Map a provider artifact role to ``pdf_document_files.role``, explicitly.

    The paper's main file is ``main``. A supplementary original is a
    ``supplement`` whether it is a PDF (``SOURCE_PDF``) or another file
    (``SOURCE_SUPPLEMENT``). Every other role is a named error.
    """

    if is_main:
        if role is SourceArtifactRole.SOURCE_PDF:
            return FILE_ROLE_MAIN
    elif role in (SourceArtifactRole.SOURCE_SUPPLEMENT, SourceArtifactRole.SOURCE_PDF):
        return FILE_ROLE_SUPPLEMENT
    raise FileRoleMappingError(f"Artifact role {role.value} has no file role (main={is_main})")


def converted_text_access_allowed(
    converted: SourceArtifact,
    original: SourceArtifact,
    *,
    authorized_group_ids: Iterable[str],
) -> bool:
    """A converted text is readable only when both rules allow the curator, checked separately.

    The original's access rule and the converted row's own rule (which can be
    narrower before the provider syncs them) are each checked on their own;
    one merged rule would not mean the same as "both allow".
    """

    groups = tuple(authorized_group_ids)
    return _authorized(original, groups) and _authorized(converted, groups)


class ConvertedTextDownloadFailure(str, Enum):
    """Why a converted text could not be downloaded. Values are codes; never rename one."""

    TOO_LARGE = "too_large"
    UNAVAILABLE = "text_unavailable"
    MISSING = "text_missing"
    ACCESS_DENIED = "text_access_denied"


@dataclass(frozen=True, slots=True)
class DownloadedText:
    content: bytes | None = None
    failure: ConvertedTextDownloadFailure | None = None
    limit_value: int | None = None


async def download_converted_texts(
    provider: DocumentSourceProvider,
    artifact_ids: Iterable[str],
    *,
    request_bearer_token: str,
    max_bytes: int,
    concurrency: int,
) -> dict[str, DownloadedText]:
    """Download converted texts, at most ``concurrency`` at once and never past ``max_bytes``.

    Each id gets its bytes or a failure code; other provider errors propagate.
    Shared by the listing and the import, so both download the same way.
    """

    semaphore = asyncio.Semaphore(max(1, concurrency))

    async def one(artifact_id: str) -> tuple[str, DownloadedText]:
        async with semaphore:
            try:
                content = await provider.download_artifact(
                    artifact_id, request_bearer_token=request_bearer_token, max_bytes=max_bytes,
                )
            except DocumentSourceArtifactTooLarge as exc:
                return artifact_id, DownloadedText(
                    failure=ConvertedTextDownloadFailure.TOO_LARGE, limit_value=exc.max_bytes,
                )
            except DocumentSourceUnavailable:
                return artifact_id, DownloadedText(failure=ConvertedTextDownloadFailure.UNAVAILABLE)
            except DocumentSourceArtifactNotFound:
                return artifact_id, DownloadedText(failure=ConvertedTextDownloadFailure.MISSING)
            except DocumentSourceAccessDenied:
                return artifact_id, DownloadedText(failure=ConvertedTextDownloadFailure.ACCESS_DENIED)
        return artifact_id, DownloadedText(content=content)

    unique_ids = list(dict.fromkeys(artifact_ids))
    return dict(await asyncio.gather(*(one(artifact_id) for artifact_id in unique_ids)))


def current_file_check_limits() -> FileCheckLimits:
    """The checks' limits from the settings, and the embedding model's token counter."""

    from src.lib.openai_agents.config import (
        get_document_file_few_characters_note_below,
        get_document_file_max_converted_text_bytes,
    )
    from src.lib.pipeline import store

    encoder = store._tiktoken_encoder
    if encoder is None:
        raise FileListingError(
            "The row-width check needs the embedding token counter "
            "(EMBEDDING_TOKEN_PREFLIGHT_ENABLED=true)"
        )
    return FileCheckLimits(
        max_bytes=get_document_file_max_converted_text_bytes(),
        few_characters_below=get_document_file_few_characters_note_below(),
        row_token_limit=store.TOKEN_HARD_LIMIT,
        count_tokens=lambda text: len(encoder.encode(text)),
    )


@dataclass(slots=True)
class _Candidate:
    original: SourceArtifact
    category: SupplementFileCategory
    converted: SourceArtifact | None = None
    duplicates: list[SourceArtifact] = field(default_factory=list)


def _authorized(artifact: SourceArtifact, groups: tuple[str, ...]) -> bool:
    return source_artifact_is_authorized(artifact, authorized_group_ids=groups)


async def list_reference_files(
    *,
    provider: DocumentSourceProvider,
    reference: SourceReference,
    authorized_group_ids: tuple[str, ...],
    bearer_kind: ProviderBearerKind,
    request_bearer_token: str,
    files_in_paper: Iterable[str] = (),
    limits: FileCheckLimits,
    download_concurrency: int,
    load_stored: Callable[[str, str, str], FileAssessment | None],
    save_assessment: Callable[[str, str, str, FileAssessment], None],
) -> FileListing:
    """List one resolved reference's files for one curator.

    ``reference`` must be the provider's resolved reference: a provider can
    apply licence rules only to a resolved reference, so a bare identifier is
    refused here rather than listed with fewer rules. ``load_stored`` and
    ``save_assessment`` read and write stored results by
    (provider file id, MD5, check version); ``files_in_paper`` holds the
    provider file ids already imported into the paper.
    """

    if not isinstance(reference, SourceReference):
        raise FileListingError("Files are listed only for a resolved provider reference")
    groups = tuple(authorized_group_ids)
    in_paper = set(files_in_paper)
    artifacts = await provider.list_artifacts(reference, request_bearer_token=request_bearer_token)

    decision = await decide_reference_import(
        provider=provider,
        identifier=reference.reference_curie or reference.reference_id or "",
        reference=reference,
        artifacts=artifacts,
        authorized_group_ids=groups,
        bearer_kind=bearer_kind,
        request_bearer_token=request_bearer_token,
        allow_conversion_request=False,
    )
    files: list[ListedFile] = []
    if decision.selected is not None and decision.status in (
        ReferenceImportDecisionStatus.READY,
        ReferenceImportDecisionStatus.CONVERSION_RUNNING,
    ):
        source = decision.selected.source_artifact
        converted = decision.selected.converted_artifact
        files.append(ListedFile(
            provider_file_id=source.artifact_id,
            converted_text_id=converted.artifact_id if converted is not None else None,
            display_name=source.display_name,
            file_role=file_role_for_artifact_role(source.role, is_main=True),
            status=ListedFileStatus.READY,
            check=ListedFileCheck(ListedFileCheckStatus.NOT_CHECKED),
            in_this_paper=source.artifact_id in in_paper,
            md5=source.md5sum,
            converted_md5=converted.md5sum if converted is not None else None,
            artifact_format=source.artifact_format.value,
            category=None,
            single_title_required=False,
        ))

    candidates: dict[str, _Candidate] = {}
    for artifact in artifacts:
        category = provider.supplement_file_category(artifact)
        if category is not None:
            candidates[artifact.artifact_id] = _Candidate(original=artifact, category=category)
    converted_by_parent: dict[str, list[SourceArtifact]] = {}
    for artifact in artifacts:
        if provider.is_listed_converted_supplement_text(artifact):
            converted_by_parent.setdefault(artifact.parent_artifact_id or "", []).append(artifact)

    not_shown = 0
    openable: list[_Candidate] = []
    for candidate in sorted(candidates.values(), key=lambda item: item.original.artifact_id):
        if not _authorized(candidate.original, groups):
            not_shown += 1
            continue
        texts = sorted(
            converted_by_parent.get(candidate.original.artifact_id, []),
            key=lambda item: item.artifact_id,
        )
        if texts:
            # Both rules, separately: the original's (already passed) and the text's own.
            readable = [
                text for text in texts
                if converted_text_access_allowed(text, candidate.original, authorized_group_ids=groups)
            ]
            if not readable:
                not_shown += 1
                continue
            if len(readable) > 1:
                raise FileListingError(
                    f"File {candidate.original.artifact_id} has {len(readable)} listed converted texts"
                )
            candidate.converted = readable[0]
        openable.append(candidate)

    by_md5: dict[str, _Candidate] = {}
    duplicate_copies = 0
    unique: list[_Candidate] = []
    for candidate in openable:
        md5 = candidate.original.md5sum
        if md5 is None:
            unique.append(candidate)
            continue
        kept = by_md5.get(md5)
        if kept is None:
            by_md5[md5] = candidate
            unique.append(candidate)
        else:
            # ``openable`` is in provider file id order, so the first copy kept is the lowest id.
            kept.duplicates.append(candidate.original)
            duplicate_copies += 1

    ready = [candidate for candidate in unique if candidate.converted is not None]
    checks: dict[str, ListedFileCheck] = {}
    to_download: list[_Candidate] = []
    for candidate in ready:
        text = candidate.converted
        assert text is not None
        stored = (
            await asyncio.to_thread(load_stored, text.artifact_id, text.md5sum, limits.check_version)
            if text.md5sum else None
        )
        if stored is not None:
            checks[candidate.original.artifact_id] = _check_from_assessment(stored)
        else:
            to_download.append(candidate)

    downloads = await download_converted_texts(
        provider,
        (candidate.converted.artifact_id for candidate in to_download if candidate.converted),
        request_bearer_token=request_bearer_token,
        max_bytes=limits.max_bytes,
        concurrency=download_concurrency,
    )
    for candidate in to_download:
        text = candidate.converted
        assert text is not None
        downloaded = downloads[text.artifact_id]
        if downloaded.failure is ConvertedTextDownloadFailure.TOO_LARGE:
            assessment = FileAssessment(
                refused=True, reason=FileCheckReason.TOO_LARGE, limit_value=downloaded.limit_value,
            )
        elif downloaded.failure is not None:
            # Not a check result: reported, never stored.
            checks[candidate.original.artifact_id] = ListedFileCheck(
                ListedFileCheckStatus(downloaded.failure.value)
            )
            continue
        else:
            assert downloaded.content is not None
            profile = FileProfile(
                single_title_required=provider.converted_text_has_single_title(candidate.original),
            )
            assessment = await asyncio.to_thread(assess_converted_file, downloaded.content, profile, limits)
        md5 = text.md5sum or (
            hashlib.md5(downloaded.content, usedforsecurity=False).hexdigest()
            if downloaded.content is not None else None
        )
        if md5 is not None:
            await asyncio.to_thread(save_assessment, text.artifact_id, md5, limits.check_version, assessment)
        checks[candidate.original.artifact_id] = _check_from_assessment(assessment)

    for candidate in unique:
        original = candidate.original
        if candidate.category is SupplementFileCategory.LARGE_DATA_FILE:
            status = ListedFileStatus.LARGE_DATA_FILE
        elif candidate.category is SupplementFileCategory.UNSUPPORTED_TYPE:
            status = ListedFileStatus.TYPE_NOT_SUPPORTED
        elif candidate.converted is None:
            status = ListedFileStatus.NO_CONVERTED_TEXT
        else:
            status = ListedFileStatus.READY
        files.append(ListedFile(
            provider_file_id=original.artifact_id,
            converted_text_id=candidate.converted.artifact_id if candidate.converted else None,
            display_name=original.display_name,
            file_role=(
                file_role_for_artifact_role(original.role, is_main=False)
                if status is ListedFileStatus.READY else None
            ),
            status=status,
            check=checks.get(original.artifact_id, ListedFileCheck(ListedFileCheckStatus.NOT_CHECKED)),
            in_this_paper=original.artifact_id in in_paper
            or any(copy.artifact_id in in_paper for copy in candidate.duplicates),
            md5=original.md5sum,
            converted_md5=candidate.converted.md5sum if candidate.converted else None,
            artifact_format=original.artifact_format.value,
            category=candidate.category,
            single_title_required=provider.converted_text_has_single_title(original),
        ))

    return FileListing(
        provider=provider.provider_id,
        reference_id=reference.reference_id,
        reference_curie=reference.reference_curie,
        main_status=decision.status,
        files=tuple(files),
        not_shown_count=not_shown,
        duplicate_copies_count=duplicate_copies,
        check_version=limits.check_version,
    )


def _check_from_assessment(assessment: FileAssessment) -> ListedFileCheck:
    return ListedFileCheck(
        status=ListedFileCheckStatus.REFUSED if assessment.refused else ListedFileCheckStatus.PASSED,
        reason=assessment.reason,
        measured_value=assessment.measured_value,
        limit_value=assessment.limit_value,
        few_characters_count=assessment.few_characters_count,
    )


def listing_to_payload(listing: FileListing) -> dict[str, Any]:
    """JSON payload: codes and numbers only."""

    return {
        "provider": listing.provider,
        "reference_id": listing.reference_id,
        "reference_curie": listing.reference_curie,
        "main_status": listing.main_status,
        "not_shown_count": listing.not_shown_count,
        "duplicate_copies_count": listing.duplicate_copies_count,
        "check_version": listing.check_version,
        "files": [
            {
                "provider_file_id": item.provider_file_id,
                "converted_text_id": item.converted_text_id,
                "display_name": item.display_name,
                "file_role": item.file_role,
                "md5": item.md5,
                "converted_md5": item.converted_md5,
                "format": item.artifact_format,
                "category": item.category.value if item.category else None,
                "status": item.status.value,
                "in_this_paper": item.in_this_paper,
                "check": {
                    "status": item.check.status.value,
                    "reason": item.check.reason.value if item.check.reason else None,
                    "measured_value": item.check.measured_value,
                    "limit_value": item.check.limit_value,
                    "few_characters_count": item.check.few_characters_count,
                },
            }
            for item in listing.files
        ],
    }
