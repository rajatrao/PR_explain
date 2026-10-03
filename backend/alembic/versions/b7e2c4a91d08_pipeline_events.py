"""pipeline events

Revision ID: b7e2c4a91d08
Revises: 144d37fbad11
Create Date: 2026-10-02 16:10:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7e2c4a91d08"
down_revision: Union[str, None] = "144d37fbad11"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "pipeline_events",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("delivery_id", sa.String(length=255), nullable=True),
        sa.Column("head_sha", sa.String(length=64), nullable=True),
        sa.Column("stage", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("message", sa.String(length=240), nullable=False),
        sa.Column("detail", sa.JSON(), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("CURRENT_TIMESTAMP"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["run_id"], ["analysis_runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pipeline_events_run_ordinal", "pipeline_events", ["run_id", "ordinal"], unique=False)
    op.create_index(op.f("ix_pipeline_events_delivery_id"), "pipeline_events", ["delivery_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_pipeline_events_delivery_id"), table_name="pipeline_events")
    op.drop_index("ix_pipeline_events_run_ordinal", table_name="pipeline_events")
    op.drop_table("pipeline_events")
