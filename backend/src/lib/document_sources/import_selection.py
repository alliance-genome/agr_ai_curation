"""Provider-neutral import selection for checksum-backed document sources."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, cast

from src.lib.document_sources.models import (
    DocumentSourceError,
    DocumentSourceProvider,
    SourceAccessPolicy,
    SourceAccessScope,
    SourceArtifact,
    SourceArtifactFormat,
    SourceArtifactRole,
    SourceArtifactStatus,
    SourceConversionResult,
    SourceConversionStatus,
)
from src.lib.document_sources.main_text import select_preferred_main_text_artifact


class ChecksumImportDecisionStatus(str, Enum):
    """Decision categories for provider checksum lookup results."""

    READY = "ready"
    NO_MATCH = "no_match"
    NO_SOURCE_ARTIFACT = "no_source_artifact"
    ACCESS_DENIED = "access_denied"
    AMBIGUOUS_MATCH = "ambiguous_match"
    NO_CONVERTED_TEXT = "no_converted_text"
    CONVERSION_RUNNING = "conversion_running"
    CONVERSION_FAILED = "conversion_failed"


@dataclass(frozen=True, slots=True)
class ChecksumImportCandidate:
    """A source PDF plus optional provider-converted text artifact."""

    source_artifact: SourceArtifact
    converted_artifact: SourceArtifact | None = None
    provider_metadata_artifacts: tuple[SourceArtifact, ...] = ()


@dataclass(frozen=True, slots=True)
class ChecksumImportDecision:
    """Provider-neutral checksum import decision."""

    status: ChecksumImportDecisionStatus
    provider: str
    checksum: str
    selected: ChecksumImportCandidate | None = None
    candidates: tuple[ChecksumImportCandidate, ...] = ()
    source_artifacts: tuple[SourceArtifact, ...] = ()
    message: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_ready(self) -> bool:
        return (
            self.status is ChecksumImportDecisionStatus.READY
            and self.selected is not None
        )


async def select_checksum_import_candidate(
    *,
    provider: DocumentSourceProvider,
    checksum: str,
    authorized_group_ids: tuple[str, ...] | list[str] | set[str],
    request_bearer_token: str | None = None,
    allow_conversion_request: bool = True,
) -> ChecksumImportDecision:
    """Resolve a checksum to exactly one authorized source PDF.

    This helper never uploads PDFs, downloads bytes, ingests content, or calls
    direct PDFX. Converted main Markdown is preferred when ready; if an
    authorized provider match has no usable main Markdown and the provider
    supports conversion, this helper requests/polls provider-side conversion
    without TEI overwrite.
    """

    normalized_checksum = _require_checksum(checksum)
    artifacts = await provider.find_artifacts_by_checksum(
        normalized_checksum,
        request_bearer_token=request_bearer_token,
    )
    if not artifacts:
        return _decision(
            provider=provider.provider_id,
            checksum=normalized_checksum,
            status=ChecksumImportDecisionStatus.NO_MATCH,
            message="No provider match found for checksum",
        )

    source_artifacts = _source_artifacts(artifacts)
    if not source_artifacts:
        return _decision(
            provider=provider.provider_id,
            checksum=normalized_checksum,
            status=ChecksumImportDecisionStatus.NO_SOURCE_ARTIFACT,
            message="Provider checksum response did not include a source PDF artifact",
        )

    authorized_source_list: list[SourceArtifact] = [
        source_artifact
        for source_artifact in source_artifacts
        if source_artifact_is_authorized(
            source_artifact,
            authorized_group_ids=authorized_group_ids,
        )
    ]
    if not authorized_source_list:
        return _decision(
            provider=provider.provider_id,
            checksum=normalized_checksum,
            status=ChecksumImportDecisionStatus.ACCESS_DENIED,
            source_artifacts=source_artifacts,
            message="No checksum matches are accessible to this curator",
        )
    authorized_sources = tuple(authorized_source_list)
    if len(authorized_source_list) > 1:
        return _decision(
            provider=provider.provider_id,
            checksum=normalized_checksum,
            status=ChecksumImportDecisionStatus.AMBIGUOUS_MATCH,
            source_artifacts=authorized_sources,
            message="Multiple accessible provider matches require curator selection",
            metadata={"match_count": len(authorized_source_list)},
        )

    source_artifact = authorized_source_list[0]
    provider_metadata_artifacts = provider_metadata_artifacts_for_source(
        provider=provider,
        source_artifact=source_artifact,
        artifacts=artifacts,
    )
    converted_artifacts = _converted_artifacts_for_source(
        source_artifact=source_artifact,
        artifacts=artifacts,
    )
    markdown_artifacts = tuple(
        artifact for artifact in converted_artifacts if _is_converted_markdown(artifact)
    )
    selected_ready_artifact, ambiguous_ready_count = select_preferred_main_text_artifact(
        provider,
        markdown_artifacts,
    )
    if ambiguous_ready_count > 1:
        candidates = tuple(
            ChecksumImportCandidate(
                source_artifact=source_artifact,
                converted_artifact=artifact,
            )
            for artifact in markdown_artifacts
            if artifact.status is SourceArtifactStatus.AVAILABLE
            and provider.is_main_text_artifact(artifact)
        )
        return _decision(
            provider=provider.provider_id,
            checksum=normalized_checksum,
            status=ChecksumImportDecisionStatus.AMBIGUOUS_MATCH,
            candidates=candidates,
            source_artifacts=authorized_sources,
            message="Multiple converted Markdown artifacts are equally preferred",
            metadata={"match_count": ambiguous_ready_count},
        )

    if selected_ready_artifact is not None:
        candidate = ChecksumImportCandidate(
            source_artifact=source_artifact,
            converted_artifact=selected_ready_artifact,
            provider_metadata_artifacts=provider_metadata_artifacts,
        )
        return _decision(
            provider=provider.provider_id,
            checksum=normalized_checksum,
            status=ChecksumImportDecisionStatus.READY,
            selected=candidate,
            candidates=(candidate,),
            source_artifacts=authorized_sources,
            message="One authorized converted Markdown artifact is ready",
        )
    if any(
        artifact.status is SourceArtifactStatus.RUNNING
        and provider.is_main_text_artifact(artifact)
        for artifact in markdown_artifacts
    ):
        return _decision(
            provider=provider.provider_id,
            checksum=normalized_checksum,
            status=ChecksumImportDecisionStatus.CONVERSION_RUNNING,
            selected=ChecksumImportCandidate(
                source_artifact=source_artifact,
                provider_metadata_artifacts=provider_metadata_artifacts,
            ),
            source_artifacts=authorized_sources,
            message="Provider conversion is still running",
        )
    if any(
        artifact.status is SourceArtifactStatus.FAILED
        and provider.is_main_text_artifact(artifact)
        for artifact in markdown_artifacts
    ):
        return _decision(
            provider=provider.provider_id,
            checksum=normalized_checksum,
            status=ChecksumImportDecisionStatus.CONVERSION_FAILED,
            selected=ChecksumImportCandidate(source_artifact=source_artifact),
            source_artifacts=authorized_sources,
            message="Provider conversion failed",
        )

    conversion_result = None
    if allow_conversion_request:
        conversion_result = await _request_conversion_if_supported(
            provider=provider,
            source_artifact=source_artifact,
            request_bearer_token=request_bearer_token,
        )
    if conversion_result is not None:
        conversion_metadata = _conversion_metadata(conversion_result)
        if provider.conversion_exposes_main_text(conversion_result):
            refreshed_artifacts = await provider.list_artifacts(
                _reference_lookup_value(source_artifact),
                request_bearer_token=request_bearer_token,
            )
            converted_artifact, ambiguous_count = _select_reference_markdown_artifact(
                provider=provider,
                source_artifact=source_artifact,
                artifacts=refreshed_artifacts,
            )
            refreshed_metadata_artifacts = provider_metadata_artifacts_for_source(
                provider=provider,
                source_artifact=source_artifact,
                artifacts=refreshed_artifacts,
            )
            if ambiguous_count > 1:
                return _decision(
                    provider=provider.provider_id,
                    checksum=normalized_checksum,
                    status=ChecksumImportDecisionStatus.AMBIGUOUS_MATCH,
                    source_artifacts=authorized_sources,
                    message="Provider conversion produced multiple equally preferred Markdown artifacts",
                    metadata={**conversion_metadata, "match_count": ambiguous_count},
                )
            if converted_artifact is not None:
                candidate = ChecksumImportCandidate(
                    source_artifact=source_artifact,
                    converted_artifact=converted_artifact,
                    provider_metadata_artifacts=refreshed_metadata_artifacts,
                )
                return _decision(
                    provider=provider.provider_id,
                    checksum=normalized_checksum,
                    status=ChecksumImportDecisionStatus.READY,
                    selected=candidate,
                    candidates=(candidate,),
                    source_artifacts=authorized_sources,
                    message="Provider conversion produced main Markdown",
                    metadata=conversion_metadata,
                )
        if conversion_result.status is SourceConversionStatus.RUNNING:
            return _decision(
                provider=provider.provider_id,
                checksum=normalized_checksum,
                status=ChecksumImportDecisionStatus.CONVERSION_RUNNING,
                selected=ChecksumImportCandidate(
                    source_artifact=source_artifact,
                    provider_metadata_artifacts=provider_metadata_artifacts,
                ),
                source_artifacts=authorized_sources,
                message="Provider conversion is still running",
                metadata=conversion_metadata,
            )
        if conversion_result.status is SourceConversionStatus.FAILED:
            return _decision(
                provider=provider.provider_id,
                checksum=normalized_checksum,
                status=ChecksumImportDecisionStatus.CONVERSION_FAILED,
                selected=ChecksumImportCandidate(source_artifact=source_artifact),
                source_artifacts=authorized_sources,
                message="Provider conversion failed",
                metadata=conversion_metadata,
            )
        if conversion_result.status is SourceConversionStatus.NO_SOURCES:
            return _decision(
                provider=provider.provider_id,
                checksum=normalized_checksum,
                status=ChecksumImportDecisionStatus.NO_CONVERTED_TEXT,
                selected=ChecksumImportCandidate(source_artifact=source_artifact),
                source_artifacts=authorized_sources,
                message="Provider has no convertible source for this reference",
                metadata=conversion_metadata,
            )
        return _decision(
            provider=provider.provider_id,
            checksum=normalized_checksum,
            status=ChecksumImportDecisionStatus.NO_CONVERTED_TEXT,
            selected=ChecksumImportCandidate(source_artifact=source_artifact),
            source_artifacts=authorized_sources,
            message="Provider conversion did not expose usable main Markdown",
            metadata=conversion_metadata,
        )

    source_only_candidate = ChecksumImportCandidate(
        source_artifact=source_artifact,
        provider_metadata_artifacts=provider_metadata_artifacts,
    )
    return _decision(
        provider=provider.provider_id,
        checksum=normalized_checksum,
        status=ChecksumImportDecisionStatus.READY,
        selected=source_only_candidate,
        candidates=(source_only_candidate,),
        source_artifacts=authorized_sources,
        message="No converted Markdown artifact is available for this source PDF",
    )


def source_artifact_is_authorized(
    source_artifact: SourceArtifact,
    *,
    authorized_group_ids: tuple[str, ...] | list[str] | set[str],
) -> bool:
    """Return whether request groups can access a source artifact."""

    return _access_policy_is_authorized(
        source_artifact.access_policy,
        authorized_group_ids=authorized_group_ids,
    )


def provider_metadata_artifacts_for_source(
    *,
    provider: DocumentSourceProvider,
    source_artifact: SourceArtifact,
    artifacts: list[SourceArtifact] | tuple[SourceArtifact, ...],
) -> tuple[SourceArtifact, ...]:
    """Return provider-declared metadata sidecars associated with a source PDF."""

    metadata_artifacts_for_source = getattr(
        provider,
        "provider_metadata_artifacts_for_source",
        None,
    )
    if not callable(metadata_artifacts_for_source):
        return ()
    typed_metadata_artifacts_for_source = cast(
        Callable[
            [SourceArtifact, list[SourceArtifact] | tuple[SourceArtifact, ...]],
            tuple[SourceArtifact, ...] | list[SourceArtifact],
        ],
        metadata_artifacts_for_source,
    )
    metadata_candidates = typed_metadata_artifacts_for_source(
        source_artifact,
        artifacts,
    )
    return tuple(
        sorted(
            metadata_candidates,
            key=lambda artifact: (
                str(artifact.display_name or "").strip().lower(),
                artifact.artifact_id,
            ),
        )
    )


def _access_policy_is_authorized(
    access_policy: SourceAccessPolicy,
    *,
    authorized_group_ids: tuple[str, ...] | list[str] | set[str],
) -> bool:
    if access_policy.scope is SourceAccessScope.GLOBAL:
        return True
    if access_policy.scope is not SourceAccessScope.RESTRICTED:
        return False

    authorized = _normalize_group_ids(authorized_group_ids)
    required = _normalize_group_ids(access_policy.mods)
    return bool(authorized.intersection(required))


def _source_artifacts(artifacts: list[SourceArtifact]) -> tuple[SourceArtifact, ...]:
    return tuple(
        artifact
        for artifact in artifacts
        if artifact.role is SourceArtifactRole.SOURCE_PDF
    )


def _converted_artifacts_for_source(
    *,
    source_artifact: SourceArtifact,
    artifacts: list[SourceArtifact],
) -> tuple[SourceArtifact, ...]:
    children: list[SourceArtifact] = []
    for artifact in artifacts:
        if artifact.role is not SourceArtifactRole.CONVERTED_TEXT:
            continue
        if artifact.parent_artifact_id:
            if artifact.parent_artifact_id == source_artifact.artifact_id:
                children.append(artifact)
            continue
        if _same_reference(source_artifact, artifact):
            children.append(artifact)

    return tuple(children)


async def _request_conversion_if_supported(
    *,
    provider: DocumentSourceProvider,
    source_artifact: SourceArtifact,
    request_bearer_token: str | None,
) -> SourceConversionResult | None:
    request_conversion = getattr(provider, "request_conversion", None)
    if not callable(request_conversion):
        return None
    if not _reference_lookup_value(source_artifact):
        return None
    typed_request_conversion = cast(
        Callable[..., Awaitable[SourceConversionResult]],
        request_conversion,
    )
    return await typed_request_conversion(
        source_artifact,
        wait=False,
        request_bearer_token=request_bearer_token,
    )


def _same_reference(source_artifact: SourceArtifact, artifact: SourceArtifact) -> bool:
    if source_artifact.reference_id and artifact.reference_id:
        return source_artifact.reference_id == artifact.reference_id
    if source_artifact.reference_curie and artifact.reference_curie:
        return source_artifact.reference_curie == artifact.reference_curie
    return not artifact.reference_id and not artifact.reference_curie


def _select_reference_markdown_artifact(
    *,
    provider: DocumentSourceProvider,
    source_artifact: SourceArtifact,
    artifacts: list[SourceArtifact],
) -> tuple[SourceArtifact | None, int]:
    reference_key = _reference_lookup_value(source_artifact)
    candidates = [
        artifact
        for artifact in artifacts
        if _is_converted_markdown(artifact)
        and _reference_lookup_value(artifact) == reference_key
    ]
    if not candidates:
        return None, 0
    return select_preferred_main_text_artifact(provider, candidates)


def _reference_lookup_value(artifact: SourceArtifact) -> str:
    return artifact.reference_curie or artifact.reference_id or ""


def _conversion_metadata(result: SourceConversionResult) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "conversion_status": result.status.value,
    }
    if result.job_id:
        metadata["conversion_job_id"] = result.job_id
    if result.converted_classes:
        metadata["converted_classes"] = list(result.converted_classes)
    if result.per_file_progress:
        metadata["per_file_progress"] = list(result.per_file_progress)
    if result.per_mod_status:
        metadata["per_mod_status"] = list(result.per_mod_status)
    return metadata


def _is_converted_markdown(artifact: SourceArtifact) -> bool:
    return (
        artifact.role is SourceArtifactRole.CONVERTED_TEXT
        and artifact.artifact_format is SourceArtifactFormat.MARKDOWN
    )


def _normalize_group_ids(values: tuple[str, ...] | list[str] | set[str]) -> set[str]:
    return {str(value).strip().upper() for value in values if str(value).strip()}


def _require_checksum(checksum: str) -> str:
    normalized = (checksum or "").strip()
    if not normalized:
        raise DocumentSourceError("checksum is required")
    return normalized


def _decision(
    *,
    provider: str,
    checksum: str,
    status: ChecksumImportDecisionStatus,
    selected: ChecksumImportCandidate | None = None,
    candidates: tuple[ChecksumImportCandidate, ...] = (),
    source_artifacts: tuple[SourceArtifact, ...] = (),
    message: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> ChecksumImportDecision:
    return ChecksumImportDecision(
        status=status,
        provider=provider,
        checksum=checksum,
        selected=selected,
        candidates=candidates,
        source_artifacts=source_artifacts,
        message=message,
        metadata=metadata or {},
    )
