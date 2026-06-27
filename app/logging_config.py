"""Logging setup. Called once at startup so every module's logger shares format."""
from __future__ import annotations

import logging

LOG_FORMAT = "%(asctime)s %(levelname)-7s %(name)s | %(message)s"

configured = False


def configure_logging(level: str = "INFO") -> None:
    """Configure the root logger once; safe to call more than once."""
    global configured
    if configured:
        return
    logging.basicConfig(level=level.upper(), format=LOG_FORMAT)
    # httpx logs every request at INFO; quiet it so our own logs stand out.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    configured = True


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
