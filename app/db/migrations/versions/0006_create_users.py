"""create users table

Revision ID: 0006
Revises: 0005
Create Date: 2026-06-24
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("pk", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("id", sa.String(128), nullable=False),
        sa.Column("username", sa.String(255), nullable=False),
        sa.Column("email", sa.String(255), nullable=True),
        sa.Column("avatar", sa.String(1024), nullable=True),
        sa.Column("is_banned", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "online_status",
            sa.String(32),
            nullable=False,
            server_default="offline",
        ),
        sa.Column(
            "risk_level",
            sa.String(32),
            nullable=False,
            server_default="low",
        ),
        sa.Column(
            "today_messages",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "registered_at",
            sa.Date(),
            nullable=False,
            server_default=sa.func.current_date(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("project_id", "id", name="uq_users_project_id_id"),
    )
    op.create_index("ix_users_project_id", "users", ["project_id"])
    op.create_index(
        "ix_users_project_status_risk",
        "users",
        ["project_id", "online_status", "risk_level"],
    )
    op.create_index("ix_users_project_username", "users", ["project_id", "username"])


def downgrade() -> None:
    op.drop_index("ix_users_project_username", table_name="users")
    op.drop_index("ix_users_project_status_risk", table_name="users")
    op.drop_index("ix_users_project_id", table_name="users")
    op.drop_table("users")
