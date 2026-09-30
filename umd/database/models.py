"""Registros persistidos: um download (historico) e seus arquivos (biblioteca)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field, fields
from datetime import datetime


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


@dataclass
class DownloadRecord:
    id: str
    url: str
    status: str
    title: str | None = None
    author: str | None = None
    platform: str | None = None
    provider: str | None = None
    content_type: str | None = None
    media_kind: str | None = None
    quality: str | None = None
    format: str | None = None
    output_dir: str | None = None
    file_path: str | None = None
    total_size: int | None = None
    file_count: int = 0
    thumbnail_url: str | None = None
    error_code: str | None = None
    error_message: str | None = None
    error_details: str | None = None
    request_json: str | None = None
    staging_dir: str | None = None
    attempts: int = 0
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    completed_at: str | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "DownloadRecord":
        names = {f.name for f in fields(cls)}
        return cls(**{key: row[key] for key in row.keys() if key in names})


@dataclass
class FileRecord:
    path: str
    filename: str
    media_kind: str
    download_id: str | None = None
    ext: str | None = None
    size: int | None = None
    title: str | None = None
    author: str | None = None
    platform: str | None = None
    source_url: str | None = None
    width: int | None = None
    height: int | None = None
    duration: float | None = None
    thumbnail_path: str | None = None
    created_at: str = field(default_factory=now_iso)
    id: int | None = None

    @classmethod
    def from_row(cls, row: sqlite3.Row) -> "FileRecord":
        names = {f.name for f in fields(cls)}
        return cls(**{key: row[key] for key in row.keys() if key in names})
