from __future__ import annotations

from pathlib import Path

from alembic import command
from alembic.config import Config


def upgrade() -> None:
    root = Path(__file__).resolve().parents[2]
    config = Config(str(root / "alembic.ini"))
    config.set_main_option("script_location", str(root / "alembic"))
    command.upgrade(config, "head")
