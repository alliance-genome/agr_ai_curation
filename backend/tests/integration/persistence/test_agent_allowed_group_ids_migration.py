"""Persistence coverage for canonical agent availability backfill/defaults."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from alembic import command  # pyright: ignore[reportAttributeAccessIssue]
from alembic.config import Config  # pyright: ignore[reportMissingImports]
import sqlalchemy as sa

from src.models.sql.database import engine


BACKEND_ROOT = Path(__file__).resolve().parents[3]
PREVIOUS_REVISION = "a823b1c2d3e4"


def test_agent_allowed_group_ids_backfill_and_database_defaults():
    alembic_config = Config(str(BACKEND_ROOT / "alembic.ini"))
    command.upgrade(alembic_config, "head")
    legacy_agent_id = uuid4()
    default_agent_id = uuid4()
    legacy_version_id = uuid4()
    default_version_id = uuid4()

    command.downgrade(alembic_config, PREVIOUS_REVISION)
    try:
        with engine.begin() as connection:
            connection.execute(
                sa.text(
                    """
                    INSERT INTO agents (
                        id, agent_key, name, instructions, model_id, visibility
                    ) VALUES (
                        :id, :agent_key, 'Legacy Agent', 'Prompt', 'gpt-test', 'system'
                    )
                    """
                ),
                {
                    "id": legacy_agent_id,
                    "agent_key": f"migration_{legacy_agent_id.hex}",
                },
            )
            connection.execute(
                sa.text(
                    """
                    INSERT INTO custom_agent_versions (
                        id, custom_agent_id, version, custom_prompt
                    ) VALUES (:id, :agent_id, 1, 'Prompt')
                    """
                ),
                {"id": legacy_version_id, "agent_id": legacy_agent_id},
            )

        command.upgrade(alembic_config, "head")
        with engine.begin() as connection:
            assert connection.execute(
                sa.text(
                    "SELECT allowed_group_ids, inherited_allowed_group_ids "
                    "FROM agents WHERE id = :id"
                ),
                {"id": legacy_agent_id},
            ).one() == ([], [])
            assert (
                connection.execute(
                    sa.text(
                        "SELECT allowed_group_ids FROM custom_agent_versions WHERE id = :id"
                    ),
                    {"id": legacy_version_id},
                ).scalar_one()
                == []
            )

            connection.execute(
                sa.text(
                    """
                    INSERT INTO agents (
                        id, agent_key, name, instructions, model_id, visibility
                    ) VALUES (
                        :id, :agent_key, 'Default Agent', 'Prompt', 'gpt-test', 'system'
                    )
                    """
                ),
                {
                    "id": default_agent_id,
                    "agent_key": f"migration_{default_agent_id.hex}",
                },
            )
            connection.execute(
                sa.text(
                    """
                    INSERT INTO custom_agent_versions (
                        id, custom_agent_id, version, custom_prompt
                    ) VALUES (:id, :agent_id, 1, 'Prompt')
                    """
                ),
                {"id": default_version_id, "agent_id": default_agent_id},
            )
            assert connection.execute(
                sa.text(
                    "SELECT allowed_group_ids, inherited_allowed_group_ids "
                    "FROM agents WHERE id = :id"
                ),
                {"id": default_agent_id},
            ).one() == ([], [])
            assert (
                connection.execute(
                    sa.text(
                        "SELECT allowed_group_ids FROM custom_agent_versions WHERE id = :id"
                    ),
                    {"id": default_version_id},
                ).scalar_one()
                == []
            )
    finally:
        command.upgrade(alembic_config, "head")
        with engine.begin() as connection:
            connection.execute(
                sa.text("DELETE FROM agents WHERE id IN (:legacy_id, :default_id)"),
                {"legacy_id": legacy_agent_id, "default_id": default_agent_id},
            )
