"""Acesso ao banco: historico de downloads e biblioteca de arquivos.

Todo SQL usa parametros (?) -- nenhum valor vindo de URL/metadado e
concatenado na consulta.
"""

from __future__ import annotations

import sqlite3
from dataclasses import asdict, fields
from typing import Any

from ..core.database import Database
from .models import DownloadRecord, FileRecord, now_iso

# status em que um download ainda estava "vivo" quando o app fechou
ACTIVE_STATUSES = ("queued", "starting", "downloading", "processing", "waiting_retry")
RESUMABLE_STATUSES = ("interrupted", "paused")

_DOWNLOAD_COLUMNS = [f.name for f in fields(DownloadRecord)]
_FILE_COLUMNS = [f.name for f in fields(FileRecord) if f.name != "id"]


def _like(term: str) -> str:
    escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


class DownloadRepository:
    def __init__(self, db: Database):
        self.db = db

    def save(self, record: DownloadRecord) -> None:
        record.updated_at = now_iso()
        data = asdict(record)
        cols = ", ".join(_DOWNLOAD_COLUMNS)
        marks = ", ".join(f":{c}" for c in _DOWNLOAD_COLUMNS)
        updates = ", ".join(f"{c} = excluded.{c}" for c in _DOWNLOAD_COLUMNS if c not in ("id", "created_at"))
        self.db.execute(
            f"INSERT INTO downloads ({cols}) VALUES ({marks}) ON CONFLICT(id) DO UPDATE SET {updates}",
            data,
        )

    def update(self, download_id: str, **changes: Any) -> None:
        changes = {k: v for k, v in changes.items() if k in _DOWNLOAD_COLUMNS and k != "id"}
        if not changes:
            return
        changes["updated_at"] = now_iso()
        assignments = ", ".join(f"{k} = :{k}" for k in changes)
        self.db.execute(f"UPDATE downloads SET {assignments} WHERE id = :id", {**changes, "id": download_id})

    def get(self, download_id: str) -> DownloadRecord | None:
        row = self.db.query_one("SELECT * FROM downloads WHERE id = ?", (download_id,))
        return DownloadRecord.from_row(row) if row else None

    def list(
        self,
        *,
        search: str = "",
        status: str | None = None,
        platform: str | None = None,
        limit: int = 1000,
        offset: int = 0,
    ) -> list[DownloadRecord]:
        where, params = [], []
        if search.strip():
            where.append("(title LIKE ? ESCAPE '\\' OR url LIKE ? ESCAPE '\\' OR author LIKE ? ESCAPE '\\')")
            params += [_like(search.strip())] * 3
        if status:
            where.append("status = ?")
            params.append(status)
        if platform:
            where.append("platform = ?")
            params.append(platform)
        sql = "SELECT * FROM downloads"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC, rowid DESC LIMIT ? OFFSET ?"
        params += [limit, offset]
        return [DownloadRecord.from_row(r) for r in self.db.query(sql, params)]

    def platforms(self) -> list[str]:
        rows = self.db.query("SELECT DISTINCT platform FROM downloads WHERE platform IS NOT NULL ORDER BY platform")
        return [r[0] for r in rows]

    def delete(self, download_id: str) -> None:
        self.db.execute("DELETE FROM downloads WHERE id = ?", (download_id,))

    def mark_interrupted(self) -> int:
        """Na abertura do app: downloads que estavam em andamento viram 'interrupted'."""
        marks = ", ".join("?" for _ in ACTIVE_STATUSES)
        return self.db.execute(
            f"UPDATE downloads SET status = 'interrupted', updated_at = ? WHERE status IN ({marks})",
            (now_iso(), *ACTIVE_STATUSES),
        )

    def list_resumable(self) -> list[DownloadRecord]:
        marks = ", ".join("?" for _ in RESUMABLE_STATUSES)
        rows = self.db.query(
            f"SELECT * FROM downloads WHERE status IN ({marks}) AND request_json IS NOT NULL ORDER BY created_at",
            RESUMABLE_STATUSES,
        )
        return [DownloadRecord.from_row(r) for r in rows]


class FileRepository:
    def __init__(self, db: Database):
        self.db = db

    def add(self, record: FileRecord) -> int:
        data = {k: v for k, v in asdict(record).items() if k != "id"}
        cols = ", ".join(_FILE_COLUMNS)
        marks = ", ".join(f":{c}" for c in _FILE_COLUMNS)
        sql = f"INSERT INTO files ({cols}) VALUES ({marks})"
        try:
            record.id = self.db.insert(sql, data)
        except sqlite3.IntegrityError:
            # o registro do historico nao existe (ex.: foi removido): o arquivo
            # continua indexado na biblioteca, so sem o vinculo
            record.download_id = data["download_id"] = None
            record.id = self.db.insert(sql, data)
        return record.id

    def get(self, file_id: int) -> FileRecord | None:
        row = self.db.query_one("SELECT * FROM files WHERE id = ?", (file_id,))
        return FileRecord.from_row(row) if row else None

    def list(
        self,
        *,
        kind: str | None = None,
        platform: str | None = None,
        since: str | None = None,
        search: str = "",
        download_id: str | None = None,
        limit: int = 2000,
    ) -> list[FileRecord]:
        where, params = [], []
        if kind:
            where.append("media_kind = ?")
            params.append(kind)
        if platform:
            where.append("platform = ?")
            params.append(platform)
        if since:
            where.append("created_at >= ?")
            params.append(since)
        if download_id:
            where.append("download_id = ?")
            params.append(download_id)
        if search.strip():
            where.append("(title LIKE ? ESCAPE '\\' OR author LIKE ? ESCAPE '\\' OR filename LIKE ? ESCAPE '\\')")
            params += [_like(search.strip())] * 3
        sql = "SELECT * FROM files"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(limit)
        return [FileRecord.from_row(r) for r in self.db.query(sql, params)]

    def platforms(self) -> list[str]:
        rows = self.db.query("SELECT DISTINCT platform FROM files WHERE platform IS NOT NULL ORDER BY platform")
        return [r[0] for r in rows]

    def update_thumbnail(self, file_id: int, thumbnail_path: str | None) -> None:
        self.db.execute("UPDATE files SET thumbnail_path = ? WHERE id = ?", (thumbnail_path, file_id))

    def delete(self, file_id: int) -> None:
        self.db.execute("DELETE FROM files WHERE id = ?", (file_id,))

    def delete_many(self, file_ids: list[int]) -> int:
        if not file_ids:
            return 0
        marks = ", ".join("?" for _ in file_ids)
        return self.db.execute(f"DELETE FROM files WHERE id IN ({marks})", file_ids)
