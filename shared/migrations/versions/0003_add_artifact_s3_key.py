"""Add s3_key column to artifacts table — Phase 5 S3 artifact storage.

Revision ID: 0003
Down Revision: 0002
Create Date: 2026-06-10
"""
from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | None = None
depends_on: str | None = None


def upgrade() -> None:
    op.add_column(
        "artifacts",
        sa.Column("s3_key", sa.String(512), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("artifacts", "s3_key")
