"""Frozen preparation through real SQL, artifacts and normal chunking.

Hierarchy/figure model calls and Weaviate network operations are synthetic.
Normal chunk serialization, persistence verification, and runtime reads are real.
No original source, model provider, or identity service is contacted.
"""

import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID, uuid4

from alembic import command  # pyright: ignore[reportAttributeAccessIssue]
from alembic.config import Config  # pyright: ignore[reportMissingImports]
import pytest
from sqlalchemy import delete, select

from src.lib.benchmarks import document_preparation as preparation
from src.lib.benchmarks import preparation_service
from src.lib.benchmarks import runtime as benchmark_runtime
from src.lib.benchmarks.models import BenchmarkExecutionTarget
from src.lib.document_context import DocumentContext
from src.lib.openai_agents.provider_usage import (
    ProviderUsageRecord, begin_provider_invocation, complete_provider_invocation,
)
from src.lib.benchmarks.execution_context import BenchmarkCuratorContext
from src.lib.benchmarks.persistence import BenchmarkRepository
from src.lib.benchmarks.snapshots import FileSystemBenchmarkSnapshotStore
from src.lib.benchmarks.worker import BenchmarkWorker
from src.models.sql.benchmark import BenchmarkCell, BenchmarkCellStatus, BenchmarkEvent, BenchmarkInputSnapshot, BenchmarkInvocation, BenchmarkJob
from src.models.sql.database import SessionLocal
from src.models.sql.pdf_document import PDFDocument
from src.models.sql.user import User
from tests.integration.persistence.test_benchmark_repository import _digest
from tests.integration.persistence.test_benchmark_worker import _plan_with_input_digest


@pytest.fixture(scope="module", autouse=True)
def migrated_database():
    command.upgrade(Config(str(Path(__file__).resolve().parents[3] / "alembic.ini")), "head")


@pytest.mark.asyncio
@pytest.mark.parametrize("content_type,content", [
    ("application/json", b'[{"text":"Frozen scientific evidence.","metadata":{"page_number":2}}]'),
    ("text/markdown", b"# Results\n\nFrozen scientific evidence."),
    ("text/plain", b"Frozen scientific evidence."),
    ("application/xml", b"<article><body><sec><title>Results</title><p>Frozen scientific evidence.</p></sec></body></article>"),
])
async def test_frozen_copy_uses_normal_owned_ingestion_and_preserves_source(
    monkeypatch, tmp_path, content_type, content,
):
    subject = f"preparation-test-{uuid4()}"
    document_id = uuid4()
    with SessionLocal() as session:
        user = User(auth_sub=subject, is_active=True)
        session.add(user)
        session.commit()
        user_id = user.id
    curator = BenchmarkCuratorContext(
        subject=subject, auth_provider="oidc", db_user_id=user_id, active_groups=("group-alpha",),
    )
    monkeypatch.setattr(preparation, "get_pdf_storage_path", lambda: str(tmp_path))
    create = AsyncMock(return_value={"success": True})
    async def require_committed_start(user_subject, vector_document):
        nonlocal document_id
        document_id = UUID(vector_document.id)
        # An independent transaction must see the start before any vector or
        # model work. A flush in the coordinator transaction is insufficient.
        with SessionLocal() as observer:
            started = observer.scalar(select(BenchmarkEvent).where(
                BenchmarkEvent.job_id == job_id,
                BenchmarkEvent.event_type == "document_preparation.started",
            ))
            assert started is not None
            assert started.payload["document_id"] == vector_document.id
        return {"success": True}
    create.side_effect = require_committed_start
    monkeypatch.setattr(preparation, "create_document", create)

    async def hierarchy(elements):
        return elements, {}

    async def figures(chunks, **kwargs):
        return chunks

    monkeypatch.setattr("src.lib.pipeline.hierarchy_resolution.resolve_document_hierarchy", hierarchy)
    monkeypatch.setattr("src.lib.pipeline.figure_locator_resolution.resolve_figure_locators", figures)
    from src.lib.pipeline import store as storage
    from src.lib.weaviate_client import chunks as chunk_reader
    vector_objects = {}
    collection = MagicMock()
    batch = collection.batch.rate_limit.return_value.__enter__.return_value
    batch.number_errors = 0
    batch.failed_objects = []

    def insert(*, properties, uuid):
        vector_objects[str(uuid)] = SimpleNamespace(uuid=uuid, properties=properties)

    def matches(properties, condition):
        if hasattr(condition, "filters"):
            return all(matches(properties, item) for item in condition.filters)
        return properties.get(condition.target) == condition.value

    def fetch(*, filters, **kwargs):
        return SimpleNamespace(objects=[obj for obj in vector_objects.values() if matches(obj.properties, filters)])

    batch.add_object.side_effect = insert
    collection.query.fetch_objects.side_effect = fetch
    collection.query.fetch_object_by_id.side_effect = lambda identifier: vector_objects.get(identifier)
    raw_client = SimpleNamespace()  # A raw SDK client has no connection.session().

    class SyntheticConnection:
        @contextmanager
        def session(self):
            yield raw_client

    connection = SyntheticConnection()

    def owned_collections(client, user_subject):
        assert client is raw_client
        assert user_subject == subject
        return collection, MagicMock()

    monkeypatch.setattr("src.lib.weaviate_helpers.get_user_collections", owned_collections)
    monkeypatch.setattr(chunk_reader, "get_connection", lambda: connection)
    monkeypatch.setattr(storage, "update_document_status_detailed", AsyncMock())
    store = AsyncMock(wraps=storage.store_to_weaviate)
    monkeypatch.setattr("src.lib.pipeline.store.store_to_weaviate", store)
    digest = f"sha256:{hashlib.sha256(content).hexdigest()}"
    kwargs: dict[str, Any] = dict(
        document_id=document_id, content=content, content_type=content_type,
        snapshot_digest=digest, curator=curator, weaviate_client=object(),
        stage_checkpoint=AsyncMock(),
    )
    snapshot_store = FileSystemBenchmarkSnapshotStore(tmp_path / "frozen")
    monkeypatch.setattr(preparation_service, "configured_benchmark_snapshot_store", lambda: snapshot_store)
    authorization = AsyncMock(return_value=curator)
    monkeypatch.setattr(preparation_service, "authorize_benchmark_curator", authorization)
    monkeypatch.setattr("src.lib.benchmarks.worker.authorize_benchmark_curator", authorization)
    monkeypatch.setattr(preparation_service, "get_connection", lambda: connection)
    owner = f"service:preparation-{uuid4()}"
    lease_owner = uuid4()
    suite, plan = _plan_with_input_digest(content)
    target = BenchmarkExecutionTarget(kind="agent", id="extractor")
    query = "Extract records from the frozen paper."
    routes = {"agent:extractor": plan.cells[0].routes["supervisor"]}
    case_updates = {"target": target, "user_query": query}
    suite = suite.model_copy(update={
        "cases": (suite.cases[0].model_copy(update=case_updates),),
        "configurations": (suite.configurations[0].model_copy(update={"routes": routes}),),
    })
    plan = plan.model_copy(update={
        "cases": (plan.cases[0].model_copy(update=case_updates),),
        "cells": (plan.cells[0].model_copy(update={**case_updates, "routes": routes}),),
    })
    with SessionLocal() as session:
        source = plan.cases[0].input
        snapshot = BenchmarkInputSnapshot(
            id=uuid4(), digest=digest, source_version=source.version,
            content_type=content_type, content_bytes=len(content), resolver_id=source.resolver,
            source_reference=source.reference, sanitized_provenance={
                "resolver": source.resolver, "reference": source.reference,
                "version": source.version, "digest": digest,
            }, owner_subject=owner, service_principal=owner,
            blob_reference=snapshot_store.put(digest=digest, content=content),
        )
        session.add(snapshot)
        session.flush()
        snapshot_id = snapshot.id
        job = BenchmarkRepository(session).create_job(
            owner_subject=owner, curator_context=curator, suite=suite, plan=plan,
            config_digest=_digest("e"), code_digest=_digest("f"), inputs_digest=digest,
            snapshot_ids_by_case={plan.cases[0].case_id: snapshot_id},
        )
        job_id = job.id
        claimed = BenchmarkRepository(session).claim_next_job(
            lease_owner=lease_owner, lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        assert claimed is not None and claimed.id == job_id
        cell = BenchmarkRepository(session).claim_next_cell(
            job_id=job_id, lease_owner=lease_owner,
            lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=5),
        )
        assert cell is not None
        cell_id = cell.id
        session.commit()
    try:
        def build_agent(agent_id, **runtime_input):
            assert agent_id == "extractor"
            assert runtime_input["active_groups"] == ["group-alpha"]
            assert runtime_input["authenticated_groups"] == ["group-alpha"]
            assert runtime_input["db_user_id"] == user_id
            return SimpleNamespace(model=SimpleNamespace())

        # Metadata-only network reads are empty; use the actual DocumentContext.
        monkeypatch.setattr("src.lib.openai_agents.agents.supervisor_agent.fetch_document_hierarchy_sync", lambda *_: None)
        monkeypatch.setattr("src.lib.openai_agents.prompt_utils.fetch_document_abstract_sync", lambda *_: None)
        monkeypatch.setattr(benchmark_runtime, "get_agent_by_id", build_agent)

        async def stream_target(**runtime_input):
            assert runtime_input["user_id"] == curator.subject
            assert runtime_input["active_groups"] == list(curator.active_groups)
            assert runtime_input["document_id"] == create.call_args.args[1].id
            assert runtime_input["context_messages"] == [{"role": "user", "content": query}]
            assert isinstance(runtime_input["doc_context"], DocumentContext)
            # Read through the normal runtime bridge using the properties emitted
            # by real storage, rather than constructing a separate reader fixture.
            from agr_ai_curation_runtime.weaviate_chunks import get_chunk_by_id  # pyright: ignore[reportMissingImports]
            from agr_ai_curation_alliance.tools.documents import create_read_chunk_tool  # pyright: ignore[reportMissingImports]
            from src.lib.packages.package_runner_entrypoint import _build_tool_context
            chunk_id = next(iter(vector_objects))
            chunk = await get_chunk_by_id(
                chunk_id, runtime_input["user_id"], document_id=runtime_input["document_id"],
            )
            assert chunk is not None and "Frozen scientific evidence." in chunk["text"]
            assert await get_chunk_by_id(
                chunk_id, runtime_input["user_id"], document_id=str(uuid4()),
            ) is None
            tool = create_read_chunk_tool(runtime_input)
            payload = {"chunk_id": chunk_id}
            read = await tool.on_invoke_tool(
                _build_tool_context(tool.name, payload), json.dumps(payload),
            )
            assert read.chunk is not None
            assert "Frozen scientific evidence." in read.chunk.content
            assert read.chunk.evidence_spans
            pending = begin_provider_invocation(
                route_slot="agent:extractor", requested_provider="provider-a",
                requested_model="model-a", reasoning_effort="high", started_at=1.0,
            )
            complete_provider_invocation(pending, ProviderUsageRecord(
                requested_provider="provider-a", requested_model="model-a",
                actual_provider="provider-a", actual_model="model-a",
                routing_attempt=0, latency_ms=10, input_tokens=2, output_tokens=3,
                total_tokens=5, billed_cost=None,
            ))
            yield {"type": "STRUCTURED_RESULT", "data": {"result": {"records": [{"text": read.chunk.content}]}}}
            yield {"type": "RUN_FINISHED", "data": {"response": "Not the extraction envelope"}}

        monkeypatch.setattr(benchmark_runtime, "run_agent_streamed", stream_target)
        worker = BenchmarkWorker(
            worker_id=lease_owner, agent_executor=benchmark_runtime.execute_resolved_agent_cell,
        )
        await worker._execute_cell(cell_id)
        with SessionLocal() as session:
            executed = session.get(BenchmarkCell, cell_id)
            assert executed is not None and executed.status == BenchmarkCellStatus.SUCCEEDED
            assert "Frozen scientific evidence." in executed.generated_envelope["records"][0]["text"]
            invocation = session.scalar(select(BenchmarkInvocation).where(BenchmarkInvocation.cell_id == cell_id))
            assert invocation is not None and invocation.route_slot == "agent:extractor"
            assert invocation.total_tokens == 5
            assert invocation.billed_amount is None
        receipt, execution_context = await preparation_service.prepare_job_document(
            job_id=job_id, snapshot_id=snapshot_id, lease_owner=lease_owner,
        )
        assert execution_context == curator
        document_id = receipt.document_id
        kwargs["document_id"] = document_id
        with SessionLocal() as session:
            stages = list(session.scalars(select(BenchmarkEvent).where(
                BenchmarkEvent.job_id == job_id,
                BenchmarkEvent.event_type == "document_preparation.stage",
            ).order_by(BenchmarkEvent.sequence)))
        assert [event.payload["stage"] for event in stages] == [
            "artifacts", "vector_document", "hierarchy", "chunking", "figure_locators",
            "vector_storage", "ready",
        ]
        assert receipt.document_id == document_id
        assert receipt.chunk_count > 0
        assert (tmp_path / receipt.source_path).read_bytes() == content
        parsed = json.loads((tmp_path / receipt.processed_json_path).read_text())
        assert any("Frozen scientific evidence." in element["text"] for element in parsed)
        args = store.call_args.args
        assert args[1] == str(document_id)
        assert args[3] == subject
        assert len(args[0]) == receipt.chunk_count
        assert create.call_args.args[0] == subject
        assert create.call_args.args[1].metadata.checksum == digest.removeprefix("sha256:")
        if content_type == "application/json":
            assert create.call_args.args[1].metadata.page_count == 2
        with SessionLocal() as session:
            document = session.get(PDFDocument, document_id)
            assert document is not None
            assert document.user_id == user_id
            assert document.status == "completed"
            assert document.file_size == len(content)
            assert document.file_path == receipt.source_path
        with pytest.raises(ValueError, match="already exists"):
            await preparation.prepare_frozen_document(**kwargs)
        reused, _ = await preparation_service.prepare_job_document(
            job_id=job_id, snapshot_id=snapshot_id, lease_owner=lease_owner,
        )
        assert reused == receipt
        assert authorization.await_count == 4
        # Reuse must verify both artifacts, not merely find a completed event.
        for relative in (receipt.source_path, receipt.processed_json_path):
            artifact = tmp_path / relative
            original_bytes = artifact.read_bytes()
            artifact.write_bytes(b"synthetic tampering")
            try:
                with pytest.raises(ValueError, match="integrity verification"):
                    await preparation_service.prepare_job_document(
                        job_id=job_id, snapshot_id=snapshot_id, lease_owner=lease_owner,
                    )
            finally:
                artifact.write_bytes(original_bytes)
        # Even identical bytes behind a replacement symlink are not the
        # isolated regular artifact recorded by preparation.
        artifact = tmp_path / receipt.processed_json_path
        original_bytes = artifact.read_bytes()
        replacement = tmp_path / "synthetic-replacement.json"
        replacement.write_bytes(original_bytes)
        artifact.unlink()
        artifact.symlink_to(replacement)
        try:
            with pytest.raises(ValueError, match="isolated regular file"):
                await preparation_service.prepare_job_document(
                    job_id=job_id, snapshot_id=snapshot_id, lease_owner=lease_owner,
                )
        finally:
            artifact.unlink()
            artifact.write_bytes(original_bytes)
        artifact.unlink()
        try:
            with pytest.raises(FileNotFoundError):
                await preparation_service.prepare_job_document(
                    job_id=job_id, snapshot_id=snapshot_id, lease_owner=lease_owner,
                )
        finally:
            artifact.write_bytes(original_bytes)
        authorization.side_effect = PermissionError("synthetic revocation")
        with pytest.raises(PermissionError, match="revocation"):
            await preparation_service.prepare_job_document(
                job_id=job_id, snapshot_id=snapshot_id, lease_owner=lease_owner,
            )
        create.assert_awaited_once()
        store.assert_awaited_once()
    finally:
        with SessionLocal() as session:
            repository = BenchmarkRepository(session)
            now = datetime.now(timezone.utc)
            repository.request_cancellation(job_id=job_id, owner_subject=owner, requested_at=now)
            repository.cancel_queued_cells(job_id=job_id, lease_owner=lease_owner, cancelled_at=now)
            repository.complete_job(job_id=job_id, lease_owner=lease_owner, completed_at=now)
            # Exact test-owned resources; all synthetic writes have been awaited,
            # vectors are in memory, and pytest owns the temporary artifacts.
            session.execute(delete(BenchmarkJob).where(BenchmarkJob.id == job_id))
            session.execute(delete(BenchmarkInputSnapshot).where(BenchmarkInputSnapshot.id == snapshot_id))
            session.execute(delete(PDFDocument).where(PDFDocument.id == document_id))
            session.execute(delete(User).where(User.id == user_id))
            session.commit()
