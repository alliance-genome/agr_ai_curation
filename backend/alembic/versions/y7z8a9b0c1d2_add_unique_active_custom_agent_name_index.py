"""Add unique active custom-agent name index per user.

Revision ID: y7z8a9b0c1d2
Revises: x6y7z8a9b0c1
Create Date: 2026-02-22
"""

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision = "y7z8a9b0c1d2"
down_revision = "x6y7z8a9b0c1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Normalize case-insensitive duplicate active custom names per user
    # before adding the partial unique index.
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            """
            SELECT id, user_id, name
            FROM agents
            WHERE is_active = true
              AND user_id IS NOT NULL
              AND visibility IN ('private', 'project')
            ORDER BY user_id, lower(name), updated_at DESC, created_at DESC, id DESC
            """
        )
    ).mappings().all()

    seen_by_user: dict[tuple[int, str], str] = {}
    for row in rows:
        user_id = int(row["user_id"])
        original_name = str(row["name"])
        lowered = original_name.lower()
        key = (user_id, lowered)
        if key not in seen_by_user:
            seen_by_user[key] = original_name
            continue

        # Rename duplicates deterministically using UUID prefix.
        suffix = f" ({str(row['id'])[:8]})"
        max_base_len = max(1, 255 - len(suffix))
        candidate_base = original_name[:max_base_len]
        candidate = f"{candidate_base}{suffix}"

        # Guard against accidental collision after truncation.
        counter = 2
        while (user_id, candidate.lower()) in seen_by_user:
            counter_suffix = f" ({str(row['id'])[:6]}-{counter})"
            max_len = max(1, 255 - len(counter_suffix))
            candidate = f"{original_name[:max_len]}{counter_suffix}"
            counter += 1

        connection.execute(
            sa.text("UPDATE agents SET name = :name WHERE id = :id"),
            {"name": candidate, "id": row["id"]},
        )
        seen_by_user[(user_id, candidate.lower())] = candidate

    op.create_index(
        "uq_agents_active_custom_name_per_user",
        "agents",
        ["user_id", sa.text("lower(name)")],
        unique=True,
        postgresql_where=sa.text(
            "is_active = true AND user_id IS NOT NULL AND visibility IN ('private', 'project')"
        ),
    )


def downgrade() -> None:
    op.drop_index("uq_agents_active_custom_name_per_user", table_name="agents")
