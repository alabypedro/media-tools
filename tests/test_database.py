from __future__ import annotations

from umd.database.models import DownloadRecord, FileRecord
from umd.database.repository import DownloadRepository, FileRepository


def _record(i: int, **kw) -> DownloadRecord:
    data = dict(id=f"job{i}", url=f"https://example.com/{i}", status="completed", title=f"Vídeo {i}",
                platform="YouTube", created_at=f"2026-09-{10 + i:02d}T10:00:00")
    data.update(kw)
    return DownloadRecord(**data)


def test_migrations_create_schema(db) -> None:
    assert db.schema_version >= 1
    tables = {r[0] for r in db.query("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"downloads", "files"} <= tables


def test_save_update_get_and_upsert(db) -> None:
    repo = DownloadRepository(db)
    repo.save(_record(1, status="queued"))
    repo.update("job1", status="completed", total_size=1234, file_path="C:/x.mp4")
    record = repo.get("job1")
    assert record.status == "completed" and record.total_size == 1234
    repo.save(_record(1, status="failed", title="Novo"))  # upsert preserva o id
    assert len(repo.list()) == 1
    assert repo.get("job1").title == "Novo"


def test_list_filters_and_search(db) -> None:
    repo = DownloadRepository(db)
    repo.save(_record(1, platform="YouTube", title="Gato fofo"))
    repo.save(_record(2, platform="TikTok", status="failed", title="Dança"))
    repo.save(_record(3, platform="YouTube", title="100% gato", author="Canal_X"))
    assert [r.id for r in repo.list()] == ["job3", "job2", "job1"]  # mais recente primeiro
    assert {r.id for r in repo.list(search="gato")} == {"job1", "job3"}
    assert [r.id for r in repo.list(search="100%")] == ["job3"]  # % tratado literalmente
    assert [r.id for r in repo.list(search="l_X")] == ["job3"]
    assert [r.id for r in repo.list(status="failed")] == ["job2"]
    assert {r.id for r in repo.list(platform="YouTube")} == {"job1", "job3"}
    assert repo.platforms() == ["TikTok", "YouTube"]


def test_sql_injection_is_harmless(db) -> None:
    repo = DownloadRepository(db)
    repo.save(_record(1))
    assert repo.list(search="' OR 1=1; DROP TABLE downloads; --") == []
    assert len(repo.list()) == 1


def test_mark_interrupted_and_resumable(db) -> None:
    repo = DownloadRepository(db)
    repo.save(_record(1, status="downloading", request_json="{}"))
    repo.save(_record(2, status="queued", request_json="{}"))
    repo.save(_record(3, status="completed", request_json="{}"))
    repo.save(_record(4, status="paused", request_json="{}"))
    assert repo.mark_interrupted() == 2
    assert {r.id: r.status for r in repo.list()}["job1"] == "interrupted"
    assert {r.id for r in repo.list_resumable()} == {"job1", "job2", "job4"}


def test_files_repository_and_history_removal_keeps_library(db) -> None:
    downloads = DownloadRepository(db)
    files = FileRepository(db)
    downloads.save(_record(1))
    files.add(FileRecord(path="C:/d/a.mp4", filename="a.mp4", media_kind="video", download_id="job1", platform="YouTube",
                         title="Gato", created_at="2026-09-20T10:00:00"))
    files.add(FileRecord(path="C:/d/b.jpg", filename="b.jpg", media_kind="image", download_id="job1", platform="Instagram",
                         title="Praia", author="fulano", created_at="2026-01-01T10:00:00"))
    assert [f.filename for f in files.list(kind="image")] == ["b.jpg"]
    assert [f.filename for f in files.list(search="fulano")] == ["b.jpg"]
    assert [f.filename for f in files.list(since="2026-09-01T00:00:00")] == ["a.mp4"]
    assert files.platforms() == ["Instagram", "YouTube"]
    downloads.delete("job1")
    remaining = files.list()
    assert len(remaining) == 2 and all(f.download_id is None for f in remaining)
