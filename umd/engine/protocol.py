"""Protocolo worker <-> app: uma linha JSON por evento.

O app escreve UMA requisicao JSON no stdin do worker. O worker responde
com eventos no stdout:

    {"type": "log", "level": "warning", "msg": "..."}
    {"type": "item", "data": {...}}          inicio de um item (titulo, tamanho esperado...)
    {"type": "progress", "downloaded": 1, "total": 2, "speed": 3.0, "eta": 4, "item": 1, "items": 5}
    {"type": "stage", "stage": "processing", "detail": "merge"}
    {"type": "file", "path": "...", "meta": {...}}
    {"type": "result", "data": {...}}        resultado de analyze/versions/check_support
    {"type": "error", "error": {"code": "...", "message": "...", "details": "..."}}
    {"type": "done"}
"""

from __future__ import annotations

import json
import threading
import time
from datetime import date, datetime
from typing import Any, TextIO

PROTOCOL_VERSION = 1
MAX_REQUEST_BYTES = 8 * 1024 * 1024


def _default(value: Any) -> Any:
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (set, frozenset, tuple)):
        return list(value)
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def encode(event: dict[str, Any]) -> str:
    return json.dumps(event, ensure_ascii=True, separators=(",", ":"), default=_default) + "\n"


def decode(line: str) -> dict[str, Any] | None:
    line = line.strip()
    if not line.startswith("{"):
        return None
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) and "type" in event else None


class Emitter:
    """Escreve eventos no canal do protocolo (thread-safe, progresso limitado)."""

    def __init__(self, stream: TextIO, progress_interval: float = 0.25):
        self._stream = stream
        self._lock = threading.Lock()
        self._progress_interval = progress_interval
        self._last_progress = 0.0

    def emit(self, event: dict[str, Any]) -> None:
        data = encode(event)
        with self._lock:
            try:
                self._stream.write(data)
                self._stream.flush()
            except (OSError, ValueError):
                pass  # app fechou o pipe: nada a fazer, o processo vai ser encerrado

    def log(self, level: str, msg: str) -> None:
        self.emit({"type": "log", "level": level, "msg": msg[:4000]})

    def progress(self, force: bool = False, **fields: Any) -> None:
        now = time.monotonic()
        if not force and now - self._last_progress < self._progress_interval:
            return
        self._last_progress = now
        self.emit({"type": "progress", **fields})

    def item(self, data: dict[str, Any]) -> None:
        self.emit({"type": "item", "data": data})

    def stage(self, stage: str, detail: str = "") -> None:
        self.emit({"type": "stage", "stage": stage, "detail": detail})

    def file(self, path: str, meta: dict[str, Any]) -> None:
        self.emit({"type": "file", "path": path, "meta": meta})

    def result(self, data: Any) -> None:
        self.emit({"type": "result", "data": data})

    def error(self, error: dict[str, Any]) -> None:
        self.emit({"type": "error", "error": error})

    def done(self) -> None:
        self.emit({"type": "done"})
