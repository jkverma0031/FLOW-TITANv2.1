# Path: FLOW/titan/observability/logging.py
from __future__ import annotations
import logging
import json
import time
import os
from typing import Optional, Dict, Any


class JsonFormatter(logging.Formatter):
    """
    Enterprise-grade JSON log formatter with consistent fields.
    This formatter is used system-wide for structured logging.
    """

    def format(self, record: logging.LogRecord) -> str:
        base: Dict[str, Any] = {
            "timestamp": time.time(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Attach extra attributes if present
        for attr in ("session_id", "plan_id", "node_id", "trace_id", "span_id"):
            if hasattr(record, attr):
                base[attr] = getattr(record, attr)

        # Errors get stack trace info
        if record.exc_info:
            base["exception"] = self.formatException(record.exc_info)

        return json.dumps(base, ensure_ascii=False)


def configure_logging(
    level: int = logging.INFO,
    log_to_file: Optional[str] = None,
    extra_modules: Optional[Dict[str, int]] = None,
):
    """
    Configures the entire TITAN logging environment.
    - JSON logs
    - Optional file handler
    - Overrides for specific modules
    """
    root = logging.getLogger()
    root.setLevel(level)

    # Remove existing handlers
    for h in list(root.handlers):
        root.removeHandler(h)

    formatter = JsonFormatter()

    # Console handler
    ch = logging.StreamHandler()
    ch.setFormatter(formatter)
    root.addHandler(ch)

    # Optional file handler
    if log_to_file:
        os.makedirs(os.path.dirname(log_to_file) or ".", exist_ok=True)
        fh = logging.FileHandler(log_to_file, encoding="utf-8")
        fh.setFormatter(formatter)
        root.addHandler(fh)

    # Module-level overrides
    if extra_modules:
        for mod, lvl in extra_modules.items():
            logging.getLogger(mod).setLevel(lvl)

    logging.getLogger(__name__).info("Structured logging configured.")
