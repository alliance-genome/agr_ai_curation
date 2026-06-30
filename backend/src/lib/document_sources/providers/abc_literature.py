"""ABC Literature document-source provider adapter."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, TypeGuard

from src.lib.document_sources.models import (
    DocumentSourceError,
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
from src.lib.literature.client import (
    ABCLiteratureClient,
    ABCLiteratureClientConfig,
    ABCLiteratureClientError,
    ABCLiteratureHTTPError,
)


ABC_LITERATURE_PROVIDER_ID = "abc_literature"

_FIGURE_METADATA_FILE_CLASSES = {
    "converted_main_figure_metadata",
    "converted_supplement_figure_metadata",
}

_ARTIFACT_STATUS_KEYS = (
    "status",
    "conversion_status",
    "file_publication_status",
)


class ABCLiteratureDocumentSourceProvider(DocumentSourceProvider):
    """Map ABC Literature REST payloads into provider-neutral source objects."""

    provider_id = ABC_LITERATURE_PROVIDER_ID

    def __init__(self, client: ABCLiteratureClient):
        self._client = client

    @classmethod
    def from_env(cls) -> "ABCLiteratureDocumentSourceProvider":
        return cls(ABCLiteratureClient(ABCLiteratureClientConfig.from_env()))

    async def __aenter__(self) -> "ABCLiteratureDocumentSourceProvider":
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
        normalized = identifier.strip()
        if not normalized:
            raise DocumentSourceError("identifier is required")

        try:
            upper = normalized.upper()
            if normalized.isdigit():
                payload = await self._client.lookup_external_curie(
                    f"PMID:{normalized}",
                    request_bearer_token=request_bearer_token,
                )
            elif upper.startswith("PMID:"):
                payload = await self._client.lookup_external_curie(
                    normalized,
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
            return NormalizedSourceIdentifier(original=original, normalized=original)

        return NormalizedSourceIdentifier(
            original=original,
            normalized=None,
            error=(
                "Unsupported identifier. Use PMID, PubMed ID, AGRKB, ABC, "
                "or an ABC Literature cross-reference such as FBrf."
            ),
        )

    def is_main_text_artifact(self, artifact: SourceArtifact) -> bool:
        file_class = str(artifact.metadata.get("file_class") or "").strip().lower()
        return file_class == "converted_merged_main" and not _artifact_looks_tei(artifact)

    def main_text_artifact_sort_key(self, artifact: SourceArtifact) -> tuple[int, ...]:
        file_class = str(artifact.metadata.get("file_class") or "").strip().lower()
        display_name = str(artifact.display_name or "").strip().lower()
        combined = f"{file_class} {display_name}"
        if file_class == "converted_merged_main":
            class_rank = 0
        elif file_class.startswith("converted") and "main" in file_class:
            class_rank = 5
        else:
            class_rank = 20

        if "_tei" in combined or "tei" in file_class:
            source_rank = 100
        elif "_nxml" in combined or "nxml" in file_class:
            source_rank = 0
        elif "_merged" in combined or "merged" in file_class:
            source_rank = 1
        else:
            source_rank = 10

        status_rank = 0 if artifact.status is SourceArtifactStatus.AVAILABLE else 1
        return (class_rank, source_rank, status_rank)

    def conversion_exposes_main_text(self, result: SourceConversionResult) -> bool:
        if "converted_merged_main" in result.converted_classes:
            return True
        for progress in result.per_file_progress:
            converted = progress.get("converted")
            if not isinstance(converted, Mapping):
                continue
            if converted.get("file_class") == "converted_merged_main":
                return True
        return False

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
        access_policy = inherited_access_policy or _map_access_policy(
            payload,
            source_pdf_null_mods_are_global=role is SourceArtifactRole.SOURCE_PDF,
        )

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
            metadata=_compact_metadata(
                payload,
                exclude={"converted_referencefiles", "referencefile_mods"},
            ),
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
    return SourceConversionResult(
        provider=provider,
        status=_map_conversion_status(_first_string(payload, "status")),
        reference_id=_first_string(payload, "reference_id"),
        reference_curie=_first_string(payload, "reference_curie"),
        job_id=_first_string(payload, "job_id"),
        error_message=_first_string(payload, "error_message"),
        converted_classes=_string_tuple(payload.get("converted_classes")),
        per_file_progress=_mapping_tuple(payload.get("per_file_progress")),
        per_mod_status=_mapping_tuple(payload.get("per_mod_status")),
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


def _artifact_looks_tei(artifact: SourceArtifact) -> bool:
    file_class = str(artifact.metadata.get("file_class") or "").strip().lower()
    display_name = str(artifact.display_name or "").strip().lower()
    return "tei" in file_class or "_tei" in display_name or display_name.endswith("tei.md")


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
    external_ids: dict[str, str | Sequence[str]] = {}
    for source_key, target_key in (
        ("pmid", "pmid"),
        ("pubmed_id", "pmid"),
        ("doi", "doi"),
        ("pmcid", "pmcid"),
    ):
        value = payload.get(source_key)
        if isinstance(value, str | int) and str(value).strip():
            external_ids[target_key] = str(value).strip()

    cross_references = payload.get("cross_references")
    if isinstance(cross_references, Sequence) and not isinstance(
        cross_references,
        (str, bytes, bytearray),
    ):
        values = [str(item).strip() for item in cross_references if str(item).strip()]
        if values:
            external_ids["cross_references"] = values

    return external_ids


def _map_access_policy(
    payload: Mapping[str, Any],
    *,
    source_pdf_null_mods_are_global: bool,
) -> SourceAccessPolicy:
    mods = _extract_mods(payload)
    if mods:
        return SourceAccessPolicy(
            scope=SourceAccessScope.RESTRICTED,
            mods=tuple(sorted(mods)),
        )
    if source_pdf_null_mods_are_global and _has_null_mod_entry(payload):
        return SourceAccessPolicy(scope=SourceAccessScope.GLOBAL)
    if payload.get("open_access") is True:
        return SourceAccessPolicy(scope=SourceAccessScope.GLOBAL)
    return SourceAccessPolicy(scope=SourceAccessScope.UNKNOWN)


def _extract_mods(payload: Mapping[str, Any]) -> set[str]:
    raw_mods = payload.get("referencefile_mods") or payload.get("mods") or []
    if not _is_non_string_sequence(raw_mods):
        return set()

    mods: set[str] = set()
    for item in raw_mods:
        if isinstance(item, Mapping):
            value = (
                item.get("mod_abbreviation")
                or item.get("abbreviation")
                or item.get("mod")
            )
        else:
            value = item
        if isinstance(value, str) and value.strip():
            mods.add(value.strip())
    return mods


def _has_null_mod_entry(payload: Mapping[str, Any]) -> bool:
    raw_mods = payload.get("referencefile_mods") or payload.get("mods") or []
    if not _is_non_string_sequence(raw_mods):
        return False

    recognized_keys = {"mod_abbreviation", "abbreviation", "mod"}
    for item in raw_mods:
        if isinstance(item, Mapping):
            for key in recognized_keys:
                if key in item and item[key] is None:
                    return True
    return False


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
