"""Real PostgreSQL delivery-lock and recovery tests; HTTP is a bounded fake.

The receiver's independent contract suite covers exact duplicate ingestion.
These tests exercise the public sender and its actual committed rows/locks.
"""

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import delete, select
from src.lib.curation_workspace import benchmark_snapshots as sender
from src.lib.curation_workspace.models import DomainEnvelopeModel
from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import PDFDocument
from src.schemas.domain_envelope import DomainEnvelopeStatus
from tests.pdf_document_test_support import ensure_test_pdf_owner

IDENTITY = {
    "sender_issuer": "https://identity.example/pool",
    "sender_subject": "handoff-test-owner",
}


@pytest.fixture(scope="module", autouse=True)
def migrated_database():
    command.upgrade(
        Config(str(Path(__file__).resolve().parents[3] / "alembic.ini")), "head"
    )


@pytest.fixture
def snapshot(monkeypatch):
    now = datetime.now(timezone.utc)
    document_id, session_id, snapshot_id = uuid4(), uuid4(), uuid4()
    envelope_id = str(uuid4())
    with SessionLocal() as db:
        owner = ensure_test_pdf_owner(db, auth_sub=IDENTITY["sender_subject"])
        db.add(
            PDFDocument(
                id=document_id,
                user_id=owner,
                filename="retry.pdf",
                title="Synthetic retry",
                file_path="synthetic/retry.pdf",
                file_hash=uuid4().hex * 2,
                file_size=1,
                page_count=1,
                upload_timestamp=now,
                last_accessed=now,
                status="processed",
            )
        )
        db.flush()
        db.add(
            sender.CurationReviewSession(
                id=session_id,
                document_id=document_id,
                adapter_key="example",
                prepared_at=now,
                created_by_id=IDENTITY["sender_subject"],
            )
        )
        db.flush()
        db.add(
            DomainEnvelopeModel(
                envelope_id=envelope_id,
                project_key="example",
                domain_pack_key="example",
                adapter_key="example",
                source_payload_hash="a" * 64,
                status=DomainEnvelopeStatus.EXTRACTED,
                document_id=document_id,
                session_id=session_id,
                envelope_json={"synthetic": True},
            )
        )
        db.flush()
        db.add(
            sender.CurationBenchmarkSnapshot(
                id=snapshot_id,
                session_id=session_id,
                envelope_id=envelope_id,
                envelope_revision=1,
                envelope_digest="sha256:" + "a" * 64,
                bundle_json='{"immutable":"synthetic"}',
                created_by_id=IDENTITY["sender_subject"],
                exported_at=now,
            )
        )
        db.commit()
    destination = sender.BenchmarkHandoffDestination(
        label="Test",
        sink_url="https://receiver.example/handoffs",
        token_url="https://identity.example/token",
        client_id="test-sender",
        client_secret_env="TEST_HANDOFF_SECRET",
        scope="handoff:send",
        allowed_redirect_origin="https://receiver.example",
        allowed_redirect_path_prefix="/comparisons",
    )
    monkeypatch.setattr(sender, "get_benchmark_snapshot_handoff_enabled", lambda: True)
    monkeypatch.setattr(sender, "_destination_registry", lambda: {"test": destination})
    monkeypatch.setenv("TEST_HANDOFF_SECRET", "synthetic")
    try:
        yield snapshot_id, envelope_id
    finally:
        with SessionLocal() as db:
            db.execute(
                delete(sender.CurationBenchmarkHandoffAttempt).where(
                    sender.CurationBenchmarkHandoffAttempt.snapshot_id == snapshot_id
                )
            )
            db.execute(
                delete(sender.CurationBenchmarkSnapshot).where(
                    sender.CurationBenchmarkSnapshot.id == snapshot_id
                )
            )
            db.execute(
                delete(DomainEnvelopeModel).where(
                    DomainEnvelopeModel.envelope_id == envelope_id
                )
            )
            db.execute(
                delete(sender.CurationReviewSession).where(
                    sender.CurationReviewSession.id == session_id
                )
            )
            db.execute(delete(PDFDocument).where(PDFDocument.id == document_id))
            db.commit()


async def deliver(snapshot_id, *, retry=False, owner=IDENTITY["sender_subject"]):
    with SessionLocal() as db:
        return await sender.handoff_benchmark_snapshot(
            db,
            snapshot_id=snapshot_id,
            destination_id="test",
            current_user_id=owner,
            retry_delivery=retry,
            **IDENTITY,
        )


def saved_attempt(snapshot_id):
    with SessionLocal() as db:
        return db.scalar(
            select(sender.CurationBenchmarkHandoffAttempt).where(
                sender.CurationBenchmarkHandoffAttempt.snapshot_id == snapshot_id
            )
        )


def install_transport(monkeypatch, on_delivery):
    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

        async def post(self, url, **kwargs):
            request = httpx.Request("POST", url)
            if url.endswith("/token"):
                return httpx.Response(
                    200, json={"access_token": "synthetic"}, request=request
                )
            return await on_delivery(request, kwargs)

    monkeypatch.setattr(sender.httpx, "AsyncClient", Client)


def receipt(request):
    return httpx.Response(
        200,
        json={
            "receipt_id": "retained",
            "redirect_url": "https://receiver.example/comparisons/retained",
        },
        request=request,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("interrupted", [False, True])
async def test_initial_delivery_and_retry_are_single_flight_and_interruption_releases_lock(
    snapshot, monkeypatch, interrupted
):
    snapshot_id, _ = snapshot
    entered, release = asyncio.Event(), asyncio.Event()
    payloads = []

    async def blocked(request, payload):
        payloads.append(payload)
        entered.set()
        await release.wait()
        return receipt(request)

    install_transport(monkeypatch, blocked)
    first = asyncio.create_task(deliver(snapshot_id))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        # Separate actual DB sessions: ordinary Send reads without corrupting
        # sending; explicit recovery cannot block the event loop or dispatch.
        duplicate = await asyncio.wait_for(deliver(snapshot_id), 5)
        assert duplicate.status == "unknown"
        assert saved_attempt(snapshot_id).status == "sending"
        with pytest.raises(sender.CurationBenchmarkSnapshotError) as busy:
            await asyncio.wait_for(deliver(snapshot_id, retry=True), 5)
        assert busy.value.error == "handoff_in_progress"
        assert len(payloads) == 1
        if interrupted:
            first.cancel()
            with pytest.raises(asyncio.CancelledError):
                await first
            assert saved_attempt(snapshot_id).status == "sending"
            release.set()
            result = await deliver(snapshot_id, retry=True)
            assert result.status == "succeeded" and len(payloads) == 2
            assert payloads[0] == payloads[1]
        else:
            release.set()
            result = await first
            assert result.status == "succeeded"
            replay = await deliver(snapshot_id, retry=True)
            assert replay.receipt_id == result.receipt_id and len(payloads) == 1
    finally:
        release.set()
        if not first.done():
            first.cancel()
        await asyncio.gather(first, return_exceptions=True)


@pytest.mark.asyncio
async def test_failed_retry_commits_uncertainty_before_http_and_survives_interruption(
    snapshot, monkeypatch
):
    snapshot_id, _ = snapshot
    monkeypatch.delenv("TEST_HANDOFF_SECRET")
    assert (await deliver(snapshot_id)).status == "failed"
    original = saved_attempt(snapshot_id)
    monkeypatch.setenv("TEST_HANDOFF_SECRET", "synthetic")
    entered, release = asyncio.Event(), asyncio.Event()
    captured = []

    async def blocked(request, payload):
        captured.append(payload)
        entered.set()
        await release.wait()
        return receipt(request)

    install_transport(monkeypatch, blocked)
    retry = asyncio.create_task(deliver(snapshot_id, retry=True))
    try:
        await asyncio.wait_for(entered.wait(), 5)
        assert saved_attempt(snapshot_id).status == "sending"
        with pytest.raises(sender.CurationBenchmarkSnapshotError) as busy:
            await deliver(snapshot_id, retry=True)
        assert busy.value.error == "handoff_in_progress"
        assert len(captured) == 1
        retry.cancel()
        with pytest.raises(asyncio.CancelledError):
            await retry
        assert saved_attempt(snapshot_id).status == "sending"
        release.set()
        result = await deliver(snapshot_id, retry=True)
        assert result.status == "succeeded"
        assert result.handoff_id == str(original.id)
        assert captured[0] == captured[1]
    finally:
        release.set()
        if not retry.done():
            retry.cancel()
        await asyncio.gather(retry, return_exceptions=True)


@pytest.mark.asyncio
@pytest.mark.parametrize("retry", [False, True])
@pytest.mark.parametrize("competing_status", ["unknown", "failed", "succeeded"])
async def test_completed_competitor_in_reservation_gap_is_not_redispatched(
    snapshot, monkeypatch, retry, competing_status
):
    snapshot_id, _ = snapshot
    if retry:
        monkeypatch.delenv("TEST_HANDOFF_SECRET")
        assert (await deliver(snapshot_id)).status == "failed"
        monkeypatch.setenv("TEST_HANDOFF_SECRET", "synthetic")

    def no_http(**kwargs):
        pytest.fail("A completed competing delivery must be returned without HTTP")

    monkeypatch.setattr(sender.httpx, "AsyncClient", no_http)
    with SessionLocal() as db:
        commit = db.commit

        def competing_completion():
            commit()
            # Another process finishes between reservation and lock reacquire.
            # Refresh this actual transaction, not the cached ORM object.
            with SessionLocal() as other:
                attempt = other.scalar(
                    select(sender.CurationBenchmarkHandoffAttempt).where(
                        sender.CurationBenchmarkHandoffAttempt.snapshot_id
                        == snapshot_id
                    )
                )
                attempt.status = competing_status
                attempt.updated_at = datetime.now(timezone.utc)
                if competing_status == "succeeded":
                    attempt.receipt_id = "retained"
                    attempt.redirect_path = "/comparisons/retained"
                other.commit()

        monkeypatch.setattr(db, "commit", competing_completion)
        result = await sender.handoff_benchmark_snapshot(
            db,
            snapshot_id=snapshot_id,
            destination_id="test",
            current_user_id=IDENTITY["sender_subject"],
            retry_delivery=retry,
            **IDENTITY,
        )
        assert result.status == competing_status
        assert saved_attempt(snapshot_id).status == competing_status


@pytest.mark.asyncio
async def test_lost_ack_recovery_uses_original_bytes_after_envelope_changes(
    snapshot, monkeypatch
):
    snapshot_id, envelope_id = snapshot
    captured = []

    async def lost_ack(request, payload):
        captured.append(payload)
        if len(captured) == 1:
            raise httpx.ReadTimeout("synthetic lost acknowledgement", request=request)
        return receipt(request)

    install_transport(monkeypatch, lost_ack)
    assert (await deliver(snapshot_id)).status == "unknown"
    prior = saved_attempt(snapshot_id)
    with SessionLocal() as db:
        envelope = db.get(DomainEnvelopeModel, envelope_id)
        envelope.revision = 2
        envelope.envelope_json = {"synthetic": "later curator review"}
        db.commit()
    with pytest.raises(sender.CurationBenchmarkSnapshotError) as forbidden:
        await deliver(snapshot_id, retry=True, owner="another-owner")
    assert forbidden.value.status_code == 404
    assert len(captured) == 1
    recovered = await deliver(snapshot_id, retry=True)
    assert recovered.receipt_id == "retained"
    current = saved_attempt(snapshot_id)
    assert (
        current.id,
        current.snapshot_id,
        current.idempotency_key,
        current.replay_key,
    ) == (prior.id, prior.snapshot_id, prior.idempotency_key, prior.replay_key)
    assert captured[0] == captured[1]
    assert captured[1]["content"] == b'{"immutable":"synthetic"}'
