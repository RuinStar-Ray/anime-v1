"""add upload checksum

Revision ID: f84b87ccdc9d
Revises: 301fd8aa3685
Create Date: 2026-11-15 12:40:32.464624

"""
from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "f84b87ccdc9d"
down_revision: str | None = "301fd8aa3685"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("upload_records") as batch_op:
        batch_op.add_column(sa.Column("file_sha256", sa.String(length=64), nullable=True))
        batch_op.create_index(
            "ix_upload_records_file_sha256", ["file_sha256"], unique=False
        )
        batch_op.create_index(
            "uq_upload_success_checksum",
            ["channel_id", "file_sha256"],
            unique=True,
            sqlite_where=sa.text("status = 'success' AND file_sha256 IS NOT NULL"),
            postgresql_where=sa.text("status = 'success' AND file_sha256 IS NOT NULL"),
        )


def downgrade() -> None:
    with op.batch_alter_table("upload_records") as batch_op:
        batch_op.drop_index("uq_upload_success_checksum")
        batch_op.drop_index("ix_upload_records_file_sha256")
        batch_op.drop_column("file_sha256")
