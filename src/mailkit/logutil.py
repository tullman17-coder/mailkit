"""Structured logging to stderr and rotating files. No message bodies by default."""

from __future__ import annotations

import json
import logging
import logging.handlers
from pathlib import Path

from mailkit.paths import log_dir


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "msg": record.getMessage(),
        }
        for key in ("account_id", "provider", "mailbox", "event_type", "op"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def setup_logging(level: str = "info", *, json_logs: bool = False, root: Path | None = None) -> logging.Logger:
    logger = logging.getLogger("mailkit")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.handlers.clear()
    logger.propagate = False

    stream = logging.StreamHandler()
    file_handler = logging.handlers.RotatingFileHandler(
        log_dir(root) / "mailkit.log",
        maxBytes=5_000_000,
        backupCount=5,
        encoding="utf-8",
    )
    fmt = JsonFormatter() if json_logs else logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
    stream.setFormatter(fmt)
    file_handler.setFormatter(fmt)
    logger.addHandler(stream)
    logger.addHandler(file_handler)
    return logger


def get_logger(name: str = "mailkit") -> logging.Logger:
    log = logging.getLogger(name)
    if not logging.getLogger("mailkit").handlers:
        setup_logging()
    return log
