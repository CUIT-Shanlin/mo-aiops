"""create m5 saved queries table

Revision ID: 0005
Revises: 0004
Create Date: 2026-06-17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0005"
down_revision = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "saved_queries",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column(
            "params",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint(
            "project_id",
            "name",
            name="uq_saved_queries_project_name",
        ),
    )
    op.create_index("ix_saved_queries_project_id", "saved_queries", ["project_id"])
    op.create_index(
        "ix_saved_queries_project_created",
        "saved_queries",
        ["project_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_saved_queries_project_created", table_name="saved_queries")
    op.drop_index("ix_saved_queries_project_id", table_name="saved_queries")
    op.execute(
        "ALTER TABLE saved_queries "
        "DROP CONSTRAINT IF EXISTS uq_saved_queries_project_name"
    )
    op.drop_table("saved_queries")
