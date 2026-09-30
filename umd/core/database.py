"""Conexao SQLite compartilhada entre threads, com migracoes versionadas.

Uma unica conexao protegida por lock: o volume de escrita e pequeno
(mudancas de status de download, nao cada tick de progresso), entao
serializar e mais simples e seguro do que uma conexao por thread.
"""

from __future__ import annotations

import sqlite3
import threading
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from pathlib import Path
from typing import Any

_MIGRATIONS: list[str] = [
    # v1
    """
    CREATE TABLE downloads (
        id              TEXT PRIMARY KEY,
        url             TEXT NOT NULL,
        title           TEXT,
        author          TEXT,
        platform        TEXT,
        provider        TEXT,
        content_type    TEXT,
        media_kind      TEXT,
        status          TEXT NOT NULL,
        quality         TEXT,
        format          TEXT,
        output_dir      TEXT,
        file_path       TEXT,
        total_size      INTEGER,
        file_count      INTEGER DEFAULT 0,
        thumbnail_url   TEXT,
        error_code      TEXT,
        error_message   TEXT,
        error_details   TEXT,
        request_json    TEXT,
        staging_dir     TEXT,
        attempts        INTEGER DEFAULT 0,
        created_at      TEXT NOT NULL,
        updated_at      TEXT NOT NULL,
        completed_at    TEXT
    );
    CREATE INDEX idx_downloads_created ON downloads(created_at);
    CREATE INDEX idx_downloads_status ON downloads(status);

    CREATE TABLE files (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        download_id     TEXT REFERENCES downloads(id) ON DELETE SET NULL,
        path            TEXT NOT NULL,
        filename        TEXT NOT NULL,
        media_kind      TEXT NOT NULL,
        ext             TEXT,
        size            INTEGER,
        title           TEXT,
        author          TEXT,
        platform        TEXT,
        source_url      TEXT,
        width           INTEGER,
        height          INTEGER,
        duration        REAL,
        thumbnail_path  TEXT,
        created_at      TEXT NOT NULL
    );
    CREATE INDEX idx_files_kind ON files(media_kind);
    CREATE INDEX idx_files_platform ON files(platform);
    CREATE INDEX idx_files_created ON files(created_at);
    CREATE INDEX idx_files_download ON files(download_id);
    """,
]


class Database:
    def __init__(self, path: Path):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=10)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        try:
            self._conn.execute("PRAGMA journal_mode = WAL")
        except sqlite3.DatabaseError:
            pass  # ex.: pasta de rede sem suporte a WAL; o modo padrao funciona
        self._migrate()

    def _migrate(self) -> None:
        with self._lock:
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
            for index in range(version, len(_MIGRATIONS)):
                with self._conn:
                    self._conn.executescript(_MIGRATIONS[index])
                    self._conn.execute(f"PRAGMA user_version = {index + 1}")

    @property
    def schema_version(self) -> int:
        with self._lock:
            return self._conn.execute("PRAGMA user_version").fetchone()[0]

    def execute(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> int:
        """Executa uma escrita e devolve o numero de linhas afetadas."""
        with self._lock, self._conn:
            cursor = self._conn.execute(sql, params)
            return cursor.rowcount

    def insert(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> int:
        with self._lock, self._conn:
            cursor = self._conn.execute(sql, params)
            return int(cursor.lastrowid or 0)

    def query(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    def query_one(self, sql: str, params: Sequence[Any] | dict[str, Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self._conn.execute(sql, params).fetchone()

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock, self._conn:
            yield self._conn

    def close(self) -> None:
        with self._lock:
            self._conn.close()
