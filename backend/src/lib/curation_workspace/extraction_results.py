"""Persistence helpers for structured extraction envelopes."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Mapping, Optional, Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from src.lib.curation_workspace.models import (
    CurationExtractionResultRecord as CurationExtractionResultRecordModel,
)
from src.models.sql.database import SessionLocal
from src.schemas.curation_workspace import (
    CurationExtractionPersistenceRequest,
    CurationExtractionPersistenceResponse,
    CurationExtractionResultRecord,
)

logger = logging.getLogger(__name__)

_EXTRACTION_TOOL_NAME_PATTERN = re.compile(
    r"^ask_(?P<tool_segment>.+?)(?:_step\d+)?_specialist$"
)
_ENVELOPE_EXTRACTION_KEYS = frozenset({"items", "raw_mentions", "exclusions", "ambiguities"})


@dataclass(frozen=True)
class ExtractionEnvelopeCandidate:
    """Parsed extraction envelope that is ready for persistence."""

    agent_key: str
    payload_json: dict[str, Any] | list[Any]
    candidate_count: int = 0
    adapter_key: Optional[str] = None
    profile_key: Optional[str] = None
    domain_key: Optional[str] = None
    conversation_summary: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


def build_safe_agent_key_map(agent_keys: Iterable[str]) -> dict[str, str]:
    """Map tool-safe agent-key segments back to canonical agent keys."""

    safe_key_map: dict[str, str] = {}
    for agent_key in agent_keys:
        canonical_key = str(agent_key or "").strip()
        if not canonical_key:
            continue
        safe_key_map[canonical_key.replace("-", "_")] = canonical_key
    return safe_key_map


def resolve_agent_key_from_tool_name(
    tool_name: str,
    *,
    safe_agent_key_map: Mapping[str, str],
) -> Optional[str]:
    """Resolve a specialist tool name back to the originating agent key."""

    match = _EXTRACTION_TOOL_NAME_PATTERN.match(str(tool_name or "").strip())
    if not match:
        return None
    return safe_agent_key_map.get(match.group("tool_segment"))


def build_extraction_envelope_candidate(
    raw_output: Any,
    *,
    agent_key: Optional[str],
    conversation_summary: Optional[str] = None,
    adapter_key: Optional[str] = None,
    profile_key: Optional[str] = None,
    domain_key: Optional[str] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> Optional[ExtractionEnvelopeCandidate]:
    """Convert a tool output payload into a persistable extraction candidate."""

    payload = _coerce_tool_output_payload(raw_output)
    if not _is_extraction_envelope_payload(payload):
        return None

    canonical_agent_key = str(agent_key or "").strip()
    if not canonical_agent_key:
        return None

    envelope_metadata = dict(metadata or {})
    run_summary = payload.get("run_summary", {}) if isinstance(payload, dict) else {}
    candidate_count_raw = run_summary.get("candidate_count", 0)
    candidate_count = candidate_count_raw if isinstance(candidate_count_raw, int) else 0

    if isinstance(payload, dict):
        payload_adapter_key = payload.get("adapter_key")
        actor = payload.get("actor")
        destination = payload.get("destination")
        if adapter_key is None and isinstance(payload_adapter_key, str):
            adapter_key = payload_adapter_key.strip() or None
        if actor:
            envelope_metadata.setdefault("envelope_actor", actor)
        if destination:
            envelope_metadata.setdefault("envelope_destination", destination)
            if adapter_key is None and isinstance(destination, str):
                # Existing extraction envelopes expose their adapter/routing target via
                # `destination`; persist that key here so downstream curation replay
                # can use adapter-owned identifiers without inventing them later.
                adapter_key = destination.strip() or None
            if domain_key is None and isinstance(destination, str):
                domain_key = destination.strip() or None

    return ExtractionEnvelopeCandidate(
        agent_key=canonical_agent_key,
        payload_json=payload,
        candidate_count=max(candidate_count, 0),
        adapter_key=adapter_key,
        profile_key=profile_key,
        domain_key=domain_key,
        conversation_summary=str(conversation_summary).strip() or None
        if conversation_summary is not None
        else None,
        metadata=envelope_metadata,
    )


def persist_extraction_result(
    request: CurationExtractionPersistenceRequest,
    *,
    db: Optional[Session] = None,
) -> CurationExtractionPersistenceResponse:
    """Persist one structured extraction envelope and return its stored record."""

    owns_session = db is None
    session = db or SessionLocal()

    try:
        record = _build_extraction_result_record(request)
        session.add(record)
        session.commit()
        session.refresh(record)

        return CurationExtractionPersistenceResponse(
            extraction_result=_record_to_schema(record)
        )
    except Exception:
        session.rollback()
        raise
    finally:
        if owns_session:
            session.close()


def persist_extraction_results(
    requests: Sequence[CurationExtractionPersistenceRequest],
    *,
    db: Optional[Session] = None,
) -> list[CurationExtractionPersistenceResponse]:
    """Persist extraction envelopes in one transaction."""

    if not requests:
        return []

    owns_session = db is None
    session = db or SessionLocal()
    records: list[CurationExtractionResultRecordModel] = []

    try:
        for request in requests:
            record = _build_extraction_result_record(request)
            session.add(record)
            records.append(record)

        session.commit()

        for record in records:
            session.refresh(record)

        return [
            CurationExtractionPersistenceResponse(
                extraction_result=_record_to_schema(record)
            )
            for record in records
        ]
    except Exception:
        session.rollback()
        raise
    finally:
        if owns_session:
            session.close()


def list_extraction_results(
    *,
    db: Optional[Session] = None,
    document_id: str | None = None,
    origin_session_id: str | None = None,
    user_id: str | None = None,
    source_kind: Any | None = None,
    exclude_agent_keys: Sequence[str] | None = None,
) -> list[CurationExtractionResultRecord]:
    """List persisted extraction-result records using the shared schema contract."""
    owns_session = db is None
    session = db or SessionLocal()

    try:
        statement = select(CurationExtractionResultRecordModel).order_by(
            CurationExtractionResultRecordModel.created_at.asc(),
            CurationExtractionResultRecordModel.id.asc(),
        )

        if document_id:
            try:
                document_uuid = UUID(str(document_id).strip())
            except (AttributeError, TypeError, ValueError):
                logger.warning(
                    "Ignoring invalid document_id filter for extraction results: %r",
                    document_id,
                )
                return []
            statement = statement.where(CurationExtractionResultRecordModel.document_id == document_uuid)
        if origin_session_id:
            statement = statement.where(
                CurationExtractionResultRecordModel.origin_session_id == origin_session_id
            )
        if user_id:
            statement = statement.where(CurationExtractionResultRecordModel.user_id == user_id)
        if source_kind is not None:
            statement = statement.where(CurationExtractionResultRecordModel.source_kind == source_kind)
        if exclude_agent_keys:
            statement = statement.where(
                CurationExtractionResultRecordModel.agent_key.notin_(
                    [str(agent_key) for agent_key in exclude_agent_keys if str(agent_key).strip()]
                )
            )

        records = session.execute(statement).scalars().all()
        return [_record_to_schema(record) for record in records]
    finally:
        if owns_session:
            session.close()


def list_extraction_results_for_origin_session(
    origin_session_id: str,
    *,
    user_id: Optional[str] = None,
    source_kind: Optional[str] = None,
    document_id: Optional[str] = None,
    exclude_agent_keys: Sequence[str] = (),
    db: Optional[Session] = None,
) -> list[CurationExtractionResultRecord]:
    """Return persisted extraction results for one chat/flow session."""

    owns_session = db is None
    session = db or SessionLocal()

    try:
        query = session.query(CurationExtractionResultRecordModel).filter(
            CurationExtractionResultRecordModel.origin_session_id == origin_session_id,
        )

        if user_id is not None:
            query = query.filter(CurationExtractionResultRecordModel.user_id == user_id)

        if source_kind is not None:
            query = query.filter(CurationExtractionResultRecordModel.source_kind == source_kind)

        if document_id is not None:
            try:
                document_uuid = UUID(str(document_id).strip())
            except (AttributeError, TypeError, ValueError):
                logger.warning(
                    "Ignoring invalid document_id filter for extraction results: %r",
                    document_id,
                )
                return []
            query = query.filter(
                CurationExtractionResultRecordModel.document_id == document_uuid
            )

        excluded = [str(agent_key).strip() for agent_key in exclude_agent_keys if str(agent_key).strip()]
        if excluded:
            query = query.filter(~CurationExtractionResultRecordModel.agent_key.in_(excluded))

        records = query.order_by(
            CurationExtractionResultRecordModel.created_at.asc(),
            CurationExtractionResultRecordModel.id.asc(),
        ).all()
        return [_record_to_schema(record) for record in records]
    finally:
        if owns_session:
            session.close()


def _coerce_tool_output_payload(raw_output: Any) -> Optional[dict[str, Any] | list[Any]]:
    """Best-effort decode of specialist tool output into JSON-compatible payload."""

    if isinstance(raw_output, (dict, list)):
        return raw_output

    if isinstance(raw_output, str):
        payload_text = raw_output.strip()
        if not payload_text:
            return None
        try:
            decoded = json.loads(payload_text)
        except json.JSONDecodeError:
            return None
        if isinstance(decoded, (dict, list)):
            return decoded

    return None


def _is_extraction_envelope_payload(payload: Any) -> bool:
    """Return True when the payload looks like a structured extraction envelope."""

    if not isinstance(payload, dict):
        return False

    run_summary = payload.get("run_summary")
    if not isinstance(run_summary, dict):
        return False

    candidate_count = run_summary.get("candidate_count")
    if candidate_count is not None and not isinstance(candidate_count, int):
        return False

    return any(key in payload for key in _ENVELOPE_EXTRACTION_KEYS)


def _record_to_schema(
    record: CurationExtractionResultRecordModel,
) -> CurationExtractionResultRecord:
    """Convert ORM extraction-result records into API schema models."""

    created_at = record.created_at
    if not isinstance(created_at, datetime):
        raise TypeError("Persisted extraction result record is missing a valid created_at timestamp")

    return CurationExtractionResultRecord(
        extraction_result_id=str(record.id),
        document_id=str(record.document_id),
        adapter_key=record.adapter_key,
        profile_key=record.profile_key,
        domain_key=record.domain_key,
        agent_key=record.agent_key,
        source_kind=record.source_kind,
        origin_session_id=record.origin_session_id,
        trace_id=record.trace_id,
        flow_run_id=record.flow_run_id,
        user_id=record.user_id,
        candidate_count=record.candidate_count,
        conversation_summary=record.conversation_summary,
        payload_json=record.payload_json,
        created_at=created_at,
        metadata=dict(record.extraction_metadata or {}),
    )


def _build_extraction_result_record(
    request: CurationExtractionPersistenceRequest,
) -> CurationExtractionResultRecordModel:
    """Construct an ORM extraction-result record from a validated request."""

    return CurationExtractionResultRecordModel(
        document_id=UUID(str(request.document_id)),
        adapter_key=request.adapter_key,
        profile_key=request.profile_key,
        domain_key=request.domain_key,
        agent_key=request.agent_key,
        source_kind=request.source_kind,
        origin_session_id=request.origin_session_id,
        trace_id=request.trace_id,
        flow_run_id=request.flow_run_id,
        user_id=request.user_id,
        candidate_count=request.candidate_count,
        conversation_summary=request.conversation_summary,
        payload_json=request.payload_json,
        extraction_metadata=dict(request.metadata),
    )


__all__ = [
    "ExtractionEnvelopeCandidate",
    "build_extraction_envelope_candidate",
    "build_safe_agent_key_map",
    "list_extraction_results",
    "list_extraction_results_for_origin_session",
    "persist_extraction_result",
    "persist_extraction_results",
    "resolve_agent_key_from_tool_name",
]
