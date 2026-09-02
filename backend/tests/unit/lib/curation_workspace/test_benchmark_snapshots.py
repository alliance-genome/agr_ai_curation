"""Focused tests for immutable curation benchmark snapshots and handoffs."""

from __future__ import annotations

from hashlib import sha256
import json
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from src.lib.curation_workspace import benchmark_snapshots as module
from src.schemas.domain_envelope import DomainEnvelopeStatus


class _ScalarRows:
    def __init__(self, value):
        self.value = value

    def first(self):
        return self.value


class _SnapshotDb:
    def __init__(self, *, envelope, modified_event=None):
        self.envelope = envelope
        self.modified_event = modified_event
        self.added = None
        self.scalar_calls = 0
        self.envelope_statement = None

    def get(self, model, _identity):
        assert model is module.CurationReviewSession
        return SimpleNamespace(assigned_curator_id=None, created_by_id="curator-1")

    def scalars(self, _statement):
        self.envelope_statement = _statement
        return _ScalarRows(self.envelope)

    def scalar(self, _statement):
        self.scalar_calls += 1
        return self.modified_event

    def add(self, value):
        self.added = value

    def flush(self):
        return None


def _envelope_row(*, revision=7, status=DomainEnvelopeStatus.EXTRACTED):
    envelope_id = "env-1"
    return SimpleNamespace(
        envelope_id=envelope_id,
        revision=revision,
        status=status,
        document_id=uuid4(),
        flow_run_id=None,
        envelope_json={
            "envelope_id": envelope_id,
            "domain_pack_id": "example",
            "status": status.value,
            "schema_ref": {
                "schema_id": "envelope-schema",
                "version": "1.2",
            },
            "extracted_objects": [
                {
                    "object_type": "example_record",
                    "pending_ref_id": "pending-1",
                    "status": "extracted",
                    "schema_ref": {
                        "schema_id": "object-schema",
                        "version": "3",
                    },
                    "payload": {"pending": True},
                }
            ],
            "validation_findings": [
                {"severity": "warning", "message": "Review this value"}
            ],
            "history": [],
            "metadata": {"semantic_value": "preserved"},
            "authenticated_context": {"active_groups": ["PRIVATE_GROUP"]},
        },
    )


@pytest.mark.parametrize(
    ("modified_event", "expected_state"),
    [(None, "ai_untouched"), ("accepted-event", "curator_modified")],
)
def test_snapshot_is_canonical_redacted_and_history_classified(
    monkeypatch, modified_event, expected_state
):
    monkeypatch.setattr(module, "get_benchmark_max_snapshot_bytes", lambda: 100_000)
    envelope = _envelope_row()
    db = _SnapshotDb(envelope=envelope, modified_event=modified_event)
    session_id = uuid4()

    response = module.create_benchmark_snapshot(
        db,
        session_id=session_id,
        envelope_id=envelope.envelope_id,
        expected_revision=7,
        current_user_id="curator-1",
    )

    assert db.added is not None
    bundle_bytes = db.added.bundle_json.encode("utf-8")
    bundle = json.loads(bundle_bytes)
    assert bundle_bytes == module.canonical_json_bytes(bundle)
    assert bundle["curation_state"] == expected_state
    assert bundle["envelope_status"] == "extracted"
    assert bundle["envelope"]["metadata"] == {"semantic_value": "preserved"}
    assert "authenticated_context" not in bundle["envelope"]
    assert bundle["schema_references"] == [
        {"schema_id": "envelope-schema", "schema_version": "1.2"},
        {"schema_id": "object-schema", "schema_version": "3"},
    ]
    envelope_bytes = module.canonical_json_bytes(bundle["envelope"])
    assert bundle["envelope_digest"] == f"sha256:{sha256(envelope_bytes).hexdigest()}"
    assert response.envelope_revision == 7
    assert str(response.snapshot_id) in response.download_path
    assert db.envelope_statement._for_update_arg is not None


def test_snapshot_rejects_stale_revision_before_export(monkeypatch):
    monkeypatch.setattr(module, "get_benchmark_max_snapshot_bytes", lambda: 100_000)
    envelope = _envelope_row(revision=8)
    db = _SnapshotDb(envelope=envelope)

    with pytest.raises(module.CurationBenchmarkSnapshotError) as exc_info:
        module.create_benchmark_snapshot(
            db,
            session_id=uuid4(),
            envelope_id=envelope.envelope_id,
            expected_revision=7,
            current_user_id="curator-1",
        )

    assert exc_info.value.status_code == 409
    assert exc_info.value.error == "stale_envelope_revision"
    assert db.added is None


def test_snapshot_allows_pending_objects_and_validation_warnings(monkeypatch):
    monkeypatch.setattr(module, "get_benchmark_max_snapshot_bytes", lambda: 100_000)
    envelope = _envelope_row(status=DomainEnvelopeStatus.EXTRACTION_PENDING)
    envelope.envelope_json["extracted_objects"][0]["status"] = "pending"
    db = _SnapshotDb(envelope=envelope)

    module.create_benchmark_snapshot(
        db,
        session_id=uuid4(),
        envelope_id=envelope.envelope_id,
        expected_revision=7,
        current_user_id="curator-1",
    )

    assert db.added is not None
    bundle = json.loads(db.added.bundle_json)
    assert bundle["envelope_status"] == "extraction_pending"
    assert bundle["envelope"]["extracted_objects"][0]["status"] == "pending"
    assert bundle["envelope"]["validation_findings"][0]["severity"] == "warning"


def test_snapshot_access_fails_closed_for_another_curator():
    class _UnauthorizedDb(_SnapshotDb):
        def get(self, model, _identity):
            return SimpleNamespace(
                assigned_curator_id="curator-2", created_by_id="curator-2"
            )

    db = _UnauthorizedDb(envelope=_envelope_row())

    with pytest.raises(module.CurationBenchmarkSnapshotError) as exc_info:
        module.create_benchmark_snapshot(
            db,
            session_id=uuid4(),
            envelope_id="env-1",
            expected_revision=7,
            current_user_id="curator-1",
        )

    assert (exc_info.value.status_code, exc_info.value.error) == (404, "snapshot_not_found")


def test_destination_registry_rejects_caller_selected_or_insecure_urls(monkeypatch):
    monkeypatch.setattr(
        module,
        "get_benchmark_snapshot_handoff_destinations_json",
        lambda: json.dumps(
            {
                "portal": {
                    "sink_url": "http://private.example/snapshots",
                    "token_url": "https://auth.example/token",
                    "client_id": "sender",
                    "scope": "snapshot:write",
                    "client_secret_env": "PORTAL_SECRET",
                    "allowed_redirect_origin": "https://portal.example",
                    "allowed_redirect_path_prefix": "/comparisons",
                }
            }
        ),
    )

    with pytest.raises(module.CurationBenchmarkSnapshotError) as exc_info:
        module._destination_registry()

    assert exc_info.value.error == "handoff_configuration_invalid"


class _HandoffDb:
    def __init__(self, snapshot, session, prior=None):
        self.snapshot = snapshot
        self.session = session
        self.prior = prior
        self.added = None

    def get(self, model, _identity):
        if model is module.CurationBenchmarkSnapshot:
            return self.snapshot
        assert model is module.CurationReviewSession
        return self.session

    def scalars(self, _statement):
        return _ScalarRows(self.prior)

    def scalar(self, _statement):
        return self.prior

    def add(self, value):
        self.added = value

    def commit(self):
        return None

    def rollback(self):
        return None


def _destination():
    return module.BenchmarkHandoffDestination(
        sink_url="https://sink.example/api/snapshots",
        token_url="https://auth.example/oauth2/token",
        client_id="snapshot-sender",
        scope="snapshot:write",
        client_secret_env="PORTAL_CLIENT_SECRET",
        allowed_redirect_origin="https://portal.example",
        allowed_redirect_path_prefix="/comparisons",
    )


@pytest.mark.asyncio
async def test_exact_handoff_replay_returns_receipt_and_conflict_fails_closed(monkeypatch):
    snapshot = SimpleNamespace(
        id=uuid4(), session_id=uuid4(), envelope_id="env-1", envelope_revision=7,
        envelope_digest="sha256:" + "d" * 64, created_by_id="curator-1",
        bundle_json='{"exact":true}',
    )
    replay_key = module._identity_digest("portal", "env-1", "7")
    idempotency_key = module._identity_digest(
        "portal", "env-1", "7", snapshot.envelope_digest
    )
    prior = SimpleNamespace(
        id=uuid4(), snapshot_id=snapshot.id, destination_id="portal",
        replay_key=replay_key, idempotency_key=idempotency_key, status="succeeded",
        receipt_id="receipt-1", redirect_path="/comparisons/receipt-1",
    )
    db = _HandoffDb(
        snapshot,
        SimpleNamespace(assigned_curator_id=None, created_by_id="curator-1"),
        prior=prior,
    )
    monkeypatch.setattr(module, "get_benchmark_snapshot_handoff_enabled", lambda: True)
    monkeypatch.setattr(module, "_destination_registry", lambda: {"portal": _destination()})

    result = await module.handoff_benchmark_snapshot(
        db, snapshot_id=snapshot.id, destination_id="portal", current_user_id="curator-1"
    )
    assert result.receipt_id == "receipt-1"

    prior.idempotency_key = "sha256:" + "e" * 64
    with pytest.raises(module.CurationBenchmarkSnapshotError) as exc_info:
        await module.handoff_benchmark_snapshot(
            db, snapshot_id=snapshot.id, destination_id="portal", current_user_id="curator-1"
        )
    assert exc_info.value.error == "handoff_replay_conflict"


class _Response:
    def __init__(self, payload, *, success=True):
        self.payload = payload
        self.is_success = success

    def json(self):
        return self.payload


@pytest.mark.asyncio
async def test_handoff_uses_server_credentials_exact_bytes_and_opaque_redirect(monkeypatch):
    snapshot = SimpleNamespace(
        id=uuid4(),
        session_id=uuid4(),
        envelope_id="env-1",
        envelope_revision=7,
        envelope_digest="sha256:" + "a" * 64,
        created_by_id="curator-1",
        bundle_json='{"exact":true}',
    )
    db = _HandoffDb(
        snapshot,
        SimpleNamespace(assigned_curator_id=None, created_by_id="curator-1"),
    )
    calls = []

    class _Client:
        def __init__(self, **kwargs):
            assert kwargs["follow_redirects"] is False

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, url, **kwargs):
            calls.append((url, kwargs))
            if url == "https://auth.example/oauth2/token":
                return _Response({"access_token": "fake-sensitive-token"})
            return _Response(
                {
                    "receipt_id": "receipt-opaque",
                    "redirect_url": "https://portal.example/comparisons/opaque",
                }
            )

    monkeypatch.setattr(module, "get_benchmark_snapshot_handoff_enabled", lambda: True)
    monkeypatch.setattr(module, "_destination_registry", lambda: {"portal": _destination()})
    monkeypatch.setattr(module, "get_benchmark_handoff_timeout_seconds", lambda: 30)
    monkeypatch.setenv("PORTAL_CLIENT_SECRET", "fake-sensitive-secret")
    monkeypatch.setattr(module.httpx, "AsyncClient", _Client)

    result = await module.handoff_benchmark_snapshot(
        db,
        snapshot_id=snapshot.id,
        destination_id="portal",
        current_user_id="curator-1",
    )

    assert result.status == "succeeded"
    assert result.receipt_id == "receipt-opaque"
    assert result.redirect_path == "/comparisons/opaque"
    assert calls[1][1]["content"] == b'{"exact":true}'
    assert calls[1][1]["headers"]["Authorization"] == "Bearer fake-sensitive-token"
    assert calls[1][1]["headers"]["Idempotency-Key"].startswith("sha256:")


@pytest.mark.asyncio
async def test_ambiguous_sink_timeout_is_persisted_unknown_without_retry(monkeypatch):
    snapshot = SimpleNamespace(
        id=uuid4(), session_id=uuid4(), envelope_id="env-1", envelope_revision=7,
        envelope_digest="sha256:" + "b" * 64, created_by_id="curator-1",
        bundle_json='{"exact":true}',
    )
    db = _HandoffDb(snapshot, SimpleNamespace(assigned_curator_id=None, created_by_id="curator-1"))
    call_count = 0

    class _Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, **_kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return _Response({"access_token": "token"})
            raise httpx.ReadTimeout("ambiguous")

    monkeypatch.setattr(module, "get_benchmark_snapshot_handoff_enabled", lambda: True)
    monkeypatch.setattr(module, "_destination_registry", lambda: {"portal": _destination()})
    monkeypatch.setattr(module, "get_benchmark_handoff_timeout_seconds", lambda: 30)
    monkeypatch.setenv("PORTAL_CLIENT_SECRET", "secret")
    monkeypatch.setattr(module.httpx, "AsyncClient", _Client)

    result = await module.handoff_benchmark_snapshot(
        db, snapshot_id=snapshot.id, destination_id="portal", current_user_id="curator-1"
    )
    assert result.status == "unknown"
    assert db.added is not None
    assert db.added.failure_code == "delivery_timeout"
    assert call_count == 2

    db.prior = db.added
    replay = await module.handoff_benchmark_snapshot(
        db, snapshot_id=snapshot.id, destination_id="portal", current_user_id="curator-1"
    )
    assert replay.status == "unknown"
    assert call_count == 2


@pytest.mark.asyncio
async def test_token_failure_is_sanitized_and_persisted_failed(monkeypatch, caplog):
    sensitive = "fake-sensitive-token-response"
    snapshot = SimpleNamespace(
        id=uuid4(), session_id=uuid4(), envelope_id="env-1", envelope_revision=7,
        envelope_digest="sha256:" + "c" * 64, created_by_id="curator-1",
        bundle_json='{"exact":true}',
    )
    db = _HandoffDb(snapshot, SimpleNamespace(assigned_curator_id=None, created_by_id="curator-1"))

    class _Client:
        def __init__(self, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def post(self, _url, **_kwargs):
            return _Response({"error_description": sensitive}, success=False)

    monkeypatch.setattr(module, "get_benchmark_snapshot_handoff_enabled", lambda: True)
    monkeypatch.setattr(module, "_destination_registry", lambda: {"portal": _destination()})
    monkeypatch.setattr(module, "get_benchmark_handoff_timeout_seconds", lambda: 30)
    monkeypatch.setenv("PORTAL_CLIENT_SECRET", "fake-sensitive-client-secret")
    monkeypatch.setattr(module.httpx, "AsyncClient", _Client)
    caplog.set_level("WARNING", logger=module.logger.name)

    result = await module.handoff_benchmark_snapshot(
        db, snapshot_id=snapshot.id, destination_id="portal", current_user_id="curator-1"
    )

    assert result.status == "failed"
    assert db.added is not None
    assert db.added.failure_code == "token_request_failed"
    assert sensitive not in caplog.text
    assert "fake-sensitive-client-secret" not in caplog.text


def test_redirect_policy_rejects_prefix_confusion_and_query_values():
    destination = _destination()
    for redirect in (
        "https://portal.example/comparisonsevil/opaque",
        "https://portal.example/comparisons/opaque?token=secret",
        "https://portal.example/comparisons/%2e%2e/private",
        "https://other.example/comparisons/opaque",
    ):
        with pytest.raises(ValueError):
            module._validated_redirect_path(redirect, destination)
