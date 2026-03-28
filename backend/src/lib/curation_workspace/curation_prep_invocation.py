"""Chat-session orchestration helpers for curation prep preview and execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from sqlalchemy.orm import Session

from src.lib.conversation_manager import SessionAccessError, conversation_manager
from src.lib.curation_workspace.curation_prep_service import (
    CurationPrepPersistenceContext,
    run_curation_prep,
)
from src.lib.curation_workspace.extraction_results import (
    enrich_extraction_result_scope,
    list_extraction_results,
)
from src.lib.openai_agents.agents.curation_prep_agent import CURATION_PREP_AGENT_ID
from src.schemas.curation_prep import (
    CurationPrepAdapterMetadata,
    CurationPrepAgentInput,
    CurationPrepChatPreviewResponse,
    CurationPrepChatRunRequest,
    CurationPrepChatRunResponse,
    CurationPrepConversationMessage,
    CurationPrepConversationRole,
    CurationPrepEvidenceRecord,
    CurationPrepScopeConfirmation,
)
from src.schemas.curation_workspace import (
    CurationExtractionResultRecord,
    CurationExtractionSourceKind,
    EvidenceAnchor,
    EvidenceAnchorKind,
    EvidenceLocatorQuality,
    EvidenceSupportsDecision,
)


_DEFAULT_ADAPTER_METADATA_NOTE = (
    "Derived from persisted chat extraction results; adapter-owned field hints were not "
    "available from this invocation path."
)
_DEFAULT_REFERENCE_ADAPTER_KEYS = frozenset({"reference", "reference_adapter"})


@dataclass(frozen=True)
class _ChatPrepContext:
    conversation_history: list[CurationPrepConversationMessage]
    extraction_results: list[CurationExtractionResultRecord]
    adapter_keys: list[str]
    profile_keys: list[str]
    domain_keys: list[str]
    candidate_count: int


def build_chat_curation_prep_preview(
    *,
    session_id: str,
    user_id: str,
    db: Session,
) -> CurationPrepChatPreviewResponse:
    """Build a curator-facing prep summary for the current chat session."""

    context = _load_chat_prep_context(session_id=session_id, user_id=user_id, db=db)
    blocking_reasons = _build_blocking_reasons(context)

    return CurationPrepChatPreviewResponse(
        ready=not blocking_reasons,
        summary_text=_build_summary_text(context, blocking_reasons),
        candidate_count=context.candidate_count,
        extraction_result_count=len(context.extraction_results),
        conversation_message_count=len(context.conversation_history),
        adapter_keys=context.adapter_keys,
        profile_keys=context.profile_keys,
        domain_keys=context.domain_keys,
        blocking_reasons=blocking_reasons,
    )


async def run_chat_curation_prep(
    request: CurationPrepChatRunRequest,
    *,
    user_id: str,
    db: Session,
) -> CurationPrepChatRunResponse:
    """Run the curation prep agent for a chat session after explicit confirmation."""

    context = _load_chat_prep_context(session_id=request.session_id, user_id=user_id, db=db)
    blocking_reasons = _build_blocking_reasons(context)
    if blocking_reasons:
        raise ValueError(blocking_reasons[0])

    adapter_keys = _resolve_scope_values(
        requested_values=request.adapter_keys,
        available_values=context.adapter_keys,
        scope_name="adapter",
    )
    profile_keys = _resolve_scope_values(
        requested_values=request.profile_keys,
        available_values=context.profile_keys,
        scope_name="profile",
    )
    domain_keys = _resolve_scope_values(
        requested_values=request.domain_keys,
        available_values=context.domain_keys,
        scope_name="domain",
    )

    adapter_metadata = _build_adapter_metadata(
        extraction_results=context.extraction_results,
        adapter_keys=adapter_keys,
        profile_keys=profile_keys,
    )
    if not adapter_metadata:
        raise ValueError(
            "No adapter metadata could be derived from the current chat extraction results."
        )

    prep_output = await run_curation_prep(
        CurationPrepAgentInput(
            conversation_history=context.conversation_history,
            extraction_results=context.extraction_results,
            evidence_records=_build_evidence_records(context.extraction_results),
            scope_confirmation=CurationPrepScopeConfirmation(
                confirmed=True,
                adapter_keys=adapter_keys,
                profile_keys=profile_keys,
                domain_keys=domain_keys,
                notes=[
                    f"Confirmed from chat session {request.session_id}.",
                    f"Prep requested by user {user_id}.",
                ],
            ),
            adapter_metadata=adapter_metadata,
        ),
        db=db,
        persistence_context=CurationPrepPersistenceContext(
            origin_session_id=request.session_id,
            user_id=user_id,
            source_kind=CurationExtractionSourceKind.CHAT,
            conversation_summary=_build_conversation_summary(context),
        ),
    )

    return CurationPrepChatRunResponse(
        summary_text=(
            f"Prepared {len(prep_output.candidates)} candidate "
            f"annotation{'s' if len(prep_output.candidates) != 1 else ''} for curation review."
        ),
        document_id=context.extraction_results[0].document_id,
        candidate_count=len(prep_output.candidates),
        warnings=list(prep_output.run_metadata.warnings),
        processing_notes=list(prep_output.run_metadata.processing_notes),
        adapter_keys=adapter_keys,
        profile_keys=profile_keys,
        domain_keys=domain_keys,
    )


def _load_chat_prep_context(
    *,
    session_id: str,
    user_id: str,
    db: Session,
) -> _ChatPrepContext:
    session_stats = conversation_manager.get_session_stats(user_id, session_id)
    extraction_results = list_extraction_results(
        db=db,
        origin_session_id=session_id,
        user_id=user_id,
        source_kind=CurationExtractionSourceKind.CHAT,
        exclude_agent_keys=[CURATION_PREP_AGENT_ID],
    )
    extraction_results = [
        enrich_extraction_result_scope(record)
        for record in extraction_results
    ]

    document_ids = _unique_non_empty(record.document_id for record in extraction_results)
    if len(document_ids) > 1:
        raise ValueError(
            "This chat session contains extraction results for multiple documents. "
            "Reset the chat before preparing for curation."
        )

    conversation_history = _build_conversation_history(session_stats.get("history", []))
    adapter_keys = _unique_non_empty(record.adapter_key for record in extraction_results)
    profile_keys = _unique_non_empty(record.profile_key for record in extraction_results)
    domain_keys = _unique_non_empty(record.domain_key for record in extraction_results)

    return _ChatPrepContext(
        conversation_history=conversation_history,
        extraction_results=extraction_results,
        adapter_keys=adapter_keys,
        profile_keys=profile_keys,
        domain_keys=domain_keys,
        candidate_count=sum(max(int(record.candidate_count), 0) for record in extraction_results),
    )


def _build_blocking_reasons(context: _ChatPrepContext) -> list[str]:
    if not context.extraction_results:
        return [
            "No candidate annotations are available from this chat yet. Ask the assistant to extract findings before preparing for curation review."
        ]
    if context.candidate_count <= 0:
        return [
            "This chat has extraction context, but it did not retain any candidate annotations to prepare yet."
        ]
    if not context.adapter_keys:
        return [
            "The current chat extraction results do not include adapter scope, so prep cannot determine what to prepare."
        ]
    return []


def _build_summary_text(context: _ChatPrepContext, blocking_reasons: Sequence[str]) -> str:
    if blocking_reasons:
        return blocking_reasons[0]

    scope_labels = []
    visible_adapter_keys = _visible_adapter_keys(context.adapter_keys)
    if visible_adapter_keys:
        scope_labels.append(_format_scope_fragment("adapter", visible_adapter_keys))
    if context.domain_keys:
        scope_labels.append(_format_scope_fragment("domain", context.domain_keys))

    scope_suffix = ""
    if scope_labels:
        preposition = " in " if len(scope_labels) == 1 else " across "
        scope_suffix = f"{preposition}{_humanize_list(scope_labels)}"

    return (
        f"You discussed {context.candidate_count} candidate "
        f"annotation{'s' if context.candidate_count != 1 else ''}{scope_suffix}. "
        "Prepare all for curation review?"
    )


def _build_conversation_history(history: Sequence[dict[str, Any]]) -> list[CurationPrepConversationMessage]:
    messages: list[CurationPrepConversationMessage] = []

    for exchange in history:
        user_message = str(exchange.get("user") or "").strip()
        assistant_message = str(exchange.get("assistant") or "").strip()

        if user_message:
            messages.append(
                CurationPrepConversationMessage(
                    role=CurationPrepConversationRole.USER,
                    content=user_message,
                )
            )
        if assistant_message:
            messages.append(
                CurationPrepConversationMessage(
                    role=CurationPrepConversationRole.ASSISTANT,
                    content=assistant_message,
                )
            )

    return messages


def _resolve_scope_values(
    *,
    requested_values: Sequence[str],
    available_values: Sequence[str],
    scope_name: str,
) -> list[str]:
    normalized_requested = _unique_non_empty(requested_values)
    normalized_available = _unique_non_empty(available_values)

    if not normalized_requested:
        return normalized_available

    invalid_values = [value for value in normalized_requested if value not in normalized_available]
    if invalid_values:
        raise ValueError(
            f"Unknown {scope_name} scope value(s): {', '.join(invalid_values)}."
        )

    return normalized_requested


def _build_adapter_metadata(
    *,
    extraction_results: Sequence[CurationExtractionResultRecord],
    adapter_keys: Sequence[str],
    profile_keys: Sequence[str],
) -> list[CurationPrepAdapterMetadata]:
    selected_adapter_keys = set(_unique_non_empty(adapter_keys))
    selected_profile_keys = set(_unique_non_empty(profile_keys))
    metadata: list[CurationPrepAdapterMetadata] = []
    seen_pairs: set[tuple[str, str | None]] = set()

    for record in extraction_results:
        adapter_key = str(record.adapter_key or "").strip()
        if not adapter_key or adapter_key not in selected_adapter_keys:
            continue

        profile_key = str(record.profile_key or "").strip() or None
        if selected_profile_keys and profile_key not in selected_profile_keys:
            continue

        pair = (adapter_key, profile_key)
        if pair in seen_pairs:
            continue
        seen_pairs.add(pair)

        metadata.append(
            CurationPrepAdapterMetadata(
                adapter_key=adapter_key,
                profile_key=profile_key,
                notes=[_DEFAULT_ADAPTER_METADATA_NOTE],
            )
        )

    return metadata


def _collect_evidence_payloads(payload: Any) -> list[dict[str, Any]]:
    collected: list[dict[str, Any]] = []

    if isinstance(payload, dict):
        for key in ("evidence_records", "evidence"):
            value = payload.get(key)
            if isinstance(value, list):
                collected.extend(item for item in value if isinstance(item, dict))

        for key, value in payload.items():
            if key in {"evidence_records", "evidence"}:
                continue
            if isinstance(value, (dict, list)):
                collected.extend(_collect_evidence_payloads(value))
    elif isinstance(payload, list):
        for item in payload:
            collected.extend(_collect_evidence_payloads(item))

    return collected


def _build_evidence_records(
    extraction_results: Sequence[CurationExtractionResultRecord],
) -> list[CurationPrepEvidenceRecord]:
    evidence_records: list[CurationPrepEvidenceRecord] = []
    seen_keys: set[tuple[str, str, str, str, str]] = set()

    for extraction_result in extraction_results:
        payload = extraction_result.payload_json

        for index, raw_record in enumerate(_collect_evidence_payloads(payload), start=1):
            snippet_text = str(raw_record.get("verified_quote") or "").strip()
            section_title = str(raw_record.get("section") or "").strip()
            subsection_title = str(raw_record.get("subsection") or "").strip()
            page_number = raw_record.get("page")
            dedupe_key = (
                extraction_result.extraction_result_id,
                snippet_text,
                str(page_number or ""),
                section_title,
                subsection_title,
            )
            if dedupe_key in seen_keys:
                continue

            anchor = _build_evidence_anchor(raw_record)
            if anchor is None:
                continue
            seen_keys.add(dedupe_key)

            evidence_records.append(
                CurationPrepEvidenceRecord(
                    evidence_record_id=(
                        f"{extraction_result.extraction_result_id}:evidence:{index}"
                    ),
                    extraction_result_id=extraction_result.extraction_result_id,
                    field_paths=_coerce_string_list(raw_record.get("field_paths")),
                    anchor=anchor,
                )
            )

    return evidence_records


def _build_evidence_anchor(raw_record: dict[str, Any]) -> EvidenceAnchor | None:
    snippet_text = str(raw_record.get("verified_quote") or "").strip() or None
    section_title = str(raw_record.get("section") or "").strip() or None
    subsection_title = str(raw_record.get("subsection") or "").strip() or None
    figure_reference = str(raw_record.get("figure_reference") or "").strip() or None
    chunk_id = str(raw_record.get("chunk_id") or "").strip() or None
    page_number = raw_record.get("page")

    if isinstance(page_number, bool) or not isinstance(page_number, int):
        page_number = None

    if snippet_text:
        anchor_kind = EvidenceAnchorKind.SNIPPET
        locator_quality = EvidenceLocatorQuality.EXACT_QUOTE
    elif section_title:
        anchor_kind = EvidenceAnchorKind.SECTION
        locator_quality = EvidenceLocatorQuality.SECTION_ONLY
    elif page_number is not None:
        anchor_kind = EvidenceAnchorKind.PAGE
        locator_quality = EvidenceLocatorQuality.PAGE_ONLY
    else:
        return None

    viewer_search_text = snippet_text or section_title

    return EvidenceAnchor(
        anchor_kind=anchor_kind,
        locator_quality=locator_quality,
        supports_decision=EvidenceSupportsDecision.SUPPORTS,
        snippet_text=snippet_text,
        sentence_text=snippet_text,
        viewer_search_text=viewer_search_text,
        page_number=page_number,
        section_title=section_title,
        subsection_title=subsection_title,
        figure_reference=figure_reference,
        chunk_ids=[chunk_id] if chunk_id else [],
    )


def _build_conversation_summary(context: _ChatPrepContext) -> str:
    recent_user_messages = [
        message.content
        for message in context.conversation_history
        if message.role is CurationPrepConversationRole.USER
    ]
    if recent_user_messages:
        return " | ".join(recent_user_messages[-3:])

    return (
        f"Chat session contained {context.candidate_count} retained candidate "
        f"annotation{'s' if context.candidate_count != 1 else ''} across "
        f"{len(context.extraction_results)} extraction run"
        f"{'s' if len(context.extraction_results) != 1 else ''}."
    )


def _format_scope_fragment(label: str, values: Sequence[str]) -> str:
    plural_suffix = "s" if len(values) != 1 else ""
    return f"{_humanize_list(_display_scope_values(label, values))} {label}{plural_suffix}"


def _humanize_list(values: Sequence[str]) -> str:
    normalized_values = [str(value).strip() for value in values if str(value).strip()]
    if not normalized_values:
        return ""
    if len(normalized_values) == 1:
        return normalized_values[0]
    if len(normalized_values) == 2:
        return f"{normalized_values[0]} and {normalized_values[1]}"
    return f"{', '.join(normalized_values[:-1])}, and {normalized_values[-1]}"


def _display_scope_values(label: str, values: Sequence[str]) -> list[str]:
    return [
        _display_scope_value(label, value)
        for value in values
        if str(value or "").strip()
    ]


def _display_scope_value(label: str, value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    if label == "adapter" and normalized in _DEFAULT_REFERENCE_ADAPTER_KEYS:
        return "reference curation"
    return normalized.replace("_", " ").replace("-", " ")


def _visible_adapter_keys(adapter_keys: Sequence[str]) -> list[str]:
    normalized = _unique_non_empty(adapter_keys)
    if len(normalized) == 1 and normalized[0] in _DEFAULT_REFERENCE_ADAPTER_KEYS:
        return []
    return normalized


def _unique_non_empty(values: Iterable[str | None]) -> list[str]:
    unique_values: list[str] = []
    seen: set[str] = set()

    for value in values:
        normalized = str(value or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        unique_values.append(normalized)

    return unique_values


def _coerce_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return _unique_non_empty(str(item) for item in value if isinstance(item, str))


__all__ = [
    "SessionAccessError",
    "build_chat_curation_prep_preview",
    "run_chat_curation_prep",
]
