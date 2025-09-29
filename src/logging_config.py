from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from typing import Dict
from pathlib import Path

from . import settings


def configure_logging(force: bool = False) -> None:
    """Configure application-wide logging with optional file rotation.

    Reads configuration from environment variables and installs handlers
    exactly once unless ``force`` is True.
    """

    root_logger = logging.getLogger()
    force_env = settings.LOG_FORCE

    if root_logger.handlers and not (force or force_env):
        return

    if root_logger.handlers:
        for handler in list(root_logger.handlers):
            root_logger.removeHandler(handler)

    log_level = settings.LOG_LEVEL
    log_file = settings.LOG_FILE
    console_enabled = settings.LOG_CONSOLE
    max_bytes = settings.LOG_MAX_BYTES
    backup_count = settings.LOG_BACKUP_COUNT

    # Prepare handlers
    handlers = []
    file_handler = _build_file_handler(log_file, max_bytes=max_bytes, backup_count=backup_count)
    handlers.append(file_handler)

    if console_enabled:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(_formatter())
        handlers.append(console_handler)

    logging.basicConfig(level=log_level, handlers=handlers)
    logging.captureWarnings(True)

    _quiet_loggers()


def _build_file_handler(path_str: str, *, max_bytes: int, backup_count: int) -> RotatingFileHandler:
    path = Path(path_str).expanduser()
    if not path.parent.exists():
        path.parent.mkdir(parents=True, exist_ok=True)

    handler = RotatingFileHandler(
        filename=path,
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    handler.setFormatter(_formatter())
    return handler


def _formatter() -> logging.Formatter:
    fmt = "%(asctime)s | %(levelname)s | %(name)s | %(message)s%(meta)s"
    return _ExtraFormatter(fmt)


class _ExtraFormatter(logging.Formatter):
    _reserved = {
        "name",
        "msg",
        "args",
        "levelname",
        "levelno",
        "pathname",
        "filename",
        "module",
        "exc_info",
        "exc_text",
        "stack_info",
        "lineno",
        "funcName",
        "created",
        "msecs",
        "relativeCreated",
        "thread",
        "threadName",
        "processName",
        "process",
        "message",
        "asctime",
    }

    def format(self, record: logging.LogRecord) -> str:  # noqa: D401
        extras = self._collect_extras(record)
        record.meta = f" | {extras}" if extras else ""
        return super().format(record)

    def _collect_extras(self, record: logging.LogRecord) -> Dict[str, object]:
        return {
            key: value
            for key, value in record.__dict__.items()
            if key not in self._reserved
        }


def _quiet_loggers() -> None:
    noisy = {
        "celery": logging.WARNING,
        "celery.app.trace": logging.WARNING,
        "celery.worker.strategy": logging.WARNING,
        "kombu": logging.WARNING,
        "httpx": logging.WARNING,
    }
    for name, level in noisy.items():
        logging.getLogger(name).setLevel(level)
