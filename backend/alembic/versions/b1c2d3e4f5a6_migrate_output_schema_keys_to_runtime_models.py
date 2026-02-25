"""Migrate output schema keys to runtime openai_agents.models class names.

Revision ID: b1c2d3e4f5a6
Revises: a9b0c1d2e3f4
Create Date: 2026-02-25
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b1c2d3e4f5a6"
down_revision: Union[str, Sequence[str], None] = "a9b0c1d2e3f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


FORWARD_SCHEMA_KEY_MAP = {
    "GeneValidationEnvelope": "GeneResultEnvelope",
    "AlleleValidationEnvelope": "AlleleResultEnvelope",
    "DiseaseValidationEnvelope": "DiseaseResultEnvelope",
    "ChemicalValidationEnvelope": "ChemicalResultEnvelope",
    "GOTermEnvelope": "GOTermResultEnvelope",
    "GOAnnotationsEnvelope": "GOAnnotationsResult",
    "OrthologsEnvelope": "OrthologsResult",
}


def _rewrite_schema_keys(mapping: dict[str, str]) -> None:
    bind = op.get_bind()
    for old_key, new_key in mapping.items():
        bind.execute(
            sa.text(
                """
                UPDATE agents
                SET output_schema_key = :new_key
                WHERE output_schema_key = :old_key
                """
            ),
            {"old_key": old_key, "new_key": new_key},
        )


def upgrade() -> None:
    """Rewrite legacy schema keys to canonical runtime schema class names."""
    _rewrite_schema_keys(FORWARD_SCHEMA_KEY_MAP)


def downgrade() -> None:
    """Restore legacy schema keys."""
    reverse_map = {new_key: old_key for old_key, new_key in FORWARD_SCHEMA_KEY_MAP.items()}
    _rewrite_schema_keys(reverse_map)
