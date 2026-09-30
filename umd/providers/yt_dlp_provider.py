"""Adaptador yt-dlp: monta os parametros (JSON) e interpreta o resultado.

Toda a traducao "escolha do usuario -> seletor de formato do yt-dlp"
mora aqui, testavel sem rede.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from ..core.config import Settings
from ..core.exceptions import ErrorCode, UMDError
from ..engine.environment import ytdlp_js_runtimes
from ..media.formats import DownloadSelection, SelectionMode
from ..media.metadata import IMAGE_EXTS, ContentType, MediaEntry, MediaFormat, MediaInfo, MediaKind
from .base import YTDLP, EngineAdapter, EngineContext, url_query

_CODEC_SORT = {"h264": "vcodec:h264", "vp9": "vcodec:vp9", "av1": "vcodec:av01"}

_KNOWN_WARNINGS = [
    (re.compile(r"javascript runtime|js runtime|n challenge|signature solving", re.I),
     "Nenhum runtime JavaScript (Deno/Node) disponível: alguns formatos do YouTube podem não aparecer."),
    (re.compile(r"drm", re.I), "Alguns formatos são protegidos por DRM e foram ocultados."),
    (re.compile(r"only images are available|storyboard", re.I), ""),
]


def common_params(settings: Settings, ctx: EngineContext | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {
        "socket_timeout": settings.socket_timeout_seconds,
        "retries": max(3, settings.retries),
        "fragment_retries": 10,
        "extractor_retries": 2,
        "continuedl": settings.resume_partial,
        "js_runtimes": ytdlp_js_runtimes(ctx.js_runtimes if ctx else None),
        "playlistend": settings.max_playlist_items,
    }
    if settings.cookies_file:
        params["cookiefile"] = settings.cookies_file
    elif settings.cookies_browser:
        params["cookiesfrombrowser"] = [settings.cookies_browser, settings.cookies_browser_profile or None]
    return params


def build_format_params(selection: DownloadSelection, *, is_live: bool = False) -> dict[str, Any]:
    """Traduz a escolha do usuario em format / format_sort / post-processors."""
    params: dict[str, Any] = {}
    postprocessors: list[dict[str, Any]] = []

    if selection.mode == SelectionMode.AUDIO:
        params["format"] = "ba/b"
        if selection.audio_format == "original":
            # extrai a faixa de audio sem recodificar
            postprocessors.append({"key": "FFmpegExtractAudio", "preferredcodec": "best"})
        else:
            quality = "0" if selection.audio_bitrate == "original" else selection.audio_bitrate
            postprocessors.append(
                {"key": "FFmpegExtractAudio", "preferredcodec": selection.audio_format, "preferredquality": quality}
            )
            params["final_ext"] = selection.audio_format
            if selection.audio_format == "m4a":
                params["format_sort"] = ["acodec:aac"]  # AAC na fonte: extrai sem recodificar
    else:
        sort: list[str] = []
        if selection.quality.startswith("format:"):
            chosen = selection.quality.split(":", 1)[1]
            # formato so de video escolhido: junta o melhor audio automaticamente
            params["format"] = chosen if "+" in chosen else f"{chosen}+ba/{chosen}"
        else:
            params["format"] = "bv*+ba/b"
            if selection.quality.isdigit():
                sort.append(f"res:{selection.quality}")
            else:
                sort.append("res")
            sort.append("fps")
        codec = _CODEC_SORT.get(selection.video_codec)
        if codec:
            sort.append(codec)

        container = selection.container
        if container == "mp4":
            sort.append("ext:mp4:m4a")
            params["merge_output_format"] = "mp4"
            # troca so o container (sem recomprimir) se vier outro formato
            postprocessors.append({"key": "FFmpegVideoRemuxer", "preferedformat": "mp4"})
        elif container == "mkv":
            params["merge_output_format"] = "mkv"
            postprocessors.append({"key": "FFmpegVideoRemuxer", "preferedformat": "mkv"})
        elif container == "webm":
            sort.append("ext:webm:webm")
            params["merge_output_format"] = "webm"
            # webm exige VP8/VP9/AV1 + Opus/Vorbis: recodifica so se a fonte nao tiver
            postprocessors.append({"key": "FFmpegVideoConvertor", "preferedformat": "webm"})
        if container in ("mp4", "mkv", "webm"):
            params["final_ext"] = container
        if sort:
            params["format_sort"] = sort

    if selection.embed_metadata and not is_live:
        postprocessors.append({"key": "FFmpegMetadata", "add_metadata": True, "add_chapters": True})
    params["postprocessors"] = postprocessors
    return params


def items_spec(items: list[int] | None) -> str | None:
    """[1,2,3,7,9,10] -> '1-3,7,9-10' (formato de playlist_items / image-range)."""
    if not items:
        return None
    ordered = sorted(set(i for i in items if i > 0))
    ranges: list[str] = []
    start = prev = ordered[0]
    for value in ordered[1:]:
        if value == prev + 1:
            prev = value
            continue
        ranges.append(f"{start}-{prev}" if start != prev else str(start))
        start = prev = value
    ranges.append(f"{start}-{prev}" if start != prev else str(start))
    return ",".join(ranges)


def _int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def parse_format(raw: dict[str, Any]) -> MediaFormat:
    size = _int(raw.get("filesize"))
    approx = _int(raw.get("filesize_approx"))
    return MediaFormat(
        format_id=str(raw.get("format_id") or "?"),
        ext=str(raw.get("ext") or ""),
        width=_int(raw.get("width")),
        height=_int(raw.get("height")),
        fps=_float(raw.get("fps")),
        vcodec=raw.get("vcodec"),
        acodec=raw.get("acodec"),
        tbr=_float(raw.get("tbr")),
        vbr=_float(raw.get("vbr")),
        abr=_float(raw.get("abr")),
        asr=_int(raw.get("asr")),
        audio_channels=_int(raw.get("audio_channels")),
        filesize=size or approx,
        filesize_estimated=not size and bool(approx),
        format_note=str(raw.get("format_note") or ""),
        protocol=str(raw.get("protocol") or ""),
        dynamic_range=raw.get("dynamic_range"),
        language=raw.get("language"),
    )


def _usable_format(raw: dict[str, Any]) -> bool:
    if raw.get("has_drm"):
        return False
    if raw.get("protocol") == "mhtml" or "storyboard" in str(raw.get("format_note") or "").lower():
        return False
    # o extrator generico as vezes devolve uma IMAGEM como se fosse video
    if str(raw.get("ext") or "").lower() in IMAGE_EXTS:
        return False
    return not (raw.get("vcodec") == "none" and raw.get("acodec") == "none")


def _friendly_warnings(raw_warnings: list[str], had_drm: bool) -> list[str]:
    messages: list[str] = []
    for warning in raw_warnings or []:
        for pattern, message in _KNOWN_WARNINGS:
            if pattern.search(warning):
                if message and message not in messages:
                    messages.append(message)
                break
    if had_drm:
        note = "Alguns formatos são protegidos por DRM e foram ocultados."
        if note not in messages:
            messages.append(note)
    return messages


def parse_info(raw: dict[str, Any], url: str, max_items: int = 500) -> MediaInfo:
    """Info-dict (reduzido) do yt-dlp -> MediaInfo."""
    raw_formats = raw.get("formats") or []
    if not raw_formats and raw.get("url") and raw.get("_type", "video") == "video":
        raw_formats = [raw]  # extrator devolveu um unico formato "achatado"
    had_drm = any(f.get("has_drm") for f in raw_formats)
    formats = [parse_format(f) for f in raw_formats if _usable_format(f)]
    if not formats and raw.get("_type", "video") == "video":
        if had_drm:
            raise UMDError(ErrorCode.DRM, details="All formats are DRM protected")
        raise UMDError(ErrorCode.NO_MEDIA, details="yt-dlp found no downloadable video/audio formats")

    info = MediaInfo(
        source_url=url,
        webpage_url=raw.get("webpage_url") or raw.get("original_url") or url,
        engine=YTDLP,
        extractor=raw.get("extractor_key") or raw.get("extractor"),
        id=str(raw["id"]) if raw.get("id") is not None else None,
        title=str(raw.get("title") or raw.get("fulltitle") or raw.get("playlist_title") or ""),
        author=raw.get("uploader") or raw.get("channel") or raw.get("creator") or raw.get("artist")
        or (", ".join(raw["creators"]) if isinstance(raw.get("creators"), list) else None),
        author_url=raw.get("uploader_url") or raw.get("channel_url"),
        description=raw.get("description"),
        duration=_float(raw.get("duration")),
        view_count=_int(raw.get("view_count")),
        like_count=_int(raw.get("like_count")),
        upload_date=raw.get("upload_date") or raw.get("release_date"),
        timestamp=_float(raw.get("timestamp") or raw.get("release_timestamp")),
        thumbnail=raw.get("thumbnail"),
        live_status=raw.get("live_status"),
        formats=formats,
        warnings=_friendly_warnings(raw.get("_warnings") or [], had_drm),
    )

    kind = raw.get("_type") or "video"
    if kind in ("playlist", "multi_video"):
        entries_raw = raw.get("entries") or []
        entries = []
        for i, entry in enumerate(entries_raw, start=1):
            entry_kind = MediaKind.AUDIO if (entry.get("ie_key") or "").lower().startswith("soundcloud") else MediaKind.VIDEO
            entries.append(
                MediaEntry(
                    index=_int(entry.get("playlist_index")) or i,
                    id=str(entry["id"]) if entry.get("id") is not None else None,
                    title=str(entry.get("title") or entry.get("id") or f"Item {i}"),
                    url=entry.get("webpage_url") or entry.get("url"),
                    thumbnail=entry.get("thumbnail"),
                    duration=_float(entry.get("duration")),
                    kind=entry_kind,
                    author=entry.get("uploader") or entry.get("channel"),
                    filesize=_int(entry.get("filesize") or entry.get("filesize_approx")),
                )
            )
        # indices unicos e em ordem (playlist_index pode faltar/repetir em alguns extratores)
        if len({e.index for e in entries}) != len(entries):
            for i, entry in enumerate(entries, start=1):
                entry.index = i
        # canal com abas (Videos/Lives/Shorts): cada item e uma playlist inteira. A selecao
        # vai por URL -- por indice, o yt-dlp aplicaria o mesmo indice DENTRO de cada aba.
        if entries_raw and all(e.get("_type") == "playlist" for e in entries_raw) and all(e.url for e in entries):
            info.entries_are_urls = True
        info.entries = entries
        info.total_entries = _int(raw.get("playlist_count")) or len(entries)
        info.truncated = len(entries) >= max_items and (info.total_entries or 0) >= len(entries)
        info.content_type = ContentType.POST if kind == "multi_video" else ContentType.PLAYLIST
        if not info.thumbnail and entries:
            info.thumbnail = next((e.thumbnail for e in entries if e.thumbnail), None)
        if not info.author and entries:
            info.author = entries[0].author
        return info

    if info.live_status == "is_live":
        info.content_type = ContentType.LIVE
    elif info.live_status == "is_upcoming":
        raise UMDError(ErrorCode.LIVE_NOT_STARTED, details="live_status=is_upcoming")
    elif formats and not any(f.has_video for f in formats):
        info.content_type = ContentType.AUDIO
    elif raw.get("media_type") == "short":
        info.content_type = ContentType.SHORT
    elif "clip" in (info.extractor or "").lower():
        info.content_type = ContentType.CLIP
    else:
        info.content_type = ContentType.VIDEO
    return info


class YtDlpAdapter(EngineAdapter):
    name = YTDLP

    def analyze(self, url: str, ctx: EngineContext) -> MediaInfo:
        settings = ctx.settings
        params = common_params(settings, ctx)
        params["noplaylist"] = _prefer_single(url)
        request = {"action": "analyze", "engine": YTDLP, "url": url, "params": params, "ffmpeg": ctx.ffmpeg}
        result = ctx.runner.run(request, cancel_event=ctx.cancel_event, timeout=settings.analysis_timeout_seconds)
        if not isinstance(result.result, dict):
            raise UMDError(ErrorCode.NO_MEDIA, details="yt-dlp returned no data")
        return parse_info(result.result, url, settings.max_playlist_items)

    def build_download(
        self,
        url: str,
        selection: DownloadSelection,
        ctx: EngineContext,
        staging_dir: Path,
        *,
        expected_items: int | None = None,
        is_collection: bool = False,
        is_live: bool = False,
    ) -> dict[str, Any]:
        settings = ctx.settings
        params = common_params(settings, ctx)
        params.update(build_format_params(selection, is_live=is_live))
        params["noplaylist"] = _prefer_single(url) and not is_collection
        if settings.rate_limit_kbps:
            params["ratelimit"] = settings.rate_limit_kbps * 1024
        if settings.concurrent_fragments > 1:
            params["concurrent_fragment_downloads"] = settings.concurrent_fragments
        if settings.max_file_size_mb:
            params["max_filesize"] = settings.max_file_size_mb * 1024 * 1024
        spec = None if selection.item_urls else items_spec(selection.items)
        if spec:
            params["playlist_items"] = spec
            params.pop("playlistend", None)
        if is_collection or spec or selection.item_urls:
            params["ignoreerrors"] = "only_download"  # um item com erro nao derruba a playlist
            if settings.download_interval_seconds:
                params["sleep_interval"] = settings.download_interval_seconds
        request = {
            "action": "download",
            "engine": YTDLP,
            "url": url,
            "params": params,
            "ffmpeg": ctx.ffmpeg,
            "staging_dir": str(staging_dir),
            "expected_items": expected_items,
        }
        if selection.item_urls:
            request["urls"] = list(selection.item_urls)  # abas/sub-playlists escolhidas
        return request


def _prefer_single(url: str) -> bool:
    """URL de video que tambem cita uma playlist (watch?v=X&list=Y): baixa so o video."""
    query = url_query(url)
    return "v" in query or "/watch" in url or "/shorts/" in url
