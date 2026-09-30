"""Format Selector: o que o usuario escolheu baixar, quais opcoes existem
de verdade para um conteudo e quanto o download deve ocupar.

Regra: so oferecer opcoes realmente disponiveis. As qualidades de video
vem das resolucoes que a plataforma entregou na analise; FLAC so aparece
quando a fonte tem audio sem perdas.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from pydantic import BaseModel

from ..core.config import QualityPreset, Settings
from ..core.i18n import tr
from .metadata import ContentType, MediaFormat, MediaInfo, MediaKind


class SelectionMode(str, Enum):
    VIDEO = "video"
    AUDIO = "audio"
    FILES = "files"


class DownloadSelection(BaseModel):
    mode: SelectionMode = SelectionMode.VIDEO
    quality: str = "best"  # "best" | "original" | "<altura>" (ex. "1080") | "format:<id>"
    container: str = "mp4"  # mp4 | mkv | webm | original
    video_codec: str = "auto"
    audio_format: str = "mp3"  # mp3 | m4a | opus | wav | flac | original
    audio_bitrate: str = "192"  # 320 | 256 | 192 | 128 | original
    image_format: str = "original"  # original | jpg | png | webp
    items: list[int] | None = None  # indices (1-based) de itens/arquivos; None = todos
    item_urls: list[str] | None = None  # colecoes de "filhos" (posts de um perfil)
    save_post_text: bool = True
    embed_metadata: bool = True

    def quality_label(self) -> str:
        if self.mode == SelectionMode.AUDIO:
            fmt = self.audio_format.upper() if self.audio_format != "original" else "Original"
            rate = f" {self.audio_bitrate} kbps" if self.audio_bitrate != "original" and self.audio_format not in ("wav", "flac", "original") else ""
            return f"{fmt}{rate}"
        if self.mode == SelectionMode.FILES:
            return "Original" if self.image_format == "original" else self.image_format.upper()
        if self.quality.startswith("format:"):
            return self.quality.split(":", 1)[1]
        if self.quality == "best":
            return "Melhor"
        if self.quality == "original":
            return "Original"
        return f"{self.quality}p"

    def format_label(self) -> str:
        if self.mode == SelectionMode.AUDIO:
            return self.audio_format
        if self.mode == SelectionMode.FILES:
            return self.image_format
        return self.container


@dataclass
class Choice:
    key: str
    label: str
    size: int | None = None
    detail: str = ""


# ---------------------------------------------------------------- tamanhos

def format_size(fmt: MediaFormat, duration: float | None) -> int | None:
    if fmt.filesize:
        return fmt.filesize
    if fmt.tbr and duration:
        return int(fmt.tbr * 1000 / 8 * duration)
    return None


def _best_audio(info: MediaInfo) -> MediaFormat | None:
    audio = info.audio_formats
    if not audio:
        return None
    return max(audio, key=lambda f: (f.abr or f.tbr or 0, f.filesize or 0))


def _best_video(info: MediaInfo, max_height: int | None) -> MediaFormat | None:
    videos = info.video_formats
    if max_height:
        within = [f for f in videos if (f.quality_height or 0) <= max_height]
        videos = within or videos
    if not videos:
        return None
    return max(videos, key=lambda f: (f.quality_height or 0, f.fps or 0, f.tbr or 0, f.filesize or 0))


def estimate_video_size(info: MediaInfo, max_height: int | None) -> int | None:
    video = _best_video(info, max_height)
    if video is None:
        return None
    size = format_size(video, info.duration)
    if size is None:
        return None
    if not video.has_audio:
        audio = _best_audio(info)
        audio_size = format_size(audio, info.duration) if audio else None
        if audio_size:
            size += audio_size
    return size


def estimate_audio_size(info: MediaInfo, audio_format: str, bitrate: str) -> int | None:
    duration = info.duration
    if audio_format in ("mp3", "m4a", "opus") and bitrate != "original" and duration:
        return int(int(bitrate) * 1000 / 8 * duration)
    if audio_format == "wav" and duration:
        return int(44100 * 2 * 2 * duration)  # PCM 16 bits estereo
    audio = _best_audio(info)
    if audio is None:
        return None
    return format_size(audio, duration)


def estimate_size(info: MediaInfo, selection: DownloadSelection) -> int | None:
    if info.direct_size:
        return info.direct_size
    if info.entries:
        chosen = [e for e in info.entries if selection.items is None or e.index in selection.items]
        sizes = [e.filesize for e in chosen]
        if chosen and all(sizes):
            return sum(s for s in sizes if s)
        return None
    if selection.mode == SelectionMode.AUDIO:
        return estimate_audio_size(info, selection.audio_format, selection.audio_bitrate)
    if selection.quality.startswith("format:"):
        ids = selection.quality.split(":", 1)[1].split("+")
        total = 0
        for fid in ids:
            fmt = next((f for f in info.formats if f.format_id == fid), None)
            size = format_size(fmt, info.duration) if fmt else None
            if size is None:
                return None
            total += size
        return total
    height = int(selection.quality) if selection.quality.isdigit() else None
    return estimate_video_size(info, height)


# ---------------------------------------------------------------- opcoes

def mode_choices(info: MediaInfo) -> list[Choice]:
    """Tipos de download possiveis ("Tipo:" na interface)."""
    choices: list[Choice] = []
    if info.engine == "yt-dlp":
        if info.has_video:
            choices.append(Choice(SelectionMode.VIDEO.value, "Vídeo"))
        if info.has_audio:
            choices.append(Choice(SelectionMode.AUDIO.value, "Apenas áudio" if info.has_video else "Áudio"))
    elif info.engine == "direct":
        if info.content_type == ContentType.VIDEO:
            choices.append(Choice(SelectionMode.VIDEO.value, "Vídeo"))
            choices.append(Choice(SelectionMode.AUDIO.value, "Apenas áudio"))
        elif info.content_type == ContentType.AUDIO:
            choices.append(Choice(SelectionMode.AUDIO.value, "Áudio"))
        else:
            choices.append(Choice(SelectionMode.FILES.value, "Imagem"))
    else:
        label = "Imagem" if info.content_type == ContentType.IMAGE else "Arquivos"
        if info.entries_are_urls:
            label = "Itens da coleção"
        choices.append(Choice(SelectionMode.FILES.value, label))
    if not choices:
        choices.append(Choice(SelectionMode.FILES.value, "Arquivo original"))
    return choices


def quality_choices(info: MediaInfo) -> list[Choice]:
    """Qualidades de video realmente oferecidas pela plataforma."""
    if info.engine == "yt-dlp" and not info.formats and info.entries:
        # playlist/canal: os formatos de cada item so sao conhecidos na hora do
        # download, entao oferecemos os presets de PRIORIDADE (o yt-dlp escolhe
        # a resolucao mais proxima disponivel em cada video)
        return [
            Choice("best", "Melhor qualidade"),
            Choice("1080", "Até 1080p (prioridade)"),
            Choice("720", "Até 720p (prioridade)"),
            Choice("480", "Até 480p (prioridade)"),
            Choice("original", "Original (sem conversão)"),
        ]
    if info.engine != "yt-dlp" or not info.formats:
        return [Choice("best", "Melhor qualidade")]
    choices = [Choice("best", "Melhor qualidade", estimate_video_size(info, None))]
    for height in info.video_heights:
        label = f"{height}p"
        if height >= 2160:
            label += " (4K)"
        elif height >= 1440:
            label += " (2K)"
        choices.append(Choice(str(height), label, estimate_video_size(info, height)))
    choices.append(Choice("original", "Original (sem conversão)", estimate_video_size(info, None)))
    return choices


def container_choices(info: MediaInfo) -> list[Choice]:
    return [
        Choice("mp4", "MP4", detail="Compatível com quase tudo"),
        Choice("mkv", "MKV", detail="Aceita qualquer codec, sem perda"),
        Choice("webm", "WEBM", detail="Pode exigir conversão"),
        Choice("original", "Original", detail="Formato entregue pela plataforma"),
    ]


def audio_format_choices(info: MediaInfo) -> list[Choice]:
    choices = [
        Choice("mp3", "MP3"),
        Choice("m4a", "M4A (AAC)"),
        Choice("opus", "OPUS"),
        Choice("wav", "WAV (sem compressão)"),
    ]
    # FLAC so faz sentido se a fonte tiver audio sem perdas (converter
    # audio com perdas para FLAC so aumenta o arquivo, sem ganho).
    if info.has_lossless_audio:
        choices.append(Choice("flac", "FLAC (sem perdas)"))
    choices.append(Choice("original", "Original (sem conversão)"))
    return choices


def bitrate_choices(info: MediaInfo) -> list[Choice]:
    source = info.audio_bitrates
    best = source[0] if source else None
    detail = tr("fonte: até {n} kbps", n=best) if best else ""
    return [
        Choice("320", "320 kbps", detail=detail),
        Choice("256", "256 kbps", detail=detail),
        Choice("192", "192 kbps", detail=detail),
        Choice("128", "128 kbps", detail=detail),
        Choice("original", "Melhor possível (VBR)", detail=detail),
    ]


def image_format_choices() -> list[Choice]:
    return [
        Choice("original", "Original"),
        Choice("jpg", "JPG"),
        Choice("png", "PNG"),
        Choice("webp", "WEBP"),
    ]


def nearest_height(target: int, available: list[int]) -> int | None:
    if not available:
        return None
    within = [h for h in available if h <= target]
    return max(within) if within else min(available)


def selection_from_settings(
    settings: Settings, info: MediaInfo | None = None, preset: QualityPreset | None = None
) -> DownloadSelection:
    """Selecao inicial a partir das configuracoes (e, se houver, da analise)."""
    preset = preset or settings.default_quality
    selection = DownloadSelection(
        container=settings.default_video_format,
        video_codec=settings.preferred_video_codec,
        audio_format=settings.default_audio_format,
        audio_bitrate=settings.default_audio_bitrate,
        image_format=settings.default_image_format,
        save_post_text=settings.save_post_text,
        embed_metadata=settings.embed_metadata,
    )
    if preset == QualityPreset.AUDIO:
        selection.mode = SelectionMode.AUDIO
    elif preset == QualityPreset.ORIGINAL:
        selection.quality = "original"
        selection.container = "original"
    elif preset in (QualityPreset.P1080, QualityPreset.P720, QualityPreset.P480):
        target = int(preset.value.rstrip("p"))
        if info is not None and info.video_heights:
            selection.quality = str(nearest_height(target, info.video_heights))
        else:
            selection.quality = str(target)

    if info is not None:
        modes = [c.key for c in mode_choices(info)]
        if selection.mode.value not in modes:
            selection.mode = SelectionMode(modes[0])
        if selection.audio_format == "flac" and not info.has_lossless_audio:
            selection.audio_format = "original"
        if selection.mode == SelectionMode.FILES and info.entries and all(e.kind == MediaKind.VIDEO for e in info.entries):
            selection.container = "original"
    return selection
