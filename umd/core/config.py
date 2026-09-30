"""Configuracoes do aplicativo (Pydantic), gravadas em settings.json.

Nada sensivel e guardado aqui: para login, guardamos so QUAL navegador
(ou qual arquivo cookies.txt) usar -- nunca senhas nem o conteudo dos
cookies.

O carregamento e tolerante: um campo invalido (ex.: editado a mao) volta
ao valor padrao em vez de invalidar o arquivo inteiro.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from . import paths
from .naming import DEFAULT_TEMPLATE, validate_template


class QualityPreset(str, Enum):
    BEST = "best"
    P1080 = "1080p"
    P720 = "720p"
    P480 = "480p"
    AUDIO = "audio"
    ORIGINAL = "original"


QUALITY_PRESET_LABELS = {
    QualityPreset.BEST: "Melhor qualidade",
    QualityPreset.P1080: "1080p",
    QualityPreset.P720: "720p",
    QualityPreset.P480: "480p",
    QualityPreset.AUDIO: "Apenas áudio",
    QualityPreset.ORIGINAL: "Original",
}

VIDEO_CONTAINERS = ("mp4", "mkv", "webm", "original")
VIDEO_CODECS = ("auto", "h264", "vp9", "av1")
AUDIO_FORMATS = ("mp3", "m4a", "opus", "wav", "flac", "original")
AUDIO_BITRATES = ("320", "256", "192", "128", "original")
IMAGE_FORMATS = ("original", "jpg", "png", "webp")
COOKIE_BROWSERS = ("", "firefox", "chrome", "edge", "brave", "opera", "vivaldi", "chromium", "safari")
THEMES = ("system", "light", "dark")


class Settings(BaseModel):
    model_config = ConfigDict(extra="ignore", validate_assignment=True)

    # --- Downloads
    download_dir: str = Field(default_factory=lambda: str(paths.default_download_dir()))
    ask_folder_each_time: bool = False
    create_subfolders: bool = True
    filename_template: str = DEFAULT_TEMPLATE
    max_concurrent_downloads: int = Field(3, ge=1, le=10)
    save_post_text: bool = True

    # --- Video
    default_quality: QualityPreset = QualityPreset.BEST
    default_video_format: str = "mp4"
    preferred_video_codec: str = "auto"

    # --- Audio
    default_audio_format: str = "mp3"
    default_audio_bitrate: str = "192"
    embed_metadata: bool = True

    # --- Imagens
    default_image_format: str = "original"

    # --- Desempenho
    rate_limit_kbps: int = Field(0, ge=0, le=10_000_000)  # 0 = sem limite (por download)
    concurrent_fragments: int = Field(1, ge=1, le=16)
    retries: int = Field(3, ge=0, le=20)
    retry_delay_seconds: int = Field(5, ge=0, le=600)
    download_interval_seconds: float = Field(0.0, ge=0, le=3600)
    resume_partial: bool = True
    max_file_size_mb: int = Field(0, ge=0, le=1_000_000)  # 0 = sem limite
    analysis_timeout_seconds: int = Field(120, ge=10, le=900)
    socket_timeout_seconds: int = Field(30, ge=5, le=300)
    max_playlist_items: int = Field(500, ge=1, le=5000)

    # --- Interface
    theme: str = "system"
    language: str = "pt_BR"
    library_view: str = "grid"

    # --- Contas e cookies (so a origem; nunca o conteudo)
    cookies_browser: str = ""
    cookies_browser_profile: str = ""
    cookies_file: str = ""

    # --- Engines / avancado
    ffmpeg_path: str = ""
    allow_local_network_urls: bool = False
    check_updates_on_start: bool = False  # engines (yt-dlp/gallery-dl)
    check_app_updates_on_start: bool = True  # o proprio programa (GitHub Releases); so avisa

    @field_validator("filename_template")
    @classmethod
    def _check_template(cls, value: str) -> str:
        return validate_template(value)

    @field_validator("download_dir", "cookies_file", "ffmpeg_path")
    @classmethod
    def _normalize_path(cls, value: str, info) -> str:
        value = (value or "").strip().strip('"')
        if len(value) > 1000 or "\0" in value:
            raise ValueError("Caminho inválido.")
        if not value:
            return str(paths.default_download_dir()) if info.field_name == "download_dir" else ""
        return str(Path(value).expanduser())

    @field_validator("default_video_format")
    @classmethod
    def _check_container(cls, value: str) -> str:
        return _one_of(value, VIDEO_CONTAINERS)

    @field_validator("preferred_video_codec")
    @classmethod
    def _check_codec(cls, value: str) -> str:
        return _one_of(value, VIDEO_CODECS)

    @field_validator("default_audio_format")
    @classmethod
    def _check_audio(cls, value: str) -> str:
        return _one_of(value, AUDIO_FORMATS)

    @field_validator("default_audio_bitrate")
    @classmethod
    def _check_bitrate(cls, value: str) -> str:
        return _one_of(str(value), AUDIO_BITRATES)

    @field_validator("default_image_format")
    @classmethod
    def _check_image(cls, value: str) -> str:
        return _one_of(value, IMAGE_FORMATS)

    @field_validator("cookies_browser")
    @classmethod
    def _check_browser(cls, value: str) -> str:
        return _one_of(value, COOKIE_BROWSERS)

    @field_validator("theme")
    @classmethod
    def _check_theme(cls, value: str) -> str:
        return _one_of(value, THEMES)

    @field_validator("language")
    @classmethod
    def _check_language(cls, value: str) -> str:
        from .i18n import LANGUAGES

        return _one_of(value, tuple(LANGUAGES))

    @field_validator("library_view")
    @classmethod
    def _check_view(cls, value: str) -> str:
        return _one_of(value, ("grid", "list"))

    @field_validator("cookies_browser_profile")
    @classmethod
    def _check_profile(cls, value: str) -> str:
        value = value.strip()
        if len(value) > 200 or any(ch in value for ch in "\r\n\0"):
            raise ValueError("Nome de perfil inválido.")
        return value

    @classmethod
    def load_tolerant(cls, data: dict[str, Any]) -> "Settings":
        data = dict(data) if isinstance(data, dict) else {}
        for _ in range(len(data) + 1):
            try:
                return cls(**data)
            except ValidationError as exc:
                bad = {err["loc"][0] for err in exc.errors() if err.get("loc")}
                if not bad or not (bad & data.keys()):
                    break
                for key in bad:
                    data.pop(key, None)
        return cls()


def _one_of(value: str, allowed: tuple[str, ...]) -> str:
    if value in allowed:
        return value
    normalized = (value or "").strip().lower()
    if normalized in allowed:
        return normalized
    raise ValueError(f"Valor inválido: {value!r}. Opções: {', '.join(a or '(nenhum)' for a in allowed)}")


class SettingsStore:
    """Guarda a configuracao atual, grava em disco e avisa quem assinou."""

    def __init__(self, path: Path | None = None):
        self.path = path or paths.settings_path()
        self._lock = threading.RLock()
        self._listeners: list[Callable[[Settings], None]] = []
        self._settings = self._load()

    def _load(self) -> Settings:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return Settings()
        except (OSError, json.JSONDecodeError):
            return Settings()
        return Settings.load_tolerant(data)

    @property
    def settings(self) -> Settings:
        with self._lock:
            return self._settings.model_copy()

    def save(self, settings: Settings) -> None:
        with self._lock:
            self._settings = settings.model_copy()
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".json.tmp")
            tmp.write_text(settings.model_dump_json(indent=2), encoding="utf-8")
            os.replace(tmp, self.path)  # troca atomica: nunca deixa o arquivo pela metade
            listeners = list(self._listeners)
        for listener in listeners:
            listener(settings.model_copy())

    def update(self, **changes: Any) -> Settings:
        with self._lock:
            new = Settings(**{**self._settings.model_dump(), **changes})
        self.save(new)
        return new

    def apply_temporary(self, **changes: Any) -> Settings:
        """Muda a configuracao so em memoria (ex.: opcoes da linha de comando), sem gravar."""
        with self._lock:
            self._settings = Settings(**{**self._settings.model_dump(), **changes})
            current = self._settings.model_copy()
            listeners = list(self._listeners)
        for listener in listeners:
            listener(current.model_copy())
        return current

    def subscribe(self, listener: Callable[[Settings], None]) -> None:
        with self._lock:
            self._listeners.append(listener)
