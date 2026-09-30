"""Monta todos os servicos do app (usado igual pela GUI e pela CLI)."""

from __future__ import annotations

from dataclasses import dataclass

from ..core import paths
from ..core.config import SettingsStore
from ..core.database import Database
from ..core.i18n import set_language
from ..core.logger import get_logger, setup_logging
from ..database.repository import DownloadRepository, FileRepository
from ..downloader.executor import JobExecutor
from ..downloader.manager import DownloadManager
from ..engine.environment import find_js_runtimes
from ..engine.runner import EngineRunner
from ..media.ffmpeg import find_ffmpeg
from .download_service import DownloadService
from .engine_service import EngineService
from .library_service import LibraryService
from .media_service import MediaService

log = get_logger("app")


@dataclass
class AppContext:
    settings_store: SettingsStore
    db: Database
    downloads: DownloadRepository
    files: FileRepository
    runner: EngineRunner
    manager: DownloadManager
    media: MediaService
    download: DownloadService
    library: LibraryService
    engines: EngineService
    interrupted_on_start: int = 0

    def close(self) -> None:
        self.manager.shutdown()
        self.db.close()


def create_context(
    *, start_manager: bool = True, restore_jobs: bool = True, debug: bool = False, console_log: bool = False
) -> AppContext:
    setup_logging(paths.logs_dir(), debug=debug, console=console_log)
    store = SettingsStore()
    set_language(store.settings.language)

    db = Database(paths.database_path())
    downloads = DownloadRepository(db)
    files = FileRepository(db)
    interrupted = downloads.mark_interrupted()

    runner = EngineRunner(paths.engines_dir())
    js_runtimes = find_js_runtimes()

    def ffmpeg_resolver() -> str | None:
        return find_ffmpeg(store.settings.ffmpeg_path)

    executor = JobExecutor(
        runner, store, files, ffmpeg_resolver=ffmpeg_resolver, js_runtimes=js_runtimes, thumbnails_dir=paths.thumbnails_dir()
    )
    manager = DownloadManager(executor, store, downloads)
    if restore_jobs:
        for record in downloads.list_resumable():
            manager.restore(record)
    if start_manager:
        manager.start()

    log.info("app context ready (data=%s, js_runtimes=%s)", paths.app_data_dir(), ",".join(js_runtimes) or "none")
    return AppContext(
        settings_store=store,
        db=db,
        downloads=downloads,
        files=files,
        runner=runner,
        manager=manager,
        media=MediaService(runner, store, ffmpeg_resolver=ffmpeg_resolver, js_runtimes=js_runtimes),
        download=DownloadService(manager, store),
        library=LibraryService(files, downloads),
        engines=EngineService(runner, paths.engines_dir(), store),
        interrupted_on_start=interrupted,
    )
