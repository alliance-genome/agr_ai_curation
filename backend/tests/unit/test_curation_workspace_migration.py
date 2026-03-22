"""Unit tests for the curation workspace table migration."""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types

import sqlalchemy as sa


EXPECTED_CURATION_SESSION_STATUSES = (
    "new",
    "in_progress",
    "paused",
    "ready_for_submission",
    "submitted",
    "rejected",
)
EXPECTED_CURATION_CANDIDATE_STATUSES = ("pending", "accepted", "rejected")
EXPECTED_CURATION_CANDIDATE_SOURCES = ("extracted", "manual", "imported")
EXPECTED_CURATION_VALIDATION_SCOPES = ("candidate", "session")
EXPECTED_CURATION_SESSION_SORT_FIELDS = (
    "prepared_at",
    "last_worked_at",
    "status",
    "document_title",
    "adapter",
    "candidate_count",
    "validation",
    "evidence",
    "curator",
)
EXPECTED_CURATION_SORT_DIRECTIONS = ("asc", "desc")


REPO_ROOT = Path(__file__).resolve().parents[3]
MIGRATION_PATH = (
    REPO_ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "e1f2a3b4c5d6_add_curation_workspace_tables.py"
)


class RecordingOp:
    """Capture Alembic operations for structural assertions."""

    def __init__(self) -> None:
        self.created_tables: list[dict[str, object]] = []
        self.created_indexes: list[dict[str, object]] = []
        self.executed: list[str] = []
        self.dropped_indexes: list[dict[str, object]] = []
        self.dropped_tables: list[str] = []

    def create_table(self, name, *elements, **kwargs):
        self.created_tables.append(
            {"name": name, "elements": elements, "kwargs": kwargs}
        )

    def create_index(self, name, table_name, columns, unique=False, **kwargs):
        self.created_indexes.append(
            {
                "name": name,
                "table_name": table_name,
                "columns": columns,
                "unique": unique,
                "kwargs": kwargs,
            }
        )

    def execute(self, statement):
        self.executed.append(str(statement))

    def drop_index(self, name, table_name=None, **kwargs):
        self.dropped_indexes.append(
            {"name": name, "table_name": table_name, "kwargs": kwargs}
        )

    def drop_table(self, name):
        self.dropped_tables.append(name)


def _load_migration_module(monkeypatch, *, module_name: str):
    dummy_alembic = types.ModuleType("alembic")
    dummy_alembic.op = object()
    monkeypatch.setitem(sys.modules, "alembic", dummy_alembic)

    spec = spec_from_file_location(module_name, MIGRATION_PATH)
    assert spec is not None
    assert spec.loader is not None

    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _table_by_name(op_recorder: RecordingOp, table_name: str) -> dict[str, object]:
    for table in op_recorder.created_tables:
        if table["name"] == table_name:
            return table
    raise AssertionError(f"Missing create_table call for {table_name}")


def _columns(table_call: dict[str, object]) -> dict[str, sa.Column]:
    return {
        element.name: element
        for element in table_call["elements"]
        if isinstance(element, sa.Column)
    }


def _constraint_names(table_call: dict[str, object]) -> set[str]:
    names: set[str] = set()
    for element in table_call["elements"]:
        if isinstance(element, (sa.CheckConstraint, sa.UniqueConstraint)) and element.name:
            names.add(element.name)
    return names


def _single_foreign_key(column: sa.Column) -> sa.ForeignKey:
    foreign_keys = list(column.foreign_keys)
    assert len(foreign_keys) == 1
    return foreign_keys[0]


def test_upgrade_creates_curation_workspace_tables_and_indexes(monkeypatch):
    module = _load_migration_module(
        monkeypatch,
        module_name="curation_workspace_migration_upgrade_test",
    )
    op_recorder = RecordingOp()
    module.op = op_recorder

    module.upgrade()

    assert [table["name"] for table in op_recorder.created_tables] == [
        "curation_review_sessions",
        "extraction_results",
        "curation_candidates",
        "evidence_anchors",
        "annotation_drafts",
        "validation_snapshots",
        "curation_submissions",
        "curation_action_log",
        "curation_saved_views",
    ]

    session_columns = _columns(_table_by_name(op_recorder, "curation_review_sessions"))
    assert set(session_columns) == {
        "id",
        "status",
        "adapter_key",
        "profile_key",
        "document_id",
        "flow_run_id",
        "current_candidate_id",
        "assigned_curator_id",
        "created_by_id",
        "session_version",
        "notes",
        "tags",
        "total_candidates",
        "reviewed_candidates",
        "pending_candidates",
        "accepted_candidates",
        "rejected_candidates",
        "manual_candidates",
        "rejection_reason",
        "warnings",
        "prepared_at",
        "last_worked_at",
        "submitted_at",
        "paused_at",
        "created_at",
        "updated_at",
    }
    assert str(session_columns["status"].server_default.arg) == "new"
    assert str(session_columns["tags"].server_default.arg) == "'[]'::jsonb"
    assert "ck_curation_review_sessions_status" in _constraint_names(
        _table_by_name(op_recorder, "curation_review_sessions")
    )
    assert list(session_columns["current_candidate_id"].foreign_keys) == []

    candidate_columns = _columns(_table_by_name(op_recorder, "curation_candidates"))
    assert set(candidate_columns) == {
        "id",
        "session_id",
        "source",
        "status",
        "order",
        "adapter_key",
        "profile_key",
        "display_label",
        "secondary_label",
        "confidence",
        "conversation_summary",
        "unresolved_ambiguities",
        "extraction_result_id",
        "metadata",
        "created_at",
        "updated_at",
        "last_reviewed_at",
    }
    assert str(candidate_columns["status"].server_default.arg) == "pending"
    assert "ck_curation_candidates_source" in _constraint_names(
        _table_by_name(op_recorder, "curation_candidates")
    )
    assert "ck_curation_candidates_status" in _constraint_names(
        _table_by_name(op_recorder, "curation_candidates")
    )

    drafts_table = _table_by_name(op_recorder, "annotation_drafts")
    draft_constraint_names = _constraint_names(drafts_table)
    assert "uq_annotation_drafts_candidate_id" in draft_constraint_names
    assert "ck_annotation_drafts_version" in draft_constraint_names

    validation_table = _table_by_name(op_recorder, "validation_snapshots")
    validation_columns = _columns(validation_table)
    assert "ck_validation_snapshots_scope" in _constraint_names(validation_table)
    assert "ck_validation_snapshots_candidate_scope" in _constraint_names(validation_table)
    assert validation_columns["summary"].server_default is None

    saved_view_table = _table_by_name(op_recorder, "curation_saved_views")
    assert "ck_curation_saved_views_sort_by" in _constraint_names(saved_view_table)
    assert "ck_curation_saved_views_sort_direction" in _constraint_names(saved_view_table)

    index_map = {index["name"]: index for index in op_recorder.created_indexes}
    assert set(index_map) == {
        "ix_curation_sessions_status",
        "ix_curation_sessions_adapter_key",
        "ix_curation_sessions_flow_run_id",
        "ix_curation_sessions_assigned_curator",
        "ix_curation_sessions_document",
        "ix_extraction_results_document",
        "ix_extraction_results_flow_run",
        "ix_curation_candidates_session",
        "ix_curation_candidates_status",
        "ix_evidence_anchors_candidate",
        "ix_validation_snapshots_session",
        "ix_validation_snapshots_candidate",
        "ix_saved_views_created_by",
    }
    assert index_map["ix_curation_sessions_flow_run_id"]["kwargs"]["postgresql_where"].text == (
        "flow_run_id IS NOT NULL"
    )
    assert index_map["ix_extraction_results_flow_run"]["kwargs"]["postgresql_where"].text == (
        "flow_run_id IS NOT NULL"
    )
    assert index_map["ix_validation_snapshots_candidate"]["kwargs"]["postgresql_where"].text == (
        "candidate_id IS NOT NULL"
    )

    assert op_recorder.executed == [
        "CREATE INDEX ix_curation_sessions_prepared_at ON curation_review_sessions (prepared_at DESC)",
        "CREATE INDEX ix_curation_sessions_last_worked ON curation_review_sessions (last_worked_at DESC NULLS LAST)",
        "CREATE INDEX ix_submissions_session ON curation_submissions (session_id, requested_at DESC)",
        "CREATE INDEX ix_action_log_session ON curation_action_log (session_id, occurred_at DESC)",
        "CREATE INDEX ix_action_log_candidate ON curation_action_log (candidate_id, occurred_at DESC) WHERE candidate_id IS NOT NULL",
    ]

    expected_foreign_keys = {
        ("curation_review_sessions", "document_id"): "pdf_documents.id",
        ("extraction_results", "document_id"): "pdf_documents.id",
        ("curation_candidates", "session_id"): "curation_review_sessions.id",
        ("curation_candidates", "extraction_result_id"): "extraction_results.id",
        ("evidence_anchors", "candidate_id"): "curation_candidates.id",
        ("annotation_drafts", "candidate_id"): "curation_candidates.id",
        ("validation_snapshots", "session_id"): "curation_review_sessions.id",
        ("validation_snapshots", "candidate_id"): "curation_candidates.id",
        ("curation_submissions", "session_id"): "curation_review_sessions.id",
        ("curation_action_log", "session_id"): "curation_review_sessions.id",
        ("curation_action_log", "candidate_id"): "curation_candidates.id",
        ("curation_action_log", "draft_id"): "annotation_drafts.id",
    }
    for (table_name, column_name), target in expected_foreign_keys.items():
        column = _columns(_table_by_name(op_recorder, table_name))[column_name]
        foreign_key = _single_foreign_key(column)
        assert foreign_key.target_fullname == target
        assert foreign_key.ondelete == module.FK_ON_DELETE_NO_ACTION


def test_upgrade_snapshots_enum_values_locally(monkeypatch):
    module = _load_migration_module(
        monkeypatch,
        module_name="curation_workspace_migration_enum_test",
    )

    assert module.revision == "e1f2a3b4c5d6"
    assert module.down_revision == "d4e5f6a7b8c9"
    assert module.CURATION_SESSION_STATUSES == EXPECTED_CURATION_SESSION_STATUSES
    assert module.CURATION_CANDIDATE_STATUSES == EXPECTED_CURATION_CANDIDATE_STATUSES
    assert module.CURATION_CANDIDATE_SOURCES == EXPECTED_CURATION_CANDIDATE_SOURCES
    assert module.CURATION_VALIDATION_SCOPES == EXPECTED_CURATION_VALIDATION_SCOPES
    assert module.CURATION_SESSION_SORT_FIELDS == EXPECTED_CURATION_SESSION_SORT_FIELDS
    assert module.CURATION_SORT_DIRECTIONS == EXPECTED_CURATION_SORT_DIRECTIONS
    assert module._enum_values(module.CURATION_CANDIDATE_SOURCES) == [
        "extracted",
        "manual",
        "imported",
    ]


def test_downgrade_drops_indexes_and_tables_in_reverse_order(monkeypatch):
    module = _load_migration_module(
        monkeypatch,
        module_name="curation_workspace_migration_downgrade_test",
    )
    op_recorder = RecordingOp()
    module.op = op_recorder

    module.downgrade()

    assert [item["name"] for item in op_recorder.dropped_indexes] == [
        "ix_saved_views_created_by",
        "ix_action_log_candidate",
        "ix_action_log_session",
        "ix_submissions_session",
        "ix_validation_snapshots_candidate",
        "ix_validation_snapshots_session",
        "ix_evidence_anchors_candidate",
        "ix_curation_candidates_status",
        "ix_curation_candidates_session",
        "ix_extraction_results_flow_run",
        "ix_extraction_results_document",
        "ix_curation_sessions_document",
        "ix_curation_sessions_last_worked",
        "ix_curation_sessions_prepared_at",
        "ix_curation_sessions_assigned_curator",
        "ix_curation_sessions_flow_run_id",
        "ix_curation_sessions_adapter_key",
        "ix_curation_sessions_status",
    ]
    assert op_recorder.dropped_tables == [
        "curation_saved_views",
        "curation_action_log",
        "curation_submissions",
        "validation_snapshots",
        "annotation_drafts",
        "evidence_anchors",
        "curation_candidates",
        "extraction_results",
        "curation_review_sessions",
    ]
