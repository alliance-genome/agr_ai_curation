"""Service layer for deterministic curation prep mapping and persistence."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping, Sequence

from sqlalchemy.orm import Session

from src.lib.curation_workspace.curation_prep_constants import CURATION_PREP_AGENT_ID
from src.lib.curation_workspace.extraction_results import persist_extraction_result
from src.schemas.curation_prep import (
    CurationPrepAgentOutput,
    CurationPrepCandidate,
    CurationPrepEvidenceRecord,
    CurationPrepRunMetadata,
    CurationPrepScopeConfirmation,
    CurationPrepTokenUsage,
)
from src.schemas.curation_workspace import (
    CurationEvidenceSource,
    CurationExtractionPersistenceRequest,
    CurationExtractionResultRecord,
    CurationExtractionSourceKind,
    EvidenceAnchor,
    EvidenceAnchorKind,
    EvidenceLocatorQuality,
    EvidenceSupportsDecision,
)


_DETERMINISTIC_PREP_MODEL_NAME = "deterministic_programmatic_mapper_v1"
_FIGURE_REFERENCE_PATTERN = re.compile(r"\bfig(?:ure)?\b", re.IGNORECASE)
_TABLE_REFERENCE_PATTERN = re.compile(r"\btable\b", re.IGNORECASE)


@dataclass(frozen=True)
class CurationPrepPersistenceContext:
    """Optional persistence metadata overrides for prep execution."""

    document_id: str | None = None
    source_kind: CurationExtractionSourceKind | None = None
    origin_session_id: str | None = None
    trace_id: str | None = None
    flow_run_id: str | None = None
    user_id: str | None = None
    conversation_summary: str | None = None


@dataclass(frozen=True)
class _CandidateBlueprint:
    adapter_key: str
    payload: dict[str, Any]
    evidence_records: list["_CandidateEvidence"]
    conversation_context_summary: str
    profile_key: str | None


@dataclass(frozen=True)
class _CandidateEvidence:
    evidence_record_id: str
    extraction_result_id: str
    anchor: EvidenceAnchor


@dataclass(frozen=True)
class _RecordMappingOutcome:
    candidates: list[CurationPrepCandidate] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    skipped_without_evidence: int = 0
    skipped_unmappable: int = 0


@dataclass(frozen=True)
class _DeterministicMapperResult:
    candidates: list[CurationPrepCandidate]
    processing_notes: list[str]
    warnings: list[str]
    selected_extraction_results: list[CurationExtractionResultRecord]


async def run_curation_prep(
    extraction_results: Sequence[CurationExtractionResultRecord],
    *,
    scope_confirmation: CurationPrepScopeConfirmation,
    db: Session | None = None,
    persistence_context: CurationPrepPersistenceContext | None = None,
) -> CurationPrepAgentOutput:
    """Build deterministic prep candidates from persisted extraction results."""

    persistence_context = persistence_context or CurationPrepPersistenceContext()
    scoped_results, scope_notes = _filter_extraction_results_for_scope(
        extraction_results,
        scope_confirmation,
    )
    if not scoped_results:
        raise ValueError("No extraction results matched the confirmed prep scope.")

    primary_extraction_result = _resolve_primary_extraction_result(scoped_results)
    document_id = _resolve_document_id(scoped_results, persistence_context)
    mapper_result = _map_extraction_results_to_candidates(
        scoped_results,
        scope_notes=scope_notes,
    )
    if not mapper_result.candidates:
        raise ValueError(
            "No evidence-verified candidates were available to prepare for curation review."
        )

    prep_output = CurationPrepAgentOutput(
        candidates=mapper_result.candidates,
        run_metadata=CurationPrepRunMetadata(
            model_name=_DETERMINISTIC_PREP_MODEL_NAME,
            token_usage=CurationPrepTokenUsage(),
            processing_notes=list(mapper_result.processing_notes),
            warnings=list(mapper_result.warnings),
        ),
    )

    adapter_key = _resolve_required_adapter_key(
        prep_output.candidates,
        extraction_results=scoped_results,
        scope_confirmation=scope_confirmation,
    )
    persist_extraction_result(
        _build_persistence_request(
            prep_output,
            adapter_key=adapter_key,
            extraction_results=scoped_results,
            scope_confirmation=scope_confirmation,
            persistence_context=persistence_context,
            document_id=document_id,
            primary_extraction_result=primary_extraction_result,
        ),
        db=db,
    )

    return prep_output


def _map_extraction_results_to_candidates(
    extraction_results: Sequence[CurationExtractionResultRecord],
    *,
    scope_notes: Sequence[str] = (),
) -> _DeterministicMapperResult:
    candidates: list[CurationPrepCandidate] = []
    warnings: list[str] = list(scope_notes)
    mapped_candidate_count = 0
    skipped_without_evidence = 0
    skipped_unmappable = 0

    for extraction_result in extraction_results:
        outcome = _map_extraction_result(extraction_result)
        candidates.extend(outcome.candidates)
        warnings.extend(outcome.warnings)
        mapped_candidate_count += len(outcome.candidates)
        skipped_without_evidence += outcome.skipped_without_evidence
        skipped_unmappable += outcome.skipped_unmappable

    processing_notes = [
        (
            "Deterministic prep mapper prepared "
            f"{mapped_candidate_count} evidence-backed candidate"
            f"{'s' if mapped_candidate_count != 1 else ''} from "
            f"{len(extraction_results)} extraction result"
            f"{'s' if len(extraction_results) != 1 else ''}."
        )
    ]
    if skipped_without_evidence:
        warnings.append(
            f"Skipped {skipped_without_evidence} candidate"
            f"{'s' if skipped_without_evidence != 1 else ''} without verified evidence."
        )
    if skipped_unmappable:
        warnings.append(
            f"Skipped {skipped_unmappable} extraction candidate"
            f"{'s' if skipped_unmappable != 1 else ''} because the payload could not be mapped."
        )

    return _DeterministicMapperResult(
        candidates=candidates,
        processing_notes=_dedupe_strings(processing_notes),
        warnings=_dedupe_strings(warnings),
        selected_extraction_results=list(extraction_results),
    )


def _map_extraction_result(
    extraction_result: CurationExtractionResultRecord,
) -> _RecordMappingOutcome:
    payload = extraction_result.payload_json
    if not isinstance(payload, dict):
        return _RecordMappingOutcome(
            warnings=[
                (
                    "Skipped extraction result "
                    f"{extraction_result.extraction_result_id} because its payload is not a JSON object."
                )
            ],
            skipped_unmappable=max(int(extraction_result.candidate_count), 1),
        )

    candidate_adapter_key = _resolve_candidate_adapter_key(extraction_result)
    if candidate_adapter_key is None:
        return _RecordMappingOutcome(
            warnings=[
                (
                    "Skipped extraction result "
                    f"{extraction_result.extraction_result_id} because it did not include a "
                    "target adapter or domain key for prep mapping."
                )
            ],
            skipped_unmappable=max(int(extraction_result.candidate_count), 1),
        )

    blueprints = _candidate_blueprints(
        extraction_result,
        payload,
        candidate_adapter_key=candidate_adapter_key,
    )
    if not blueprints:
        return _RecordMappingOutcome(
            warnings=[
                (
                    "Skipped extraction result "
                    f"{extraction_result.extraction_result_id} because it did not contain any "
                    "mappable extraction items."
                )
            ],
            skipped_unmappable=max(int(extraction_result.candidate_count), 1),
        )

    candidates: list[CurationPrepCandidate] = []
    skipped_without_evidence = 0

    for blueprint in blueprints:
        if not blueprint.evidence_records:
            skipped_without_evidence += 1
            continue

        field_paths = _payload_field_paths(blueprint.payload)
        candidates.append(
            CurationPrepCandidate(
                adapter_key=blueprint.adapter_key,
                profile_key=blueprint.profile_key,
                payload=blueprint.payload,
                evidence_records=[
                    CurationPrepEvidenceRecord(
                        evidence_record_id=source_record.evidence_record_id,
                        source=CurationEvidenceSource.EXTRACTED,
                        extraction_result_id=source_record.extraction_result_id,
                        field_paths=field_paths,
                        anchor=source_record.anchor,
                    )
                    for source_record in blueprint.evidence_records
                ],
                conversation_context_summary=blueprint.conversation_context_summary,
            )
        )

    return _RecordMappingOutcome(
        candidates=candidates,
        skipped_without_evidence=skipped_without_evidence,
    )


def _resolve_candidate_adapter_key(
    extraction_result: CurationExtractionResultRecord,
) -> str | None:
    # Extraction persistence may use a transport adapter key while the review
    # session should target the extracted domain-specific adapter/runtime.
    domain_key = _normalized_optional_string(extraction_result.domain_key)
    if domain_key is not None:
        return domain_key
    return _normalized_optional_string(extraction_result.adapter_key)


def _candidate_blueprints(
    extraction_result: CurationExtractionResultRecord,
    payload: Mapping[str, Any],
    *,
    candidate_adapter_key: str,
) -> list[_CandidateBlueprint]:
    raw_items = payload.get("items")
    if not isinstance(raw_items, list):
        return []

    blueprints: list[_CandidateBlueprint] = []
    profile_key = _normalized_optional_string(extraction_result.profile_key)

    for candidate_index, raw_item in enumerate(raw_items, start=1):
        if not isinstance(raw_item, Mapping):
            continue
        candidate_payload = _candidate_payload_from_item(raw_item)
        if not candidate_payload:
            continue

        blueprints.append(
            _CandidateBlueprint(
                adapter_key=candidate_adapter_key,
                payload=candidate_payload,
                evidence_records=_candidate_evidence_records(
                    extraction_result,
                    raw_item,
                    candidate_index=candidate_index,
                ),
                conversation_context_summary=_candidate_conversation_summary(
                    extraction_result,
                    candidate_payload,
                ),
                profile_key=profile_key,
            )
        )

    return blueprints


def _candidate_payload_from_item(
    raw_item: Mapping[str, Any],
) -> dict[str, Any]:
    candidate_payload = _compact_payload(
        {
            key: value
            for key, value in raw_item.items()
            if key != "evidence"
        }
    )
    if not isinstance(candidate_payload, dict):
        return {}
    return candidate_payload


def _candidate_evidence_records(
    extraction_result: CurationExtractionResultRecord,
    raw_item: Mapping[str, Any],
    *,
    candidate_index: int,
) -> list[_CandidateEvidence]:
    raw_records = raw_item.get("evidence")
    if not isinstance(raw_records, list):
        return []

    source_records: list[_CandidateEvidence] = []
    for evidence_index, raw_record in enumerate(raw_records, start=1):
        if not isinstance(raw_record, Mapping):
            continue
        anchor = _build_source_evidence_anchor(raw_record)
        if anchor is None:
            continue

        source_records.append(
            _CandidateEvidence(
                evidence_record_id=(
                    f"{extraction_result.extraction_result_id}:"
                    f"candidate:{candidate_index}:evidence:{evidence_index}"
                ),
                extraction_result_id=extraction_result.extraction_result_id,
                anchor=anchor,
            )
        )

    return source_records


def _build_source_evidence_anchor(raw_record: Mapping[str, Any]) -> EvidenceAnchor | None:
    verified_quote = _normalized_optional_string(raw_record.get("verified_quote"))
    if not verified_quote:
        return None

    figure_reference, table_reference = _split_figure_reference(
        _normalized_optional_string(raw_record.get("figure_reference"))
    )
    page_number = _normalized_optional_page(raw_record.get("page"))
    section_title = _normalized_optional_string(raw_record.get("section"))
    subsection_title = _normalized_optional_string(raw_record.get("subsection"))
    chunk_id = _normalized_optional_string(raw_record.get("chunk_id"))

    return EvidenceAnchor(
        anchor_kind=EvidenceAnchorKind.SNIPPET,
        locator_quality=EvidenceLocatorQuality.EXACT_QUOTE,
        supports_decision=EvidenceSupportsDecision.SUPPORTS,
        snippet_text=verified_quote,
        sentence_text=verified_quote,
        normalized_text=None,
        viewer_search_text=verified_quote,
        pdfx_markdown_offset_start=None,
        pdfx_markdown_offset_end=None,
        page_number=page_number,
        page_label=None,
        section_title=section_title,
        subsection_title=subsection_title,
        figure_reference=figure_reference,
        table_reference=table_reference,
        chunk_ids=[chunk_id] if chunk_id else [],
    )


def _split_figure_reference(value: str | None) -> tuple[str | None, str | None]:
    if value is None:
        return None, None
    if _TABLE_REFERENCE_PATTERN.search(value):
        return None, value
    if _FIGURE_REFERENCE_PATTERN.search(value):
        return value, None
    return value, None


def _candidate_conversation_summary(
    extraction_result: CurationExtractionResultRecord,
    payload: Mapping[str, Any],
) -> str:
    summary = _normalized_optional_string(extraction_result.conversation_summary)
    if summary is not None:
        return summary

    label = _normalized_optional_string(payload.get("label"))
    entity_type = _normalized_optional_string(payload.get("entity_type"))
    if label and entity_type:
        return f"Prepared deterministic {entity_type} candidate for {label}."
    if label:
        return f"Prepared deterministic candidate for {label}."
    return "Prepared deterministic candidate from structured extraction output."


def _compact_payload(value: Any) -> Any:
    if isinstance(value, Mapping):
        compacted: dict[str, Any] = {}
        for key, item in value.items():
            compacted_item = _compact_payload(item)
            if compacted_item is None:
                continue
            compacted[str(key)] = compacted_item
        return compacted or None
    if isinstance(value, list):
        compacted_items = [_compact_payload(item) for item in value]
        compacted_items = [item for item in compacted_items if item is not None]
        return compacted_items or None
    if isinstance(value, str):
        normalized = _normalized_optional_string(value)
        return normalized
    if isinstance(value, bool):
        return value
    return value


def _resolve_primary_extraction_result(
    extraction_results: Sequence[CurationExtractionResultRecord],
) -> CurationExtractionResultRecord:
    if not extraction_results:
        raise ValueError("Curation prep requires at least one extraction result.")
    return extraction_results[0]


def _resolve_document_id(
    extraction_results: Sequence[CurationExtractionResultRecord],
    persistence_context: CurationPrepPersistenceContext,
) -> str:
    document_ids = _unique_non_empty(result.document_id for result in extraction_results)
    if len(document_ids) != 1:
        raise ValueError(
            "Curation prep persistence requires extraction_results for exactly one document."
        )

    resolved_document_id = document_ids[0]
    if (
        persistence_context.document_id is not None
        and persistence_context.document_id != resolved_document_id
    ):
        raise ValueError(
            "Curation prep persistence_context.document_id must match the single "
            "document_id present in extraction_results."
        )

    return resolved_document_id


def _resolve_adapter_key(
    candidates: Sequence[CurationPrepCandidate],
    *,
    extraction_results: Sequence[CurationExtractionResultRecord],
    scope_confirmation: CurationPrepScopeConfirmation,
) -> str | None:
    candidate_adapter_key = _resolve_single_value(candidate.adapter_key for candidate in candidates)
    if candidate_adapter_key is not None:
        return candidate_adapter_key

    return _resolve_single_value(
        [
            *scope_confirmation.adapter_keys,
            *(record.adapter_key for record in extraction_results if record.adapter_key),
        ]
    )


def _resolve_required_adapter_key(
    candidates: Sequence[CurationPrepCandidate],
    *,
    extraction_results: Sequence[CurationExtractionResultRecord],
    scope_confirmation: CurationPrepScopeConfirmation,
) -> str:
    adapter_key = _resolve_adapter_key(
        candidates,
        extraction_results=extraction_results,
        scope_confirmation=scope_confirmation,
    )
    if adapter_key is None:
        raise ValueError("Curation prep requires extraction results for exactly one adapter key.")
    return adapter_key


def _resolve_profile_key(
    candidates: Sequence[CurationPrepCandidate],
    *,
    extraction_results: Sequence[CurationExtractionResultRecord],
    scope_confirmation: CurationPrepScopeConfirmation,
) -> str | None:
    candidate_profile_key = _resolve_single_value(
        candidate.profile_key for candidate in candidates if candidate.profile_key
    )
    if candidate_profile_key is not None:
        return candidate_profile_key

    return _resolve_single_value(
        [
            *scope_confirmation.profile_keys,
            *(record.profile_key for record in extraction_results if record.profile_key),
        ]
    )


def _resolve_domain_key(
    extraction_results: Sequence[CurationExtractionResultRecord],
    scope_confirmation: CurationPrepScopeConfirmation,
) -> str | None:
    return _resolve_single_value(
        [
            *scope_confirmation.domain_keys,
            *(record.domain_key for record in extraction_results if record.domain_key),
        ]
    )


def _build_persistence_request(
    prep_output: CurationPrepAgentOutput,
    *,
    adapter_key: str,
    extraction_results: Sequence[CurationExtractionResultRecord],
    scope_confirmation: CurationPrepScopeConfirmation,
    persistence_context: CurationPrepPersistenceContext,
    document_id: str,
    primary_extraction_result: CurationExtractionResultRecord,
) -> CurationExtractionPersistenceRequest:
    return CurationExtractionPersistenceRequest(
        document_id=document_id,
        agent_key=CURATION_PREP_AGENT_ID,
        source_kind=persistence_context.source_kind or primary_extraction_result.source_kind,
        adapter_key=adapter_key,
        profile_key=_resolve_profile_key(
            prep_output.candidates,
            extraction_results=extraction_results,
            scope_confirmation=scope_confirmation,
        ),
        domain_key=_resolve_domain_key(extraction_results, scope_confirmation),
        origin_session_id=(
            persistence_context.origin_session_id
            or primary_extraction_result.origin_session_id
        ),
        trace_id=persistence_context.trace_id or primary_extraction_result.trace_id,
        flow_run_id=persistence_context.flow_run_id or primary_extraction_result.flow_run_id,
        user_id=persistence_context.user_id or primary_extraction_result.user_id,
        candidate_count=len(prep_output.candidates),
        conversation_summary=_resolve_conversation_summary(
            extraction_results,
            persistence_context,
        ),
        payload_json=prep_output.model_dump(mode="json"),
        metadata={
            "final_run_metadata": prep_output.run_metadata.model_dump(mode="json"),
            "scope_confirmed": scope_confirmation.confirmed,
            "scope_adapter_keys": list(scope_confirmation.adapter_keys),
            "scope_profile_keys": list(scope_confirmation.profile_keys),
            "scope_domain_keys": list(scope_confirmation.domain_keys),
            "source_extraction_result_ids": [
                record.extraction_result_id for record in extraction_results
            ],
        },
    )


def _resolve_conversation_summary(
    extraction_results: Sequence[CurationExtractionResultRecord],
    persistence_context: CurationPrepPersistenceContext,
) -> str | None:
    if persistence_context.conversation_summary is not None:
        summary = persistence_context.conversation_summary.strip()
        return summary or None

    summaries = _unique_non_empty(
        record.conversation_summary for record in extraction_results
    )
    if summaries:
        return " ".join(summaries)

    return None


def _record_matches_scope(
    record: CurationExtractionResultRecord,
    scope_confirmation: CurationPrepScopeConfirmation,
) -> bool:
    adapter_key = _normalized_optional_string(record.adapter_key)
    profile_key = _normalized_optional_string(record.profile_key)
    domain_key = _normalized_optional_string(record.domain_key)

    if scope_confirmation.adapter_keys:
        if adapter_key is None or adapter_key not in scope_confirmation.adapter_keys:
            return False
    if scope_confirmation.profile_keys:
        if profile_key is None or profile_key not in scope_confirmation.profile_keys:
            return False
    if scope_confirmation.domain_keys:
        if domain_key is None or domain_key not in scope_confirmation.domain_keys:
            return False

    return True


def _filter_extraction_results_for_scope(
    extraction_results: Sequence[CurationExtractionResultRecord],
    scope_confirmation: CurationPrepScopeConfirmation,
) -> tuple[list[CurationExtractionResultRecord], list[str]]:
    scoped_results = [
        record
        for record in extraction_results
        if _record_matches_scope(record, scope_confirmation)
    ]
    if scoped_results:
        return scoped_results, []

    if extraction_results and all(
        _normalized_optional_string(record.adapter_key) is None
        and _normalized_optional_string(record.profile_key) is None
        and _normalized_optional_string(record.domain_key) is None
        for record in extraction_results
    ):
        return list(extraction_results), [
            (
                "Persisted extraction results did not include scope keys; using current "
                "session extraction context with curator-confirmed scope."
            )
        ]

    return [], []


def _resolve_single_value(values: Iterable[str | None]) -> str | None:
    distinct_values = _unique_non_empty(values)
    if len(distinct_values) == 1:
        return distinct_values[0]
    return None


def _payload_field_paths(payload: Any, *, prefix: str = "") -> list[str]:
    if isinstance(payload, Mapping):
        field_paths: list[str] = []
        for key, value in payload.items():
            field_key = f"{prefix}.{key}" if prefix else str(key)
            field_paths.extend(_payload_field_paths(value, prefix=field_key))
        return field_paths
    if isinstance(payload, list):
        field_paths: list[str] = []
        for index, value in enumerate(payload):
            field_key = f"{prefix}.{index}" if prefix else str(index)
            field_paths.extend(_payload_field_paths(value, prefix=field_key))
        return field_paths
    return [prefix] if prefix else []


def _normalized_optional_page(value: Any) -> int | None:
    if value is None or isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if value >= 1 else None


def _normalized_optional_string(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized or None


def _unique_non_empty(values: Iterable[str | None]) -> list[str]:
    unique_values: list[str] = []
    seen: set[str] = set()

    for value in values:
        normalized = _normalized_optional_string(value)
        if normalized is None or normalized in seen:
            continue
        unique_values.append(normalized)
        seen.add(normalized)

    return unique_values


def _dedupe_strings(values: Iterable[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()

    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        deduped.append(normalized)
        seen.add(normalized)

    return deduped


__all__ = [
    "CurationPrepPersistenceContext",
    "run_curation_prep",
]
