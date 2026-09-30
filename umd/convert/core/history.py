"""Historico de conversoes (item 20), em SQLite.

SQLite faz sentido aqui: precisamos consultar/filtrar um numero
potencialmente grande de registros de forma simples, e a stdlib ja traz
tudo (modulo `sqlite3`), sem dependencia nova.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import config

# None = <pasta de dados do app>/converter/history.db (os testes podem trocar).
DB_PATH: Path | None = None

_SCHEMA = """
CREATE TABLE IF NOT EXISTS history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TEXT NOT NULL,
    source_path TEXT NOT NULL,
    target_path TEXT,
    status TEXT NOT NULL,
    error_message TEXT
);
"""


@dataclass
class HistoryEntry:
    id: int
    timestamp: str
    source_path: str
    target_path: str | None
    status: str
    error_message: str | None


class History:
    def __init__(self, db_path: Path | None = None):
        # resolvido em tempo de chamada (nao como default do parametro) de
        # proposito: assim os testes conseguem isolar DB_PATH via
        # monkeypatch sem precisar que todo chamador passe o caminho.
        self.db_path = db_path or DB_PATH or config.app_dir() / "history.db"
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with closing(self._connect()) as conn:
            conn.execute(_SCHEMA)
            conn.commit()

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path)

    def record(
        self,
        source_path: str,
        target_path: str | None,
        status: str,
        error_message: str | None = None,
    ) -> None:
        with closing(self._connect()) as conn:
            conn.execute(
                "INSERT INTO history (timestamp, source_path, target_path, status, error_message) "
                "VALUES (?, ?, ?, ?, ?)",
                (datetime.now().isoformat(timespec="seconds"), source_path, target_path, status, error_message),
            )
            conn.commit()

    def recent(self, limit: int = 200) -> list[HistoryEntry]:
        with closing(self._connect()) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM history ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        return [
            HistoryEntry(
                id=row["id"],
                timestamp=row["timestamp"],
                source_path=row["source_path"],
                target_path=row["target_path"],
                status=row["status"],
                error_message=row["error_message"],
            )
            for row in rows
        ]

    def clear(self) -> None:
        with closing(self._connect()) as conn:
            conn.execute("DELETE FROM history")
            conn.commit()
