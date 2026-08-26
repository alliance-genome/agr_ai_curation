"""Structural checks for canonical agent availability persistence."""

from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
import sys
import types
from typing import Any, cast

from src.models.sql.agent import Agent
from src.models.sql.custom_agent import CustomAgentVersion


MIGRATION_PATH = (
    Path(__file__).resolve().parents[2]
    / "alembic"
    / "versions"
    / "b824c1d2e3f4_add_agent_allowed_group_ids.py"
)


class RecordingOp:
    def __init__(self) -> None:
        self.added: list[tuple[str, str, bool]] = []
        self.statements: list[str] = []
        self.altered: list[tuple[str, str, bool, str]] = []
        self.dropped: list[tuple[str, str]] = []

    def add_column(self, table_name: str, column: Any) -> None:
        self.added.append((table_name, column.name, column.nullable))

    def execute(self, statement: Any) -> None:
        self.statements.append(str(statement))

    def alter_column(self, table_name: str, column_name: str, **kwargs: Any) -> None:
        self.altered.append(
            (
                table_name,
                column_name,
                kwargs["nullable"],
                str(kwargs["server_default"]),
            )
        )

    def drop_column(self, table_name: str, column_name: str) -> None:
        self.dropped.append((table_name, column_name))


def _load_migration(monkeypatch) -> Any:
    dummy_alembic = types.ModuleType("alembic")
    setattr(dummy_alembic, "op", object())
    monkeypatch.setitem(sys.modules, "alembic", dummy_alembic)
    spec = spec_from_file_location("agent_allowed_group_ids_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return cast(Any, module)


def test_upgrade_explicitly_backfills_before_setting_non_null_defaults(monkeypatch):
    module = _load_migration(monkeypatch)
    recorder = RecordingOp()
    module.op = recorder

    module.upgrade()

    assert module.down_revision == "a823b1c2d3e4"
    assert recorder.added == [
        ("agents", "allowed_group_ids", True),
        ("agents", "inherited_allowed_group_ids", True),
        ("custom_agent_versions", "allowed_group_ids", True),
    ]
    assert recorder.statements == [
        "UPDATE agents SET allowed_group_ids = '[]'::jsonb WHERE allowed_group_ids IS NULL",
        "UPDATE agents SET inherited_allowed_group_ids = '[]'::jsonb WHERE inherited_allowed_group_ids IS NULL",
        "UPDATE custom_agent_versions SET allowed_group_ids = '[]'::jsonb WHERE allowed_group_ids IS NULL",
    ]
    assert recorder.altered == [
        ("agents", "allowed_group_ids", False, "'[]'::jsonb"),
        ("agents", "inherited_allowed_group_ids", False, "'[]'::jsonb"),
        ("custom_agent_versions", "allowed_group_ids", False, "'[]'::jsonb"),
    ]


def test_sql_models_define_non_null_jsonb_defaults():
    for column in (
        Agent.__table__.c.allowed_group_ids,
        Agent.__table__.c.inherited_allowed_group_ids,
        CustomAgentVersion.__table__.c.allowed_group_ids,
    ):
        assert column.nullable is False
        assert str(column.server_default.arg) == "'[]'::jsonb"
        assert callable(column.default.arg)
        assert column.default.arg(None) == []
