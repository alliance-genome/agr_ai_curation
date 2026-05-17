"""Unit tests for the curator field-patch constraint migration."""

from __future__ import annotations

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types


REPO_ROOT = Path(__file__).resolve().parents[2]
MIGRATION_PATH = (
    REPO_ROOT
    / "alembic"
    / "versions"
    / "k0l1m2n3o4p5_allow_curator_field_patch_events.py"
)
REPAIR_EVENT_REMOVAL_MIGRATION_PATH = (
    REPO_ROOT
    / "alembic"
    / "versions"
    / "n1o2p3q4r5s6_remove_repair_history_event_kinds.py"
)


class RecordingOp:
    def __init__(self) -> None:
        self.created_constraints: list[tuple[str, str, str]] = []
        self.dropped_constraints: list[tuple[str, str, str | None]] = []

    def create_check_constraint(self, name, table_name, condition):
        self.created_constraints.append((name, table_name, condition))

    def drop_constraint(self, name, table_name, type_=None):
        self.dropped_constraints.append((name, table_name, type_))


def _load_migration_module(monkeypatch, *, module_name: str, path: Path):
    dummy_alembic = types.ModuleType("alembic")
    dummy_alembic.op = object()
    monkeypatch.setitem(sys.modules, "alembic", dummy_alembic)

    spec = spec_from_file_location(module_name, path)
    assert spec is not None
    assert spec.loader is not None

    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_refreshes_history_and_action_log_check_constraints(monkeypatch):
    module = _load_migration_module(
        monkeypatch,
        module_name="curator_field_patch_constraints_upgrade_test",
        path=MIGRATION_PATH,
    )
    op_recorder = RecordingOp()
    module.op = op_recorder

    module.upgrade()

    assert op_recorder.dropped_constraints == [
        (
            "ck_domain_envelope_history_event_type",
            "domain_envelope_history",
            "check",
        ),
        ("ck_curation_action_log_action_type", "curation_action_log", "check"),
    ]
    assert op_recorder.created_constraints == [
        (
            "ck_domain_envelope_history_event_type",
            "domain_envelope_history",
            module._check_sql("event_type", module.CURRENT_HISTORY_EVENT_KINDS),
        ),
        (
            "ck_curation_action_log_action_type",
            "curation_action_log",
            module._check_sql("action_type", module.CURRENT_ACTION_TYPES),
        ),
    ]
    assert "curator_field_patch_accepted" in module.CURRENT_HISTORY_EVENT_KINDS
    assert "curator_field_patch_rejected" in module.CURRENT_HISTORY_EVENT_KINDS
    assert "repair_requested" in module.CURRENT_HISTORY_EVENT_KINDS
    assert "repair_patch_accepted" in module.CURRENT_HISTORY_EVENT_KINDS
    assert "repair_patch_rejected" in module.CURRENT_HISTORY_EVENT_KINDS
    assert "repair_final_classified" in module.CURRENT_HISTORY_EVENT_KINDS
    assert "envelope_field_patched" in module.CURRENT_ACTION_TYPES


def test_downgrade_restores_previous_check_constraints(monkeypatch):
    module = _load_migration_module(
        monkeypatch,
        module_name="curator_field_patch_constraints_downgrade_test",
        path=MIGRATION_PATH,
    )
    op_recorder = RecordingOp()
    module.op = op_recorder

    module.downgrade()

    assert op_recorder.dropped_constraints == [
        ("ck_curation_action_log_action_type", "curation_action_log", "check"),
        (
            "ck_domain_envelope_history_event_type",
            "domain_envelope_history",
            "check",
        ),
    ]
    assert op_recorder.created_constraints == [
        (
            "ck_curation_action_log_action_type",
            "curation_action_log",
            module._check_sql("action_type", module.PREVIOUS_ACTION_TYPES),
        ),
        (
            "ck_domain_envelope_history_event_type",
            "domain_envelope_history",
            module._check_sql("event_type", module.PREVIOUS_HISTORY_EVENT_KINDS),
        ),
    ]
    assert "curator_field_patch_accepted" not in module.PREVIOUS_HISTORY_EVENT_KINDS
    assert "curator_field_patch_rejected" not in module.PREVIOUS_HISTORY_EVENT_KINDS
    assert "envelope_field_patched" not in module.PREVIOUS_ACTION_TYPES


def test_repair_event_removal_upgrade_refreshes_history_constraint(monkeypatch):
    module = _load_migration_module(
        monkeypatch,
        module_name="repair_event_removal_upgrade_test",
        path=REPAIR_EVENT_REMOVAL_MIGRATION_PATH,
    )
    op_recorder = RecordingOp()
    module.op = op_recorder

    module.upgrade()

    assert module.down_revision == "m1n2o3p4q5r6"
    assert op_recorder.dropped_constraints == [
        (
            "ck_domain_envelope_history_event_type",
            "domain_envelope_history",
            "check",
        ),
    ]
    assert op_recorder.created_constraints == [
        (
            "ck_domain_envelope_history_event_type",
            "domain_envelope_history",
            module._check_sql(module.CURRENT_HISTORY_EVENT_KINDS),
        ),
    ]
    assert "curator_field_patch_accepted" in module.CURRENT_HISTORY_EVENT_KINDS
    assert "curator_field_patch_rejected" in module.CURRENT_HISTORY_EVENT_KINDS
    assert "validation_rerun_requested" in module.CURRENT_HISTORY_EVENT_KINDS
    assert "repair_requested" not in module.CURRENT_HISTORY_EVENT_KINDS
    assert "repair_patch_accepted" not in module.CURRENT_HISTORY_EVENT_KINDS
    assert "repair_patch_rejected" not in module.CURRENT_HISTORY_EVENT_KINDS
    assert "repair_final_classified" not in module.CURRENT_HISTORY_EVENT_KINDS


def test_repair_event_removal_downgrade_restores_previous_constraint(monkeypatch):
    module = _load_migration_module(
        monkeypatch,
        module_name="repair_event_removal_downgrade_test",
        path=REPAIR_EVENT_REMOVAL_MIGRATION_PATH,
    )
    op_recorder = RecordingOp()
    module.op = op_recorder

    module.downgrade()

    assert op_recorder.dropped_constraints == [
        (
            "ck_domain_envelope_history_event_type",
            "domain_envelope_history",
            "check",
        ),
    ]
    assert op_recorder.created_constraints == [
        (
            "ck_domain_envelope_history_event_type",
            "domain_envelope_history",
            module._check_sql(module.PREVIOUS_HISTORY_EVENT_KINDS),
        ),
    ]
    assert "repair_requested" in module.PREVIOUS_HISTORY_EVENT_KINDS
    assert "repair_patch_accepted" in module.PREVIOUS_HISTORY_EVENT_KINDS
    assert "repair_patch_rejected" in module.PREVIOUS_HISTORY_EVENT_KINDS
    assert "repair_final_classified" in module.PREVIOUS_HISTORY_EVENT_KINDS
