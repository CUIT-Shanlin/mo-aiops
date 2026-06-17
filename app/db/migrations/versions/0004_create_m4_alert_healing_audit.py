"""create m4 alert healing audit tables

Revision ID: 0004
Revises: 0003
Create Date: 2026-06-17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "alert_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("fingerprint", sa.String(64), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("severity", sa.String(32), nullable=False),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("service", sa.String(255), nullable=True),
        sa.Column("namespace", sa.String(255), nullable=True),
        sa.Column("pod", sa.String(255), nullable=True),
        sa.Column(
            "labels",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "annotations",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("agent_run_id", sa.BigInteger(), nullable=True),
        sa.Column("related_heal_action_id", sa.BigInteger(), nullable=True),
        sa.Column("alert_count", sa.BigInteger(), nullable=False, server_default="1"),
        sa.Column(
            "fired_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "last_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
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
    )
    op.create_index("ix_alert_events_project_id", "alert_events", ["project_id"])
    op.create_index(
        "ix_alert_events_project_status", "alert_events", ["project_id", "status"]
    )
    op.create_index(
        "ix_alert_events_project_created",
        "alert_events",
        ["project_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "uq_alert_events_active_project_fingerprint",
        "alert_events",
        ["project_id", "fingerprint"],
        unique=True,
        postgresql_where=sa.text("status IN ('firing', 'processing', 'healing')"),
    )

    op.create_table(
        "heal_actions",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("alert_event_id", sa.BigInteger(), nullable=True),
        sa.Column("action_type", sa.String(64), nullable=False),
        sa.Column("target_resource", sa.String(255), nullable=False),
        sa.Column("target_namespace", sa.String(255), nullable=True),
        sa.Column("status", sa.String(32), nullable=False),
        sa.Column("risk_level", sa.String(32), nullable=False),
        sa.Column("operator", sa.String(64), nullable=False),
        sa.Column("approved_by", sa.String(128), nullable=True),
        sa.Column("approver_note", sa.Text(), nullable=True),
        sa.Column("result_message", sa.Text(), nullable=True),
        sa.Column("retry_count", sa.BigInteger(), nullable=False, server_default="0"),
        sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
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
    )
    op.create_index("ix_heal_actions_project_id", "heal_actions", ["project_id"])
    op.create_index(
        "ix_heal_actions_project_status", "heal_actions", ["project_id", "status"]
    )
    op.create_index(
        "ix_heal_actions_project_created",
        "heal_actions",
        ["project_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "uq_heal_actions_project_alert_action",
        "heal_actions",
        ["project_id", "alert_event_id", "action_type"],
        unique=True,
        postgresql_where=sa.text("alert_event_id IS NOT NULL"),
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.String(128), nullable=True),
        sa.Column("operator_uid", sa.String(128), nullable=True),
        sa.Column("operator_type", sa.String(32), nullable=False),
        sa.Column("operator_name", sa.String(128), nullable=False),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("resource_type", sa.String(64), nullable=False),
        sa.Column("resource_id", sa.String(128), nullable=True),
        sa.Column("before_state", postgresql.JSONB(), nullable=True),
        sa.Column("after_state", postgresql.JSONB(), nullable=True),
        sa.Column("ip_address", sa.String(64), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("result", sa.String(32), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_audit_logs_project_id", "audit_logs", ["project_id"])
    op.create_index(
        "ix_audit_logs_project_created",
        "audit_logs",
        ["project_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_audit_logs_action_created",
        "audit_logs",
        ["action", sa.text("created_at DESC")],
    )

    op.create_table(
        "notifications",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("type", sa.String(32), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("read", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_notifications_project_id", "notifications", ["project_id"])
    op.create_index(
        "ix_notifications_project_read_created",
        "notifications",
        ["project_id", "read", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_notifications_project_read_created", table_name="notifications")
    op.drop_index("ix_notifications_project_id", table_name="notifications")
    op.drop_table("notifications")

    op.drop_index("ix_audit_logs_action_created", table_name="audit_logs")
    op.drop_index("ix_audit_logs_project_created", table_name="audit_logs")
    op.drop_index("ix_audit_logs_project_id", table_name="audit_logs")
    op.drop_table("audit_logs")

    op.drop_index("uq_heal_actions_project_alert_action", table_name="heal_actions")
    op.drop_index("ix_heal_actions_project_created", table_name="heal_actions")
    op.drop_index("ix_heal_actions_project_status", table_name="heal_actions")
    op.drop_index("ix_heal_actions_project_id", table_name="heal_actions")
    op.drop_table("heal_actions")

    op.drop_index("uq_alert_events_active_project_fingerprint", table_name="alert_events")
    op.drop_index("ix_alert_events_project_created", table_name="alert_events")
    op.drop_index("ix_alert_events_project_status", table_name="alert_events")
    op.drop_index("ix_alert_events_project_id", table_name="alert_events")
    op.drop_table("alert_events")
