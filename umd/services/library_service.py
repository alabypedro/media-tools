"""Biblioteca: arquivos baixados pelo app (indexados no SQLite)."""

from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path

from ..database.models import FileRecord
from ..database.repository import DownloadRepository, FileRepository

PERIODS = {
    "all": None,
    "today": 0,
    "7d": 7,
    "30d": 30,
    "365d": 365,
}


def since_for(period: str) -> str | None:
    days = PERIODS.get(period)
    if days is None:
        return None
    start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days)
    return start.isoformat(timespec="seconds")


class LibraryService:
    def __init__(self, files: FileRepository, downloads: DownloadRepository):
        self.files = files
        self.downloads = downloads

    def list(self, *, kind: str | None = None, platform: str | None = None, period: str = "all",
             search: str = "") -> list[FileRecord]:
        return self.files.list(kind=kind or None, platform=platform or None, since=since_for(period), search=search)

    def platforms(self) -> list[str]:
        return self.files.platforms()

    @staticmethod
    def exists(record: FileRecord) -> bool:
        return Path(record.path).is_file()

    def remove_missing(self) -> int:
        """Tira da biblioteca os arquivos que foram apagados/movidos fora do app."""
        missing = [r.id for r in self.files.list(limit=100_000) if r.id is not None and not Path(r.path).exists()]
        return self.files.delete_many(missing)

    def remove(self, file_id: int) -> None:
        self.files.delete(file_id)
