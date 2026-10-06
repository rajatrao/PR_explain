"""single explanation per run: drop the depth columns

Revision ID: d4a7e9c21b3f
Revises: c8f1a2b34d90
Create Date: 2026-10-06 10:00:00.000000

Each run now has one explanation packet and one explanation. Rows for the old
deep, developer, and architecture depths are deleted; the quick row is kept.
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "d4a7e9c21b3f"
down_revision: Union[str, None] = "c8f1a2b34d90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("DELETE FROM explanations WHERE depth <> 'quick'")
    op.execute("DELETE FROM explanation_packets WHERE depth <> 'quick'")
    with op.batch_alter_table("explanations") as batch:
        batch.drop_constraint("uq_explanation_run_depth", type_="unique")
        batch.drop_column("depth")
        batch.create_unique_constraint("uq_explanation_run", ["run_id"])
    with op.batch_alter_table("explanation_packets") as batch:
        batch.drop_constraint("uq_packet_run_depth", type_="unique")
        batch.drop_column("depth")
        batch.create_unique_constraint("uq_packet_run", ["run_id"])
    with op.batch_alter_table("analysis_jobs") as batch:
        batch.drop_column("depth")


def downgrade() -> None:
    with op.batch_alter_table("analysis_jobs") as batch:
        batch.add_column(sa.Column("depth", sa.String(length=32), nullable=True))
    with op.batch_alter_table("explanation_packets") as batch:
        batch.drop_constraint("uq_packet_run", type_="unique")
        batch.add_column(sa.Column("depth", sa.String(length=32), nullable=False, server_default="quick"))
        batch.create_unique_constraint("uq_packet_run_depth", ["run_id", "depth"])
    with op.batch_alter_table("explanations") as batch:
        batch.drop_constraint("uq_explanation_run", type_="unique")
        batch.add_column(sa.Column("depth", sa.String(length=32), nullable=False, server_default="quick"))
        batch.create_unique_constraint("uq_explanation_run_depth", ["run_id", "depth"])
