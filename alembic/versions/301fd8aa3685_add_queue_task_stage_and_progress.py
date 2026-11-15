"""add queue task stage and progress

Revision ID: 301fd8aa3685
Revises: 75259fca1cbf
Create Date: 2026-11-15 12:35:57.152707

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "301fd8aa3685"
down_revision: str | None = "75259fca1cbf"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("queue_tasks") as batch_op:
        batch_op.add_column(
            sa.Column("stage", sa.String(length=80), nullable=False, server_default="")
        )
        batch_op.add_column(
            sa.Column("progress", sa.Integer(), nullable=False, server_default="0")
        )


def downgrade() -> None:
    with op.batch_alter_table("queue_tasks") as batch_op:
        batch_op.drop_column("progress")
        batch_op.drop_column("stage")
