"""Modelo normalizado do conteudo analisado, independente da engine.

Os providers convertem a saida crua do yt-dlp / gallery-dl / download
direto neste formato; a interface e a CLI so conhecem estes modelos.
"""

from __future__ import annotations

from enum import Enum
from pathlib import PurePosixPath
from urllib.parse import urlsplit

from pydantic import BaseModel, Field


class ContentType(str, Enum):
    VIDEO = "video"
    SHORT = "short"
    REEL = "reel"
    CLIP = "clip"
    LIVE = "live"
    AUDIO = "audio"
    IMAGE = "image"
    GALLERY = "gallery"
    POST = "post"
    PLAYLIST = "playlist"
    CHANNEL = "channel"
    PROFILE = "profile"
    COLLECTION = "collection"


CONTENT_TYPE_LABELS = {
    ContentType.VIDEO: "Vídeo",
    ContentType.SHORT: "Short",
    ContentType.REEL: "Reel",
    ContentType.CLIP: "Clipe",
    ContentType.LIVE: "Transmissão ao vivo",
    ContentType.AUDIO: "Áudio",
    ContentType.IMAGE: "Imagem",
    ContentType.GALLERY: "Galeria",
    ContentType.POST: "Post",
    ContentType.PLAYLIST: "Playlist",
    ContentType.CHANNEL: "Canal",
    ContentType.PROFILE: "Perfil",
    ContentType.COLLECTION: "Coleção",
}

COLLECTION_TYPES = {ContentType.PLAYLIST, ContentType.CHANNEL, ContentType.PROFILE, ContentType.COLLECTION}
MULTI_FILE_TYPES = {ContentType.GALLERY, ContentType.POST}


class MediaKind(str, Enum):
    VIDEO = "video"
    AUDIO = "audio"
    IMAGE = "image"
    DOCUMENT = "document"
    OTHER = "other"


VIDEO_EXTS = {"mp4", "mkv", "webm", "mov", "m4v", "avi", "flv", "3gp", "ts", "mpg", "mpeg", "wmv", "ogv"}
AUDIO_EXTS = {"mp3", "m4a", "aac", "ogg", "oga", "opus", "wav", "flac", "alac", "wma", "aiff", "aif", "mka", "weba"}
IMAGE_EXTS = {"jpg", "jpeg", "png", "gif", "webp", "avif", "bmp", "tif", "tiff", "heic", "heif", "jfif", "svg"}
DOCUMENT_EXTS = {"txt", "json", "pdf", "srt", "vtt", "ass", "md", "html", "htm"}
LOSSLESS_ACODECS = ("flac", "alac", "pcm", "wav", "aiff", "ape", "wavpack", "tta")


def kind_from_ext(ext: str | None) -> MediaKind:
    ext = (ext or "").lower().lstrip(".")
    if ext in VIDEO_EXTS:
        return MediaKind.VIDEO
    if ext in AUDIO_EXTS:
        return MediaKind.AUDIO
    if ext in IMAGE_EXTS:
        return MediaKind.IMAGE
    if ext in DOCUMENT_EXTS:
        return MediaKind.DOCUMENT
    return MediaKind.OTHER


def ext_from_url(url: str) -> str:
    try:
        suffix = PurePosixPath(urlsplit(url).path).suffix
    except ValueError:
        return ""
    return suffix.lower().lstrip(".")


def _is_none_codec(codec: str | None) -> bool:
    return (codec or "none").lower() == "none"


class MediaFormat(BaseModel):
    format_id: str
    ext: str = ""
    width: int | None = None
    height: int | None = None
    fps: float | None = None
    vcodec: str | None = None
    acodec: str | None = None
    tbr: float | None = None
    vbr: float | None = None
    abr: float | None = None
    asr: int | None = None
    audio_channels: int | None = None
    filesize: int | None = None
    filesize_estimated: bool = False
    format_note: str = ""
    protocol: str = ""
    dynamic_range: str | None = None
    language: str | None = None

    @property
    def has_video(self) -> bool:
        if self.vcodec is None:
            # codec desconhecido (comum em links diretos): decide pelas dimensoes/extensao
            return bool(self.height or self.width) or self.ext.lower() in VIDEO_EXTS
        return not _is_none_codec(self.vcodec)

    @property
    def has_audio(self) -> bool:
        if self.acodec is None:
            # formato "muxado" sem info de codec: assume que tem audio se nao for so video declarado
            return not self.has_video or self.vcodec is None
        return not _is_none_codec(self.acodec)

    @property
    def is_audio_only(self) -> bool:
        return self.has_audio and not self.has_video

    @property
    def is_lossless_audio(self) -> bool:
        codec = (self.acodec or "").lower()
        return self.has_audio and any(codec.startswith(c) for c in LOSSLESS_ACODECS)

    @property
    def quality_height(self) -> int | None:
        """Resolucao "de rotulo" (menor dimensao): 1080x1920 vertical conta como 1080p."""
        if self.width and self.height:
            return min(self.width, self.height)
        return self.height

    @property
    def resolution_label(self) -> str:
        height = self.quality_height
        if not height:
            return ""
        label = f"{height}p"
        if self.fps and self.fps > 30.5:
            label += f"{round(self.fps)}"
        return label

    @property
    def short_vcodec(self) -> str:
        return short_codec(self.vcodec)

    @property
    def short_acodec(self) -> str:
        return short_codec(self.acodec)


def short_codec(codec: str | None) -> str:
    if not codec or _is_none_codec(codec):
        return ""
    c = codec.lower()
    for prefix, name in (
        ("avc", "H.264"), ("h264", "H.264"), ("hev", "H.265"), ("hvc", "H.265"), ("h265", "H.265"),
        ("vp09", "VP9"), ("vp9", "VP9"), ("vp8", "VP8"), ("av01", "AV1"), ("av1", "AV1"),
        ("mp4a", "AAC"), ("aac", "AAC"), ("opus", "Opus"), ("vorbis", "Vorbis"), ("mp3", "MP3"),
        ("flac", "FLAC"), ("alac", "ALAC"), ("ac-3", "AC-3"), ("ac3", "AC-3"), ("ec-3", "E-AC-3"), ("pcm", "PCM"),
    ):
        if c.startswith(prefix):
            return name
    return codec.split(".")[0]


class MediaEntry(BaseModel):
    """Um item de uma colecao (video de playlist) ou um arquivo de um post/galeria."""

    index: int
    id: str | None = None
    title: str = ""
    url: str | None = None
    thumbnail: str | None = None
    duration: float | None = None
    kind: MediaKind = MediaKind.VIDEO
    ext: str | None = None
    filesize: int | None = None
    width: int | None = None
    height: int | None = None
    author: str | None = None


class MediaInfo(BaseModel):
    source_url: str
    webpage_url: str | None = None
    platform_key: str = "generic"
    platform_name: str = "Site"
    engine: str = "yt-dlp"
    extractor: str | None = None
    content_type: ContentType = ContentType.VIDEO

    id: str | None = None
    title: str = ""
    author: str | None = None
    author_url: str | None = None
    description: str | None = None
    duration: float | None = None
    view_count: int | None = None
    like_count: int | None = None
    upload_date: str | None = None
    timestamp: float | None = None
    thumbnail: str | None = None
    live_status: str | None = None

    formats: list[MediaFormat] = Field(default_factory=list)
    entries: list[MediaEntry] = Field(default_factory=list)
    total_entries: int | None = None
    truncated: bool = False
    # colecao de "filhos" (posts de um perfil, videos de playlist): a
    # selecao manda URLs; senao, arquivos de um post: a selecao manda indices.
    entries_are_urls: bool = False

    direct_size: int | None = None
    direct_mime: str | None = None

    warnings: list[str] = Field(default_factory=list)
    support_note: str | None = None

    @property
    def is_collection(self) -> bool:
        return self.content_type in COLLECTION_TYPES

    @property
    def is_multi_file(self) -> bool:
        return self.content_type in MULTI_FILE_TYPES or (self.is_collection and bool(self.entries))

    @property
    def is_live(self) -> bool:
        return self.live_status == "is_live" or self.content_type == ContentType.LIVE

    @property
    def video_formats(self) -> list[MediaFormat]:
        return [f for f in self.formats if f.has_video]

    @property
    def audio_formats(self) -> list[MediaFormat]:
        return [f for f in self.formats if f.is_audio_only]

    @property
    def has_video(self) -> bool:
        if self.formats:
            return any(f.has_video for f in self.formats)
        if self.entries:
            return any(e.kind == MediaKind.VIDEO for e in self.entries)
        return self.content_type in (ContentType.VIDEO, ContentType.SHORT, ContentType.REEL, ContentType.CLIP, ContentType.LIVE)

    @property
    def has_audio(self) -> bool:
        if self.formats:
            return any(f.has_audio for f in self.formats)
        if self.entries:
            return any(e.kind in (MediaKind.VIDEO, MediaKind.AUDIO) for e in self.entries)
        return self.content_type != ContentType.IMAGE

    @property
    def has_images(self) -> bool:
        return self.content_type == ContentType.IMAGE or any(e.kind == MediaKind.IMAGE for e in self.entries)

    @property
    def has_lossless_audio(self) -> bool:
        return any(f.is_lossless_audio for f in self.formats)

    @property
    def video_heights(self) -> list[int]:
        heights = {f.quality_height for f in self.formats if f.has_video and f.quality_height}
        return sorted(heights, reverse=True)

    @property
    def audio_bitrates(self) -> list[int]:
        rates = {round(f.abr) for f in self.audio_formats if f.abr}
        return sorted(rates, reverse=True)
