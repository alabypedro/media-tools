"""Backend yt-dlp (roda dentro do worker).

Os parametros do yt-dlp chegam prontos (JSON) do provider no app; aqui
so acrescentamos o que nao e serializavel: logger, hooks de progresso e
o post-processor que avisa quando cada arquivo final ficou pronto.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...core.exceptions import ErrorCode, UMDError
from ..protocol import Emitter
from .common import ErrorCollector, json_safe

_TOP_KEYS = {
    "id", "title", "fulltitle", "uploader", "uploader_id", "uploader_url", "channel", "channel_url", "creator",
    "creators", "artist", "artists", "track", "album", "description", "duration", "view_count", "like_count",
    "upload_date", "timestamp", "release_timestamp", "thumbnail", "webpage_url", "original_url", "extractor",
    "extractor_key", "_type", "live_status", "is_live", "was_live", "playlist_count", "n_entries", "ext", "width",
    "height", "vcodec", "acodec", "url", "format_id", "filesize", "filesize_approx", "tbr", "abr", "vbr", "fps",
    "media_type", "availability", "age_limit", "_has_drm", "playlist_title", "playlist_id", "ie_key", "protocol",
    "modified_date", "release_date",
}
_FORMAT_KEYS = {
    "format_id", "ext", "width", "height", "fps", "vcodec", "acodec", "tbr", "vbr", "abr", "asr", "audio_channels",
    "filesize", "filesize_approx", "format_note", "protocol", "dynamic_range", "language", "has_drm", "resolution",
    "video_ext", "audio_ext", "format",
}
_ENTRY_KEYS = {
    "id", "title", "url", "webpage_url", "duration", "uploader", "channel", "ie_key", "_type", "playlist_index",
    "thumbnail", "view_count", "live_status", "ext", "width", "height", "filesize", "filesize_approx",
}

_PP_STAGES = {
    "Merger": "merge",
    "ExtractAudio": "extract_audio",
    "VideoRemuxer": "remux",
    "VideoConvertor": "convert",
    "Metadata": "metadata",
    "EmbedThumbnail": "thumbnail",
    "MoveFiles": "move",
}


class _YtdlpLogger:
    def __init__(self, collector: ErrorCollector, verbose: bool = False):
        self.collector = collector
        self.verbose = verbose

    def debug(self, msg: str) -> None:
        if self.verbose and not msg.startswith("[download]"):
            self.collector.debug(msg)

    def info(self, msg: str) -> None:
        if self.verbose:
            self.collector.debug(msg)

    def warning(self, msg: str) -> None:
        self.collector.warning(msg)

    def error(self, msg: str) -> None:
        self.collector.error(msg)


def _best_thumbnail(info: dict[str, Any]) -> str | None:
    thumb = info.get("thumbnail")
    if isinstance(thumb, str) and thumb.startswith(("http://", "https://")):
        return thumb
    thumbs = info.get("thumbnails") or []
    candidates = [t for t in thumbs if isinstance(t, dict) and str(t.get("url", "")).startswith(("http://", "https://"))]
    if not candidates:
        return None
    # a de maior resolucao declarada, senao a ultima (o yt-dlp ordena da pior para a melhor)
    with_size = [t for t in candidates if t.get("width") or t.get("preference") is not None]
    best = max(with_size, key=lambda t: ((t.get("preference") or 0), (t.get("width") or 0))) if with_size else candidates[-1]
    return best.get("url")


def trim_info(info: dict[str, Any]) -> dict[str, Any]:
    """Reduz o info-dict do yt-dlp ao que o app usa (ele pode ter varios MB)."""
    out = {k: json_safe(v) for k, v in info.items() if k in _TOP_KEYS}
    out["thumbnail"] = _best_thumbnail(info)
    formats = info.get("formats") or []
    out["formats"] = [{k: json_safe(v) for k, v in f.items() if k in _FORMAT_KEYS} for f in formats if isinstance(f, dict)]
    entries = info.get("entries")
    if entries is not None:
        trimmed = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            item = {k: json_safe(v) for k, v in entry.items() if k in _ENTRY_KEYS}
            item["thumbnail"] = _best_thumbnail(entry)
            trimmed.append(item)
        out["entries"] = trimmed
    return out


def _prepare_params(req: dict[str, Any], collector: ErrorCollector) -> dict[str, Any]:
    params = dict(req.get("params") or {})
    browser = params.pop("cookiesfrombrowser", None)
    if browser:
        params["cookiesfrombrowser"] = tuple(browser) + (None,) * (4 - len(browser))
    params.update(
        quiet=True,
        no_warnings=False,
        noprogress=True,
        logger=_YtdlpLogger(collector, verbose=bool(req.get("verbose"))),
        color={"stdout": "no_color", "stderr": "no_color"},
    )
    if req.get("ffmpeg"):
        params["ffmpeg_location"] = req["ffmpeg"]
    return params


def _raise_from(exc: BaseException, collector: ErrorCollector) -> None:
    raise collector.to_error(extra=f"{type(exc).__name__}: {exc}") from None


def analyze(req: dict[str, Any], emitter: Emitter) -> None:
    import yt_dlp

    collector = ErrorCollector(emitter)
    params = _prepare_params(req, collector)
    params.update(skip_download=True, extract_flat="in_playlist")
    try:
        with yt_dlp.YoutubeDL(params) as ydl:
            info = ydl.extract_info(req["url"], download=False)
            if not info:
                raise collector.to_error(ErrorCode.NO_MEDIA)
            info = ydl.sanitize_info(info)
    except UMDError:
        raise
    except Exception as exc:  # noqa: BLE001 - DownloadError, ExtractorError, erros de rede...
        _raise_from(exc, collector)
    result = trim_info(info)
    result["_warnings"] = collector.warnings[-10:]
    emitter.result(result)


class _ProgressState:
    def __init__(self, expected_items: int | None):
        self.expected_items = expected_items
        self.item_index = 0
        self.item_key: object = None
        self.expected_size: int | None = None
        self.done_bytes = 0
        self.files = 0


def download(req: dict[str, Any], emitter: Emitter) -> None:
    import yt_dlp
    from yt_dlp.postprocessor.common import PostProcessor

    collector = ErrorCollector(emitter)
    staging = Path(req["staging_dir"])
    (staging / "_parts").mkdir(parents=True, exist_ok=True)
    state = _ProgressState(req.get("expected_items"))

    def start_item(info: dict[str, Any]) -> None:
        key = (info.get("id"), info.get("playlist_index"))
        if key == state.item_key:
            return
        state.item_key = key
        state.item_index = int(info.get("playlist_autonumber") or state.item_index + 1)
        state.expected_size = info.get("filesize") or info.get("filesize_approx")
        state.done_bytes = 0
        emitter.item(
            {
                "index": state.item_index,
                "count": state.expected_items or info.get("n_entries") or 1,
                "id": info.get("id"),
                "title": info.get("title"),
                "author": info.get("uploader") or info.get("channel") or info.get("creator"),
                "thumbnail": _best_thumbnail(info),
                "expected_size": state.expected_size,
                "format": info.get("format"),
                "is_live": bool(info.get("is_live")),
            }
        )

    class ItemStart(PostProcessor):
        def run(self, info: dict[str, Any]):
            start_item(info)
            return [], info

    class FileReady(PostProcessor):
        def run(self, info: dict[str, Any]):
            filepath = info.get("filepath")
            if filepath:
                state.files += 1
                meta = {k: json_safe(v) for k, v in info.items() if k in _TOP_KEYS}
                meta["thumbnail"] = _best_thumbnail(info)
                meta["playlist_index"] = info.get("playlist_index")
                meta["playlist_title"] = info.get("playlist_title") or info.get("playlist")
                meta["format_note"] = info.get("format_note")
                meta["resolution"] = info.get("resolution")
                emitter.file(str(filepath), meta)
            return [], info

    def progress_hook(d: dict[str, Any]) -> None:
        status = d.get("status")
        info = d.get("info_dict") or {}
        if state.item_key is None and info:
            start_item(info)
        current = d.get("downloaded_bytes") or 0
        current_total = d.get("total_bytes") or d.get("total_bytes_estimate")
        if status == "downloading":
            downloaded = state.done_bytes + current
            total = None
            if current_total:
                total = state.done_bytes + current_total
                if state.expected_size and state.expected_size > total:
                    total = state.expected_size
            elif state.expected_size:
                total = state.expected_size
            speed = d.get("speed")
            # ETA do item inteiro (video + audio), nao so da parte que esta baixando agora
            eta = (total - downloaded) / speed if total and speed and total >= downloaded else d.get("eta")
            emitter.progress(
                status="downloading",
                downloaded=downloaded,
                total=total,
                speed=speed,
                eta=eta,
                item=state.item_index or 1,
                items=state.expected_items,
                fragment=d.get("fragment_index"),
                fragments=d.get("fragment_count"),
            )
        elif status == "finished":
            state.done_bytes += current_total or current
            emitter.progress(
                force=True,
                status="downloading",
                downloaded=state.done_bytes,
                total=max(state.done_bytes, state.expected_size or 0) or None,
                speed=None,
                eta=None,
                item=state.item_index or 1,
                items=state.expected_items,
            )

    def pp_hook(d: dict[str, Any]) -> None:
        if d.get("status") == "started":
            name = str(d.get("postprocessor") or "")
            stage = next((v for k, v in _PP_STAGES.items() if k in name), "fixup" if name.startswith("Fixup") else "")
            if stage and stage != "move":
                emitter.stage("processing", stage)

    params = _prepare_params(req, collector)
    params.update(
        paths={"home": str(staging), "temp": str(staging / "_parts")},
        outtmpl={"default": "%(id).80B.%(ext)s"},
        progress_hooks=[progress_hook],
        postprocessor_hooks=[pp_hook],
        windowsfilenames=True,
        overwrites=False,
    )
    try:
        with yt_dlp.YoutubeDL(params) as ydl:
            ydl.add_post_processor(ItemStart(), when="before_dl")
            ydl.add_post_processor(FileReady(), when="after_move")
            ydl.download(list(req.get("urls") or [req["url"]]))
    except UMDError:
        raise
    except Exception as exc:  # noqa: BLE001
        if state.files == 0:
            _raise_from(exc, collector)
        collector.error(f"{type(exc).__name__}: {exc}")

    if state.files == 0:
        raise collector.to_error(ErrorCode.NO_MEDIA)
    if collector.errors:
        # colecao com alguns itens falhando (ignoreerrors): conclui com aviso
        emitter.emit({"type": "summary", "failed": len(collector.errors), "errors": collector.errors[-10:]})
