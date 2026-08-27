"""Structural checks for the chat route preference migration."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types
from typing import Any, cast

import sqlalchemy as sa


MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "d1e2f3a4b5c6_add_chat_route_preferences.py"
)


class RecordingOp:
    def __init__(self) -> None:
        self.created = None
        self.dropped = None

    def create_table(self, name, *elements):
        self.created = (name, elements)

    def drop_table(self, name):
        self.dropped = name


def _load_migration(monkeypatch):
    dummy_alembic = types.ModuleType("alembic")
    setattr(dummy_alembic, "op", object())
    monkeypatch.setitem(sys.modules, "alembic", dummy_alembic)
    spec = spec_from_file_location("chat_route_preference_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(Any, module)


def test_upgrade_creates_constrained_user_owned_target_table(monkeypatch):
    module = _load_migration(monkeypatch)
    recorder = RecordingOp()
    module.op = recorder

    module.upgrade()

    assert module.down_revision == "c925d1e2f3a4"
    assert recorder.created is not None
    name, elements = recorder.created
    assert name == "chat_route_preferences"
    columns = {item.name: item for item in elements if isinstance(item, sa.Column)}
    assert set(columns) == {
        "user_id",
        "mode",
        "agent_id",
        "flow_id",
        "target_public_id",
        "target_display_name",
        "created_at",
        "updated_at",
    }
    constraints = {
        item.name: item
        for item in elements
        if isinstance(item, (sa.CheckConstraint, sa.PrimaryKeyConstraint))
    }
    assert "ck_chat_route_preference_mode_target" in constraints
    assert "agent_id IS NOT NULL AND flow_id IS NULL" in str(
        constraints["ck_chat_route_preference_mode_target"].sqltext
    )
    foreign_targets = {
        tuple(constraint.column_keys): tuple(
            element.target_fullname for element in constraint.elements
        )
        for constraint in elements
        if isinstance(constraint, sa.ForeignKeyConstraint)
    }
    assert foreign_targets[("user_id",)] == ("users.user_id",)
    assert foreign_targets[("agent_id",)] == ("agents.id",)
    assert foreign_targets[("flow_id",)] == ("curation_flows.id",)


def test_downgrade_drops_preference_table(monkeypatch):
    module = _load_migration(monkeypatch)
    recorder = RecordingOp()
    module.op = recorder

    module.downgrade()

    assert recorder.dropped == "chat_route_preferences"
