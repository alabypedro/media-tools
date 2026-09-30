"""Backend gallery-dl (roda dentro do worker): imagens, galerias, posts.

A configuracao global do gallery-dl e zerada e montada a partir da
requisicao, entao arquivos de configuracao do usuario (se existirem) nao
interferem no comportamento do app.
"""

from __future__ import annotations

import collections
import logging
from pathlib import Path
from typing import Any

from ...core.exceptions import ErrorCode, UMDError
from ..protocol import Emitter
from .common import ErrorCollector, ForwardingLogHandler, json_safe

_FILE_KEYS = {
    "num", "count", "filename", "extension", "width", "height", "date", "id", "media_id", "type", "duration",
    "title", "description", "content", "caption", "text", "filesize", "size", "post_url", "url", "category",
    "subcategory", "tweet_id", "post_id", "shortcode", "username", "user", "author", "owner", "blog_name",
    "uploader", "subreddit", "selftext", "body", "summary", "fullname", "nick", "handle", "name", "display_name",
    "created_at", "timestamp", "extension_original", "playlist_title",
}


def _configure(req: dict[str, Any], collector: ErrorCollector) -> None:
    from gallery_dl import config

    config.clear()
    for entry in req.get("config") or []:
        path, key, value = entry
        config.set(tuple(path), key, value)
    config.set(("output",), "mode", "null")
    config.set(("output",), "skip", False)

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
    root.addHandler(ForwardingLogHandler(collector, logging.DEBUG if req.get("verbose") else logging.WARNING))
    root.setLevel(logging.DEBUG if req.get("verbose") else logging.WARNING)


def _map_exception(exc: BaseException, collector: ErrorCollector) -> UMDError:
    from gallery_dl import exception

    details = collector.details(f"{type(exc).__name__}: {exc}")
    if isinstance(exc, exception.NoExtractorError):
        return UMDError(ErrorCode.UNSUPPORTED, details=details)
    if isinstance(exc, exception.ChallengeError):
        return UMDError(ErrorCode.BOT_CHECK, details=details)
    if isinstance(exc, (exception.AuthRequired, exception.AuthorizationError, exception.AuthenticationError)):
        return UMDError(ErrorCode.AUTH_REQUIRED, details=details)
    if isinstance(exc, exception.NotFoundError):
        return UMDError(ErrorCode.NOT_FOUND, details=details)
    if isinstance(exc, exception.HttpError):
        status = getattr(exc, "status", 0) or 0
        code = {401: ErrorCode.AUTH_REQUIRED, 403: ErrorCode.ACCESS_DENIED, 404: ErrorCode.NOT_FOUND,
                410: ErrorCode.NOT_FOUND, 429: ErrorCode.RATE_LIMITED}.get(status)
        if code is None:
            code = ErrorCode.NETWORK if status >= 500 or status == 0 else ErrorCode.UNKNOWN
        return UMDError(code, details=details)
    return collector.to_error(extra=f"{type(exc).__name__}: {exc}")


def _file_meta(kwdict: dict[str, Any]) -> dict[str, Any]:
    return {k: json_safe(v, max_str=3000) for k, v in kwdict.items() if k in _FILE_KEYS}


def analyze(req: dict[str, Any], emitter: Emitter) -> None:
    from gallery_dl import extractor, job
    from gallery_dl.extractor.message import Message

    collector = ErrorCollector(emitter)
    _configure(req, collector)
    extr = extractor.find(req["url"])
    if extr is None:
        raise UMDError(ErrorCode.UNSUPPORTED, details="gallery-dl: no extractor for this URL")

    data_job = job.DataJob(extr, file=None)
    data_job.run()
    if data_job.exception is not None:
        raise _map_exception(data_job.exception, collector)

    posts: list[dict[str, Any]] = []
    files: list[dict[str, Any]] = []
    queue: list[dict[str, Any]] = []
    for message in data_job.data:
        kind = message[0]
        if kind == Message.Directory and len(posts) < 50:
            posts.append(json_safe(message[-1]))
        elif kind == Message.Url:
            files.append({"url": message[1], "meta": _file_meta(message[2])})
        elif kind == Message.Queue:
            url = message[1][0] if isinstance(message[1], (list, tuple)) else message[1]
            queue.append({"url": url, "meta": _file_meta(message[2])})

    if not files and not queue:
        if collector.errors:
            raise collector.to_error(ErrorCode.NO_MEDIA)
        raise UMDError(ErrorCode.NO_MEDIA, details="gallery-dl: extractor returned no files")

    emitter.result(
        {
            "category": extr.category,
            "subcategory": extr.subcategory,
            "extractor": type(extr).__name__,
            "posts": posts,
            "files": files,
            "queue": queue,
            "_warnings": collector.warnings[-10:],
        }
    )


def download(req: dict[str, Any], emitter: Emitter) -> None:
    from gallery_dl import job

    collector = ErrorCollector(emitter)
    staging = Path(req["staging_dir"])
    staging.mkdir(parents=True, exist_ok=True)
    _configure(req, collector)

    expected = req.get("expected_items")
    state = {"index": 0, "files": 0}

    class ProtocolOutput:
        def start(self, path: str) -> None:
            pass

        def skip(self, path: str) -> None:
            pass

        def success(self, path: str) -> None:
            pass

        def progress(self, bytes_total: int, bytes_downloaded: int, bytes_per_second: int) -> None:
            eta = None
            if bytes_total and bytes_per_second:
                eta = max(0, (bytes_total - bytes_downloaded) / bytes_per_second)
            emitter.progress(
                status="downloading",
                downloaded=bytes_downloaded,
                total=bytes_total or None,
                speed=bytes_per_second,
                eta=eta,
                item=max(1, state["index"]),
                items=expected,
            )

    output = ProtocolOutput()

    def on_prepare(pathfmt: Any) -> None:
        state["index"] += 1
        kwdict = pathfmt.kwdict or {}
        emitter.item(
            {
                "index": state["index"],
                "count": expected,
                "id": str(kwdict.get("id") or ""),
                "title": kwdict.get("title") or kwdict.get("filename"),
                "expected_size": kwdict.get("filesize") or None,
            }
        )

    def on_file(pathfmt: Any) -> None:
        path = pathfmt.realpath or pathfmt.path
        if not path:
            return
        if path.startswith("\\\\?\\"):
            path = path[4:]
        state["files"] += 1
        emitter.file(path, _file_meta(pathfmt.kwdict or {}))
        emitter.progress(force=True, status="downloading", downloaded=None, total=None, speed=None, eta=None,
                         item=state["index"], items=expected, files_done=state["files"])

    class UMDDownloadJob(job.DownloadJob):
        def __init__(self, url: Any, parent: Any = None):
            super().__init__(url, parent)
            self.out = output

        def initialize(self, kwdict: Any = None) -> None:
            super().initialize(kwdict)
            if not isinstance(self.hooks, dict):
                self.hooks = collections.defaultdict(list)
            self.hooks["prepare"].append(on_prepare)
            self.hooks["after"].append(on_file)
            self.hooks["skip"].append(on_file)

    urls = req.get("urls") or [req["url"]]
    status = 0
    for url in urls:
        try:
            download_job = UMDDownloadJob(url)
        except Exception as exc:  # noqa: BLE001 - NoExtractorError etc.
            collector.error(f"{type(exc).__name__}: {exc}")
            continue
        try:
            status |= download_job.run()
        except Exception as exc:  # noqa: BLE001
            if state["files"] == 0 and len(urls) == 1:
                raise _map_exception(exc, collector) from None
            collector.error(f"{type(exc).__name__}: {exc}")

    if state["files"] == 0:
        raise collector.to_error(ErrorCode.NO_MEDIA)
    if status or collector.errors:
        emitter.emit({"type": "summary", "failed": max(1, len(collector.errors)), "errors": collector.errors[-10:]})
