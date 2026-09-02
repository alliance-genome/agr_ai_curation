"""Immutable curation snapshot export and configured benchmark handoff."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import json
import logging
import os
import re
from typing import Any, Mapping
from urllib.parse import unquote, urlsplit
from uuid import UUID, uuid4

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from src.lib.curation_workspace.models import (
    CurationBenchmarkHandoffAttempt,
    CurationBenchmarkSnapshot,
    CurationReviewSession,
    DomainEnvelopeHistory,
    DomainEnvelopeModel,
)
from src.lib.openai_agents.config import (
    get_benchmark_handoff_timeout_seconds,
    get_benchmark_max_snapshot_bytes,
    get_benchmark_snapshot_handoff_destinations_json,
    get_benchmark_snapshot_handoff_enabled,
)
from src.schemas.curation_workspace import (
    CurationBenchmarkHandoffResponse,
    CurationBenchmarkSchemaReference,
    CurationBenchmarkSnapshotBundleV1,
    CurationBenchmarkSnapshotCreateResponse,
    CurationBenchmarkSnapshotProvenance,
)
from src.schemas.domain_envelope import DomainEnvelope, HistoryEventKind


logger = logging.getLogger(__name__)
SNAPSHOT_SCHEMA_VERSION = "curation-benchmark-snapshot/v1"
_DESTINATION_FIELDS = {
    "sink_url",
    "token_url",
    "client_id",
    "scope",
    "client_secret_env",
    "allowed_redirect_origin",
    "allowed_redirect_path_prefix",
}
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


class CurationBenchmarkSnapshotError(RuntimeError):
    """Stable public error raised by snapshot and handoff operations."""

    def __init__(self, status_code: int, error: str, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error = error
        self.message = message


@dataclass(frozen=True)
class BenchmarkHandoffDestination:
    sink_url: str
    token_url: str
    client_id: str
    scope: str
    client_secret_env: str
    allowed_redirect_origin: str
    allowed_redirect_path_prefix: str


def canonical_json_bytes(value: Mapping[str, Any]) -> bytes:
    """Serialize one JSON object using the v1 canonical byte contract."""

    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _require_session_access(
    db: Session,
    session_id: UUID,
    current_user_id: str,
) -> CurationReviewSession:
    session_row = db.get(CurationReviewSession, session_id)
    if session_row is None:
        raise CurationBenchmarkSnapshotError(404, "snapshot_not_found", "Snapshot was not found")
    permitted = session_row.assigned_curator_id == current_user_id or (
        session_row.assigned_curator_id is None
        and session_row.created_by_id == current_user_id
    )
    if not permitted:
        raise CurationBenchmarkSnapshotError(404, "snapshot_not_found", "Snapshot was not found")
    return session_row


def _schema_references(envelope: DomainEnvelope) -> list[CurationBenchmarkSchemaReference]:
    references: dict[tuple[str, str], CurationBenchmarkSchemaReference] = {}
    candidates = [envelope.schema_ref]
    candidates.extend(item.schema_ref for item in envelope.extracted_objects)
    for schema_ref in candidates:
        if schema_ref is None:
            continue
        key = (schema_ref.schema_id, schema_ref.version or "")
        references[key] = CurationBenchmarkSchemaReference(
            schema_id=schema_ref.schema_id,
            schema_version=schema_ref.version,
        )
    return [references[key] for key in sorted(references)]


def create_benchmark_snapshot(
    db: Session,
    *,
    session_id: UUID,
    envelope_id: str,
    expected_revision: int,
    current_user_id: str,
) -> CurationBenchmarkSnapshotCreateResponse:
    """Lock and export the exact current persisted envelope revision."""

    _require_session_access(db, session_id, current_user_id)
    envelope_row = db.scalars(
        select(DomainEnvelopeModel)
        .where(
            DomainEnvelopeModel.envelope_id == envelope_id,
            DomainEnvelopeModel.session_id == session_id,
        )
        .with_for_update()
    ).first()
    if envelope_row is None:
        raise CurationBenchmarkSnapshotError(404, "envelope_not_found", "Envelope was not found")
    if envelope_row.revision != expected_revision:
        raise CurationBenchmarkSnapshotError(
            409,
            "stale_envelope_revision",
            "Envelope revision is not current; reload before exporting",
        )

    envelope = DomainEnvelope.model_validate(envelope_row.envelope_json)
    exported_envelope = json.loads(json.dumps(envelope_row.envelope_json))
    exported_envelope.pop("authenticated_context", None)
    envelope_bytes = canonical_json_bytes(exported_envelope)
    envelope_digest = f"sha256:{sha256(envelope_bytes).hexdigest()}"
    curator_modified = db.scalar(
        select(DomainEnvelopeHistory.event_id)
        .where(
            DomainEnvelopeHistory.envelope_id == envelope_id,
            DomainEnvelopeHistory.envelope_revision <= expected_revision,
            DomainEnvelopeHistory.event_type
            == HistoryEventKind.CURATOR_FIELD_PATCH_ACCEPTED,
        )
        .limit(1)
    ) is not None

    snapshot_id = uuid4()
    exported_at = datetime.now(timezone.utc)
    bundle = CurationBenchmarkSnapshotBundleV1(
        schema_version=SNAPSHOT_SCHEMA_VERSION,
        snapshot_id=str(snapshot_id),
        session_id=str(session_id),
        envelope_id=envelope_id,
        envelope_revision=expected_revision,
        envelope_status=envelope_row.status.value,
        curation_state="curator_modified" if curator_modified else "ai_untouched",
        schema_references=_schema_references(envelope),
        provenance=CurationBenchmarkSnapshotProvenance(
            document_id=str(envelope_row.document_id),
            flow_run_id=envelope_row.flow_run_id,
        ),
        exported_at=exported_at,
        envelope_digest=envelope_digest,
        envelope=exported_envelope,
    )
    bundle_bytes = canonical_json_bytes(bundle.model_dump(mode="json"))
    if len(bundle_bytes) > get_benchmark_max_snapshot_bytes():
        raise CurationBenchmarkSnapshotError(
            413,
            "snapshot_too_large",
            "Snapshot exceeds the configured size limit",
        )

    db.add(
        CurationBenchmarkSnapshot(
            id=snapshot_id,
            session_id=session_id,
            envelope_id=envelope_id,
            envelope_revision=expected_revision,
            envelope_digest=envelope_digest,
            bundle_json=bundle_bytes.decode("utf-8"),
            created_by_id=current_user_id,
            exported_at=exported_at,
        )
    )
    db.flush()
    return CurationBenchmarkSnapshotCreateResponse(
        snapshot_id=str(snapshot_id),
        schema_version=SNAPSHOT_SCHEMA_VERSION,
        envelope_revision=expected_revision,
        envelope_digest=envelope_digest,
        download_path=f"/api/curation-workspace/benchmark-snapshots/{snapshot_id}/download",
    )


def load_benchmark_snapshot_bytes(
    db: Session,
    *,
    snapshot_id: UUID,
    current_user_id: str,
) -> bytes:
    """Load immutable canonical bundle bytes after exact owner authorization."""

    snapshot = db.get(CurationBenchmarkSnapshot, snapshot_id)
    if snapshot is None or snapshot.created_by_id != current_user_id:
        raise CurationBenchmarkSnapshotError(404, "snapshot_not_found", "Snapshot was not found")
    _require_session_access(db, snapshot.session_id, current_user_id)
    return snapshot.bundle_json.encode("utf-8")


def _https_url(value: str, *, field_name: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.fragment
    ):
        raise CurationBenchmarkSnapshotError(
            503,
            "handoff_configuration_invalid",
            f"Configured {field_name} is invalid",
        )
    return value


def _destination_registry() -> dict[str, BenchmarkHandoffDestination]:
    try:
        raw = json.loads(get_benchmark_snapshot_handoff_destinations_json())
    except (TypeError, ValueError):
        raise CurationBenchmarkSnapshotError(
            503,
            "handoff_configuration_invalid",
            "Benchmark handoff destination registry is invalid",
        ) from None
    if not isinstance(raw, dict):
        raise CurationBenchmarkSnapshotError(
            503,
            "handoff_configuration_invalid",
            "Benchmark handoff destination registry is invalid",
        )

    registry: dict[str, BenchmarkHandoffDestination] = {}
    for destination_id, payload in raw.items():
        if not isinstance(destination_id, str) or not destination_id or not isinstance(payload, dict):
            raise CurationBenchmarkSnapshotError(503, "handoff_configuration_invalid", "Benchmark handoff destination registry is invalid")
        if set(payload) != _DESTINATION_FIELDS or not all(
            isinstance(payload[field], str) and payload[field]
            for field in _DESTINATION_FIELDS
        ):
            raise CurationBenchmarkSnapshotError(503, "handoff_configuration_invalid", "Benchmark handoff destination registry is invalid")
        secret_env = payload["client_secret_env"]
        if not _ENV_NAME.fullmatch(secret_env):
            raise CurationBenchmarkSnapshotError(503, "handoff_configuration_invalid", "Configured client secret environment name is invalid")
        sink_url = _https_url(payload["sink_url"], field_name="sink URL")
        token_url = _https_url(payload["token_url"], field_name="token URL")
        allowed_origin = _https_url(
            payload["allowed_redirect_origin"],
            field_name="redirect origin",
        )
        origin_parts = urlsplit(allowed_origin)
        if origin_parts.path not in ("", "/") or origin_parts.query:
            raise CurationBenchmarkSnapshotError(503, "handoff_configuration_invalid", "Configured redirect origin is invalid")
        prefix = payload["allowed_redirect_path_prefix"]
        if not prefix.startswith("/") or "?" in prefix or "#" in prefix:
            raise CurationBenchmarkSnapshotError(503, "handoff_configuration_invalid", "Configured redirect path policy is invalid")
        registry[destination_id] = BenchmarkHandoffDestination(
            sink_url=sink_url,
            token_url=token_url,
            client_id=payload["client_id"],
            scope=payload["scope"],
            client_secret_env=secret_env,
            allowed_redirect_origin=allowed_origin.rstrip("/"),
            allowed_redirect_path_prefix=prefix,
        )
    return registry


def _identity_digest(*parts: str) -> str:
    encoded = "\x1f".join(parts).encode("utf-8")
    return f"sha256:{sha256(encoded).hexdigest()}"


def _handoff_response(attempt: CurationBenchmarkHandoffAttempt) -> CurationBenchmarkHandoffResponse:
    public_status = "unknown" if attempt.status == "sending" else attempt.status
    return CurationBenchmarkHandoffResponse(
        handoff_id=str(attempt.id),
        snapshot_id=str(attempt.snapshot_id),
        destination_id=attempt.destination_id,
        status=public_status,
        receipt_id=attempt.receipt_id if public_status == "succeeded" else None,
        redirect_path=attempt.redirect_path if public_status == "succeeded" else None,
    )


def _finish_attempt(
    db: Session,
    attempt: CurationBenchmarkHandoffAttempt,
    *,
    status: str,
    failure_code: str | None = None,
    receipt_id: str | None = None,
    redirect_path: str | None = None,
) -> CurationBenchmarkHandoffResponse:
    attempt.status = status
    attempt.failure_code = failure_code
    attempt.receipt_id = receipt_id
    attempt.redirect_path = redirect_path
    attempt.updated_at = datetime.now(timezone.utc)
    db.commit()
    return _handoff_response(attempt)


def _validated_redirect_path(redirect_url: str, destination: BenchmarkHandoffDestination) -> str:
    parsed = urlsplit(redirect_url)
    configured = urlsplit(destination.allowed_redirect_origin)
    decoded_path = unquote(parsed.path)
    if (
        parsed.scheme != configured.scheme
        or parsed.hostname != configured.hostname
        or parsed.port != configured.port
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or "\\" in decoded_path
        or any(ord(character) < 32 for character in decoded_path)
        or any(segment in {".", ".."} for segment in decoded_path.split("/"))
        or not _path_matches_prefix(
            parsed.path, destination.allowed_redirect_path_prefix
        )
        or not _path_matches_prefix(
            decoded_path, destination.allowed_redirect_path_prefix
        )
    ):
        raise ValueError("redirect policy rejected")
    return parsed.path


def _path_matches_prefix(path: str, prefix: str) -> bool:
    if path == prefix or prefix.endswith("/"):
        return path.startswith(prefix)
    return path.startswith(f"{prefix}/")


async def handoff_benchmark_snapshot(
    db: Session,
    *,
    snapshot_id: UUID,
    destination_id: str,
    current_user_id: str,
) -> CurationBenchmarkHandoffResponse:
    """Reserve and deliver one immutable bundle without automatic retries."""

    if not get_benchmark_snapshot_handoff_enabled():
        raise CurationBenchmarkSnapshotError(503, "handoff_disabled", "Benchmark snapshot handoff is disabled")
    destination = _destination_registry().get(destination_id)
    if destination is None:
        raise CurationBenchmarkSnapshotError(400, "destination_not_configured", "Benchmark handoff destination is not configured")

    snapshot = db.get(CurationBenchmarkSnapshot, snapshot_id)
    if snapshot is None or snapshot.created_by_id != current_user_id:
        raise CurationBenchmarkSnapshotError(404, "snapshot_not_found", "Snapshot was not found")
    _require_session_access(db, snapshot.session_id, current_user_id)

    replay_key = _identity_digest(
        destination_id,
        snapshot.envelope_id,
        str(snapshot.envelope_revision),
    )
    idempotency_key = _identity_digest(
        destination_id,
        snapshot.envelope_id,
        str(snapshot.envelope_revision),
        snapshot.envelope_digest,
    )
    attempt = db.scalars(
        select(CurationBenchmarkHandoffAttempt)
        .where(CurationBenchmarkHandoffAttempt.replay_key == replay_key)
        .with_for_update()
    ).first()
    if attempt is not None:
        if attempt.idempotency_key != idempotency_key:
            raise CurationBenchmarkSnapshotError(409, "handoff_replay_conflict", "Handoff replay identity conflicts with the reserved snapshot")
        if attempt.status == "sending":
            return _finish_attempt(db, attempt, status="unknown", failure_code="prior_delivery_ambiguous")
        return _handoff_response(attempt)

    attempt = CurationBenchmarkHandoffAttempt(
        id=uuid4(),
        snapshot_id=snapshot.id,
        destination_id=destination_id,
        replay_key=replay_key,
        idempotency_key=idempotency_key,
        status="sending",
    )
    db.add(attempt)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        reserved = db.scalar(
            select(CurationBenchmarkHandoffAttempt).where(
                CurationBenchmarkHandoffAttempt.replay_key == replay_key
            )
        )
        if reserved is None or reserved.idempotency_key != idempotency_key:
            raise CurationBenchmarkSnapshotError(409, "handoff_replay_conflict", "Handoff replay identity conflicts with the reserved snapshot") from None
        return _handoff_response(reserved)

    client_secret = os.getenv(destination.client_secret_env, "")
    if not client_secret:
        logger.error("Benchmark snapshot handoff credential is unavailable")
        return _finish_attempt(db, attempt, status="failed", failure_code="credential_unavailable")

    timeout = get_benchmark_handoff_timeout_seconds()
    async with httpx.AsyncClient(
        timeout=httpx.Timeout(timeout),
        follow_redirects=False,
    ) as client:
        try:
            token_response = await client.post(
                destination.token_url,
                data={"grant_type": "client_credentials", "scope": destination.scope},
                auth=(destination.client_id, client_secret),
            )
        except (httpx.TimeoutException, httpx.RequestError):
            logger.warning("Benchmark snapshot handoff token request failed")
            return _finish_attempt(
                db, attempt, status="failed", failure_code="token_request_failed"
            )
        if not token_response.is_success:
            logger.warning("Benchmark snapshot handoff token request failed")
            return _finish_attempt(
                db, attempt, status="failed", failure_code="token_request_failed"
            )
        try:
            token_payload = token_response.json()
            access_token = (
                token_payload.get("access_token")
                if isinstance(token_payload, dict)
                else None
            )
        except (TypeError, ValueError):
            access_token = None
        if not isinstance(access_token, str) or not access_token:
            logger.warning("Benchmark snapshot handoff token response was invalid")
            return _finish_attempt(
                db, attempt, status="failed", failure_code="token_response_invalid"
            )
        try:
            sink_response = await client.post(
                destination.sink_url,
                content=snapshot.bundle_json.encode("utf-8"),
                headers={
                    "Authorization": f"Bearer {access_token}",
                    "Content-Type": "application/json",
                    "Idempotency-Key": idempotency_key,
                },
            )
        except httpx.TimeoutException:
            logger.warning(
                "Benchmark snapshot handoff timed out with an ambiguous delivery result"
            )
            return _finish_attempt(
                db, attempt, status="unknown", failure_code="delivery_timeout"
            )
        except httpx.RequestError:
            logger.warning(
                "Benchmark snapshot handoff transport failed with an ambiguous delivery result"
            )
            return _finish_attempt(
                db,
                attempt,
                status="unknown",
                failure_code="delivery_transport_error",
            )

    if not sink_response.is_success:
        logger.warning("Benchmark snapshot handoff sink rejected the delivery")
        return _finish_attempt(db, attempt, status="failed", failure_code="sink_rejected")
    try:
        sink_payload = sink_response.json()
        receipt_id = sink_payload.get("receipt_id") if isinstance(sink_payload, dict) else None
        redirect_url = sink_payload.get("redirect_url") if isinstance(sink_payload, dict) else None
        if not isinstance(receipt_id, str) or not receipt_id or not isinstance(redirect_url, str):
            raise ValueError("invalid receipt")
        redirect_path = _validated_redirect_path(redirect_url, destination)
    except (TypeError, ValueError):
        logger.warning("Benchmark snapshot handoff receipt was invalid")
        return _finish_attempt(db, attempt, status="failed", failure_code="receipt_invalid")

    return _finish_attempt(
        db,
        attempt,
        status="succeeded",
        receipt_id=receipt_id,
        redirect_path=redirect_path,
    )


__all__ = [
    "CurationBenchmarkSnapshotError",
    "SNAPSHOT_SCHEMA_VERSION",
    "canonical_json_bytes",
    "create_benchmark_snapshot",
    "handoff_benchmark_snapshot",
    "load_benchmark_snapshot_bytes",
]
