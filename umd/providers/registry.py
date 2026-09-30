"""Platform Detector: URL -> provider, e engine -> adaptador."""

from __future__ import annotations

from functools import lru_cache

from ..core.security import host_of
from .base import DIRECT, GALLERYDL, YTDLP, EngineAdapter, MediaProvider
from .generic_provider import DirectAdapter, GenericProvider
from .platforms import PLATFORM_PROVIDERS


@lru_cache(maxsize=1)
def _providers() -> tuple[MediaProvider, ...]:
    return tuple(cls() for cls in PLATFORM_PROVIDERS)


_GENERIC = GenericProvider()


def all_providers() -> tuple[MediaProvider, ...]:
    return _providers()


def detect_provider(url: str) -> MediaProvider:
    host = host_of(url)
    for provider in _providers():
        if provider.matches(host):
            return provider
    return _GENERIC


def provider_by_key(key: str | None) -> MediaProvider:
    for provider in _providers():
        if provider.key == key:
            return provider
    return _GENERIC


@lru_cache(maxsize=None)
def adapter_for(engine: str) -> EngineAdapter:
    if engine == YTDLP:
        from .yt_dlp_provider import YtDlpAdapter

        return YtDlpAdapter()
    if engine == GALLERYDL:
        from .gallery_dl_provider import GalleryDlAdapter

        return GalleryDlAdapter()
    if engine == DIRECT:
        return DirectAdapter()
    raise ValueError(f"Engine desconhecida: {engine}")
