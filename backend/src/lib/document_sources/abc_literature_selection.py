"""Selection policy for ABC Literature converted main Markdown artifacts."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Iterable, Sequence


CANONICAL_CONVERTED_FILE_CLASS = "converted_merged_main"
CANONICAL_CONVERTED_EXTENSION = "md"
CANONICAL_SOURCE_FILE_CLASS = "main"
CANONICAL_SOURCE_EXTENSION = "pdf"
FINAL_PUBLICATION_STATUS = "final"
TEI_DERIVED_SUFFIX = "_tei"

_NON_TEI_SUFFIX_RANK = {
    "_merged": 0,
    "_nxml": 1,
    "_grobid": 2,
    "_docling": 3,
    "_marker": 4,
}


class AbcConvertedMarkdownDecisionStatus(StrEnum):
    """Provider-neutral outcome of ABC converted main Markdown selection."""

    READY = "ready"
    NO_AUTHORIZED_SOURCE_PDF = "no_authorized_source_pdf"
    NO_CONVERTED_MAIN_MARKDOWN = "no_converted_main_markdown"
    TEI_ONLY = "tei_only"


@dataclass(frozen=True)
class AbcReferenceFileCandidate:
    """Small ABC referencefile shape needed for converted-text selection."""

    referencefile_id: int
    display_name: str
    file_class: str
    file_extension: str
    file_publication_status: str = FINAL_PUBLICATION_STATUS
    open_access: bool = False
    mod_abbreviations: tuple[str | None, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class AbcConvertedMarkdownSelection:
    """Selected converted Markdown artifact or the deterministic reason none is ready."""

    status: AbcConvertedMarkdownDecisionStatus
    source_pdf: AbcReferenceFileCandidate | None = None
    converted_markdown: AbcReferenceFileCandidate | None = None
    reason: str = ""

    @property
    def ready(self) -> bool:
        return self.status == AbcConvertedMarkdownDecisionStatus.READY


def select_converted_main_markdown(
    *,
    source_files: Iterable[AbcReferenceFileCandidate],
    converted_files: Iterable[AbcReferenceFileCandidate],
    authorized_mod_abbreviations: Iterable[str],
) -> AbcConvertedMarkdownSelection:
    """Select the canonical converted main Markdown for one ABC reference.

    The selected converted artifact must be derived from an authorized final
    source/main PDF. Converted-row MOD metadata is intentionally ignored because
    historical converted rows can be global/null even when the source PDF is
    MOD-scoped.
    """

    authorized_mods = {
        mod.strip().upper()
        for mod in authorized_mod_abbreviations
        if isinstance(mod, str) and mod.strip()
    }
    source_candidates = sorted(
        (source for source in source_files if _is_final_main_pdf(source)),
        key=lambda source: source.referencefile_id,
        reverse=True,
    )
    authorized_sources = [
        source for source in source_candidates if _is_source_pdf_authorized(source, authorized_mods)
    ]
    if not authorized_sources:
        return AbcConvertedMarkdownSelection(
            status=AbcConvertedMarkdownDecisionStatus.NO_AUTHORIZED_SOURCE_PDF,
            reason="No final main PDF is open/global or scoped to an authorized MOD.",
        )

    selected_source = authorized_sources[0]
    converted_for_source = [
        converted
        for converted in converted_files
        if _is_final_converted_main_markdown(converted)
        and _derived_suffix_for_source(selected_source, converted) is not None
    ]
    if not converted_for_source:
        return AbcConvertedMarkdownSelection(
            status=AbcConvertedMarkdownDecisionStatus.NO_CONVERTED_MAIN_MARKDOWN,
            source_pdf=selected_source,
            reason="No final converted_merged_main Markdown is associated with the authorized source PDF.",
        )

    non_tei_candidates = [
        converted
        for converted in converted_for_source
        if _derived_suffix_for_source(selected_source, converted) != TEI_DERIVED_SUFFIX
    ]
    if not non_tei_candidates:
        return AbcConvertedMarkdownSelection(
            status=AbcConvertedMarkdownDecisionStatus.TEI_ONLY,
            source_pdf=selected_source,
            reason="Only TEI-derived converted Markdown is available; TEI is not canonical for import.",
        )

    selected_converted = _choose_preferred_converted(selected_source, non_tei_candidates)
    return AbcConvertedMarkdownSelection(
        status=AbcConvertedMarkdownDecisionStatus.READY,
        source_pdf=selected_source,
        converted_markdown=selected_converted,
        reason="Selected final non-TEI converted_merged_main Markdown for an authorized source PDF.",
    )


def _is_final_main_pdf(candidate: AbcReferenceFileCandidate) -> bool:
    return (
        candidate.file_class == CANONICAL_SOURCE_FILE_CLASS
        and candidate.file_extension.lower() == CANONICAL_SOURCE_EXTENSION
        and candidate.file_publication_status == FINAL_PUBLICATION_STATUS
    )


def _is_source_pdf_authorized(
    candidate: AbcReferenceFileCandidate,
    authorized_mods: set[str],
) -> bool:
    if candidate.open_access:
        return True
    source_mods = _normal_mod_abbreviations(candidate.mod_abbreviations)
    if not source_mods:
        return True
    return bool(source_mods & authorized_mods)


def _normal_mod_abbreviations(mod_abbreviations: Sequence[str | None]) -> set[str]:
    return {
        mod.strip().upper()
        for mod in mod_abbreviations
        if isinstance(mod, str) and mod.strip()
    }


def _is_final_converted_main_markdown(candidate: AbcReferenceFileCandidate) -> bool:
    return (
        candidate.file_class == CANONICAL_CONVERTED_FILE_CLASS
        and candidate.file_extension.lower() == CANONICAL_CONVERTED_EXTENSION
        and candidate.file_publication_status == FINAL_PUBLICATION_STATUS
    )


def _derived_suffix_for_source(
    source: AbcReferenceFileCandidate,
    converted: AbcReferenceFileCandidate,
) -> str | None:
    if not converted.display_name.startswith(source.display_name):
        return None
    suffix = converted.display_name[len(source.display_name) :]
    if suffix == TEI_DERIVED_SUFFIX or suffix in _NON_TEI_SUFFIX_RANK:
        return suffix
    return None


def _choose_preferred_converted(
    source: AbcReferenceFileCandidate,
    candidates: Iterable[AbcReferenceFileCandidate],
) -> AbcReferenceFileCandidate:
    return sorted(
        candidates,
        key=lambda candidate: (
            _NON_TEI_SUFFIX_RANK[_derived_suffix_for_source(source, candidate) or ""],
            -candidate.referencefile_id,
        ),
    )[0]

