"""Persistence helpers for structured extraction envelopes."""

from __future__ import annotations

import json
import hashlib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable, Mapping, Optional, Sequence
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from pydantic import ValidationError

from src.lib.openai_agents.evidence_summary import (
    extract_evidence_records_from_structured_result,
)
from src.lib.curation_workspace.models import (
    CurationExtractionResultRecord as CurationExtractionResultRecordModel,
)
from src.models.sql.database import SessionLocal
from src.schemas.curation_workspace import (
    CurationExtractionPersistenceRequest,
    CurationExtractionPersistenceResponse,
    CurationExtractionResultRecord,
)
from src.schemas.domain_envelope import DomainEnvelope

logger = logging.getLogger(__name__)

_EXTRACTION_TOOL_NAME_PATTERN = re.compile(
    r"^ask_(?P<tool_segment>.+?)(?:_step\d+)?_specialist$"
)
_ENVELOPE_EXTRACTION_KEYS = frozenset(
    {"curatable_objects", "items", "raw_mentions", "exclusions", "ambiguities"}
)
_DOMAIN_ENVELOPE_KEYS = frozenset(
    {"envelope_id", "domain_pack_id", "extracted_objects"}
)
_NUL_CHARACTER = "\x00"


@dataclass(frozen=True)
class ExtractionEnvelopeCandidate:
    """Parsed extraction envelope that is ready for persistence."""

    agent_key: str
    payload_json: dict[str, Any] | list[Any]
    candidate_count: int = 0
    adapter_key: Optional[str] = None
    conversation_summary: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class InlineExtractionPersistenceResult:
    """Stable identifiers returned after inline validated extraction persistence."""

    extraction_result_id: str
    result_ref: str
    created_new: bool
    idempotency_key: str
    payload_hash: str
    extraction_result: CurationExtractionResultRecord


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
    metadata: Optional[Mapping[str, Any]] = None,
) -> Optional[ExtractionEnvelopeCandidate]:
    """Convert a tool output payload into a persistable extraction candidate."""

    payload = _coerce_tool_output_payload(raw_output)
    if payload is None or not _is_extraction_envelope_payload(payload):
        return None

    canonical_agent_key = str(agent_key or "").strip()
    if not canonical_agent_key:
        return None

    envelope_metadata = dict(metadata or {})
    if _is_domain_envelope_payload(payload):
        extracted_objects = (
            payload.get("extracted_objects", []) if isinstance(payload, dict) else []
        )
        candidate_count = (
            len(extracted_objects) if isinstance(extracted_objects, list) else 0
        )
    else:
        run_summary = payload.get("run_summary", {}) if isinstance(payload, dict) else {}
        candidate_count_raw = run_summary.get("candidate_count", 0)
        candidate_count = candidate_count_raw if isinstance(candidate_count_raw, int) else 0
    resolved_adapter_key = str(adapter_key or "").strip() or None
    agent_curation = None
    if resolved_adapter_key is None:
        agent_curation = _get_agent_curation_metadata(canonical_agent_key)
        if agent_curation is not None and not agent_curation["launchable"]:
            return None
        if agent_curation is not None:
            resolved_adapter_key = agent_curation["adapter_key"]

    if isinstance(payload, dict):
        actor = payload.get("actor")
        destination = payload.get("destination")
        envelope_destination = (
            destination.strip() or None if isinstance(destination, str) else None
        )

        if actor:
            envelope_metadata.setdefault("envelope_actor", actor)
        if envelope_destination is not None:
            envelope_metadata.setdefault("envelope_destination", envelope_destination)

    if agent_curation is not None and resolved_adapter_key is None:
        raise ValueError(
            "Launchable curation extraction agents must declare curation.adapter_key "
            f"(agent_key={canonical_agent_key})."
        )
    if resolved_adapter_key is None:
        return None

    if isinstance(payload, dict):
        payload = _normalize_extraction_payload_for_adapter(
            payload,
            adapter_key=resolved_adapter_key,
        )

    return ExtractionEnvelopeCandidate(
        agent_key=canonical_agent_key,
        payload_json=payload,
        candidate_count=max(candidate_count, 0),
        adapter_key=resolved_adapter_key,
        conversation_summary=str(conversation_summary).strip() or None
        if conversation_summary is not None
        else None,
        metadata=envelope_metadata,
    )


def _normalize_extraction_payload_for_adapter(
    payload: dict[str, Any],
    *,
    adapter_key: str,
) -> dict[str, Any]:
    normalizer = _extraction_payload_normalizer_for_adapter(adapter_key)
    if normalizer is None:
        return payload
    normalized = normalizer(payload)
    if not isinstance(normalized, dict):
        raise ValueError(
            "Curation extraction payload normalizers must return a JSON object "
            f"(adapter_key={adapter_key})."
        )
    return normalized


def _extraction_payload_normalizer_for_adapter(adapter_key: str) -> Any | None:
    from src.lib.curation_workspace.adapter_registry import (
        load_curation_adapter_registry,
    )

    return load_curation_adapter_registry().get_extraction_payload_normalizer(adapter_key)


def build_extraction_envelope_candidate_with_evidence(
    raw_output: Any,
    *,
    agent_key: Optional[str],
    conversation_summary: Optional[str] = None,
    adapter_key: Optional[str] = None,
    metadata: Optional[Mapping[str, Any]] = None,
) -> tuple[Optional[ExtractionEnvelopeCandidate], dict[str, Any]]:
    """Build one extraction candidate plus normalized evidence metadata."""

    candidate = build_extraction_envelope_candidate(
        raw_output,
        agent_key=agent_key,
        conversation_summary=conversation_summary,
        adapter_key=adapter_key,
        metadata=metadata,
    )
    evidence_source = candidate.payload_json if candidate is not None else raw_output
    evidence_records = extract_evidence_records_from_structured_result(evidence_source)

    return candidate, {
        "evidence_records": evidence_records,
        "evidence_count": len(evidence_records),
    }


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


def persist_inline_validated_extraction_result(
    *,
    payload_json: Mapping[str, Any],
    document_id: str,
    agent_key: str,
    adapter_key: str,
    tool_name: str,
    source_kind: Any,
    origin_session_id: str | None = None,
    trace_id: str | None = None,
    flow_run_id: str | None = None,
    user_id: str | None = None,
    builder_finalization: Any | None = None,
    metadata: Mapping[str, Any] | None = None,
    db: Optional[Session] = None,
) -> InlineExtractionPersistenceResult:
    """Persist a validated canonical domain envelope and return its stable ref.

    This helper is intentionally stricter than the candidate builder:
    builder-backed chat extractions must persist canonical ``extracted_objects`` envelopes
    only, never old row sources or prose-derived artifacts.
    """

    canonical_payload = _validated_inline_domain_envelope_payload(payload_json)
    payload_hash = _canonical_payload_hash(canonical_payload)
    builder_summary = _builder_finalization_summary(builder_finalization)
    idempotency_key = _inline_extraction_idempotency_key(
        source_kind=source_kind,
        origin_session_id=origin_session_id,
        trace_id=trace_id,
        tool_name=tool_name,
        agent_key=agent_key,
        adapter_key=adapter_key,
        builder_run_id=builder_summary.get("builder_run_id"),
        builder_invocation_id=builder_summary.get("builder_invocation_id"),
        canonical_payload_hash=payload_hash,
    )
    candidate_count = len(canonical_payload.get("extracted_objects", []))

    owns_session = db is None
    session = db or SessionLocal()

    try:
        existing = _load_extraction_result_by_idempotency_key(
            session,
            idempotency_key,
        )
        if existing is not None:
            return _inline_persistence_result(
                existing,
                created_new=False,
                idempotency_key=idempotency_key,
                payload_hash=payload_hash,
            )

        persistence_metadata = dict(metadata or {})
        persistence_metadata.update(
            {
                "persistence_phase": "inline_validated_extraction",
                "tool_name": str(tool_name or "").strip(),
                "builder_finalization": builder_summary,
                "payload_hash": payload_hash,
                "idempotency_key": idempotency_key,
            }
        )
        request = CurationExtractionPersistenceRequest(
            document_id=document_id,
            adapter_key=adapter_key,
            agent_key=agent_key,
            source_kind=source_kind,
            origin_session_id=origin_session_id,
            trace_id=trace_id,
            flow_run_id=flow_run_id,
            user_id=user_id,
            candidate_count=max(candidate_count, 0),
            conversation_summary=None,
            payload_json=canonical_payload,
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
            metadata=persistence_metadata,
        )
        record = _build_extraction_result_record(request)
        session.add(record)

        try:
            if owns_session:
                session.commit()
            else:
                session.flush()
        except IntegrityError:
            session.rollback()
            existing = _load_extraction_result_by_idempotency_key(
                session,
                idempotency_key,
            )
            if existing is not None:
                return _inline_persistence_result(
                    existing,
                    created_new=False,
                    idempotency_key=idempotency_key,
                    payload_hash=payload_hash,
                )
            raise

        session.refresh(record)
        return _inline_persistence_result(
            record,
            created_new=True,
            idempotency_key=idempotency_key,
            payload_hash=payload_hash,
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
    """Persist extraction envelopes, reusing the caller transaction when provided."""

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

        if owns_session:
            session.commit()
        else:
            session.flush()

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
    flow_run_id: str | None = None,
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
        if flow_run_id:
            statement = statement.where(
                CurationExtractionResultRecordModel.flow_run_id == str(flow_run_id).strip()
            )
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

    if _is_domain_envelope_payload(payload):
        return True

    run_summary = payload.get("run_summary")
    if not isinstance(run_summary, dict):
        return False

    candidate_count = run_summary.get("candidate_count")
    if candidate_count is not None and not isinstance(candidate_count, int):
        return False

    return any(key in payload for key in _ENVELOPE_EXTRACTION_KEYS)


def _is_domain_envelope_payload(payload: Any) -> bool:
    """Return True when the payload already uses the persisted domain-envelope shape."""

    if not isinstance(payload, dict):
        return False
    if not _DOMAIN_ENVELOPE_KEYS.issubset(payload):
        return False
    return isinstance(payload.get("extracted_objects"), list)


def _is_strict_canonical_domain_envelope_payload(payload: Any) -> bool:
    """Return True only for canonical domain envelopes accepted by inline persistence."""

    if not _is_domain_envelope_payload(payload):
        return False
    if any(key in payload for key in _ENVELOPE_EXTRACTION_KEYS):
        return False
    return True


def _validated_inline_domain_envelope_payload(
    payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate and normalize the canonical envelope persisted from inline chat."""

    if not _is_strict_canonical_domain_envelope_payload(payload):
        raise ValueError(
            "Inline extraction persistence requires a strict canonical domain envelope "
            "that validates as DomainEnvelope and includes extracted_objects."
        )

    try:
        envelope = DomainEnvelope.model_validate(dict(payload))
    except ValidationError as exc:
        raise ValueError(
            "Inline extraction persistence DomainEnvelope schema validation failed: "
            f"{exc}"
        ) from exc

    return _sanitize_persisted_json_value(envelope.model_dump(mode="json"))


def _canonical_payload_hash(payload: Mapping[str, Any]) -> str:
    serialized = json.dumps(
        payload,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _inline_extraction_idempotency_key(
    *,
    source_kind: Any,
    origin_session_id: str | None,
    trace_id: str | None,
    tool_name: str,
    agent_key: str,
    adapter_key: str,
    builder_run_id: Any,
    builder_invocation_id: Any,
    canonical_payload_hash: str,
) -> str:
    material = {
        "source_kind": _source_kind_value(source_kind),
        "origin_session_id": _optional_idempotency_text(origin_session_id),
        "trace_id": _optional_idempotency_text(trace_id),
        "tool_name": _required_idempotency_text(tool_name, "tool_name"),
        "agent_key": _required_idempotency_text(agent_key, "agent_key"),
        "adapter_key": _required_idempotency_text(adapter_key, "adapter_key"),
        "builder_run_id": _optional_idempotency_text(builder_run_id),
        "builder_invocation_id": _optional_idempotency_text(builder_invocation_id),
        "canonical_payload_hash": _required_idempotency_text(
            canonical_payload_hash,
            "canonical_payload_hash",
        ),
    }
    serialized = json.dumps(
        material,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
    return f"inline-extraction:{digest}"


def _source_kind_value(source_kind: Any) -> str:
    return _required_idempotency_text(
        getattr(source_kind, "value", source_kind),
        "source_kind",
    )


def _required_idempotency_text(value: Any, field_name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"Inline extraction persistence requires {field_name}.")
    return text


def _optional_idempotency_text(value: Any) -> str | None:
    text = str(value or "").strip()
    return text or None


def _builder_finalization_summary(builder_finalization: Any | None) -> dict[str, Any]:
    if builder_finalization is None:
        return {}
    summary = getattr(builder_finalization, "summary", None)
    if callable(summary):
        value = summary()
        if isinstance(value, Mapping):
            return dict(value)
    if isinstance(builder_finalization, Mapping):
        return dict(builder_finalization)
    return {}


def _load_extraction_result_by_idempotency_key(
    session: Session,
    idempotency_key: str,
) -> CurationExtractionResultRecordModel | None:
    statement = select(CurationExtractionResultRecordModel).where(
        CurationExtractionResultRecordModel.idempotency_key == idempotency_key
    )
    return session.execute(statement).scalars().first()


def _inline_persistence_result(
    record: CurationExtractionResultRecordModel,
    *,
    created_new: bool,
    idempotency_key: str,
    payload_hash: str,
) -> InlineExtractionPersistenceResult:
    schema = _record_to_schema(record)
    extraction_result_id = schema.extraction_result_id
    return InlineExtractionPersistenceResult(
        extraction_result_id=extraction_result_id,
        result_ref=f"extraction-result:{extraction_result_id}",
        created_new=created_new,
        idempotency_key=idempotency_key,
        payload_hash=payload_hash,
        extraction_result=schema,
    )


def get_agent_curation_metadata(agent_key: str) -> dict[str, Any] | None:
    from src.lib.agent_studio.catalog_service import get_agent_metadata

    try:
        metadata = get_agent_metadata(agent_key)
    except ValueError:
        return None

    curation = metadata.get("curation")
    if not isinstance(curation, Mapping):
        return None

    adapter_key = str(curation.get("adapter_key") or "").strip() or None
    launchable = bool(curation.get("launchable", False))
    return {
        "adapter_key": adapter_key,
        "launchable": launchable,
    }


def _get_agent_curation_metadata(agent_key: str) -> dict[str, Any] | None:
    return get_agent_curation_metadata(agent_key)


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
        agent_key=record.agent_key,
        source_kind=record.source_kind,
        origin_session_id=record.origin_session_id,
        trace_id=record.trace_id,
        flow_run_id=record.flow_run_id,
        user_id=record.user_id,
        candidate_count=record.candidate_count,
        conversation_summary=record.conversation_summary,
        payload_json=record.payload_json,
        idempotency_key=getattr(record, "idempotency_key", None),
        payload_hash=getattr(record, "payload_hash", None),
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
        agent_key=request.agent_key,
        source_kind=request.source_kind,
        origin_session_id=request.origin_session_id,
        trace_id=request.trace_id,
        flow_run_id=request.flow_run_id,
        user_id=request.user_id,
        candidate_count=request.candidate_count,
        conversation_summary=_sanitize_persisted_text(request.conversation_summary),
        payload_json=_sanitize_persisted_json_value(request.payload_json),
        idempotency_key=_sanitize_persisted_text(request.idempotency_key),
        payload_hash=_sanitize_persisted_text(request.payload_hash),
        extraction_metadata=_sanitize_persisted_json_value(dict(request.metadata)),
    )


def _sanitize_persisted_text(value: str | None) -> str | None:
    """Remove characters Postgres cannot store in text-backed JSON payloads."""

    if value is None:
        return None
    return value.replace(_NUL_CHARACTER, "")


def _sanitize_persisted_json_value(value: Any) -> Any:
    """Recursively sanitize JSON-like payloads before persisting them."""

    if isinstance(value, str):
        return _sanitize_persisted_text(value)

    if isinstance(value, Mapping):
        return {
            _sanitize_persisted_text(key) if isinstance(key, str) else key:
            _sanitize_persisted_json_value(nested_value)
            for key, nested_value in value.items()
        }

    if isinstance(value, list):
        return [_sanitize_persisted_json_value(item) for item in value]

    if isinstance(value, tuple):
        return tuple(_sanitize_persisted_json_value(item) for item in value)

    return value


__all__ = [
    "ExtractionEnvelopeCandidate",
    "InlineExtractionPersistenceResult",
    "build_extraction_envelope_candidate",
    "build_extraction_envelope_candidate_with_evidence",
    "build_safe_agent_key_map",
    "get_agent_curation_metadata",
    "list_extraction_results",
    "persist_inline_validated_extraction_result",
    "persist_extraction_result",
    "persist_extraction_results",
    "resolve_agent_key_from_tool_name",
]
