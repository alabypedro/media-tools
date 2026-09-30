"""Executa UM download: engine -> conversao -> organizacao -> biblioteca.

Fluxo:
1. o worker baixa tudo para uma area temporaria do job
   (<destino>/.umd-staging/<id>/), no mesmo disco do destino;
2. conversoes pedidas (so para o que nao passou pelo yt-dlp);
3. cada arquivo recebe o nome final pelo template, dentro da pasta de
   destino (sanitizado e protegido contra path traversal) e nunca
   sobrescreve nada;
4. o arquivo e registrado na biblioteca (SQLite), com miniatura.

Pausar mantem a area temporaria (para retomar); cancelar apaga.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import sys
from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import Any

from ..core.config import Settings, SettingsStore
from ..core.exceptions import ErrorCode, UMDError
from ..core.i18n import tr
from ..core.logger import get_logger
from ..core.naming import build_fields, format_date, render_template, safe_join, unique_path
from ..core.security import is_dangerous_file, validate_url
from ..database.models import FileRecord
from ..database.repository import FileRepository
from ..engine.runner import EngineRunner
from ..media.converter import convert_image, convert_media, salvage_partial
from ..media.ffmpeg import extract_frame
from ..media.formats import SelectionMode
from ..media.metadata import MediaKind, kind_from_ext
from ..providers.base import GALLERYDL, YTDLP, EngineContext
from ..providers.gallery_dl_provider import extract_author, extract_date, extract_text, title_from
from ..providers.registry import adapter_for
from .job import STAGING_DIRNAME, DownloadJob, JobStatus

log = get_logger("executor")

_TYPE_FIELD = {MediaKind.VIDEO: "video", MediaKind.AUDIO: "audio", MediaKind.IMAGE: "imagem"}
_POST_TYPES = {"post", "gallery", "image"}
_THUMB_MAX_BYTES = 5 * 1024 * 1024


class JobExecutor:
    def __init__(
        self,
        runner: EngineRunner,
        settings_store: SettingsStore,
        files: FileRepository | None = None,
        *,
        ffmpeg_resolver: Callable[[], str | None] = lambda: None,
        js_runtimes: dict[str, str] | None = None,
        thumbnails_dir: Path | None = None,
    ):
        self._runner = runner
        self._store = settings_store
        self._files = files
        self._ffmpeg = ffmpeg_resolver
        self._js_runtimes = js_runtimes or {}
        self._thumbnails_dir = thumbnails_dir

    # ------------------------------------------------------------ execucao

    def execute(self, job: DownloadJob, report: Callable[[], None]) -> None:
        settings = self._store.settings
        request = job.request
        ffmpeg = self._ffmpeg()
        engines = list(request.engines or [YTDLP])
        if engines[0] == YTDLP and not ffmpeg and request.provider_key != "generic":
            raise UMDError(ErrorCode.FFMPEG_MISSING, details="FFmpeg is required to merge/convert yt-dlp downloads")

        ctx = EngineContext(self._runner, settings, ffmpeg, self._js_runtimes, job.cancel_event)
        if request.provider_key == "generic" and len(engines) > 1:
            # site nao cadastrado, sem analise previa (lote/CLI): descobre antes qual
            # engine tem extrator de verdade, em vez de confiar no extrator generico
            engines = [self._resolve_generic_engine(job, ctx, report)]
            request.engines = engines  # retomadas/novas tentativas ja usam a engine certa
            if engines[0] == YTDLP and not ffmpeg:
                raise UMDError(ErrorCode.FFMPEG_MISSING)
        self._prepare_staging(job)
        produced: list[tuple[Path, dict[str, Any]]] = []
        used_engine = engines[0]
        on_event = partial(self._on_event, job, produced, report)

        for position, engine in enumerate(engines):
            worker_request = adapter_for(engine).build_download(
                request.url,
                request.selection,
                ctx,
                job.staging_dir,
                expected_items=request.expected_items,
                is_collection=request.is_collection,
                is_live=request.is_live,
            )
            try:
                self._runner.run(worker_request, on_event=on_event, cancel_event=job.cancel_event)
                used_engine = engine
                break
            except UMDError as exc:
                if exc.code == ErrorCode.CANCELLED and job.stop_reason == "stop":
                    salvaged = self._salvage(job, ffmpeg, report)
                    if salvaged:
                        produced.extend(salvaged)
                        used_engine = engine
                        break
                    raise
                if exc.fallthrough and not produced and position < len(engines) - 1:
                    log.info("job %s: %s could not handle the URL (%s), trying next engine", job.id, engine, exc.code.value)
                    continue
                raise

        if not produced:
            raise UMDError(ErrorCode.NO_MEDIA, details="The engine finished without producing files")

        with job.lock:
            job.status = JobStatus.PROCESSING
            job.progress.stage_detail = "move"
        report()
        job.files = self._finalize(job, produced, used_engine, settings, ffmpeg, report)
        with job.lock:
            if job.progress.total:
                job.progress.downloaded = job.progress.total
            job.progress.item = job.progress.items or job.progress.item
        self.discard(job)

    def _resolve_generic_engine(self, job: DownloadJob, ctx: EngineContext, report: Callable[[], None]) -> str:
        from ..providers.registry import detect_provider

        info = detect_provider(job.request.url).analyze(job.request.url, ctx)
        with job.lock:
            if not job.request.title and info.title:
                job.title = info.title
            job.request.platform_name = info.platform_name
            job.request.author = job.request.author or info.author
            job.thumbnail = job.thumbnail or info.thumbnail
        report()
        return info.engine

    def discard(self, job: DownloadJob) -> None:
        """Apaga a area temporaria do job (parciais)."""
        staging = job.staging_dir
        if staging.name and staging.parent.name == STAGING_DIRNAME:
            shutil.rmtree(staging, ignore_errors=True)
            try:
                staging.parent.rmdir()  # so remove se ficou vazia
            except OSError:
                pass

    # ------------------------------------------------------------ eventos do worker

    def _on_event(self, job: DownloadJob, produced: list, report: Callable[[], None], event: dict[str, Any]) -> None:
        kind = event.get("type")
        if kind == "log":
            return
        with job.lock:
            progress = job.progress
            if kind == "progress":
                for key in ("downloaded", "total", "speed", "eta", "item", "items", "files_done"):
                    value = event.get(key)
                    if value is not None:
                        setattr(progress, key, value)
                if job.status != JobStatus.PROCESSING:
                    job.status = JobStatus.DOWNLOADING
            elif kind == "item":
                data = event.get("data") or {}
                job.item_title = data.get("title") or job.item_title
                if data.get("index"):
                    progress.item = int(data["index"])
                if data.get("count"):
                    progress.items = int(data["count"])
                progress.downloaded = 0
                progress.total = data.get("expected_size")
                progress.speed = progress.eta = None
                single = not progress.items or progress.items == 1
                if single and data.get("title") and not job.request.title:
                    job.title = str(data["title"])
                if not job.thumbnail and data.get("thumbnail"):
                    job.thumbnail = data["thumbnail"]
                job.status = JobStatus.DOWNLOADING
            elif kind == "stage":
                job.status = JobStatus.PROCESSING
                progress.stage_detail = str(event.get("detail") or "")
            elif kind == "file":
                meta = event.get("meta") or {}
                produced.append((Path(str(event.get("path"))), meta))
                progress.files_done = len(produced)
                if not job.request.title and job.title == job.request.url:
                    job.title = str(meta.get("playlist_title") or meta.get("title") or job.title)
                if job.status == JobStatus.PROCESSING and event.get("path"):
                    job.status = JobStatus.DOWNLOADING
            elif kind == "summary":
                failed = int(event.get("failed") or 1)
                job.warning = tr("{n} item(ns) não puderam ser baixados.", n=failed)
        report()

    # ------------------------------------------------------------ pos-download

    def _finalize(
        self,
        job: DownloadJob,
        produced: list[tuple[Path, dict[str, Any]]],
        engine: str,
        settings: Settings,
        ffmpeg: str | None,
        report: Callable[[], None],
    ) -> list[Path]:
        request = job.request
        root = Path(request.output_dir)
        root.mkdir(parents=True, exist_ok=True)
        root_len = len(str(root.resolve()))
        multi = len(produced) > 1
        final: list[Path] = []
        post_text: str | None = None

        for index, (path, meta) in enumerate(produced, start=1):
            if job.cancel_event.is_set() and job.stop_reason in ("cancel", "pause", "shutdown"):
                raise UMDError(ErrorCode.CANCELLED)
            if not path.exists() or not _is_within(job.staging_dir, path):
                log.warning("job %s: ignoring unexpected file %s", job.id, path)
                continue
            if is_dangerous_file(path):
                log.warning("job %s: refusing executable/unsafe file type %s", job.id, path.suffix)
                path.unlink(missing_ok=True)
                continue
            path = self._convert(job, path, engine, ffmpeg, report)
            kind = kind_from_ext(path.suffix)
            fields = self._fields(job, meta, engine, index, multi, kind, path)
            relative = render_template(
                request.filename_template, fields, create_subfolders=request.create_subfolders, root_len=root_len
            )
            destination = unique_path(safe_join(root, relative))
            destination.parent.mkdir(parents=True, exist_ok=True)
            _move(path, destination)
            final.append(destination)
            self._record(job, destination, kind, fields, meta, ffmpeg, settings)
            if post_text is None:
                post_text = self._post_text(job, meta, engine, fields)

        if not final:
            raise UMDError(ErrorCode.NO_MEDIA, details="No usable files were produced")
        if post_text and request.selection.save_post_text:
            sidecar = unique_path(final[0].with_suffix(".txt"))
            sidecar.write_text(post_text, encoding="utf-8")
            final.append(sidecar)
            self._record(job, sidecar, MediaKind.DOCUMENT, {"title": job.title, "author": request.author or ""}, {},
                         ffmpeg, settings)
        return final

    def _convert(self, job: DownloadJob, path: Path, engine: str, ffmpeg: str | None, report: Callable[[], None]) -> Path:
        """Conversoes pedidas pelo usuario para arquivos que nao passaram pelo yt-dlp."""
        if engine == YTDLP:
            return path  # os post-processors do yt-dlp ja cuidaram disso (sem recompressao desnecessaria)
        selection = job.request.selection
        kind = kind_from_ext(path.suffix)
        if kind == MediaKind.IMAGE and selection.image_format != "original":
            return convert_image(path, selection.image_format)

        target: str | None = None
        if kind == MediaKind.VIDEO and selection.mode == SelectionMode.VIDEO and selection.container != "original":
            target = selection.container
        elif kind in (MediaKind.VIDEO, MediaKind.AUDIO) and selection.mode == SelectionMode.AUDIO:
            if selection.audio_format != "original":
                target = selection.audio_format
            elif kind == MediaKind.VIDEO:
                target = "m4a"
        if target is None or path.suffix.lower().lstrip(".") == target:
            return path
        if not ffmpeg:
            raise UMDError(ErrorCode.FFMPEG_MISSING)

        def on_progress(fraction: float) -> None:
            with job.lock:
                job.progress.total = 1000
                job.progress.downloaded = int(fraction * 1000)
                job.progress.speed = job.progress.eta = None
            report()

        with job.lock:
            job.status = JobStatus.PROCESSING
            job.progress.stage_detail = "convert"
        report()
        return convert_media(
            ffmpeg, path, target, audio_bitrate=selection.audio_bitrate, cancel_event=job.cancel_event,
            duration=job.request.duration, on_progress=on_progress,
        )

    def _fields(
        self, job: DownloadJob, meta: dict[str, Any], engine: str, index: int, multi: bool, kind: MediaKind, path: Path
    ) -> dict[str, str]:
        request = job.request
        if engine == GALLERYDL:
            text = extract_text(meta)
            title = title_from(meta, text, request.title or path.stem)
            if request.title and not meta.get("title") and not text:
                title = request.title
            author = extract_author(meta) or request.author
            date = format_date(extract_date(meta)) or format_date(request.upload_date)
            media_id = meta.get("id") or request.media_id
        else:
            title = meta.get("title") or request.title or path.stem
            author = meta.get("uploader") or meta.get("channel") or meta.get("creator") or request.author
            date = format_date(meta.get("upload_date") or request.upload_date, meta.get("timestamp"))
            media_id = meta.get("id") or request.media_id

        if multi and engine != YTDLP and "{index}" not in request.filename_template:
            title = f"{title} - {index:02d}"
        platform = request.platform_name
        extractor = str(meta.get("extractor_key") or "")
        if platform in ("Site", "Link direto") and extractor and extractor.lower() != "generic":
            platform = extractor
        position = meta.get("playlist_index") or index
        try:
            position_str = f"{int(position):02d}"
        except (TypeError, ValueError):
            position_str = f"{index:02d}"
        return build_fields(
            title=title,
            author=author,
            platform=platform,
            date=date,
            id=media_id,
            index=position_str,
            playlist=meta.get("playlist_title") or (request.title if request.is_collection else ""),
            type=_TYPE_FIELD.get(kind, "arquivo"),
            quality=request.selection.quality_label(),
            ext=path.suffix.lstrip("."),
        )

    def _post_text(self, job: DownloadJob, meta: dict[str, Any], engine: str, fields: dict[str, str]) -> str | None:
        request = job.request
        if engine != GALLERYDL and (request.content_type or "") not in _POST_TYPES:
            return None
        text = extract_text(meta) if engine == GALLERYDL else None
        text = text or request.description
        if not text or not text.strip():
            return None
        lines = [
            f"{tr('Título')}: {job.title}",
            f"{tr('Autor')}: {fields.get('author') or request.author or '-'}",
            f"{tr('Plataforma')}: {request.platform_name}",
            f"{tr('Data')}: {fields.get('date') or '-'}",
            f"URL: {meta.get('post_url') or request.url}",
            "",
            text.strip(),
            "",
        ]
        return "\n".join(lines)

    def _record(
        self,
        job: DownloadJob,
        path: Path,
        kind: MediaKind,
        fields: dict[str, str],
        meta: dict[str, Any],
        ffmpeg: str | None,
        settings: Settings,
    ) -> None:
        if self._files is None:
            return
        try:
            size = path.stat().st_size
        except OSError:
            size = None
        record = FileRecord(
            path=str(path),
            filename=path.name,
            media_kind=kind.value,
            download_id=job.id,
            ext=path.suffix.lstrip(".").lower(),
            size=size,
            title=fields.get("title") or job.title,
            author=fields.get("author") or None,
            platform=job.request.platform_name,
            source_url=str(meta.get("webpage_url") or meta.get("post_url") or job.request.url),
            width=meta.get("width") if isinstance(meta.get("width"), int) else None,
            height=meta.get("height") if isinstance(meta.get("height"), int) else None,
            duration=meta.get("duration") if isinstance(meta.get("duration"), (int, float)) else None,
        )
        if kind in (MediaKind.VIDEO, MediaKind.AUDIO):
            record.thumbnail_path = self._thumbnail(job, path, kind, meta, ffmpeg, settings)
        try:
            self._files.add(record)
        except Exception:  # noqa: BLE001 - biblioteca e acessoria; o arquivo ja esta salvo
            log.exception("could not index %s", path)

    def _thumbnail(
        self, job: DownloadJob, path: Path, kind: MediaKind, meta: dict[str, Any], ffmpeg: str | None, settings: Settings
    ) -> str | None:
        if self._thumbnails_dir is None:
            return None
        digest = hashlib.sha1(str(path).encode("utf-8")).hexdigest()[:20]
        url = meta.get("thumbnail") or (job.thumbnail if (job.progress.items or 1) == 1 else None)
        if isinstance(url, str) and url.startswith(("http://", "https://")):
            saved = _fetch_thumbnail(url, self._thumbnails_dir / digest, settings)
            if saved:
                return str(saved)
        if kind == MediaKind.VIDEO and ffmpeg:
            target = self._thumbnails_dir / f"{digest}.jpg"
            if extract_frame(ffmpeg, path, target):
                return str(target)
        return None

    # ------------------------------------------------------------ utilitarios

    def _prepare_staging(self, job: DownloadJob) -> None:
        job.staging_dir.mkdir(parents=True, exist_ok=True)
        _hide(job.staging_dir.parent)

    def _salvage(self, job: DownloadJob, ffmpeg: str | None, report: Callable[[], None]) -> list[tuple[Path, dict[str, Any]]]:
        """Live interrompida pelo usuario: transforma o parcial num arquivo jogavel."""
        if not ffmpeg:
            return []
        with job.lock:
            job.status = JobStatus.PROCESSING
            job.progress.stage_detail = "salvage"
        report()
        parts = sorted(job.staging_dir.rglob("*.part"), key=lambda p: p.stat().st_size, reverse=True)
        for part in parts:
            recovered = salvage_partial(ffmpeg, part, "mp4")
            if recovered is not None:
                meta = {"title": job.request.title or job.title, "id": job.request.media_id,
                        "uploader": job.request.author}
                return [(recovered, meta)]
        return []


def _is_within(directory: Path, target: Path) -> bool:
    try:
        directory = directory.resolve()
        target = target.resolve()
    except OSError:
        return False
    return directory in target.parents


def _move(source: Path, destination: Path) -> None:
    try:
        os.replace(source, destination)
    except OSError:
        shutil.move(str(source), str(destination))  # outro disco


def _hide(path: Path) -> None:
    if sys.platform != "win32":
        return
    try:
        import ctypes

        ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x02)  # FILE_ATTRIBUTE_HIDDEN
    except (OSError, AttributeError):
        pass


_IMAGE_EXT_BY_MIME = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp", "image/gif": "gif"}


def _fetch_thumbnail(url: str, base: Path, settings: Settings) -> Path | None:
    try:
        validate_url(url, allow_local=settings.allow_local_network_urls)
        import requests

        with requests.get(url, stream=True, timeout=(10, 20), headers={"User-Agent": "Mozilla/5.0"}) as response:
            if response.status_code != 200:
                return None
            mime = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
            ext = _IMAGE_EXT_BY_MIME.get(mime)
            if ext is None:
                return None
            data = bytearray()
            for chunk in response.iter_content(64 * 1024):
                data.extend(chunk)
                if len(data) > _THUMB_MAX_BYTES:
                    return None
        target = base.with_suffix(f".{ext}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(bytes(data))
        return target
    except Exception:  # noqa: BLE001 - miniatura e opcional
        return None
