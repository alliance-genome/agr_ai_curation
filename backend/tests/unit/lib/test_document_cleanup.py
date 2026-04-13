from __future__ import annotations

from uuid import uuid4

from src.lib.document_cleanup import cleanup_document_curation_dependencies


class _ScalarsResult:
    def __init__(self, values):
        self._values = list(values)

    def all(self):
        return list(self._values)


class _ExecuteResult:
    def __init__(self, *, scalar_values=None, rowcount=0):
        self.rowcount = rowcount
        self._scalar_values = [] if scalar_values is None else list(scalar_values)

    def scalars(self):
        return _ScalarsResult(self._scalar_values)


class _FakeSession:
    def __init__(self, results):
        self._results = list(results)
        self.executed = []

    def execute(self, statement):
        self.executed.append(str(statement))
        assert self._results, f"Unexpected extra execute call: {statement}"
        return self._results.pop(0)


def test_cleanup_document_curation_dependencies_removes_workspace_chain_before_document_delete():
    document_id = uuid4()
    session_id = uuid4()
    candidate_id = uuid4()
    extraction_result_id = uuid4()
    session = _FakeSession(
        [
            _ExecuteResult(scalar_values=[session_id]),
            _ExecuteResult(scalar_values=[candidate_id]),
            _ExecuteResult(scalar_values=[extraction_result_id]),
            _ExecuteResult(rowcount=1),
            _ExecuteResult(rowcount=3),
            _ExecuteResult(rowcount=4),
            _ExecuteResult(rowcount=5),
            _ExecuteResult(rowcount=6),
            _ExecuteResult(rowcount=7),
            _ExecuteResult(rowcount=2),
            _ExecuteResult(rowcount=8),
            _ExecuteResult(rowcount=1),
            _ExecuteResult(rowcount=9),
        ]
    )

    summary = cleanup_document_curation_dependencies(session, document_id)

    assert summary == {
        "current_candidate_refs_cleared": 1,
        "candidate_refs_cleared": 2,
        "action_logs_deleted": 3,
        "validation_snapshots_deleted": 4,
        "submissions_deleted": 5,
        "evidence_anchors_deleted": 6,
        "drafts_deleted": 7,
        "candidates_deleted": 8,
        "sessions_deleted": 1,
        "extraction_results_deleted": 9,
    }
    assert session._results == []
    expected_sql_fragments = [
        "FROM curation_review_sessions",
        "FROM curation_candidates",
        "FROM extraction_results",
        "UPDATE curation_review_sessions",
        "DELETE FROM curation_action_log",
        "DELETE FROM validation_snapshots",
        "DELETE FROM curation_submissions",
        "DELETE FROM evidence_anchors",
        "DELETE FROM annotation_drafts",
        "UPDATE curation_candidates",
        "DELETE FROM curation_candidates",
        "DELETE FROM curation_review_sessions",
        "DELETE FROM extraction_results",
    ]
    assert len(session.executed) == len(expected_sql_fragments)
    for statement, fragment in zip(session.executed, expected_sql_fragments):
        assert fragment in statement


def test_cleanup_document_curation_dependencies_noops_when_document_has_no_workspace_rows():
    session = _FakeSession(
        [
            _ExecuteResult(scalar_values=[]),
            _ExecuteResult(scalar_values=[]),
        ]
    )

    summary = cleanup_document_curation_dependencies(session, uuid4())

    assert summary == {
        "current_candidate_refs_cleared": 0,
        "candidate_refs_cleared": 0,
        "action_logs_deleted": 0,
        "validation_snapshots_deleted": 0,
        "submissions_deleted": 0,
        "evidence_anchors_deleted": 0,
        "drafts_deleted": 0,
        "candidates_deleted": 0,
        "sessions_deleted": 0,
        "extraction_results_deleted": 0,
    }
    assert session._results == []
    assert len(session.executed) == 2
    assert "FROM curation_review_sessions" in session.executed[0]
    assert "FROM extraction_results" in session.executed[1]


def test_cleanup_document_curation_dependencies_handles_extraction_results_without_sessions():
    extraction_result_id = uuid4()
    session = _FakeSession(
        [
            _ExecuteResult(scalar_values=[]),
            _ExecuteResult(scalar_values=[extraction_result_id]),
            _ExecuteResult(rowcount=2),
            _ExecuteResult(rowcount=3),
        ]
    )

    summary = cleanup_document_curation_dependencies(session, uuid4())

    assert summary == {
        "current_candidate_refs_cleared": 0,
        "candidate_refs_cleared": 2,
        "action_logs_deleted": 0,
        "validation_snapshots_deleted": 0,
        "submissions_deleted": 0,
        "evidence_anchors_deleted": 0,
        "drafts_deleted": 0,
        "candidates_deleted": 0,
        "sessions_deleted": 0,
        "extraction_results_deleted": 3,
    }
    assert session._results == []
    expected_sql_fragments = [
        "FROM curation_review_sessions",
        "FROM extraction_results",
        "UPDATE curation_candidates",
        "DELETE FROM extraction_results",
    ]
    assert len(session.executed) == len(expected_sql_fragments)
    for statement, fragment in zip(session.executed, expected_sql_fragments):
        assert fragment in statement
