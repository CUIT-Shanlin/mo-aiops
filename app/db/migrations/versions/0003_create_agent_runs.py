"""create agent_runs table

Revision ID: 0003
Revises: 0002
Create Date: 2026-06-17
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "agent_runs",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("project_id", sa.String(128), nullable=False),
        sa.Column("trigger_source", sa.String(32), nullable=False),
        sa.Column("alert_event_id", sa.BigInteger(), nullable=True),
        sa.Column("status", sa.String(32), nullable=False, server_default="running"),
        sa.Column("fault_service", sa.String(255), nullable=True),
        sa.Column("anomaly_type", sa.String(64), nullable=True),
        sa.Column("severity", sa.String(32), nullable=True),
        sa.Column("confidence", sa.Numeric(5, 2), nullable=True),
        sa.Column("root_cause_summary", sa.Text(), nullable=True),
        sa.Column("action_type", sa.String(64), nullable=True),
        sa.Column("target_resource", sa.String(255), nullable=True),
        sa.Column("auto_heal", sa.Boolean(), nullable=True),
        sa.Column("risk_level", sa.String(32), nullable=True),
        sa.Column(
            "node_states",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "evidence_chain",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "timeline",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "rag_results",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "llm_calls_count",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "analyzed_logs_count",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "related_traces_count",
            sa.BigInteger(),
            nullable=False,
            server_default="0",
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
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
    op.create_index("ix_agent_runs_project_id", "agent_runs", ["project_id"])
    op.create_index(
        "ix_agent_runs_project_created",
        "agent_runs",
        ["project_id", sa.text("created_at DESC")],
    )
    op.create_index(
        "ix_agent_runs_project_service_type",
        "agent_runs",
        ["project_id", "fault_service", "anomaly_type"],
    )


def downgrade() -> None:
    op.drop_index("ix_agent_runs_project_service_type", table_name="agent_runs")
    op.drop_index("ix_agent_runs_project_created", table_name="agent_runs")
    op.drop_index("ix_agent_runs_project_id", table_name="agent_runs")
    op.drop_table("agent_runs")
