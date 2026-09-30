"""Analise de URL: validar -> detectar plataforma -> provider -> metadados."""

from __future__ import annotations

import threading
from collections.abc import Callable

from ..core.config import SettingsStore
from ..core.logger import get_logger
from ..core.security import validate_url
from ..engine.runner import EngineRunner
from ..media.metadata import MediaInfo
from ..providers.base import EngineContext, MediaProvider
from ..providers.registry import detect_provider

log = get_logger("media")


class MediaService:
    def __init__(
        self,
        runner: EngineRunner,
        settings_store: SettingsStore,
        *,
        ffmpeg_resolver: Callable[[], str | None] = lambda: None,
        js_runtimes: dict[str, str] | None = None,
    ):
        self._runner = runner
        self._store = settings_store
        self._ffmpeg = ffmpeg_resolver
        self._js_runtimes = js_runtimes or {}

    def detect(self, raw_url: str) -> tuple[str, MediaProvider]:
        settings = self._store.settings
        url = validate_url(raw_url, allow_local=settings.allow_local_network_urls)
        return url, detect_provider(url)

    def analyze(self, raw_url: str, cancel_event: threading.Event | None = None) -> MediaInfo:
        url, provider = self.detect(raw_url)
        settings = self._store.settings
        ctx = EngineContext(self._runner, settings, self._ffmpeg(), self._js_runtimes, cancel_event)
        log.info("analyze %s via provider=%s", url, provider.key)
        info = provider.analyze(url, ctx)
        log.info("analyzed %s: engine=%s type=%s entries=%d formats=%d", provider.key, info.engine,
                 info.content_type.value, len(info.entries), len(info.formats))
        return info
