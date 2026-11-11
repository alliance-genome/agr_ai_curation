"""ABC Literature document-source provider adapter."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from enum import Enum
import logging
import re
from typing import Any, Self, TypeGuard

from src.lib.document_sources.models import (
    DocumentSourceAccessDenied,
    DocumentSourceError,
    DocumentSourceReferenceNotFound,
    DocumentSourceHealth,
    DocumentSourceProvider,
    NormalizedSourceIdentifier,
    SourceAccessPolicy,
    SourceAccessScope,
    SourceArtifact,
    SourceArtifactFormat,
    SourceArtifactRole,
    SourceArtifactStatus,
    SourceConversionResult,
    SourceConversionStatus,
    SourceReference,
)
from agr_ai_curation_alliance.literature.client import (
    ABCLiteratureClient,
    ABCLiteratureClientError,
    ABCLiteratureHTTPError,
)

from agr_ai_curation_alliance.document_sources import ABC_LITERATURE_PROVIDER_ID

_FIGURE_METADATA_FILE_CLASSES = {
    "converted_main_figure_metadata",
    "converted_supplement_figure_metadata",
}

_ARTIFACT_STATUS_KEYS = (
    "status",
    "conversion_status",
    "file_publication_status",
)

# ABC cross-reference prefix -> provider-neutral external-ID key read by core.
_CROSS_REFERENCE_PREFIX_KEYS = {
    "PMID": "pmid",
    "DOI": "doi",
    "PMCID": "pmcid",
}

# Artifact metadata key holding a file's named MODs from ``referencefile_mods``.
# Source-PDF ranking reads it; the access policy records named MODs only when
# they restrict the file.
_MOD_ABBREVIATIONS_METADATA_KEY = "mod_abbreviations"

logger = logging.getLogger(__name__)


def _canonical_publication_identifier(identifier: str) -> str:
    """ABC cross-reference lookup requires ZFIN's full publication CURIE."""
    normalized = identifier.strip()
    if re.fullmatch(r"ZDB-PUB-\d{6}-\d+", normalized):
        return f"ZFIN:{normalized}"
    return normalized


class ABCLiteratureDocumentSourceProvider(DocumentSourceProvider):
    """Map ABC Literature REST payloads into provider-neutral source objects."""

    provider_id = ABC_LITERATURE_PROVIDER_ID

    def __init__(self, client: ABCLiteratureClient):
        self._client = client

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._client.aclose()

    async def resolve_reference(
        self,
        identifier: str,
        *,
        request_bearer_token: str | None = None,
    ) -> SourceReference:
        normalized = _canonical_publication_identifier(identifier)
        if not normalized:
            raise DocumentSourceError("identifier is required")

        try:
            upper = normalized.upper()
            if normalized.isdigit() or upper.startswith("PMID:"):
                payload = await self._client.lookup_external_curie(
                    f"PMID:{normalized}" if normalized.isdigit() else normalized,
                    request_bearer_token=request_bearer_token,
                )
                # The PMID lookup returns only the ABC curie and title; the
                # reference record carries the cross-references (PMID, DOI).
                # No curie means ABC does not hold the paper.
                found_curie = _first_string(payload, "reference_curie")
                if found_curie is not None:
                    payload = await self._client.show_reference(
                        found_curie,
                        request_bearer_token=request_bearer_token,
                    )
            elif upper.startswith(("AGRKB:", "ABC:")):
                payload = await self._client.show_reference(
                    normalized,
                    request_bearer_token=request_bearer_token,
                )
            else:
                payload = await self._client.lookup_cross_reference(
                    normalized,
                    request_bearer_token=request_bearer_token,
                )
        except ABCLiteratureHTTPError as exc:
            if exc.status_code == 404 and exc.endpoint.startswith("/reference/"):
                raise DocumentSourceReferenceNotFound(
                    "No matching reference was found in ABC Literature. "
                    "Check the publication identifier or upload the PDF."
                ) from exc
            raise DocumentSourceError("ABC Literature reference lookup failed") from exc
        except ABCLiteratureClientError as exc:
            raise DocumentSourceError("ABC Literature reference lookup failed") from exc

        return self._map_reference(payload)

    def normalize_identifier(self, identifier: str) -> NormalizedSourceIdentifier:
        original = (identifier or "").strip()
        if not original:
            return NormalizedSourceIdentifier(
                original=identifier,
                normalized=None,
                error="Identifier is empty",
            )
        if original.isdigit():
            return NormalizedSourceIdentifier(original=original, normalized=f"PMID:{original}")

        upper = original.upper()
        for prefix in ("PMID:", "AGRKB:", "ABC:"):
            if upper.startswith(prefix):
                value = original.split(":", 1)[1].strip()
                if value:
                    return NormalizedSourceIdentifier(
                        original=original,
                        normalized=f"{prefix}{value}",
                    )

        pubmed_prefix = "PUBMED ID"
        if upper.startswith(pubmed_prefix):
            value = original[len(pubmed_prefix):].strip(" :#")
            if value.isdigit():
                return NormalizedSourceIdentifier(
                    original=original,
                    normalized=f"PMID:{value}",
                )

        if _looks_like_provider_cross_reference(original):
            return NormalizedSourceIdentifier(
                original=original, normalized=_canonical_publication_identifier(original),
            )

        return NormalizedSourceIdentifier(
            original=original,
            normalized=None,
            error=(
                "Unsupported identifier. Use PMID, PubMed ID, AGRKB, ABC, "
                "or an ABC Literature cross-reference such as FBrf."
            ),
        )

    def is_main_text_artifact(self, artifact: SourceArtifact) -> bool:
        file_class = _artifact_file_class(artifact)
        if file_class != "converted_merged_main":
            return False
        # Removed TEI-only main-text fallback — ABC's canonical contract requires
        # non-TEI converted_merged_main text (ALL-665 follow-up).
        return not _artifact_is_tei_derived(artifact)

    def checksum_match_uses_local_pdf(self, source_artifact: SourceArtifact) -> bool:
        """Keep checksum-matched supplements tied to their exact uploaded bytes."""

        return _artifact_file_class(source_artifact) == "supplement"

    def main_text_artifact_sort_key(self, artifact: SourceArtifact) -> tuple[int, ...]:
        display_name = str(artifact.display_name or "").strip().lower()
        return (0,) if "_nxml" in display_name else (1,)

    def is_reference_source_artifact(self, artifact: SourceArtifact) -> bool:
        """Accept only ABC's canonical main PDF: final, ``main`` class, type ``pdf``.

        These are the candidates ABC's own main-PDF lookup considers.
        Supplements and non-final or non-``pdf`` main files are never the
        paper an identifier import brings in.
        """

        metadata = artifact.metadata
        return (
            str(metadata.get("file_class") or "").strip().casefold() == "main"
            and str(metadata.get("file_publication_status") or "").strip().casefold()
            == "final"
            and str(metadata.get("pdf_type") or "").strip().casefold() == "pdf"
        )

    def reference_source_artifact_sort_key(
        self,
        artifact: SourceArtifact,
        authorized_group_ids: tuple[str, ...] | list[str] | set[str],
    ) -> tuple[int, ...]:
        """Prefer a curator's MOD PDF over ABC's shared PMC fallback.

        Callers rank only canonical main PDFs (``is_reference_source_artifact``).
        ABC intentionally keeps one final main PDF per MOD plus at most one
        shared/null-MOD PMC PDF. Its own main-PDF lookup applies this
        precedence for a requested MOD: a PDF with an entry for that MOD, then
        a PDF with a no-MOD entry. The MOD tier reads the file's own MOD
        entries, so a PDF tagged both no-MOD and with the curator's MOD still
        ranks first.

        Below ABC's precedence, AI Curation adds its own tie-break, which ABC
        does not have (ABC takes whichever no-MOD PDF it reads first): among
        PDFs open beyond the curator's MOD (``GLOBAL`` or ``ANY_GROUP``), one
        with no named MOD entries (the general copy) outranks one that also
        names another MOD. This keeps the general copy and avoids an
        ambiguous-match error when a reference holds both. Any remaining equal
        ranks stay tied so callers do not guess, for example when a curator
        spans several MODs or two PDFs are both no-MOD only.
        """

        authorized = {
            str(group_id).strip().casefold()
            for group_id in authorized_group_ids
            if str(group_id).strip()
        }
        artifact_mods = {
            str(mod).strip().casefold()
            for mod in artifact.metadata.get(_MOD_ABBREVIATIONS_METADATA_KEY) or ()
            if str(mod).strip()
        }
        if authorized.intersection(artifact_mods):
            return (0,)
        if artifact.access_policy.scope in {
            SourceAccessScope.GLOBAL,
            SourceAccessScope.ANY_GROUP,
        }:
            # AI Curation's own tie-break (not ABC's): the general copy, with no
            # named MOD entries, before a shared PDF that names another MOD.
            return (1,) if not artifact_mods else (2,)
        return (10,)

    def conversion_exposes_main_text(self, result: SourceConversionResult) -> bool:
        if "converted_merged_main" in result.converted_classes:
            return True
        for progress in result.per_file_progress:
            converted = progress.get("converted")
            if not isinstance(converted, Mapping):
                continue
            if converted.get("file_class") != "converted_merged_main":
                continue
            # ABC lists legacy TEI-derived rows in per-file progress until its
            # overwrite_tei_md conversion replaces them; they are not main text.
            if _display_name_is_tei_derived(converted.get("display_name")):
                continue
            return True
        return False

    def conversion_progress_percentage(self, result: SourceConversionResult) -> int:
        if result.status is SourceConversionStatus.RUNNING:
            return 35 if self.conversion_exposes_main_text(result) else 20
        if result.status is SourceConversionStatus.CONVERTED:
            return 35
        if result.status in {
            SourceConversionStatus.FAILED,
            SourceConversionStatus.NO_SOURCES,
        }:
            return 100
        return 15

    def conversion_progress_message(self, result: SourceConversionResult) -> str:
        if self.conversion_exposes_main_text(result):
            return "ABC Literature main text is ready; importing converted Markdown"
        if result.status is SourceConversionStatus.RUNNING:
            pending_count = _count_progress_status(result, "pending")
            if pending_count:
                noun = "file" if pending_count == 1 else "files"
                return f"ABC Literature conversion running ({pending_count} {noun} pending)"
            return "ABC Literature conversion running"
        if result.status is SourceConversionStatus.CONVERTED:
            return "ABC Literature conversion completed; locating converted Markdown"
        if result.status is SourceConversionStatus.FAILED:
            return self.conversion_failure_message(result)
        if result.status is SourceConversionStatus.NO_SOURCES:
            return "ABC Literature found no convertible source files"
        return "Waiting for ABC Literature conversion status"

    def conversion_failure_message(self, result: SourceConversionResult) -> str:
        if result.error_message:
            return result.error_message
        failures = []
        for progress in result.per_file_progress:
            if str(progress.get("status") or "").strip().lower() != "failed":
                continue
            source = progress.get("source")
            source_name = source.get("display_name") if isinstance(source, Mapping) else None
            error = progress.get("error")
            if source_name and error:
                failures.append(f"{source_name}: {error}")
            elif error:
                failures.append(str(error))
        if failures:
            return "; ".join(failures)
        return "ABC Literature conversion failed"

    def provider_metadata_artifacts_for_source(
        self,
        source_artifact: SourceArtifact,
        artifacts: list[SourceArtifact] | tuple[SourceArtifact, ...],
    ) -> tuple[SourceArtifact, ...]:
        """Return ABC figure-metadata JSON sidecars for a source PDF."""

        metadata_candidates = [
            artifact
            for artifact in artifacts
            if artifact.role is SourceArtifactRole.PROVIDER_METADATA
            and artifact.artifact_format is SourceArtifactFormat.JSON
            and artifact.status in {
                SourceArtifactStatus.AVAILABLE,
                SourceArtifactStatus.UNKNOWN,
            }
            and _same_reference(source_artifact, artifact)
        ]
        if not metadata_candidates:
            return ()

        expected_class = _expected_figure_metadata_file_class(source_artifact)
        if expected_class:
            class_matched = [
                artifact
                for artifact in metadata_candidates
                if _artifact_file_class(artifact) == expected_class
            ]
            if class_matched:
                metadata_candidates = class_matched

        exact_display_names = _figure_artifact_display_names_for_source(
            source_artifact=source_artifact,
            artifacts=artifacts,
        )
        if exact_display_names:
            metadata_candidates = [
                artifact
                for artifact in metadata_candidates
                if str(artifact.display_name or "").strip() in exact_display_names
            ]
        else:
            display_prefixes = _source_display_prefixes(source_artifact)
            if display_prefixes:
                display_matched = [
                    artifact
                    for artifact in metadata_candidates
                    if _artifact_display_name_matches_prefix(artifact, display_prefixes)
                ]
                if display_matched:
                    metadata_candidates = display_matched

        return tuple(
            sorted(
                metadata_candidates,
                key=lambda artifact: (
                    str(artifact.display_name or "").strip().lower(),
                    artifact.artifact_id,
                ),
            )
        )

    async def list_artifacts(
        self,
        reference: SourceReference | str,
        *,
        request_bearer_token: str | None = None,
    ) -> list[SourceArtifact]:
        reference_lookup = _reference_lookup_value(reference)
        try:
            payload = await self._client.show_referencefiles(
                reference_lookup,
                request_bearer_token=request_bearer_token,
            )
        except ABCLiteratureClientError as exc:
            raise DocumentSourceError("ABC Literature artifact listing failed") from exc
        files = _extract_referencefiles(payload)
        artifacts: list[SourceArtifact] = []
        seen_artifact_ids: set[str] = set()
        for file_payload in files:
            source_artifact = self._map_referencefile(
                file_payload,
                reference=reference,
                default_available_when_status_missing=_is_converted_payload(
                    file_payload
                ),
            )
            if source_artifact.artifact_id not in seen_artifact_ids:
                artifacts.append(source_artifact)
                seen_artifact_ids.add(source_artifact.artifact_id)
            for converted in _extract_converted_referencefiles(file_payload):
                converted_artifact = self._map_referencefile(
                    converted,
                    parent_artifact_id=source_artifact.artifact_id,
                    inherited_access_policy=source_artifact.access_policy,
                    source_reference_id=source_artifact.reference_id,
                    source_reference_curie=source_artifact.reference_curie,
                    default_available_when_status_missing=True,
                )
                if converted_artifact.artifact_id in seen_artifact_ids:
                    continue
                artifacts.append(converted_artifact)
                seen_artifact_ids.add(converted_artifact.artifact_id)
        return artifacts

    async def find_artifacts_by_checksum(
        self,
        checksum: str,
        *,
        request_bearer_token: str | None = None,
    ) -> list[SourceArtifact]:
        try:
            payloads = await self._client.lookup_referencefile_by_md5(
                checksum,
                request_bearer_token=request_bearer_token,
            )
        except ABCLiteratureClientError as exc:
            raise DocumentSourceError("ABC Literature checksum lookup failed") from exc
        artifacts: list[SourceArtifact] = []
        for payload in payloads:
            source_artifact = self._map_referencefile(payload)
            artifacts.append(source_artifact)
            for converted in _extract_converted_referencefiles(payload):
                artifacts.append(
                    self._map_referencefile(
                        converted,
                        parent_artifact_id=source_artifact.artifact_id,
                        inherited_access_policy=source_artifact.access_policy,
                        source_reference_id=source_artifact.reference_id,
                        source_reference_curie=source_artifact.reference_curie,
                        default_available_when_status_missing=True,
                    )
                )
        return artifacts

    async def download_artifact(
        self,
        artifact_id: str,
        *,
        request_bearer_token: str | None = None,
    ) -> bytes:
        try:
            return await self._client.download_referencefile(
                artifact_id,
                request_bearer_token=request_bearer_token,
            )
        except ABCLiteratureHTTPError as exc:
            if exc.status_code == 403:
                raise DocumentSourceAccessDenied(
                    "Document-source artifact access was denied"
                ) from exc
            raise DocumentSourceError(
                "ABC Literature artifact download failed"
            ) from exc
        except ABCLiteratureClientError as exc:
            raise DocumentSourceError("ABC Literature artifact download failed") from exc

    async def request_conversion(
        self,
        reference: SourceReference | SourceArtifact | str,
        *,
        wait: bool = False,
        request_bearer_token: str | None = None,
    ) -> SourceConversionResult:
        reference_lookup = _conversion_reference_lookup_value(reference)
        try:
            payload = await self._client.request_referencefile_conversion(
                reference_lookup,
                wait=wait,
                request_bearer_token=request_bearer_token,
            )
        except ABCLiteratureClientError as exc:
            raise DocumentSourceError("ABC Literature conversion request failed") from exc
        return _map_conversion_result(payload, provider=self.provider_id)

    async def health(self) -> DocumentSourceHealth:
        try:
            await self._client.search_references({"limit": 1})
        except ABCLiteratureHTTPError as exc:
            if exc.status_code == 401:
                return DocumentSourceHealth(
                    provider=self.provider_id,
                    ok=True,
                    message="ABC Literature endpoint reachable; request authentication required",
                    metadata={"auth": "request_bearer"},
                )
            return DocumentSourceHealth(
                provider=self.provider_id,
                ok=False,
                message=str(exc),
            )
        except ABCLiteratureClientError as exc:
            return DocumentSourceHealth(
                provider=self.provider_id,
                ok=False,
                message=str(exc),
            )
        return DocumentSourceHealth(
            provider=self.provider_id,
            ok=True,
            message="ABC Literature search endpoint reachable",
        )

    def _map_reference(self, payload: Mapping[str, Any]) -> SourceReference:
        reference_id = _first_string(payload, "reference_id", "id")
        reference_curie = _first_string(payload, "reference_curie", "curie")
        return SourceReference(
            provider=self.provider_id,
            reference_id=reference_id,
            reference_curie=reference_curie,
            title=_first_string(payload, "title", "display_name"),
            external_ids=_extract_external_ids(payload),
            metadata=_compact_metadata(
                payload,
                exclude={"abstract", "full_text", "files", "referencefiles"},
            ),
        )

    def _map_referencefile(
        self,
        payload: Mapping[str, Any],
        *,
        reference: SourceReference | str | None = None,
        parent_artifact_id: str | None = None,
        inherited_access_policy: SourceAccessPolicy | None = None,
        source_reference_id: str | None = None,
        source_reference_curie: str | None = None,
        default_available_when_status_missing: bool = False,
    ) -> SourceArtifact:
        artifact_id = _first_string(payload, "referencefile_id", "id")
        if not artifact_id:
            raise DocumentSourceError("ABC Literature referencefile payload missing id")

        reference_id = (
            source_reference_id
            or _first_string(payload, "reference_id")
            or _reference_id(reference)
        )
        reference_curie = (
            source_reference_curie
            or _first_string(payload, "reference_curie")
            or _reference_curie(reference)
        )
        file_class = _first_string(payload, "file_class") or ""
        file_extension = _first_string(payload, "file_extension") or ""
        role = _map_artifact_role(file_class=file_class, extension=file_extension)
        status = _map_artifact_status(payload)
        if (
            default_available_when_status_missing
            and status is SourceArtifactStatus.UNKNOWN
            and not _has_artifact_status_signal(payload)
        ):
            status = SourceArtifactStatus.AVAILABLE
        mod_abbreviations = _extract_access_group_ids(payload)
        access_policy = inherited_access_policy or _map_access_policy(
            payload,
            mod_abbreviations=mod_abbreviations,
            source_pdf_null_mods_grant_any_group=role is SourceArtifactRole.SOURCE_PDF,
            license_access=_license_access(payload, reference),
        )
        metadata = dict(
            _compact_metadata(
                payload,
                exclude={"converted_referencefiles", "referencefile_mods"},
            )
        )
        if mod_abbreviations:
            metadata[_MOD_ABBREVIATIONS_METADATA_KEY] = tuple(sorted(mod_abbreviations))

        return SourceArtifact(
            provider=self.provider_id,
            artifact_id=artifact_id,
            role=role,
            artifact_format=_map_artifact_format(file_extension),
            status=status,
            reference_id=reference_id,
            reference_curie=reference_curie,
            display_name=_first_string(payload, "display_name", "filename", "name"),
            md5sum=_first_string(payload, "md5sum", "md5"),
            parent_artifact_id=parent_artifact_id,
            access_policy=access_policy,
            metadata=metadata,
        )


def _reference_lookup_value(reference: SourceReference | str) -> str:
    if isinstance(reference, SourceReference):
        value = reference.reference_curie or reference.reference_id
    else:
        value = reference
    if not value:
        raise DocumentSourceError("reference_curie or reference_id is required")
    return value


def _conversion_reference_lookup_value(reference: SourceReference | SourceArtifact | str) -> str:
    if isinstance(reference, SourceArtifact):
        value = reference.reference_curie or reference.reference_id
    else:
        value = _reference_lookup_value(reference)
    if not value:
        raise DocumentSourceError("reference_curie or reference_id is required")
    return value


def _reference_id(reference: SourceReference | str | None) -> str | None:
    if isinstance(reference, SourceReference):
        return reference.reference_id
    return None


def _map_conversion_result(
    payload: Mapping[str, Any],
    *,
    provider: str,
) -> SourceConversionResult:
    converted_classes = list(_string_tuple(payload.get("converted_classes")))
    if any(
        item.get("main_converted") is True
        for item in _mapping_tuple(payload.get("per_mod_status"))
    ) and "converted_merged_main" not in converted_classes:
        converted_classes.append("converted_merged_main")

    return SourceConversionResult(
        provider=provider,
        status=_map_conversion_status(_first_string(payload, "status")),
        reference_id=_first_string(payload, "reference_id"),
        reference_curie=_first_string(payload, "reference_curie"),
        job_id=_first_string(payload, "job_id"),
        error_message=_first_string(payload, "error_message"),
        converted_classes=tuple(converted_classes),
        per_file_progress=_mapping_tuple(payload.get("per_file_progress")),
        metadata=_compact_metadata(
            payload,
            exclude={"per_file_progress", "per_mod_status"},
        ),
    )


def _map_conversion_status(status: str | None) -> SourceConversionStatus:
    normalized = (status or "").strip().lower()
    if normalized == "converted":
        return SourceConversionStatus.CONVERTED
    if normalized == "running":
        return SourceConversionStatus.RUNNING
    if normalized == "failed":
        return SourceConversionStatus.FAILED
    if normalized == "no_sources":
        return SourceConversionStatus.NO_SOURCES
    return SourceConversionStatus.UNKNOWN


def _count_progress_status(result: SourceConversionResult, status: str) -> int:
    return sum(
        1
        for progress in result.per_file_progress
        if str(progress.get("status") or "").strip().lower() == status
    )


def _string_tuple(value: object) -> tuple[str, ...]:
    if not _is_non_string_sequence(value):
        return ()
    return tuple(item for item in (_string_or_none(raw) for raw in value) if item)


def _mapping_tuple(value: object) -> tuple[Mapping[str, Any], ...]:
    if not _is_non_string_sequence(value):
        return ()
    return tuple(item for item in value if isinstance(item, Mapping))


def _reference_curie(reference: SourceReference | str | None) -> str | None:
    if isinstance(reference, SourceReference):
        return reference.reference_curie
    if isinstance(reference, str) and ":" in reference:
        return reference
    return None


def _extract_referencefiles(
    payload: Mapping[str, Any] | Sequence[Any],
) -> list[Mapping[str, Any]]:
    if _is_non_string_sequence(payload):
        return [item for item in payload if isinstance(item, Mapping)]

    if not isinstance(payload, Mapping):
        return []

    for key in ("referencefiles", "reference_files", "files", "results"):
        value = payload.get(key)
        if _is_non_string_sequence(value):
            return [item for item in value if isinstance(item, Mapping)]

    return []


def _extract_converted_referencefiles(
    payload: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    value = payload.get("converted_referencefiles")
    if not _is_non_string_sequence(value):
        return []
    return [item for item in value if isinstance(item, Mapping)]


def _is_converted_payload(payload: Mapping[str, Any]) -> bool:
    file_class = _first_string(payload, "file_class", "class") or ""
    extension = _first_string(
        payload,
        "file_extension",
        "extension",
        "file_type",
        "type",
    ) or ""
    return _map_artifact_role(
        file_class=file_class,
        extension=extension,
    ) is SourceArtifactRole.CONVERTED_TEXT


def _looks_like_provider_cross_reference(identifier: str) -> bool:
    normalized = identifier.strip()
    if not normalized or any(char.isspace() for char in normalized):
        return False
    lowered = normalized.lower()
    if lowered.startswith(("http://", "https://", "doi:")):
        return False
    return any(char.isalpha() for char in normalized) and any(
        char.isdigit() for char in normalized
    )


def _extract_external_ids(
    payload: Mapping[str, Any],
) -> dict[str, str | Sequence[str]]:
    """Read publication identifiers from ABC's ``cross_references`` objects.

    ABC reference records carry PMID, DOI and PMCID only as cross-references
    (``{"curie": "PMID:123", "curie_prefix": "PMID", "is_obsolete": ...}``).
    Obsolete entries are skipped. ``pmid``/``doi``/``pmcid`` are chosen by
    ``curie_prefix`` and hold the local identifier after the curie's first
    ``:`` (``"123"``, ``"10.1/x"``); an entry without ``curie_prefix`` is
    listed under ``cross_references`` only. ABC allows at most
    one current cross-reference per prefix on a reference; if a payload ever
    carries more than one, no identifier is chosen for that prefix and the
    curies stay listed under ``cross_references``.
    """

    raw_cross_references = payload.get("cross_references")
    if not _is_non_string_sequence(raw_cross_references):
        return {}

    curies: list[str] = []
    local_ids_by_key: dict[str, list[str]] = {}
    for item in raw_cross_references:
        if not isinstance(item, Mapping) or item.get("is_obsolete") is True:
            continue
        curie = _first_string(item, "curie")
        if curie is None:
            continue
        curies.append(curie)
        curie_prefix = _first_string(item, "curie_prefix")
        key = _CROSS_REFERENCE_PREFIX_KEYS.get(curie_prefix) if curie_prefix else None
        _, separator, local_id = curie.partition(":")
        if key is not None and separator and local_id.strip():
            local_ids_by_key.setdefault(key, []).append(local_id.strip())

    external_ids: dict[str, str | Sequence[str]] = {}
    for key, local_ids in local_ids_by_key.items():
        if len(local_ids) == 1:
            external_ids[key] = local_ids[0]
            continue
        logger.warning(
            "ABC reference %s has %d current %s cross-references; leaving %s unset",
            _first_string(payload, "curie", "reference_curie"),
            len(local_ids),
            key,
            key,
        )
    if curies:
        external_ids["cross_references"] = curies
    return external_ids


class _LicenseAccess(Enum):
    """What a reference's copyright license decides in ABC's ``download_file``."""

    OPEN = "open"  # open_access true: every caller, even one with no MOD access
    REFUSED = "refused"  # license with open_access unset: no caller at all
    MOD_RULE = "mod_rule"  # no license, or open_access false: the MOD rows decide


def _license_access(
    payload: Mapping[str, Any],
    reference: SourceReference | str | None,
) -> _LicenseAccess:
    """Read the license decision from the structured ABC fields in hand.

    ``by_md5`` rows send ``open_access`` as ``bool(license and
    license.open_access)``, so there an unset flag cannot be told from
    ``false`` and applies the MOD rule. The reference record
    (``ReferenceSchemaShow``, held when listing an identifier import's files)
    sends ``copyright_license_name`` (non-null in ABC whenever a license is
    set) and the raw ``copyright_license_open_access``, so a license whose flag
    is unset is recognized and, as in ABC, refused. A bare curie or id carries
    no record and applies the MOD rule.
    """

    if payload.get("open_access") is True:
        return _LicenseAccess.OPEN
    if not isinstance(reference, SourceReference):
        return _LicenseAccess.MOD_RULE
    open_access = reference.metadata.get("copyright_license_open_access")
    if open_access is True:
        return _LicenseAccess.OPEN
    if open_access is None and reference.metadata.get("copyright_license_name"):
        return _LicenseAccess.REFUSED
    return _LicenseAccess.MOD_RULE


def _map_access_policy(
    payload: Mapping[str, Any],
    *,
    mod_abbreviations: set[str],
    source_pdf_null_mods_grant_any_group: bool,
    license_access: _LicenseAccess,
) -> SourceAccessPolicy:
    """Map one ABC file row to the access ABC's ``download_file`` grants.

    ABC serves a file when its reference's copyright license is open access
    (any caller); otherwise, unless the license's flag is unset (no caller),
    to a caller with some MOD access when any ``referencefile_mods`` row has
    no MOD, or to a caller whose MOD a row names. The no-MOD grant is honored
    only on source PDFs: converted rows inherit their source PDF's policy, and
    a no-MOD row on a converted row alone grants nothing. A file with no
    usable MOD rows and no open-access signal stays ``UNKNOWN`` and is denied.
    """

    if license_access is _LicenseAccess.OPEN:
        return SourceAccessPolicy(scope=SourceAccessScope.GLOBAL)
    if license_access is _LicenseAccess.REFUSED:
        return SourceAccessPolicy(scope=SourceAccessScope.UNKNOWN)
    if source_pdf_null_mods_grant_any_group and _has_null_mod_entry(payload):
        return SourceAccessPolicy(scope=SourceAccessScope.ANY_GROUP)
    if mod_abbreviations:
        return SourceAccessPolicy(
            scope=SourceAccessScope.RESTRICTED,
            group_ids=tuple(sorted(mod_abbreviations)),
        )
    return SourceAccessPolicy(scope=SourceAccessScope.UNKNOWN)


def _extract_access_group_ids(payload: Mapping[str, Any]) -> set[str]:
    raw_mods = payload.get("referencefile_mods") or []
    if not _is_non_string_sequence(raw_mods):
        return set()

    group_ids: set[str] = set()
    for item in raw_mods:
        if not isinstance(item, Mapping) or "mod_abbreviation" not in item:
            continue
        value = item.get("mod_abbreviation")
        if isinstance(value, str) and value.strip():
            group_ids.add(value.strip())
    return group_ids


def _has_null_mod_entry(payload: Mapping[str, Any]) -> bool:
    raw_mods = payload.get("referencefile_mods") or []
    if not _is_non_string_sequence(raw_mods):
        return False

    for item in raw_mods:
        if (
            isinstance(item, Mapping)
            and "mod_abbreviation" in item
            and item["mod_abbreviation"] is None
        ):
            return True
    return False


def _same_reference(source_artifact: SourceArtifact, artifact: SourceArtifact) -> bool:
    if source_artifact.reference_id and artifact.reference_id:
        return source_artifact.reference_id == artifact.reference_id
    if source_artifact.reference_curie and artifact.reference_curie:
        return source_artifact.reference_curie == artifact.reference_curie
    return not artifact.reference_id and not artifact.reference_curie


def _artifact_file_class(artifact: SourceArtifact) -> str:
    return str(artifact.metadata.get("file_class") or "").strip().lower()


def _artifact_is_tei_derived(artifact: SourceArtifact) -> bool:
    return _display_name_is_tei_derived(artifact.display_name)


def _display_name_is_tei_derived(display_name: object) -> bool:
    return "_tei" in str(display_name or "").strip().lower()


def _expected_figure_metadata_file_class(
    source_artifact: SourceArtifact,
) -> str | None:
    source_class = _artifact_file_class(source_artifact)
    if source_class == "main":
        return "converted_main_figure_metadata"
    if source_class == "supplement":
        return "converted_supplement_figure_metadata"
    return None


def _expected_figure_file_class(source_artifact: SourceArtifact) -> str | None:
    source_class = _artifact_file_class(source_artifact)
    if source_class == "main":
        return "converted_main_figure"
    if source_class == "supplement":
        return "converted_supplement_figure"
    return None


def _figure_artifact_display_names_for_source(
    *,
    source_artifact: SourceArtifact,
    artifacts: list[SourceArtifact] | tuple[SourceArtifact, ...],
) -> set[str]:
    expected_class = _expected_figure_file_class(source_artifact)
    prefixes = _source_display_prefixes(source_artifact)
    display_names: set[str] = set()
    for artifact in artifacts:
        artifact_class = _artifact_file_class(artifact)
        if expected_class and artifact_class != expected_class:
            continue
        if not expected_class and artifact_class not in {
            "converted_main_figure",
            "converted_supplement_figure",
        }:
            continue
        if artifact.status not in {
            SourceArtifactStatus.AVAILABLE,
            SourceArtifactStatus.UNKNOWN,
        }:
            continue
        if not _same_reference(source_artifact, artifact):
            continue
        display_name = str(artifact.display_name or "").strip()
        if not display_name:
            continue
        if prefixes and not any(display_name.startswith(prefix) for prefix in prefixes):
            continue
        display_names.add(display_name)
    return display_names


def _source_display_prefixes(source_artifact: SourceArtifact) -> tuple[str, ...]:
    display_name = str(source_artifact.display_name or "").strip()
    if not display_name:
        return ()
    prefixes = [f"{display_name}_image_"]
    if "." in display_name:
        stem = display_name.rsplit(".", 1)[0]
        if stem and stem != display_name:
            prefixes.append(f"{stem}_image_")
    return tuple(dict.fromkeys(prefixes))


def _artifact_display_name_matches_prefix(
    artifact: SourceArtifact,
    prefixes: tuple[str, ...],
) -> bool:
    display_name = str(artifact.display_name or "").strip()
    return any(display_name.startswith(prefix) for prefix in prefixes)


def _map_artifact_role(*, file_class: str, extension: str) -> SourceArtifactRole:
    normalized_class = file_class.strip().lower()
    normalized_extension = extension.strip().lower()
    if normalized_extension == "pdf" or normalized_class in {
        "main",
        "source_pdf",
        "pdf",
    }:
        return SourceArtifactRole.SOURCE_PDF
    if (
        normalized_class in _FIGURE_METADATA_FILE_CLASSES
        and normalized_extension == "json"
    ):
        return SourceArtifactRole.PROVIDER_METADATA
    if normalized_class.startswith("converted") or normalized_extension in {
        "md",
        "xml",
        "txt",
    }:
        return SourceArtifactRole.CONVERTED_TEXT
    return SourceArtifactRole.UNKNOWN


def _map_artifact_format(extension: str) -> SourceArtifactFormat:
    normalized = extension.strip().lower()
    if normalized == "pdf":
        return SourceArtifactFormat.PDF
    if normalized in {"md", "markdown"}:
        return SourceArtifactFormat.MARKDOWN
    if normalized == "xml":
        return SourceArtifactFormat.XML
    if normalized == "txt":
        return SourceArtifactFormat.TEXT
    if normalized == "json":
        return SourceArtifactFormat.JSON
    return SourceArtifactFormat.UNKNOWN


def _map_artifact_status(payload: Mapping[str, Any]) -> SourceArtifactStatus:
    status = _first_string(payload, *_ARTIFACT_STATUS_KEYS)
    normalized = (status or "").strip().lower()
    if normalized in {
        "available",
        "done",
        "complete",
        "completed",
        "final",
        "published",
    }:
        return SourceArtifactStatus.AVAILABLE
    if normalized in {"running", "pending", "processing", "in_progress"}:
        return SourceArtifactStatus.RUNNING
    if normalized in {"failed", "error"}:
        return SourceArtifactStatus.FAILED
    return SourceArtifactStatus.UNKNOWN


def _has_artifact_status_signal(payload: Mapping[str, Any]) -> bool:
    for key in _ARTIFACT_STATUS_KEYS:
        value = payload.get(key)
        if isinstance(value, str):
            if value.strip():
                return True
            continue
        if value is not None:
            return True
    return False


def _first_string(payload: Mapping[str, Any], *keys: str) -> str | None:
    for key in keys:
        value = payload.get(key)
        normalized = _string_or_none(value)
        if normalized is not None:
            return normalized
    return None


def _string_or_none(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, int):
        return str(value)
    return None


def _compact_metadata(
    payload: Mapping[str, Any],
    *,
    exclude: set[str],
) -> Mapping[str, Any]:
    metadata: dict[str, Any] = {}
    for key, value in payload.items():
        if key in exclude:
            continue
        if isinstance(value, str | int | float | bool) or value is None:
            metadata[key] = value
    return metadata


def _is_non_string_sequence(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, Sequence) and not isinstance(
        value,
        (str, bytes, bytearray),
    )
