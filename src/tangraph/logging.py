"""Single logger shared across tangraph modules.

Before consolidation, each module created its own ``TangosLogger`` at
import time, which produced a fresh ``tangraph_<module>_<timestamp>.log``
file *per module* on every process start (7+ files). This module gives
all internal callers a single shared logger instance — one log file per
process — while keeping the TangosLogger API the modules already use.

Tests / alternate runtimes can swap the underlying logger via
``set_shared_logger``.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from crewai_mvp.src.crewai_mvp.tangos_logging import TangosLogger

_shared: TangosLogger | None = None


def _build_default_logger() -> TangosLogger:
    log_name = f"tangraph_{datetime.now():%Y%m%d_%H%M%S}.log"
    logger = TangosLogger(log_filename=log_name, overwrite=True)
    logger.logger.setLevel(logging.INFO)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    return logger


def get_logger() -> TangosLogger:
    """Return the process-wide TangosLogger, creating it on first call."""
    global _shared
    if _shared is None:
        _shared = _build_default_logger()
    return _shared


def set_shared_logger(logger: Any) -> None:
    """Replace the shared logger (mostly for tests / alternate hosts)."""
    global _shared
    _shared = logger
