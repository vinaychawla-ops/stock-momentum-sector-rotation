"""Logging configuration — console plus rotating file.

Reads ``logging.level``, ``logging.max_bytes``, ``logging.backup_count`` and
``paths.logs`` from config. Until this module existed nothing configured the
root logger, so ``logs/`` stayed empty.
"""
from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path
from typing import Optional

FORMAT = "%(asctime)s %(levelname)-8s %(name)s: %(message)s"
LOG_FILENAME = "momentum.log"


def setup_logging(config: Optional[dict] = None, force: bool = False) -> Optional[Path]:
    """Configure the root logger once.

    Returns the log file path, or None when logging was already configured or
    the log file could not be opened (console logging still works).
    """
    config = config or {}
    root = logging.getLogger()
    if root.handlers and not force:
        return None

    if force:
        for handler in list(root.handlers):
            root.removeHandler(handler)

    log_cfg = config.get("logging", {}) or {}
    level_name = str(log_cfg.get("level", "INFO")).upper()
    root.setLevel(getattr(logging, level_name, logging.INFO))
    formatter = logging.Formatter(FORMAT)

    console = logging.StreamHandler()
    console.setFormatter(formatter)
    root.addHandler(console)

    logs_dir = Path((config.get("paths", {}) or {}).get("logs", "logs"))
    try:
        logs_dir.mkdir(parents=True, exist_ok=True)
        file_path = logs_dir / LOG_FILENAME
        file_handler = logging.handlers.RotatingFileHandler(
            file_path,
            maxBytes=int(log_cfg.get("max_bytes", 10 * 1024 * 1024)),
            backupCount=int(log_cfg.get("backup_count", 5)),
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)
        return file_path
    except OSError as e:  # noqa: BLE001
        logging.getLogger(__name__).warning(f"File logging disabled ({logs_dir}): {e}")
        return None
