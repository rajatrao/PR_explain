"""Process-wide logging for the API and worker."""

from __future__ import annotations

import logging
import sys

_FORMAT = "%(levelname)-5.5s [%(name)s] %(message)s"


def configure_logging() -> None:
    """Send INFO logs to stderr for the whole application.

    Alembic's fileConfig attaches a stderr handler and leaves the root logger
    at WARNING, which drops application INFO records. basicConfig does nothing
    once that handler exists, so the root level is raised afterward. Loggers
    that fileConfig disabled are turned back on.
    """
    logging.basicConfig(level=logging.INFO, format=_FORMAT, stream=sys.stderr)
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.disabled = False
    app_logger = logging.getLogger("app")
    app_logger.setLevel(logging.INFO)
    app_logger.disabled = False
    for name, existing in list(root.manager.loggerDict.items()):
        if name.startswith("app.") and isinstance(existing, logging.Logger):
            existing.disabled = False
