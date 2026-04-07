"""Chat-session orchestration helpers for curation prep preview and execution."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

from sqlalchemy.orm import Session

from src.lib.curation_workspace.curation_prep_constants import (
    CURATION_PREP_AGENT_ID,
)
from src.lib.curation_workspace.curation_prep_service import (
    CurationPrepPersistenceContext,
    run_curation_prep,
)
from src.lib.curation_workspace.extraction_results import (
    list_extraction_results,
)
from src.schemas.curation_prep import (
    CurationPrepChatPreviewResponse,
    CurationPrepChatRunRequest,
    CurationPrepChatRunResponse,
    CurationPrepScopeConfirmation,
)
from src.schemas.curation_workspace import (
    CurationExtractionResultRecord,
    CurationExtractionSourceKind,
)

@dataclass(frozen=True)
class _ChatPrepContext:
    extraction_results: list[CurationExtractionResultRecord]
    adapter_keys: list[str]
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
    requires_adapter_selection = _requires_adapter_selection(context)

    return CurationPrepChatPreviewResponse(
        ready=not blocking_reasons,
        summary_text=_build_summary_text(context, blocking_reasons),
        candidate_count=context.candidate_count,
        extraction_result_count=len(context.extraction_results),
        conversation_message_count=0,
        adapter_keys=context.adapter_keys,
        submit_adapter_keys=_build_submit_adapter_keys(context),
        requires_adapter_selection=requires_adapter_selection,
        blocking_reasons=blocking_reasons,
    )


async def run_chat_curation_prep(
    request: CurationPrepChatRunRequest,
    *,
    user_id: str,
    db: Session,
) -> CurationPrepChatRunResponse:
    """Run curation prep for a chat session after explicit confirmation."""

    context, adapter_keys = validate_chat_curation_prep_request(
        session_id=request.session_id,
        user_id=user_id,
        db=db,
        requested_adapter_keys=request.adapter_keys,
    )
    scope_confirmation = CurationPrepScopeConfirmation(
        confirmed=True,
        adapter_keys=adapter_keys,
        notes=[
            f"Confirmed from chat session {request.session_id}.",
            f"Prep requested by user {user_id}.",
        ],
    )

    prep_output = await run_curation_prep(
        context.extraction_results,
        scope_confirmation=scope_confirmation,
        db=db,
        persistence_context=CurationPrepPersistenceContext(
            origin_session_id=request.session_id,
            user_id=user_id,
            source_kind=CurationExtractionSourceKind.CHAT,
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
    )


def validate_chat_curation_prep_request(
    *,
    session_id: str,
    user_id: str,
    db: Session,
    requested_adapter_keys: Sequence[str] = (),
) -> tuple[_ChatPrepContext, list[str]]:
    """Validate prep prerequisites and resolve the adapter scope for a chat session."""

    context = _load_chat_prep_context(session_id=session_id, user_id=user_id, db=db)
    blocking_reasons = _build_run_blocking_reasons(context)
    if blocking_reasons:
        raise ValueError(blocking_reasons[0])

    adapter_keys = _resolve_scope_values(
        requested_values=requested_adapter_keys,
        available_values=context.adapter_keys,
        scope_name="adapter",
    )
    return context, adapter_keys


def _load_chat_prep_context(
    *,
    session_id: str,
    user_id: str,
    db: Session,
) -> _ChatPrepContext:
    extraction_results = list_extraction_results(
        db=db,
        origin_session_id=session_id,
        user_id=user_id,
        source_kind=CurationExtractionSourceKind.CHAT,
        exclude_agent_keys=[CURATION_PREP_AGENT_ID],
    )
    document_ids = _unique_non_empty(record.document_id for record in extraction_results)
    if len(document_ids) > 1:
        raise ValueError(
            "This chat session contains extraction results for multiple documents. "
            "Reset the chat before preparing for curation."
        )

    adapter_keys = _unique_non_empty(record.adapter_key for record in extraction_results)
    return _ChatPrepContext(
        extraction_results=extraction_results,
        adapter_keys=adapter_keys,
        candidate_count=sum(max(int(record.candidate_count), 0) for record in extraction_results),
    )


def _build_blocking_reasons(context: _ChatPrepContext) -> list[str]:
    blocking_reasons = _build_run_blocking_reasons(context)
    if blocking_reasons:
        return blocking_reasons

    if len(context.adapter_keys) > 1:
        return [
            "This chat includes findings for multiple adapters. Narrow the extraction scope to one adapter before preparing for curation review."
        ]
    return []


def _build_run_blocking_reasons(context: _ChatPrepContext) -> list[str]:
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
    if context.adapter_keys:
        scope_labels.append(_format_scope_fragment("adapter", context.adapter_keys))

    scope_suffix = ""
    if scope_labels:
        preposition = " in " if len(scope_labels) == 1 else " across "
        scope_suffix = f"{preposition}{_humanize_list(scope_labels)}"

    return (
        f"You discussed {context.candidate_count} candidate "
        f"annotation{'s' if context.candidate_count != 1 else ''}{scope_suffix}. "
        "Prepare all for curation review?"
    )


def _resolve_scope_values(
    *,
    requested_values: Sequence[str],
    available_values: Sequence[str],
    scope_name: str,
) -> list[str]:
    normalized_requested = _unique_non_empty(requested_values)
    normalized_available = _unique_non_empty(available_values)

    if not normalized_requested:
        if scope_name == "adapter" and len(normalized_available) > 1:
            raise ValueError(
                "Prep requires exactly one adapter scope when the current chat contains multiple adapters."
            )
        return normalized_available

    invalid_values = [value for value in normalized_requested if value not in normalized_available]
    if invalid_values:
        raise ValueError(
            f"Unknown {scope_name} scope value(s): {', '.join(invalid_values)}."
        )

    if scope_name == "adapter" and len(normalized_requested) != 1:
        raise ValueError(
            "Prep requires exactly one adapter scope when the current chat contains multiple adapters."
        )

    return normalized_requested


def _requires_adapter_selection(context: _ChatPrepContext) -> bool:
    return not _build_run_blocking_reasons(context) and len(context.adapter_keys) > 1


def _build_submit_adapter_keys(context: _ChatPrepContext) -> list[str]:
    if _build_run_blocking_reasons(context) or _requires_adapter_selection(context):
        return []
    return list(context.adapter_keys)


def _format_scope_fragment(label: str, values: Sequence[str]) -> str:
    display_values = _display_scope_values(values)
    if not display_values:
        return ""
    if _display_values_already_include_label(display_values, label):
        return _humanize_list(display_values)

    plural_suffix = "s" if len(display_values) != 1 else ""
    return f"{_humanize_list(display_values)} {label}{plural_suffix}"


def _humanize_list(values: Sequence[str]) -> str:
    normalized_values = [str(value).strip() for value in values if str(value).strip()]
    if not normalized_values:
        return ""
    if len(normalized_values) == 1:
        return normalized_values[0]
    if len(normalized_values) == 2:
        return f"{normalized_values[0]} and {normalized_values[1]}"
    return f"{', '.join(normalized_values[:-1])}, and {normalized_values[-1]}"


def _display_scope_values(values: Sequence[str]) -> list[str]:
    return [
        _display_scope_value(value)
        for value in values
        if str(value or "").strip()
    ]


def _display_scope_value(value: str) -> str:
    normalized = str(value or "").strip()
    if not normalized:
        return ""
    return normalized.replace("_", " ").replace("-", " ")


def _display_values_already_include_label(values: Sequence[str], label: str) -> bool:
    normalized_label = str(label or "").strip().lower()
    if not normalized_label:
        return False

    return all(
        value.strip().lower() == normalized_label
        or value.strip().lower().endswith(f" {normalized_label}")
        for value in values
    )


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
