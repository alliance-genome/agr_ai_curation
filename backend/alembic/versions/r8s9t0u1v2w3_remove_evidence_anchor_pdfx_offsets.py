"""Remove unused PDFX markdown offsets from persisted evidence anchors.

Revision ID: r8s9t0u1v2w3
Revises: q2r3s4t5u6v7
Create Date: 2026-04-09 17:25:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "r8s9t0u1v2w3"
down_revision: Union[str, Sequence[str], None] = "q2r3s4t5u6v7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""

    op.execute(
        sa.text(
            """
            UPDATE evidence_anchors
            SET anchor = anchor
                - 'pdfx_markdown_offset_start'
                - 'pdfx_markdown_offset_end'
            WHERE anchor ? 'pdfx_markdown_offset_start'
               OR anchor ? 'pdfx_markdown_offset_end'
            """
        )
    )


def downgrade() -> None:
    """Downgrade schema."""

    # The removed keys were optional diagnostic metadata. Older schema revisions
    # tolerate anchors that omit them, so there is nothing to restore here.
    return None
