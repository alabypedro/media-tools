"""Adaptador gallery-dl: imagens, galerias, carrosseis e posts.

Os metadados do gallery-dl variam por site (Instagram usa 'description',
X usa 'content', Reddit usa 'selftext'...). As listas de chaves abaixo
cobrem os sites mais comuns; o que nao for encontrado fica em branco
(nunca inventado).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..core.config import Settings
from ..core.exceptions import ErrorCode, UMDError
from ..media.formats import DownloadSelection
from ..media.metadata import ContentType, MediaEntry, MediaInfo, MediaKind, ext_from_url, kind_from_ext
from .base import GALLERYDL, EngineAdapter, EngineContext
from .yt_dlp_provider import items_spec

CATEGORY_NAMES = {
    "instagram": "Instagram", "twitter": "X (Twitter)", "reddit": "Reddit", "tumblr": "Tumblr",
    "bluesky": "Bluesky", "pinterest": "Pinterest", "tiktok": "TikTok", "facebook": "Facebook", "imgur": "Imgur",
    "deviantart": "DeviantArt", "flickr": "Flickr", "wikimediacommons": "Wikimedia Commons", "wikipedia": "Wikipedia",
    "artstation": "ArtStation", "pixiv": "pixiv", "mastodon": "Mastodon", "vsco": "VSCO", "behance": "Behance",
    "danbooru": "Danbooru", "weibo": "Weibo", "threads": "Threads", "directlink": "Link direto",
}

_TEXT_KEYS = ("content", "description", "caption", "text", "selftext", "body", "summary")
_TITLE_KEYS = ("title", "post_title")
_ID_KEYS = ("tweet_id", "post_id", "shortcode", "id", "media_id")
_DATE_KEYS = ("date", "created_at", "timestamp", "published", "date_created")
_URL_KEYS = ("post_url", "permalink", "link", "url")
_AUTHOR_OBJECT_KEYS = ("author", "user", "owner", "account", "blog")
_AUTHOR_NAME_KEYS = ("nick", "name", "display_name", "fullname", "username", "handle", "screen_name")
_AUTHOR_FLAT_KEYS = ("username", "uploader", "blog_name", "author_name", "fullname", "owner_username", "subreddit")


def _first(data: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        value = data.get(key)
        if value not in (None, "", [], {}):
            return value
    return None


def extract_author(data: dict[str, Any]) -> str | None:
    for key in _AUTHOR_OBJECT_KEYS:
        value = data.get(key)
        if isinstance(value, dict):
            name = _first(value, _AUTHOR_NAME_KEYS)
            if name:
                return str(name)
        elif isinstance(value, str) and value.strip():
            return value.strip()
    value = _first(data, _AUTHOR_FLAT_KEYS)
    return str(value) if value else None


def extract_text(data: dict[str, Any]) -> str | None:
    value = _first(data, _TEXT_KEYS)
    if isinstance(value, list):
        value = " ".join(str(v) for v in value if v)
    if not isinstance(value, str):
        return None
    text = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)  # alguns sites (Tumblr) mandam HTML
    return text.strip() or None


def extract_date(data: dict[str, Any]) -> str | None:
    """Devolve AAAAMMDD (formato do yt-dlp) quando der para identificar."""
    value = _first(data, _DATE_KEYS)
    if value is None:
        return None
    if isinstance(value, (int, float)) and value > 10_000_000:
        from datetime import datetime, timezone

        return datetime.fromtimestamp(value, tz=timezone.utc).strftime("%Y%m%d")
    match = re.match(r"(\d{4})-?(\d{2})-?(\d{2})", str(value))
    return "".join(match.groups()) if match else None


_MEDIA_SUFFIX = re.compile(r"\.(jpe?g|png|gif|webp|avif|bmp|tiff?|heic|mp4|webm|mkv|mov|mp3|m4a|ogg|opus|wav|flac)$", re.I)


def title_from(data: dict[str, Any], text: str | None, fallback: str) -> str:
    title = _first(data, _TITLE_KEYS)
    if isinstance(title, str) and title.strip() and not title.lower().startswith("file:"):
        return _MEDIA_SUFFIX.sub("", title.strip())[:150] or fallback
    if text:
        first_line = text.strip().splitlines()[0]
        return (first_line[:80] + "…") if len(first_line) > 80 else first_line
    if isinstance(title, str) and title.strip():
        cleaned = re.sub(r"^file:", "", title.strip(), flags=re.I)
        return _MEDIA_SUFFIX.sub("", cleaned)[:150] or fallback
    return _MEDIA_SUFFIX.sub("", fallback) or fallback


def platform_name(category: str | None) -> str:
    if not category:
        return "Site"
    return CATEGORY_NAMES.get(category, category.replace("_", " ").title())


def _entry_from_file(index: int, item: dict[str, Any]) -> MediaEntry:
    meta = item.get("meta") or {}
    url = str(item.get("url") or "")
    ext = str(meta.get("extension") or ext_from_url(url) or "").lower()
    kind = kind_from_ext(ext)
    if url.startswith("ytdl:"):
        kind, ext = MediaKind.VIDEO, ext or "mp4"
    if kind in (MediaKind.OTHER, MediaKind.DOCUMENT):
        kind = MediaKind.IMAGE if meta.get("width") else MediaKind.OTHER
    name = str(meta.get("filename") or f"arquivo_{index:02d}")
    return MediaEntry(
        index=index,
        id=str(meta.get("id") or meta.get("media_id") or "") or None,
        title=f"{name}.{ext}" if ext else name,
        url=None if url.startswith("ytdl:") else url,
        thumbnail=url if kind == MediaKind.IMAGE and url.startswith("http") else None,
        kind=kind,
        ext=ext or None,
        width=meta.get("width") if isinstance(meta.get("width"), int) else None,
        height=meta.get("height") if isinstance(meta.get("height"), int) else None,
        filesize=meta.get("filesize") if isinstance(meta.get("filesize"), int) else None,
        duration=meta.get("duration") if isinstance(meta.get("duration"), (int, float)) else None,
    )


def _entry_from_queue(index: int, item: dict[str, Any]) -> MediaEntry:
    meta = item.get("meta") or {}
    url = str(item.get("url") or "")
    text = extract_text(meta)
    fallback = urlsplit(url).path.rstrip("/").rsplit("/", 1)[-1] or url
    return MediaEntry(
        index=index,
        id=str(_first(meta, _ID_KEYS) or "") or None,
        title=title_from(meta, text, fallback),
        url=url,
        kind=MediaKind.OTHER,
        author=extract_author(meta),
    )


_PROFILE_SUBCATEGORIES = ("user", "profile", "account", "blog", "channel", "posts", "media", "timeline", "tweets")
_COLLECTION_SUBCATEGORIES = ("subreddit", "board", "playlist", "tag", "search", "gallery", "album", "collection", "section", "feed")


def parse_result(raw: dict[str, Any], url: str) -> MediaInfo:
    files = raw.get("files") or []
    queue = raw.get("queue") or []
    posts = raw.get("posts") or []
    post = posts[0] if posts else {}
    first_file_meta = (files[0].get("meta") if files else None) or {}
    merged = {**first_file_meta, **post}

    text = extract_text(merged)
    info = MediaInfo(
        source_url=url,
        webpage_url=str(_first(merged, ("post_url", "permalink")) or url),
        engine=GALLERYDL,
        extractor=raw.get("extractor") or raw.get("category"),
        platform_name=platform_name(raw.get("category")),
        id=str(_first(merged, _ID_KEYS) or "") or None,
        author=extract_author(merged),
        description=text,
        upload_date=extract_date(merged),
        warnings=[],
    )

    subcategory = str(raw.get("subcategory") or "").lower()
    if files:
        info.entries = [_entry_from_file(i, item) for i, item in enumerate(files, start=1)]
        info.total_entries = len(info.entries)
        kinds = {e.kind for e in info.entries}
        if len(info.entries) == 1:
            only = info.entries[0]
            if text:
                info.content_type = ContentType.POST
            elif only.kind == MediaKind.VIDEO:
                info.content_type = ContentType.VIDEO
            else:
                info.content_type = ContentType.IMAGE
            info.direct_size = only.filesize
        elif any(s in subcategory for s in _PROFILE_SUBCATEGORIES):
            info.content_type = ContentType.PROFILE
        elif text or kinds - {MediaKind.IMAGE}:
            info.content_type = ContentType.POST
        else:
            info.content_type = ContentType.GALLERY
        info.thumbnail = next((e.thumbnail for e in info.entries if e.thumbnail), None)
    elif queue:
        info.entries = [_entry_from_queue(i, item) for i, item in enumerate(queue, start=1)]
        info.entries_are_urls = True
        info.total_entries = len(info.entries)
        info.content_type = (
            ContentType.PROFILE if any(s in subcategory for s in _PROFILE_SUBCATEGORIES) else ContentType.COLLECTION
        )
    else:
        raise UMDError(ErrorCode.NO_MEDIA, details="gallery-dl returned no files")

    fallback_title = f"{info.platform_name} {info.id}" if info.id else info.platform_name
    info.title = title_from(merged, text, fallback_title)
    return info


def build_config(settings: Settings, ctx: EngineContext, *, analysis: bool = False) -> list[list[Any]]:
    cfg: list[list[Any]] = [
        [["extractor"], "timeout", float(settings.socket_timeout_seconds)],
        [["extractor"], "retries", max(1, settings.retries)],
        [["extractor"], "videos", True],
        [["extractor", "ytdl"], "module", "yt_dlp"],
        [["downloader"], "retries", max(1, settings.retries)],
        [["downloader"], "timeout", float(settings.socket_timeout_seconds)],
        [["downloader"], "part", True],
        [["downloader"], "progress", 0.5],
        [["downloader", "ytdl"], "module", "yt_dlp"],
    ]
    if ctx.ffmpeg:
        cfg.append([["downloader", "ytdl"], "raw-options", {"ffmpeg_location": ctx.ffmpeg}])
    if settings.cookies_file:
        cfg.append([["extractor"], "cookies", settings.cookies_file])
    elif settings.cookies_browser:
        browser = [settings.cookies_browser]
        if settings.cookies_browser_profile:
            browser.append(settings.cookies_browser_profile)
        cfg.append([["extractor"], "cookies", browser])
    if analysis:
        cap = f"1-{settings.max_playlist_items}"
        cfg += [[["extractor"], "image-range", cap], [["extractor"], "child-range", cap]]
    else:
        if settings.rate_limit_kbps:
            cfg.append([["downloader"], "rate", f"{settings.rate_limit_kbps}k"])
        if settings.max_file_size_mb:
            cfg.append([["downloader"], "filesize-max", f"{settings.max_file_size_mb}M"])
        if settings.download_interval_seconds:
            cfg.append([["extractor"], "sleep", float(settings.download_interval_seconds)])
    return cfg


class GalleryDlAdapter(EngineAdapter):
    name = GALLERYDL

    def analyze(self, url: str, ctx: EngineContext) -> MediaInfo:
        request = {"action": "analyze", "engine": GALLERYDL, "url": url, "config": build_config(ctx.settings, ctx, analysis=True)}
        result = ctx.runner.run(request, cancel_event=ctx.cancel_event, timeout=ctx.settings.analysis_timeout_seconds)
        if not isinstance(result.result, dict):
            raise UMDError(ErrorCode.NO_MEDIA, details="gallery-dl returned no data")
        info = parse_result(result.result, url)
        cap = ctx.settings.max_playlist_items
        info.truncated = len(info.entries) >= cap
        return info

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
        cfg = build_config(ctx.settings, ctx)
        cfg += [
            [["extractor"], "base-directory", str(staging_dir)],
            [["extractor"], "skip", True],  # arquivos ja baixados (retomada) sao pulados
        ]
        request: dict[str, Any] = {
            "action": "download",
            "engine": GALLERYDL,
            "url": url,
            "config": cfg,
            "staging_dir": str(staging_dir),
            "expected_items": expected_items,
        }
        if selection.item_urls:
            request["urls"] = list(selection.item_urls)
        else:
            spec = items_spec(selection.items)
            cap = f"1-{ctx.settings.max_playlist_items}"
            cfg.append([["extractor"], "image-range", spec or cap])
            if not spec:
                cfg.append([["extractor"], "child-range", cap])  # perfis enormes: mesmo limite da analise
        return request
