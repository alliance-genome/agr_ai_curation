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
- Converted text not checked before (converted text id, MD5, check version) is
  downloaded, at most ``DOCUMENT_FILE_DOWNLOAD_CONCURRENCY`` at a time across the
  process, never past ``DOCUMENT_FILE_MAX_CONVERTED_TEXT_BYTES`` and at most
  ``DOCUMENT_FILE_LISTING_CHECK_LIMIT`` files per listing; each file is checked
  and released before its download slot is freed, and its result stored.
  A stored result is reused only after the access checks. No Sentry event is
  sent for a refusal found while listing.

The listing never cleans or parses text: the checks read the downloaded bytes.

Each entry also says whether the curator can tick it (P6f). A supplement the
provider has not converted can be ticked only where the import would ask the
provider to convert it: ``will_ask_provider`` holds that whole rule and reads it
from the import's own decision. On an imported paper, each entry carries the last thing the provider told
one of this paper's imports about the file (``ProviderObservation``).
"""

from __future__ import annotations

import asyncio
import weakref
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Generic, TypeVar

from src.lib.document_sources.file_assessment import (
    FileAssessment,
    FileCheckLimits,
    FileProfile,
    assess_converted_file,
)
from src.lib.document_sources.file_reasons import (
    PROVIDER_OBSERVATION_REASONS,
    FileReason,
    reason_in_group,
)
from src.lib.document_sources.identifier_import import (
    ReferenceImportDecision,
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
    # The provider gives the converted text no MD5: not downloaded, cannot be imported safely.
    TEXT_WITHOUT_CHECKSUM = "text_without_checksum"
    # Past ``DOCUMENT_FILE_LISTING_CHECK_LIMIT`` for this listing; checked on a later one.
    CHECK_LIMIT_REACHED = "check_limit_reached"
    NOT_CHECKED = "not_checked"


@dataclass(frozen=True, slots=True)
class ListedFileCheck:
    status: ListedFileCheckStatus
    reason: FileReason | None = None
    measured_value: int | None = None
    limit_value: int | None = None
    few_characters_count: int | None = None


class ListingRoute(str, Enum):
    """Which listing this is: what the curator's choice can lead to differs."""

    # Before an import: the import may send the provider's conversion request.
    IDENTIFIER = "identifier"
    # An imported paper: adding files sends no conversion request (P7c1).
    PAPER = "paper"


@dataclass(frozen=True, slots=True)
class ProviderObservation:
    """The last thing the provider told one of this paper's imports about a chosen file."""

    code: FileReason
    observed_at: datetime

    def __post_init__(self) -> None:
        reason_in_group(self.code.value, PROVIDER_OBSERVATION_REASONS, group_name="provider observation reasons")


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
    # Whether the curator can choose this file now, and if not, why (a ``NOT_TICKABLE_REASONS`` code).
    tickable: bool
    not_tickable_reason: FileReason | None
    # Per-paper listing only; ``None`` for files no import of this paper chose or that were added.
    last_provider_observation: ProviderObservation | None
    # The provider's own records, for an import or add of this entry; never sent to the browser.
    source_artifact: SourceArtifact
    converted_artifact: SourceArtifact | None


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
    # Identifier listing only: the curator's paper an import would answer ``duplicate`` with;
    # its files are added through that paper's own listing.
    existing_document_id: str | None


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


R = TypeVar("R")


@dataclass(frozen=True, slots=True)
class DownloadOutcome(Generic[R]):
    """What ``use`` returned for the downloaded bytes, or why there were no bytes."""

    result: R | None = None
    failure: ConvertedTextDownloadFailure | None = None
    limit_value: int | None = None


# One limiter per event loop, so every listing and import in the process shares one bound.
_download_limiters: "weakref.WeakKeyDictionary[asyncio.AbstractEventLoop, asyncio.Semaphore]" = (
    weakref.WeakKeyDictionary()
)


def _download_limiter() -> asyncio.Semaphore:
    from src.lib.openai_agents.config import get_document_file_download_concurrency

    loop = asyncio.get_running_loop()
    limiter = _download_limiters.get(loop)
    if limiter is None:
        limiter = asyncio.Semaphore(get_document_file_download_concurrency())
        _download_limiters[loop] = limiter
    return limiter


async def download_converted_text(
    provider: DocumentSourceProvider,
    artifact_id: str,
    *,
    request_bearer_token: str,
    max_bytes: int,
    use: Callable[[bytes], R],
) -> DownloadOutcome[R]:
    """Download one converted text and hand its bytes to ``use``, within the process-wide limit.

    At most ``DOCUMENT_FILE_DOWNLOAD_CONCURRENCY`` downloads run at once across the
    process, and a download stops past ``max_bytes``. ``use`` runs in a worker
    thread while the slot is still held, so at most that many files are in
    memory at once; the bytes are not kept afterwards. Each download is tried
    once. Other provider errors propagate. Shared by the listing and the import.
    """

    async with _download_limiter():
        try:
            content = await provider.download_artifact(
                artifact_id, request_bearer_token=request_bearer_token, max_bytes=max_bytes,
            )
        except DocumentSourceArtifactTooLarge as exc:
            return DownloadOutcome(failure=ConvertedTextDownloadFailure.TOO_LARGE, limit_value=exc.max_bytes)
        except DocumentSourceUnavailable:
            return DownloadOutcome(failure=ConvertedTextDownloadFailure.UNAVAILABLE)
        except DocumentSourceArtifactNotFound:
            return DownloadOutcome(failure=ConvertedTextDownloadFailure.MISSING)
        except DocumentSourceAccessDenied:
            return DownloadOutcome(failure=ConvertedTextDownloadFailure.ACCESS_DENIED)
        return DownloadOutcome(result=await asyncio.to_thread(use, content))


def current_file_check_limits() -> FileCheckLimits:
    """The checks' limits from the settings, and the embedding model's token counter."""

    from src.lib.openai_agents.config import (
        get_document_file_few_characters_note_below,
        get_document_file_max_converted_text_bytes,
    )
    from src.lib.pipeline import store

    encoder = store.embedding_token_encoder()
    return FileCheckLimits(
        max_bytes=get_document_file_max_converted_text_bytes(),
        few_characters_below=get_document_file_few_characters_note_below(),
        row_token_limit=store.TOKEN_HARD_LIMIT,
        count_tokens=lambda text: len(encoder.encode(text)),
        token_encoding=encoder.name,
    )


def _id_order(artifact: SourceArtifact) -> tuple[int, str]:
    # Numeric order for provider ids of different widths ("99" before "100").
    return (len(artifact.artifact_id), artifact.artifact_id)


@dataclass(slots=True)
class _Candidate:
    original: SourceArtifact
    category: SupplementFileCategory
    converted: SourceArtifact | None = None
    duplicates: list[SourceArtifact] = field(default_factory=list)


def _authorized(artifact: SourceArtifact, groups: tuple[str, ...]) -> bool:
    return source_artifact_is_authorized(artifact, authorized_group_ids=groups)


def will_ask_provider(
    decision: ReferenceImportDecision, *, bearer_kind: ProviderBearerKind, route: ListingRoute,
) -> bool:
    """Whether the import or add chosen from this listing asks the provider to convert the paper.

    Interface I9 (signature ``(decision, *, bearer_kind, route)``): other parts read
    it as "the import will ask the provider", so it never encodes a tick policy. It is
    read from the decision ``decide_reference_import(..., allow_conversion_request=False)``
    made, never re-derived from the artifacts, and is true only when that decision
    reached its own conversion request:
    - a curator's own login only: a machine login (``SERVICE``) never asks, and its
      branch is also ``READY`` with no converted text, so it is checked here;
    - ``READY`` only: a main text the provider lists as failed (``CONVERSION_FAILED``)
      returns before any request. A main text listed as running (``CONVERSION_RUNNING``)
      returns before the decision's request too and is excluded on purpose (plan M1),
      although the import of such a paper then polls with the same request;
    - before an import (``IDENTIFIER``) with no converted main text. A main text the
      provider does not count as main text (ABC's legacy ``_tei`` rows) is no main
      text, as in the import. An add from an imported paper (``PAPER``) sends no request.

    P7d adds its one condition (its setting on: also true with a converted main, on both
    routes) before the last line.
    """

    reference = decision.reference
    if (
        bearer_kind is not ProviderBearerKind.CURATOR
        or decision.status != ReferenceImportDecisionStatus.READY
        or decision.selected is None
        or reference is None
        or not (reference.reference_curie or reference.reference_id)
    ):
        return False
    return route is ListingRoute.IDENTIFIER and decision.selected.converted_artifact is None


async def list_reference_files(
    *,
    provider: DocumentSourceProvider,
    reference: SourceReference,
    authorized_group_ids: tuple[str, ...],
    bearer_kind: ProviderBearerKind,
    request_bearer_token: str,
    files_in_paper: Iterable[str] = (),
    limits: FileCheckLimits,
    check_limit: int,
    load_stored: Callable[[str, str, str], FileAssessment | None],
    save_assessment: Callable[[str, str, str, FileAssessment], None],
    route: ListingRoute,
    provider_observations: Mapping[str, ProviderObservation],
    find_paper_in_library: Callable[[ReferenceImportDecision], Awaitable[str | None]] | None,
) -> FileListing:
    """List one resolved reference's files for one curator.

    ``reference`` must be the provider's resolved reference: a provider can
    apply licence rules only to a resolved reference, so a bare identifier is
    refused here rather than listed with fewer rules. ``load_stored`` and
    ``save_assessment`` read and write stored results by
    (converted text id, MD5, check version); ``files_in_paper`` holds the
    provider file ids already imported into the paper. At most ``check_limit``
    files not checked before are downloaded by one listing. ``provider_observations``
    maps a provider file id to the last observation of this paper's imports; only the
    per-paper listing has any. ``find_paper_in_library`` (identifier listing only)
    returns the curator's paper an import of this decision would answer ``duplicate``
    with, the import's own check.
    """

    if not isinstance(reference, SourceReference):
        raise FileListingError("Files are listed only for a resolved provider reference")
    if provider_observations and route is not ListingRoute.PAPER:
        raise FileListingError("Provider observations belong to the per-paper listing only")
    if (find_paper_in_library is None) != (route is not ListingRoute.IDENTIFIER):
        raise FileListingError("Only the identifier listing checks the curator's Library")
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
    unconverted_tickable = will_ask_provider(decision, bearer_kind=bearer_kind, route=route)
    paper_reason, existing_document_id = await _paper_refusal(decision, route, find_paper_in_library)
    files: list[ListedFile] = []
    if decision.selected is not None and decision.status in (
        ReferenceImportDecisionStatus.READY,
        ReferenceImportDecisionStatus.CONVERSION_RUNNING,
    ):
        source = decision.selected.source_artifact
        converted = decision.selected.converted_artifact
        main_in_paper = source.artifact_id in in_paper
        files.append(ListedFile(
            provider_file_id=source.artifact_id,
            converted_text_id=converted.artifact_id if converted is not None else None,
            display_name=source.display_name,
            file_role=file_role_for_artifact_role(source.role, is_main=True),
            status=ListedFileStatus.READY,
            check=ListedFileCheck(ListedFileCheckStatus.NOT_CHECKED),
            in_this_paper=main_in_paper,
            md5=source.md5sum,
            converted_md5=converted.md5sum if converted is not None else None,
            artifact_format=source.artifact_format.value,
            category=None,
            single_title_required=False,
            # The main paper is always part of an import; it is never added twice.
            tickable=paper_reason is None and not main_in_paper,
            not_tickable_reason=paper_reason or (FileReason.ALREADY_IN_PAPER if main_in_paper else None),
            last_provider_observation=None,
            source_artifact=source,
            converted_artifact=converted,
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
    for candidate in sorted(candidates.values(), key=lambda item: _id_order(item.original)):
        if not _authorized(candidate.original, groups):
            not_shown += 1
            continue
        texts = converted_by_parent.get(candidate.original.artifact_id, [])
        if texts:
            # Both rules, separately: the original's (already passed) and the text's own.
            readable = [
                text for text in texts
                if converted_text_access_allowed(text, candidate.original, authorized_group_ids=groups)
            ]
            if not readable:
                not_shown += 1
                continue
            # A provider can hold more than one listed converted text for one original
            # (ABC: a legacy TEI-derived text next to the merged one). The provider's
            # rank decides; within one rank the newest, highest provider file id.
            newest_first = sorted(readable, key=_id_order, reverse=True)
            candidate.converted = min(newest_first, key=provider.supplement_text_artifact_sort_key)
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

    checks: dict[str, ListedFileCheck] = {}
    to_check: list[_Candidate] = []
    for candidate in unique:
        text = candidate.converted
        if text is None:
            continue
        if not text.md5sum:
            # Without the provider's MD5 an import cannot tell the text changed since
            # listing, and a result cannot be stored: not downloaded, not offered.
            checks[candidate.original.artifact_id] = ListedFileCheck(ListedFileCheckStatus.TEXT_WITHOUT_CHECKSUM)
            continue
        stored = await asyncio.to_thread(load_stored, text.artifact_id, text.md5sum, limits.check_version)
        if stored is not None:
            checks[candidate.original.artifact_id] = _check_from_assessment(stored)
        elif len(to_check) < check_limit:
            to_check.append(candidate)
        else:
            checks[candidate.original.artifact_id] = ListedFileCheck(ListedFileCheckStatus.CHECK_LIMIT_REACHED)

    async def check(candidate: _Candidate) -> ListedFileCheck:
        text = candidate.converted
        assert text is not None and text.md5sum
        profile = FileProfile(single_title_required=provider.converted_text_has_single_title(candidate.original))
        outcome = await download_converted_text(
            provider, text.artifact_id, request_bearer_token=request_bearer_token,
            max_bytes=limits.max_bytes, use=lambda content: assess_converted_file(content, profile, limits),
        )
        if outcome.failure is ConvertedTextDownloadFailure.TOO_LARGE:
            assessment = FileAssessment(refused=True, reason=FileReason.TOO_LARGE, limit_value=outcome.limit_value)
        elif outcome.failure is not None:
            # Not a property of the file: reported, never stored, tried once per listing.
            return ListedFileCheck(ListedFileCheckStatus(outcome.failure.value))
        else:
            assert outcome.result is not None
            assessment = outcome.result
        await asyncio.to_thread(save_assessment, text.artifact_id, text.md5sum, limits.check_version, assessment)
        return _check_from_assessment(assessment)

    results = await asyncio.gather(*(check(candidate) for candidate in to_check))
    checks.update(zip((candidate.original.artifact_id for candidate in to_check), results))

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
        copy_ids = (original.artifact_id, *(copy.artifact_id for copy in candidate.duplicates))
        in_this_paper = any(file_id in in_paper for file_id in copy_ids)
        check = checks.get(original.artifact_id, ListedFileCheck(ListedFileCheckStatus.NOT_CHECKED))
        not_tickable = paper_reason or _not_tickable_reason(
            status=status, check=check, in_this_paper=in_this_paper, unconverted_tickable=unconverted_tickable,
        )
        files.append(ListedFile(
            provider_file_id=original.artifact_id,
            converted_text_id=candidate.converted.artifact_id if candidate.converted else None,
            display_name=original.display_name,
            file_role=(
                file_role_for_artifact_role(original.role, is_main=False)
                if status is ListedFileStatus.READY else None
            ),
            status=status,
            check=check,
            in_this_paper=in_this_paper,
            md5=original.md5sum,
            converted_md5=candidate.converted.md5sum if candidate.converted else None,
            artifact_format=original.artifact_format.value,
            category=candidate.category,
            single_title_required=provider.converted_text_has_single_title(original),
            tickable=not_tickable is None,
            not_tickable_reason=not_tickable,
            # A file in the paper shows no observation: what the provider said before it was added is stale.
            last_provider_observation=None if in_this_paper else next(
                (provider_observations[file_id] for file_id in copy_ids if file_id in provider_observations),
                None,
            ),
            source_artifact=original,
            converted_artifact=candidate.converted,
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
        existing_document_id=existing_document_id,
    )


async def _paper_refusal(
    decision: ReferenceImportDecision,
    route: ListingRoute,
    find_paper_in_library: Callable[[ReferenceImportDecision], Awaitable[str | None]] | None,
) -> tuple[FileReason | None, str | None]:
    """Whether an import through this listing refuses the whole paper, read from the import's decision.

    On the identifier listing, an import goes ahead only when the decision selected a
    main file and is ``READY`` or ``CONVERSION_RUNNING`` (``_import_one_identifier``);
    anything else (no main file, access denied, an ambiguous main, a failed conversion)
    ends it with an error, so no file can be ticked (``paper_not_importable``;
    ``main_status`` says which). When the curator already has the paper, the import
    answers ``duplicate`` and imports nothing (``paper_already_in_library``, with the
    paper's id so the screen can open its own listing). The per-paper listing's adds
    do not depend on the main file's decision.
    """

    if route is not ListingRoute.IDENTIFIER:
        return None, None
    assert find_paper_in_library is not None
    importable = decision.selected is not None and decision.status in (
        ReferenceImportDecisionStatus.READY,
        ReferenceImportDecisionStatus.CONVERSION_RUNNING,
    )
    if not importable:
        return FileReason.PAPER_NOT_IMPORTABLE, None
    existing = await find_paper_in_library(decision)
    if existing is not None:
        return FileReason.PAPER_ALREADY_IN_LIBRARY, existing
    return None, None


def _not_tickable_reason(
    *,
    status: ListedFileStatus,
    check: ListedFileCheck,
    in_this_paper: bool,
    unconverted_tickable: bool,
) -> FileReason | None:
    """Why a supplement cannot be ticked, or ``None`` when it can. Codes from ``NOT_TICKABLE_REASONS``.

    Decision O2 (option (a), decided 22 Nov 2026): an unconverted file is tickable when
    the import will ask the provider to convert the paper (``will_ask_provider``); the
    provider's own rules decide what it converts and are never copied here, so on a
    paper whose supplements the provider does not convert, a ticked file is later
    reported as not converted. Under option (b) the ``NO_CONVERTED_TEXT`` line would
    always give ``PROVIDER_NOT_CONVERTED_YET``; under (c) it would read the provider's
    eligibility field. ``will_ask_provider`` stays the same in every option.
    """

    if in_this_paper:
        return FileReason.ALREADY_IN_PAPER
    if status is ListedFileStatus.LARGE_DATA_FILE:
        return FileReason.LARGE_DATA_FILE
    if status is ListedFileStatus.TYPE_NOT_SUPPORTED:
        return FileReason.TYPE_NOT_SUPPORTED
    if status is ListedFileStatus.NO_CONVERTED_TEXT:
        return None if unconverted_tickable else FileReason.PROVIDER_NOT_CONVERTED_YET
    # Ready: only a converted text that passed its check (a few-characters note still passes).
    return None if check.status is ListedFileCheckStatus.PASSED else FileReason.CHECK_NOT_PASSED


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
        "existing_document_id": listing.existing_document_id,
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
                "tickable": item.tickable,
                "not_tickable_reason": item.not_tickable_reason.value if item.not_tickable_reason else None,
                "last_provider_observation": (
                    {
                        "code": item.last_provider_observation.code.value,
                        "observed_at": item.last_provider_observation.observed_at.isoformat(),
                    }
                    if item.last_provider_observation is not None else None
                ),
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
