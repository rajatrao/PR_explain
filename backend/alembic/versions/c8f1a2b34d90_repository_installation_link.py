"""repository installation link

Revision ID: c8f1a2b34d90
Revises: b7e2c4a91d08
Create Date: 2026-10-02 22:40:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "c8f1a2b34d90"
down_revision: Union[str, None] = "b7e2c4a91d08"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "github_installations",
        sa.Column("account_type", sa.String(length=32), nullable=True),
    )
    op.alter_column("repositories", "installation_id", existing_type=sa.BigInteger(), nullable=True)


def downgrade() -> None:
    op.alter_column("repositories", "installation_id", existing_type=sa.BigInteger(), nullable=False)
    op.drop_column("github_installations", "account_type")
