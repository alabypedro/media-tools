"""Base dos providers.

Um provider representa uma PLATAFORMA (YouTube, Instagram...): sabe
reconhecer suas URLs, qual engine tentar primeiro para cada tipo de
link e como rotular o conteudo. Quem conversa com as engines de verdade
sao os adaptadores (yt_dlp_provider, gallery_dl_provider,
generic_provider.DirectAdapter); nenhum codigo especifico de plataforma
fica espalhado pelo resto do app.

O suporte nunca e presumido: se nenhuma engine reconhecer a URL, o
resultado e "Esta plataforma/conteudo nao e suportado atualmente".
"""

from __future__ import annotations

import threading
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qs, urlsplit

from ..core.config import Settings
from ..core.exceptions import ErrorCode, UMDError
from ..core.i18n import tr
from ..core.logger import get_logger
from ..media.metadata import ContentType, MediaInfo

if TYPE_CHECKING:
    from ..engine.runner import EngineRunner
    from ..media.formats import DownloadSelection

log = get_logger("providers")

YTDLP = "yt-dlp"
GALLERYDL = "gallery-dl"
DIRECT = "direct"


@dataclass
class EngineContext:
    """Tudo que um adaptador precisa para falar com o worker."""

    runner: "EngineRunner"
    settings: Settings
    ffmpeg: str | None = None
    js_runtimes: dict[str, str] = field(default_factory=dict)
    cancel_event: threading.Event | None = None


class EngineAdapter(ABC):
    name: str = ""

    @abstractmethod
    def analyze(self, url: str, ctx: EngineContext) -> MediaInfo: ...

    @abstractmethod
    def build_download(
        self,
        url: str,
        selection: "DownloadSelection",
        ctx: EngineContext,
        staging_dir: Path,
        *,
        expected_items: int | None = None,
        is_collection: bool = False,
        is_live: bool = False,
    ) -> dict[str, Any]:
        """Monta a requisicao de download para o worker (JSON)."""


class MediaProvider:
    key = "generic"
    name = "Site"
    domains: tuple[str, ...] = ()
    engines: tuple[str, ...] = (YTDLP, GALLERYDL, DIRECT)

    def matches(self, host: str) -> bool:
        host = host.lower().rstrip(".")
        if host.startswith("www."):
            host = host[4:]
        return any(host == domain or host.endswith("." + domain) for domain in self.domains)

    def engine_order(self, url: str) -> list[str]:
        return list(self.engines)

    def classify(self, url: str) -> ContentType | None:
        """Tipo de conteudo sugerido so pela URL (ex.: /shorts/ -> Short)."""
        return None

    # ------------------------------------------------------------ analise

    def analyze(self, url: str, ctx: EngineContext) -> MediaInfo:
        from .registry import adapter_for

        last_error: UMDError | None = None
        tried: list[str] = []
        for engine in self.engine_order(url):
            if ctx.cancel_event is not None and ctx.cancel_event.is_set():
                raise UMDError(ErrorCode.CANCELLED)
            tried.append(engine)
            try:
                info = adapter_for(engine).analyze(url, ctx)
            except UMDError as exc:
                log.info("analyze %s via %s: %s", self.key, engine, exc.code.value)
                if exc.fallthrough:
                    last_error = exc if last_error is None or last_error.code == ErrorCode.UNSUPPORTED else last_error
                    continue
                raise
            return self.finalize(info, url)

        if last_error is not None and last_error.code == ErrorCode.NO_MEDIA:
            raise last_error
        raise UMDError(
            ErrorCode.UNSUPPORTED,
            details=f"Engines tentadas para {self.name}: {', '.join(tried)}\n{last_error.details if last_error else ''}",
        )

    def finalize(self, info: MediaInfo, url: str) -> MediaInfo:
        info.platform_key = self.key
        if self.key != "generic":
            info.platform_name = self.name
        hinted = self.classify(url)
        if hinted is not None and self._compatible(hinted, info):
            info.content_type = hinted
        if self.key != "generic" and (info.extractor or "").lower() == "generic":
            info.support_note = tr(
                "Sem extrator dedicado para {name}: foi usado o extrator genérico, e o resultado pode ser incompleto.",
                name=self.name,
            )
        return info

    @staticmethod
    def _compatible(hinted: ContentType, info: MediaInfo) -> bool:
        """So aceita a dica da URL se ela nao contradizer o que a engine achou."""
        from ..media.metadata import COLLECTION_TYPES

        if info.content_type == ContentType.LIVE:
            return False
        if hinted in COLLECTION_TYPES:
            return info.is_collection or bool(info.entries)
        if info.is_collection:
            return False
        if hinted in (ContentType.SHORT, ContentType.REEL, ContentType.CLIP):
            return info.has_video and not info.entries
        return True


# ------------------------------------------------------------ helpers de URL

def url_path(url: str) -> str:
    try:
        return urlsplit(url).path.lower()
    except ValueError:
        return ""


def url_query(url: str) -> dict[str, list[str]]:
    try:
        return parse_qs(urlsplit(url).query)
    except ValueError:
        return {}
