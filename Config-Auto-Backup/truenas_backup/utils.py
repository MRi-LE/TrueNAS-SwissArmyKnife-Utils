"""
truenas_backup.utils
~~~~~~~~~~~~~~~~~~~~
Logging configuration and miscellaneous helpers.
"""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path


LOG_MAX_BYTES = 5 * 1024 * 1024   # 5 MB
LOG_BACKUP_COUNT = 3               # keep 3 rotated files


def setup_logging(log_file: Path, log_level: str = "INFO") -> logging.Logger:
    """Configure root logger with both a rotating file handler and a
    StreamHandler (stdout).  Returns the package-level logger.
    """
    log_file.parent.mkdir(parents=True, exist_ok=True)

    level = getattr(logging, log_level.upper(), logging.INFO)

    fmt = logging.Formatter(
        fmt="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    file_handler = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)

    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)

    root = logging.getLogger()
    root.setLevel(level)
    # Clear any existing handlers so repeated calls (e.g. in tests or a
    # long-running process) don't stack duplicate handlers / file handles.
    for h in root.handlers[:]:
        root.removeHandler(h)
        try:
            h.close()
        except Exception:
            pass
    root.addHandler(file_handler)
    root.addHandler(stream_handler)

    return logging.getLogger("truenas_backup")


def ensure_dirs(*paths: Path) -> None:
    """Create directories (and parents) if they do not exist."""
    for p in paths:
        p.mkdir(parents=True, exist_ok=True)
