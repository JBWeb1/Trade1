"""Structured JSON logging with secret redaction."""
from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone

_SECRET_KEYS = re.compile(r"(password|passwd|secret|token|api[_-]?key|authorization)", re.I)
_INLINE = re.compile(
    r"(?P<k>\b(?:password|passwd|secret|token|api[_-]?key|authorization)\b\s*[=:]\s*)(?P<v>[^\s,;}\"']+)",
    re.I)
_registered_secrets: set[str] = set()


def register_secret(value: str | None) -> None:
    """Any registered literal value is masked wherever it appears in a log line."""
    if value and len(value) >= 4:
        _registered_secrets.add(value)


def redact(text: str) -> str:
    for s in _registered_secrets:
        text = text.replace(s, "***")
    return _INLINE.sub(lambda m: m.group("k") + "***", text)


def redact_obj(obj):
    if isinstance(obj, dict):
        return {k: "***" if _SECRET_KEYS.search(str(k)) else redact_obj(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [redact_obj(v) for v in obj]
    if isinstance(obj, str):
        return redact(obj)
    return obj


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "msg": redact(record.getMessage()),
        }
        extra = getattr(record, "data", None)
        if extra is not None:
            payload["data"] = redact_obj(extra)
        if record.exc_info:
            payload["exc"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO", log_file: str | None = None, console: bool = True) -> None:
    """JSON logs to stderr (API) and/or a file (CLI, so command output stays readable)."""
    handlers: list[logging.Handler] = []
    if console:
        handlers.append(logging.StreamHandler())
    if log_file:
        from pathlib import Path
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
    for h in handlers:
        h.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = handlers
    root.setLevel(level.upper())
