"""Single logger shared across investigator modules.

A process-wide ``logging.Logger`` with one log file per process plus a
stdout mirror, so all internal callers (``log = get_logger()``) share
one instance. Tests / alternate runtimes can swap it via
``set_shared_logger``.
"""

from __future__ import annotations

import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

_LOG_DIR = Path("logs")
_shared: logging.Logger | None = None


def _build_default_logger() -> logging.Logger:
    logger = logging.getLogger("investigator")
    logger.setLevel(logging.INFO)
    if not logger.handlers:
        fmt = logging.Formatter("%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        stream = logging.StreamHandler(sys.stdout)
        stream.setFormatter(fmt)
        logger.addHandler(stream)
        try:
            _LOG_DIR.mkdir(parents=True, exist_ok=True)
            log_path = _LOG_DIR / f"investigator_{datetime.now():%Y%m%d_%H%M%S}.log"
            file_handler = logging.FileHandler(log_path, mode="w")
            file_handler.setFormatter(fmt)
            logger.addHandler(file_handler)
        except OSError:
            # A read-only working dir shouldn't take the process down; the
            # stdout handler still carries the logs.
            pass
        logger.propagate = False
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return logger


def get_logger() -> logging.Logger:
    """Return the process-wide logger, creating it on first call."""
    global _shared
    if _shared is None:
        _shared = _build_default_logger()
    return _shared


def set_shared_logger(logger: Any) -> None:
    """Replace the shared logger (mostly for tests / alternate hosts)."""
    global _shared
    _shared = logger
