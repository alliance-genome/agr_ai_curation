"""Failure contracts exercised through both public intake services."""

from abc import ABC, abstractmethod
import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest

from src.lib.document_files import paper_lock
from src.lib.observability import runtime
from src.lib.observability.sentry import hash_sentry_identifier


PRIVATE_DETAIL = "private-cleanup-parameter"
PRIVATE_REPORTER_DETAIL = "private-reporter-error"


class IntakeCompensationContract(ABC):
    component: str

    @abstractmethod
    async def run_intake(self, tmp_path, **dependencies):
        """Run one upload or identifier import through its public service entry point."""

    @pytest.fixture
    def session(self):
        session = MagicMock()
        query = session.execute.return_value.scalars.return_value.first
        query.return_value = None

        def store_row(row):
            query.return_value = row

        session.add.side_effect = store_row
        return session

    @pytest.fixture(params=[False, True], ids=["report-delivered", "reporter-failed"])
    def reporter(self, request, monkeypatch):
        report = Mock(side_effect=RuntimeError(PRIVATE_REPORTER_DETAIL) if request.param else None)
        monkeypatch.setattr(runtime, "report_runtime_exception", report)
        return report

    async def fail_intake(self, tmp_path, session, *, committed=True, **dependencies):
        original_failure = RuntimeError("intake failed")
        if not committed:
            session.commit.side_effect = original_failure
        create_document = AsyncMock(return_value={"success": True})
        with pytest.raises(RuntimeError) as caught:
            await self.run_intake(
                tmp_path,
                session_factory=lambda: session,
                create_document_fn=create_document,
                create_job_fn=Mock(side_effect=original_failure),
                **dependencies,
            )
        assert caught.value is original_failure
        create_document.assert_awaited_once()
        return str(create_document.await_args.args[1].id)

    def assert_report(self, reporter, caplog, document_id, operation, **context):
        reporter.assert_called_once()
        (error,) = reporter.call_args.args
        assert error.__cause__ is None and error.__context__ is None
        kwargs = reporter.call_args.kwargs
        assert kwargs["operation"] == operation
        assert kwargs["component"] == self.component
        assert kwargs["include_request"] is False
        assert kwargs["context"] == {"document_id_hash": hash_sentry_identifier(document_id), **context}
        warnings = [r for r in caplog.records if r.name == "src.lib.document_cleanup"]
        assert len(warnings) == (2 if reporter.side_effect else 1)
        assert all(record.sentry_skip_event for record in warnings)
        for sensitive in (document_id, PRIVATE_DETAIL, PRIVATE_REPORTER_DETAIL):
            assert sensitive not in repr(reporter.call_args)
            assert sensitive not in caplog.text

    @pytest.mark.asyncio
    @pytest.mark.parametrize("committed", [False, True], ids=["pre-commit", "committed"])
    @pytest.mark.parametrize("raises", [False, True], ids=["unsuccessful", "exception"])
    async def test_index_cleanup_failure(self, tmp_path, session, reporter, caplog, committed, raises):
        delete = AsyncMock(
            return_value={
                "success": False,
                "message": "Document deletion reported as unsuccessful",
                "detail": PRIVATE_DETAIL,
            },
            side_effect=ValueError(PRIVATE_DETAIL) if raises else None,
        )
        doc_id = await self.fail_intake(tmp_path, session, committed=committed, delete_document_fn=delete)
        delete.assert_awaited_once()
        session.delete.assert_not_called()
        assert not list(tmp_path.rglob("*.pdf"))
        reason = {"exception_type": "ValueError"} if raises else {"failure_kind": "delete_unsuccessful"}
        self.assert_report(
            reporter, caplog, doc_id, "intake_compensation_index_cleanup_failed",
            sql_record_committed=committed, **reason,
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize("result", [
        {"success": False, "error": {"code": "DELETE_FAILED"}},
        {"success": False, "chunks_failed": 1},
    ])
    async def test_index_failure_already_reported(self, tmp_path, session, monkeypatch, result):
        report = Mock()
        monkeypatch.setattr(runtime, "report_runtime_exception", report)
        delete = AsyncMock(return_value=result)
        await self.fail_intake(tmp_path, session, delete_document_fn=delete)
        delete.assert_awaited_once()
        report.assert_not_called()
        session.delete.assert_not_called()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("step", "dependency"), [
        ("paper_lock_acquire", "paper_operation_lock_fn"),
        ("active_job_check", "get_active_job_fn"),
    ])
    async def test_compensation_aborted(self, tmp_path, session, reporter, caplog, step, dependency):
        delete = AsyncMock()
        failure = LookupError(PRIVATE_DETAIL)
        dependency_mock = Mock(side_effect=failure)
        if step == "paper_lock_acquire":
            lock = MagicMock()
            lock.__aenter__.side_effect = failure
            dependency_mock = Mock(return_value=lock)
        doc_id = await self.fail_intake(
            tmp_path, session, delete_document_fn=delete, **{dependency: dependency_mock},
        )
        delete.assert_not_awaited()
        session.delete.assert_not_called()
        assert list(tmp_path.rglob("*.pdf"))
        self.assert_report(
            reporter, caplog, doc_id, "intake_compensation_aborted", step=step, exception_type="LookupError",
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("cancel_during", "lookup_error"), [
        ("lookup", LookupError),
        ("unlock", LookupError),
        ("lookup", paper_lock.PaperOperationInProgress),
        ("lookup", None),
    ])
    async def test_cancel_during_active_job_check_or_unlock(
        self, tmp_path, session, reporter, caplog, monkeypatch, cancel_during, lookup_error,
    ):
        paused = threading.Event()
        resume = threading.Event()
        events = []
        release = paper_lock._release

        def pause_worker():
            paused.set()
            assert resume.wait(timeout=5)

        def check_active_job(**kwargs):
            if cancel_during == "lookup":
                pause_worker()
            events.append(("lookup", kwargs["connection"]))
            if lookup_error:
                raise lookup_error(PRIVATE_DETAIL)
            return None

        def release_lock(connection, key):
            if cancel_during == "unlock":
                pause_worker()
            release(connection, key)
            events.append(("released", connection))

        monkeypatch.setattr(paper_lock, "_release", release_lock)
        create_document = AsyncMock(return_value={"success": True})
        delete = AsyncMock()
        task = asyncio.create_task(self.run_intake(
            tmp_path,
            session_factory=lambda: session,
            create_document_fn=create_document,
            create_job_fn=Mock(side_effect=RuntimeError("intake failed")),
            get_active_job_fn=check_active_job,
            delete_document_fn=delete,
        ))
        try:
            assert await asyncio.to_thread(paused.wait, 5)
            task.cancel("intake cancelled")
            await asyncio.sleep(0)
            assert not task.done()
        finally:
            resume.set()
        with pytest.raises(asyncio.CancelledError, match="intake cancelled"):
            await asyncio.wait_for(task, timeout=5)

        assert [step for step, _ in events] == ["lookup", "released"]
        assert events[0][1] is events[1][1]
        delete.assert_not_awaited()
        session.delete.assert_not_called()
        assert list(tmp_path.rglob("*.pdf"))
        if lookup_error is LookupError:
            document_id = str(create_document.await_args.args[1].id)
            self.assert_report(
                reporter, caplog, document_id, "intake_compensation_aborted",
                step="active_job_check", exception_type="LookupError",
            )
        else:
            reporter.assert_not_called()
            assert not [r for r in caplog.records if r.name == "src.lib.document_cleanup"]

    @pytest.mark.asyncio
    async def test_abort_after_lock_acquisition(self, tmp_path, session, reporter, caplog):
        def active_job_check(**_kwargs):
            # Closing the committed file snapshot session fails inside the lock.
            session.close.side_effect = OSError(PRIVATE_DETAIL)
            return None

        delete = AsyncMock()
        doc_id = await self.fail_intake(
            tmp_path, session, delete_document_fn=delete, get_active_job_fn=active_job_check,
        )
        delete.assert_not_awaited()
        session.delete.assert_not_called()
        self.assert_report(
            reporter, caplog, doc_id, "intake_compensation_aborted",
            step="committed_cleanup", exception_type="OSError",
        )

    @pytest.mark.asyncio
    async def test_rejected_sql_cleanup(self, tmp_path, session, reporter, caplog):
        session.delete.side_effect = ValueError(PRIVATE_DETAIL)
        delete = AsyncMock(return_value={"success": True})
        doc_id = await self.fail_intake(tmp_path, session, delete_document_fn=delete)
        session.delete.assert_called_once()
        session.rollback.assert_called_once()
        self.assert_report(reporter, caplog, doc_id, "paper_delete_sql_cleanup_failed", exception_type="ValueError")
