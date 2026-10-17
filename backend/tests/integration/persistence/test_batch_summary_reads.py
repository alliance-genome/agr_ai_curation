"""Actual PostgreSQL statements for bounded summaries and bulk detail projections."""

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.orm import Session
import os

from src.lib.batch.service import BatchService
from src.lib.batch.summaries import list_summaries, terminal_summaries, lookup_summaries
from src.models.sql.batch import Batch, BatchDocument, BatchDocumentStatus


@pytest.fixture
def db():
    engine = create_engine(os.environ["DATABASE_URL"])
    schema = "batch_reads_" + uuid4().hex
    with engine.connect() as conn:
        transaction = conn.begin()
        conn.execute(text(f"CREATE SCHEMA {schema}"))
        conn.execute(text(f"SET LOCAL search_path TO {schema}"))
        # Real mapped batch schema; unrelated foreign-key graphs are unnecessary
        # for projection tests, so other tables expose the actual read columns.
        Batch.__table__.create(conn)
        BatchDocument.__table__.create(conn)
        conn.execute(
            text("CREATE TABLE curation_flows (id uuid PRIMARY KEY, name text)")
        )
        conn.execute(
            text(
                "CREATE TABLE pdf_documents (id uuid PRIMARY KEY, title text, filename text)"
            )
        )
        conn.execute(
            text(
                "CREATE TABLE curation_review_sessions (id uuid PRIMARY KEY, adapter_key text)"
            )
        )
        conn.execute(
            text("""CREATE TABLE extraction_results (
            id uuid PRIMARY KEY, document_id uuid, flow_run_id text, agent_key text,
            adapter_key text, candidate_count integer, trace_id text,
            origin_session_id text, created_at timestamptz)""")
        )
        with Session(bind=conn) as session:
            yield session
        transaction.rollback()
    engine.dispose()


def batch(db, owner=1, status="completed", created=None, completed=None):
    now = datetime.now(timezone.utc)
    row = Batch(
        id=uuid4(),
        user_id=owner,
        flow_id=uuid4(),
        status=status,
        total_documents=10,
        completed_documents=7 if status == "completed" else 0,
        failed_documents=3 if status == "completed" else 0,
        created_at=created or now - timedelta(days=2),
        completed_at=completed,
        active_group_ids=[],
    )
    db.add(row)
    db.flush()
    return row


@pytest.fixture
def sql(db):
    statements = []

    def capture(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(db.bind, "before_cursor_execute", capture)
    yield statements
    event.remove(db.bind, "before_cursor_execute", capture)


@pytest.mark.parametrize("size", [10, 100, 1000])
def test_history_sql_and_payload_are_bounded(db, sql, size):
    for _ in range(size):
        batch(db)
    sql.clear()
    result = list_summaries(db, 1, limit=5)
    assert result.total == size and len(result.batches) == 5 and result.next_cursor
    assert len(sql) == 3  # exact count, page, scalar existence probe
    assert all(
        not any(
            name in q
            for name in (
                "batch_documents",
                "extraction_results",
                "curation_review_sessions",
                "pdf_documents",
            )
        )
        for q in sql
    )
    assert all("documents" not in b.model_dump() for b in result.batches)
    payload_bytes = len(result.model_dump_json().encode())
    assert payload_bytes < 4000
    print(
        f"history={size} queries={len(sql)} categories=count,page,exists summaries=5 bytes={payload_bytes} metadata_queries=0"
    )


def test_ties_insert_retry_filter_owner_and_exact_counts(db):
    same = datetime.now(timezone.utc) - timedelta(days=1)
    for _ in range(8):
        batch(db, created=same)
    for completed, failed in [(8, 2), (3, 0), (0, 0)]:
        active = batch(db, status="running", created=same - timedelta(days=5))
        active.completed_documents, active.failed_documents = completed, failed
    db.flush()
    batch(db, owner=2)
    first = list_summaries(db, 1, limit=5)
    assert all(b.status == "completed" for b in first.batches)
    assert BatchService(db).active_totals(1) == {
        "running_count": 3,
        "pending_documents": 17,
    }
    assert BatchService(db).active_totals(99) == {
        "running_count": 0,
        "pending_documents": 0,
    }
    new = batch(db, created=datetime.now(timezone.utc) + timedelta(seconds=1))
    second = list_summaries(db, 1, limit=5, cursor=first.next_cursor)
    retry = list_summaries(db, 1, limit=5, cursor=first.next_cursor)
    assert retry == second
    assert new.id not in [b.id for b in second.batches]
    assert not set(b.id for b in first.batches) & set(b.id for b in second.batches)
    final = list_summaries(db, 1, limit=5, cursor=second.next_cursor)
    assert len({b.id for p in [first, second, final] for b in p.batches}) == 11
    assert list_summaries(db, 1, status="active").total == 3
    for kwargs in [{"status": "active"}, {"user": 2}]:
        with pytest.raises(ValueError, match="cursor"):
            list_summaries(
                db,
                kwargs.get("user", 1),
                cursor=first.next_cursor,
                status=kwargs.get("status"),
            )
    for limit in [0, -1, 101]:
        with pytest.raises(ValueError):
            list_summaries(db, 1, limit=limit)
    for cursor in ["", "garbage"]:
        with pytest.raises(ValueError):
            list_summaries(db, 1, cursor=cursor)
    assert list_summaries(db, 99).batches == []


def test_terminal_traversal_overlap_reset_and_observed_ids(db, monkeypatch):
    now = datetime.now(timezone.utc)
    old = batch(db, status="running", created=now - timedelta(days=100))
    observed = list_summaries(db, 1, status="active").batches[0].id
    old.status, old.completed_at = "cancelled", now - timedelta(seconds=1)
    for _ in range(6):
        batch(db, completed=old.completed_at)
    db.flush()
    first = terminal_summaries(db, 1, limit=2)
    cursor, ids = first.next_cursor, [b.id for b in first.batches]
    while cursor:
        page = terminal_summaries(db, 1, limit=2, cursor=cursor)
        assert page.coverage == "recent_window"
        assert (page.window_start, page.window_end) == (
            first.window_start,
            first.window_end,
        )
        ids.extend(b.id for b in page.batches)
        cursor = page.next_cursor
    assert len(ids) == len(set(ids)) == 7 and old.id in ids
    # A row committed late with an earlier timestamp is found by overlap, not
    # promised by an exhausted cursor. No durable event delivery is claimed.
    late = batch(db, completed=first.window_end - timedelta(seconds=1))
    overlap = terminal_summaries(db, 1, since=first.window_start)
    assert late.id in [b.id for b in overlap.batches]
    old.completed_at = now - timedelta(days=3)
    db.flush()
    missing, foreign = uuid4(), batch(db, owner=2).id
    reconciled = lookup_summaries(db, 1, [observed, missing, foreign])
    assert reconciled.batches[0].status == "cancelled"
    assert reconciled.unavailable_ids == [missing, foreign]
    reset = terminal_summaries(db, 1, since=now - timedelta(days=20))
    assert reset.coverage == "reset"
    assert old.id not in [b.id for b in reset.batches]
    with pytest.raises(ValueError):
        list_summaries(db, 1, cursor=first.next_cursor)
    with pytest.raises(ValueError):
        lookup_summaries(db, 1, [])


@pytest.mark.parametrize("count", [1, 10])
def test_detail_real_sql_chunking_order_and_corruption(db, sql, monkeypatch, count):
    monkeypatch.setenv("BATCH_METADATA_CHUNK_SIZE", "3")
    row = batch(db)
    other = batch(db)
    docs = []
    now = datetime.now(timezone.utc)
    for i in range(count):
        document_id, review1, review2 = uuid4(), uuid4(), uuid4()
        doc = BatchDocument(
            id=uuid4(),
            batch_id=row.id,
            document_id=document_id,
            position=i,
            status=BatchDocumentStatus.COMPLETED,
            review_session_ids=[str(review2), str(review1)],
            result_files=[],
        )
        db.add(doc)
        docs.append(doc)
        db.execute(
            text("INSERT INTO pdf_documents VALUES (:id, NULL, :filename)"),
            {"id": document_id, "filename": f"{i}.pdf"},
        )
        for identity, key in [(review1, "first"), (review2, "second")]:
            db.execute(
                text("INSERT INTO curation_review_sessions VALUES (:id, :key)"),
                {"id": identity, "key": key},
            )
        from src.lib.curation_workspace.curation_prep_constants import (
            CURATION_PREP_AGENT_ID,
        )

        for run, agent, origin in [
            (row.id, "extractor", ""),
            (row.id, "extractor", "origin"),
            (other.id, "extractor", "wrong"),
            (row.id, CURATION_PREP_AGENT_ID, "prep"),
        ]:
            db.execute(
                text(
                    "INSERT INTO extraction_results VALUES (:id, :doc, :run, :agent, :adapter, 2, :trace, :origin, :created)"
                ),
                {
                    "id": uuid4(),
                    "doc": document_id,
                    "run": str(run),
                    "agent": agent,
                    "adapter": "test",
                    "trace": "trace",
                    "origin": origin,
                    "created": now,
                },
            )
    db.flush()
    sql.clear()
    result = BatchService(db).get_documents_handoff_metadata(row, docs)
    assert len(sql) == (2 * count + 2) // 3 + (count + 2) // 3
    for doc in docs:
        entry = result[doc.id]
        assert entry["adapter_keys"] == ["second", "first"]
        assert len(entry["extraction_result_ids"]) == 2
        assert entry["extraction_result_ids"] == sorted(entry["extraction_result_ids"])
        assert entry["origin_session_id"] == "origin"
    print(f"detail_documents={count} metadata_queries={len(sql)} chunk_size=3")
    assert (
        BatchService(db).get_documents_handoff_metadata(row, [docs[0]])[docs[0].id]
        == result[docs[0].id]
    )
    detail = BatchService(db).batch_to_response(row)
    assert detail.flow_name is None
    assert len(detail.documents) == count
    assert detail.documents[0].document_title == "0.pdf"
    assert detail.documents[0].adapter_keys == ["second", "first"]
    docs[0].review_session_ids = [str(uuid4())]
    with pytest.raises(ValueError, match="missing authoritative"):
        BatchService(db).get_documents_handoff_metadata(row, docs)
    docs[0].review_session_ids = ["invalid"]
    with pytest.raises(ValueError, match="invalid authoritative"):
        BatchService(db).get_documents_handoff_metadata(row, docs)
    docs[0].status = BatchDocumentStatus.FAILED
    assert (
        BatchService(db).get_documents_handoff_metadata(row, [docs[0]])[docs[0].id][
            "extraction_result_ids"
        ]
        == []
    )


def test_query_plans_use_owner_keyset_indexes(db):
    db.execute(
        text("""INSERT INTO batches (id, user_id, flow_id, status, total_documents,
      completed_documents, failed_documents, active_group_ids, created_at, completed_at)
      SELECT gen_random_uuid(), 1, gen_random_uuid(),
      CASE WHEN n % 2 = 0 THEN 'completed' ELSE 'running' END::batchstatus,
      10, 0, 0, '[]', now() - n * interval '1 minute', now() - n * interval '1 minute'
      FROM generate_series(1, 10000) n""")
    )
    db.execute(text("ANALYZE batches"))
    for where, column, index in [
        ("", "created_at", "idx_batches_owner_history"),
        (
            "AND status IN ('pending', 'running')",
            "created_at",
            "idx_batches_owner_active_history",
        ),
        (
            "AND status IN ('completed', 'cancelled')",
            "completed_at",
            "idx_batches_owner_terminal",
        ),
    ]:
        from sqlalchemy import func
        from src.lib.batch.summaries import _projection

        timestamp = getattr(Batch, column)
        statement = _projection().where(Batch.user_id == 1, timestamp <= func.now())
        if where:
            statement = statement.where(text(where.removeprefix("AND ")))
        statement = statement.order_by(timestamp.desc(), Batch.id.desc()).limit(5)
        compiled = statement.compile(
            dialect=db.bind.dialect, compile_kwargs={"literal_binds": True}
        )
        plan = "\n".join(db.scalars(text("EXPLAIN " + str(compiled))))
        assert index in plan
        print(plan)


def test_index_migration_matches_models(db):
    import importlib.util
    from pathlib import Path
    from alembic.migration import MigrationContext
    from alembic.operations import Operations

    path = (
        Path(__file__).resolve().parents[3]
        / "alembic/versions/e1f5a6b7c8d9_batch_summary_indexes.py"
    )
    spec = importlib.util.spec_from_file_location("batch_summary_migration", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    names = {
        "idx_batches_owner_history",
        "idx_batches_owner_active_history",
        "idx_batches_owner_terminal",
    }

    def definitions():
        return dict(
            db.execute(
                text(
                    "SELECT indexname, indexdef FROM pg_indexes WHERE schemaname=current_schema() AND tablename='batches'"
                )
            ).all()
        )

    expected = definitions()
    with Operations.context(MigrationContext.configure(db.connection())):
        migration.downgrade()
        assert not names.intersection(definitions())
        migration.upgrade()
    assert definitions() == expected


def test_terminal_cursor_expiration_and_summary_only_paths(db, sql, monkeypatch):
    import base64
    import json

    now = datetime.now(timezone.utc)
    for _ in range(4):
        batch(db, completed=now - timedelta(seconds=5))
    sql.clear()
    first = terminal_summaries(db, 1, limit=2)
    assert len(first.batches) == 2 and first.next_cursor
    assert len(sql) == 2  # page and scalar existence
    assert not any(
        name in q
        for q in sql
        for name in (
            "batch_documents",
            "pdf_documents",
            "extraction_results",
            "curation_review_sessions",
        )
    )
    sql.clear()
    lookup_summaries(db, 1, [b.id for b in first.batches])
    assert len(sql) == 1 and "batch_documents" not in sql[0]
    # Simulate elapsed suspension without a real sleep or transaction held over HTTP.
    token = json.loads(base64.urlsafe_b64decode(first.next_cursor))
    for field in ("start", "end", "last_time"):
        token[field] = (
            datetime.fromisoformat(token[field].replace("Z", "+00:00"))
            - timedelta(days=3)
        ).isoformat()
    expired = base64.urlsafe_b64encode(json.dumps(token).encode()).decode()
    resumed = terminal_summaries(db, 1, limit=2, cursor=expired)
    assert resumed.coverage == "reset" and resumed.window_end > first.window_end
    assert resumed.next_cursor
    with pytest.raises(ValueError):
        terminal_summaries(db, 1, cursor=first.next_cursor, since=now)
    with pytest.raises(ValueError):
        terminal_summaries(db, 1, since=now + timedelta(days=1))
    with pytest.raises(ValueError):
        terminal_summaries(db, 1, since=now.replace(tzinfo=None))
    monkeypatch.setenv("BATCH_PAGE_MAX", "2")
    monkeypatch.setenv("BATCH_PAGE_DEFAULT", "10")
    assert len(list_summaries(db, 1).batches) == 2
    with pytest.raises(ValueError):
        lookup_summaries(db, 1, [uuid4() for _ in range(3)])


def test_stream_snapshots_real_sql_delta_ownership_and_corruption(db, sql, monkeypatch):
    from sqlalchemy.orm import sessionmaker
    from src.lib.batch import streaming

    row = batch(db, status="running")
    doc = BatchDocument(
        id=uuid4(), batch_id=row.id, document_id=uuid4(), position=0,
        status=BatchDocumentStatus.COMPLETED, result_files=[], review_session_ids=[],
    )
    db.add(doc)
    db.flush()
    monkeypatch.setattr(streaming, "SessionLocal", sessionmaker(bind=db.bind))
    reports = []
    monkeypatch.setattr(streaming, "report_runtime_exception", lambda *a, **kw: reports.append(a))
    previous = streaming.Snapshot()
    for expected_queries in (3, 2):
        sql.clear()
        previous = streaming.read_snapshot(row.id, row.user_id, previous)
        # Batch + selectin documents; only initial adds extraction projection.
        assert len(sql) == expected_queries
    doc.status = BatchDocumentStatus.FAILED
    db.flush()
    sql.clear()
    previous = streaming.read_snapshot(row.id, row.user_id, previous)
    assert len(sql) == 2
    assert len(previous.events) == 1
    assert '"status": "failed"' in previous.events[0]
    sql.clear()
    assert not streaming.verify_batch_owner(row.id, row.user_id + 1)
    assert len(sql) == 1  # No document or handoff read for unauthorized owner.
    missing = streaming.read_snapshot(row.id, row.user_id + 1, streaming.Snapshot())
    assert missing.terminal and "Batch not found" in missing.events[0]
    doc.status = BatchDocumentStatus.COMPLETED
    for references in (["malformed"], [str(uuid4())]):
        doc.review_session_ids = references
        db.flush()
        with pytest.raises(ValueError, match="authoritative"):
            streaming.read_snapshot(row.id, row.user_id, previous)
    assert len(reports) == 2
