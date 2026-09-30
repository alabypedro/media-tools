"""Utilitarios compartilhados pelos backends (lado do worker)."""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Any

from ...core.exceptions import ErrorCode, UMDError, classify_error_text
from ..protocol import Emitter

MAX_TEXT = 5000


def json_safe(value: Any, depth: int = 0, max_str: int = MAX_TEXT) -> Any:
    """Copia 'value' so com tipos JSON, strings limitadas e profundidade limitada."""
    if depth > 4:
        return None
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return value[:max_str]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        out = {}
        for key, item in list(value.items())[:200]:
            if not isinstance(key, str) or key.startswith("_"):
                continue
            safe = json_safe(item, depth + 1, max_str)
            if safe is not None:
                out[key] = safe
        return out
    if isinstance(value, (list, tuple, set)):
        return [json_safe(item, depth + 1, max_str) for item in list(value)[:500]]
    return None


class ErrorCollector:
    """Guarda erros/avisos da engine para compor a mensagem final."""

    def __init__(self, emitter: Emitter):
        self.emitter = emitter
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def warning(self, msg: str) -> None:
        msg = str(msg)
        self.warnings.append(msg)
        self.emitter.log("warning", msg)

    def error(self, msg: str) -> None:
        msg = str(msg)
        self.errors.append(msg)
        self.emitter.log("error", msg)

    def debug(self, msg: str) -> None:
        self.emitter.log("debug", str(msg))

    def details(self, extra: str = "") -> str:
        lines = [*self.errors[-20:]]
        if extra and extra not in lines:
            lines.append(extra)
        return "\n".join(lines)

    def to_error(self, fallback: ErrorCode = ErrorCode.UNKNOWN, extra: str = "") -> UMDError:
        text = "\n".join([extra, *self.errors])
        code = classify_error_text(text)
        if code == ErrorCode.UNKNOWN:
            code = fallback
        return UMDError(code, details=self.details(extra))


class ForwardingLogHandler(logging.Handler):
    """Encaminha o logging do gallery-dl (WARNING+) para o protocolo."""

    def __init__(self, collector: ErrorCollector, level: int = logging.WARNING):
        super().__init__(level)
        self.collector = collector

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = f"[{record.name}] {record.getMessage()}"
        except Exception:  # noqa: BLE001 - formatacao de log nunca pode derrubar o worker
            return
        if record.levelno >= logging.ERROR:
            self.collector.error(msg)
        elif record.levelno >= logging.WARNING:
            self.collector.warning(msg)
        else:
            self.collector.debug(msg)
