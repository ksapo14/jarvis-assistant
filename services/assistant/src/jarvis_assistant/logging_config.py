from __future__ import annotations

import json
import logging
import os
import re
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

_SENSITIVE_KEY = re.compile(
    r"(?:api[_-]?key|authorization|password|passwd|secret|session[_-]?token|access[_-]?token|"
    r"refresh[_-]?token|credential|cookie|clipboard)",
    re.IGNORECASE,
)
_BEARER = re.compile(r"(?i)\b(?:bearer|token)\s+[A-Za-z0-9._~+/-]{8,}=*")
_GOOGLE_KEY = re.compile(r"\bAIza[A-Za-z0-9_-]{20,}\b")
_SENSITIVE_ASSIGNMENT = re.compile(
    r"(?i)(\b[A-Za-z0-9_-]*(?:api[_-]?key|authorization|password|passwd|secret|"
    r"session[_-]?token|access[_-]?token|refresh[_-]?token|credential|cookie)"
    r"[A-Za-z0-9_-]*\b\s*(?:=|:)\s*)"
    r"(?:\"[^\"]*\"|'[^']*'|[^\s,;&]+)"
)


def redact(value: Any, *, key: str | None = None) -> Any:
    if key and _SENSITIVE_KEY.search(key):
        return "[REDACTED]"
    if isinstance(value, dict):
        return {str(item_key): redact(item, key=str(item_key)) for item_key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [redact(item) for item in value]
    if isinstance(value, str):
        redacted = _SENSITIVE_ASSIGNMENT.sub(r"\1[REDACTED]", value)
        return _GOOGLE_KEY.sub("[REDACTED]", _BEARER.sub("[REDACTED]", redacted))
    return value


class JsonFormatter(logging.Formatter):
    _standard = frozenset(logging.makeLogRecord({}).__dict__)

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": redact(record.getMessage()),
        }
        extras = {
            key: value
            for key, value in record.__dict__.items()
            if key not in self._standard and key not in {"message", "asctime"}
        }
        if extras:
            payload["context"] = redact(extras)
        if record.exc_info:
            payload["exception"] = redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(
    log_dir: Path,
    level: str = "INFO",
    *,
    max_bytes: int = 5_000_000,
    backup_count: int = 5,
) -> None:
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(level.upper())
    for existing in tuple(root.handlers):
        if getattr(existing, "_jarvis_handler", False):
            root.removeHandler(existing)
            existing.close()
    formatter = JsonFormatter()
    file_handler = RotatingFileHandler(
        log_dir / "assistant.jsonl",
        maxBytes=max_bytes,
        backupCount=backup_count,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    file_handler._jarvis_handler = True  # type: ignore[attr-defined]
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    console_handler._jarvis_handler = True  # type: ignore[attr-defined]
    root.addHandler(file_handler)
    root.addHandler(console_handler)


def clear_rotating_logs(
    log_dir: Path,
    *,
    data_dir: Path,
    level: str,
    max_bytes: int,
    backup_count: int,
) -> None:
    """Close, securely scope, remove app-owned logs, then resume structured logging."""
    resolved_data_dir = data_dir.resolve(strict=False)
    lexical_log_dir = Path(os.path.abspath(log_dir))
    expected_log_dir = lexical_log_dir.parent.resolve(strict=False) / lexical_log_dir.name
    try:
        expected_log_dir.relative_to(resolved_data_dir)
    except ValueError as exc:
        raise ValueError("log directory must stay inside the assistant data directory") from exc
    resolved_log_dir = lexical_log_dir.resolve(strict=False)
    final_component_is_redirect = not _same_path(expected_log_dir, resolved_log_dir)
    root = logging.getLogger()
    handlers = tuple(
        handler for handler in root.handlers if getattr(handler, "_jarvis_handler", False)
    )
    for handler in handlers:
        root.removeHandler(handler)
        handler.close()
    if final_component_is_redirect:
        if lexical_log_dir.exists() or lexical_log_dir.is_symlink():
            try:
                lexical_log_dir.unlink()
            except (IsADirectoryError, PermissionError):
                lexical_log_dir.rmdir()
        resolved_log_dir = expected_log_dir
    if resolved_log_dir.is_dir():
        for entry in resolved_log_dir.iterdir():
            if entry.is_dir() or not re.fullmatch(r"assistant\.jsonl(?:\.\d+)?", entry.name):
                continue
            entry.unlink(missing_ok=True)
    if handlers:
        configure_logging(
            resolved_log_dir,
            level,
            max_bytes=max_bytes,
            backup_count=backup_count,
        )


def _same_path(first: Path, second: Path) -> bool:
    return os.path.normcase(os.path.normpath(str(first))) == os.path.normcase(
        os.path.normpath(str(second))
    )
