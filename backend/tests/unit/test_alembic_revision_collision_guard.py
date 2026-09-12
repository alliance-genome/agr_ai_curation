"""Exercise the actual Alembic environment's pre-upgrade collision guard."""

import sys
from io import StringIO
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
import sqlalchemy as sa
from alembic.config import Config
from alembic.runtime.environment import EnvironmentContext
from alembic.script import ScriptDirectory

SCRIPT_DIR = Path(__file__).resolve().parents[2] / "alembic"


def _environment(monkeypatch, url, *, offline=False):
    # This tests migration plumbing, not application model initialization.
    # Real SQLAlchemy/Alembic perform the stamp read and transaction handling.
    application_config = ModuleType("src.config")
    application_config.get_app_database_url = lambda: url
    models = ModuleType("src.models.sql")
    models.Base = SimpleNamespace(metadata=sa.MetaData())
    monkeypatch.setitem(sys.modules, "src.config", application_config)
    monkeypatch.setitem(sys.modules, "src.models.sql", models)
    config = Config(output_buffer=StringIO())
    config.set_main_option("script_location", str(SCRIPT_DIR))
    scripts = ScriptDirectory.from_config(config)
    visited = []

    def migrations(heads, context):
        visited.append(heads)
        return ()

    return (
        scripts,
        visited,
        EnvironmentContext(config, scripts, fn=migrations, as_sql=offline),
    )


@pytest.mark.parametrize(
    "stamp",
    [
        None,
        "7c9e2a4b6d80",
        "p3e4f5a6b7c8",
        "f54e2c6f6848",
        "f3a4b5c6d7e8",
        "g4b5c6d7e8f9",
        "h5c6d7e8f9a0",
    ],
)
def test_online_guard_precedes_migrations_and_preserves_stamp(
    tmp_path, monkeypatch, stamp
):
    url = f"sqlite:///{tmp_path / 'guard.db'}"
    engine = sa.create_engine(url)
    if stamp is not None:
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    "CREATE TABLE alembic_version (version_num VARCHAR(32) PRIMARY KEY)"
                )
            )
            connection.execute(
                sa.text("INSERT INTO alembic_version VALUES (:stamp)"), {"stamp": stamp}
            )
    scripts, visited, environment = _environment(monkeypatch, url)
    ambiguous = stamp in {"f3a4b5c6d7e8", "g4b5c6d7e8f9", "h5c6d7e8f9a0"}
    with environment:
        if ambiguous:
            with pytest.raises(
                RuntimeError, match="Ambiguous production/main Alembic revision"
            ):
                scripts.run_env()
        else:
            scripts.run_env()
    assert visited == ([] if ambiguous else [() if stamp is None else (stamp,)])
    if stamp is not None:
        with engine.connect() as connection:
            assert connection.execute(
                sa.text("SELECT version_num FROM alembic_version")
            ).scalars().all() == [stamp]
    engine.dispose()


def test_offline_sql_does_not_attempt_live_revision_read(monkeypatch):
    scripts, visited, environment = _environment(
        monkeypatch, "postgresql://unused/unused", offline=True
    )
    with environment:
        scripts.run_env()
    assert visited == [()]
