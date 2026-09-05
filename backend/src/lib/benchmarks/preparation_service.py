"""Coordinate authorized, durable preparation of one frozen job input."""

from __future__ import annotations

import json
import asyncio
from typing import Any, Callable
from uuid import UUID

from src.lib.benchmarks.curator_authorization import authorize_benchmark_curator
from src.lib.benchmarks.document_preparation import PreparedBenchmarkDocument, prepare_frozen_document, verify_prepared_document
from src.lib.benchmarks.execution_context import BenchmarkCuratorContext
from src.lib.benchmarks.preparation_repository import BenchmarkPreparationRepository, PreparationStageCheckpoint
from src.lib.benchmarks.snapshots import BenchmarkSnapshotRepository, configured_benchmark_snapshot_store
from src.lib.weaviate_client.connection import get_connection
from src.models.sql.benchmark import BenchmarkInputSnapshot, BenchmarkJob
from src.models.sql.database import SessionLocal


async def prepare_job_document(
    *, job_id: UUID, snapshot_id: UUID, lease_owner: UUID,
    session_factory: Callable[..., Any] = SessionLocal,
) -> tuple[PreparedBenchmarkDocument, BenchmarkCuratorContext]:
    """Return the same preparation for all arms of an authorized job/snapshot.

    The worker must keep its heartbeat active and apply its execution timeout
    around this operation. No original input resolver or human token is used.
    """
    with session_factory() as session:
        job = session.get(BenchmarkJob, job_id)
        if job is None:
            raise LookupError("benchmark preparation job not found")
        curator = BenchmarkCuratorContext.model_validate_json(json.dumps(job.curator_context))
        owner = job.owner_subject
    await authorize_benchmark_curator(curator, session_factory=session_factory)
    frozen_input: tuple[bytes, str] | None = None
    with session_factory() as session:
        repository = BenchmarkPreparationRepository(session)
        claim = repository.begin(job_id=job_id, snapshot_id=snapshot_id, lease_owner=lease_owner)
        if claim.prepared is None:
            snapshot = session.get(BenchmarkInputSnapshot, snapshot_id)
            if snapshot is None:
                raise LookupError("benchmark preparation snapshot not found")
            frozen_input = (
                BenchmarkSnapshotRepository(session, configured_benchmark_snapshot_store()).read_verified(
                    snapshot_id, owner_subject=owner,
                ),
                snapshot.content_type,
            )
            # The identity survives interruption from this point onward. Never run
            # paid preparation before this commit succeeds.
            session.commit()
    if claim.prepared is not None:
        # Release the journal transaction before filesystem I/O and the fresh
        # ownership query; do not require two pool connections while holding it.
        await asyncio.to_thread(verify_prepared_document, claim.prepared, curator)
        return claim.prepared, curator
    assert frozen_input is not None
    content, content_type = frozen_input
    checkpoint = PreparationStageCheckpoint(
        job_id=job_id, snapshot_id=snapshot_id, document_id=claim.document_id,
        lease_owner=lease_owner, session_factory=session_factory,
    )
    # Separate operation from comparison execution; no target routing override
    # or target provider-invocation observer should surround preparation.
    # Normal storage opens its own connection session inside its worker thread.
    # Passing the raw SDK client here would lose that lifecycle interface.
    receipt = await prepare_frozen_document(
        document_id=claim.document_id, content=content, content_type=content_type,
        snapshot_digest=claim.snapshot_digest, curator=curator,
        weaviate_client=get_connection(), stage_checkpoint=checkpoint,
    )
    await asyncio.to_thread(verify_prepared_document, receipt, curator)
    with session_factory() as session:
        BenchmarkPreparationRepository(session).complete(
            job_id=job_id, snapshot_id=snapshot_id, lease_owner=lease_owner, receipt=receipt,
        )
        session.commit()
    return receipt, curator
