"""Merge PDF size limit and flow prompt dataflow heads.

Revision ID: v8w9x0y1z2a3
Revises: c0d1e2f3a4b5, u7v8w9x0y1z2
Create Date: 2026-06-12
"""

from typing import Sequence, Union


revision: str = "v8w9x0y1z2a3"
down_revision: Union[str, Sequence[str], None] = (
    "c0d1e2f3a4b5",
    "u7v8w9x0y1z2",
)
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
