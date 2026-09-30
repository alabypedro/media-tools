"""Generic URL Downloader: sites que nao estao cadastrados.

Ordem de tentativa:
* link que termina em extensao de midia (.mp4, .jpg...) -> download direto;
* manifesto HLS/DASH (.m3u8/.mpd)                       -> yt-dlp;
* qualquer outra pagina -> yt-dlp (1800+ sites + extrator generico que
  procura <video>, og:video, embeds...) -> gallery-dl -> download direto.

Se nada reconhecer, o usuario recebe "Esta plataforma/conteudo nao e
suportado atualmente" -- sem inventar suporte.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.exceptions import ErrorCode, UMDError
from ..core.security import host_of
from ..media.formats import DownloadSelection
from ..media.metadata import AUDIO_EXTS, IMAGE_EXTS, VIDEO_EXTS, ContentType, MediaInfo, ext_from_url
from .base import DIRECT, GALLERYDL, YTDLP, EngineAdapter, EngineContext, MediaProvider

_KIND_TO_TYPE = {"video": ContentType.VIDEO, "audio": ContentType.AUDIO, "image": ContentType.IMAGE}


class DirectAdapter(EngineAdapter):
    name = DIRECT

    def analyze(self, url: str, ctx: EngineContext) -> MediaInfo:
        request = {
            "action": "analyze",
            "engine": DIRECT,
            "url": url,
            "timeout": ctx.settings.socket_timeout_seconds,
            "allow_local": ctx.settings.allow_local_network_urls,
        }
        result = ctx.runner.run(request, cancel_event=ctx.cancel_event, timeout=ctx.settings.analysis_timeout_seconds)
        data = result.result
        if not isinstance(data, dict):
            raise UMDError(ErrorCode.NO_MEDIA, details="direct probe returned no data")
        return parse_probe(data, url)

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
        return {
            "action": "download",
            "engine": DIRECT,
            "url": url,
            "staging_dir": str(staging_dir),
            "timeout": settings.socket_timeout_seconds,
            "max_size": settings.max_file_size_mb * 1024 * 1024,
            "rate_limit": settings.rate_limit_kbps * 1024,
            "resume": settings.resume_partial,
            "allow_local": settings.allow_local_network_urls,
        }


def parse_probe(data: dict[str, Any], url: str) -> MediaInfo:
    content_type = _KIND_TO_TYPE.get(str(data.get("kind")), ContentType.VIDEO)
    return MediaInfo(
        source_url=url,
        webpage_url=data.get("final_url") or url,
        engine=DIRECT,
        extractor="direct",
        title=str(data.get("stem") or "arquivo"),
        author=host_of(data.get("final_url") or url) or None,  # link direto: o "autor" e o site
        content_type=content_type,
        direct_size=data.get("size"),
        direct_mime=data.get("mime"),
        thumbnail=(data.get("final_url") or url) if content_type == ContentType.IMAGE else None,
        formats=[],
        warnings=[],
    )


class GenericProvider(MediaProvider):
    key = "generic"
    name = "Site"
    domains = ()

    def matches(self, host: str) -> bool:
        return True

    def engine_order(self, url: str) -> list[str]:
        ext = ext_from_url(url)
        if ext in ("m3u8", "mpd"):
            return [YTDLP]
        if ext in VIDEO_EXTS | AUDIO_EXTS | IMAGE_EXTS:
            return [DIRECT, YTDLP, GALLERYDL]
        return [YTDLP, GALLERYDL, DIRECT]

    def analyze(self, url: str, ctx: EngineContext) -> MediaInfo:
        """Extrator DEDICADO ganha do generico.

        O yt-dlp sempre "tenta" qualquer pagina com o extrator generico; se
        foi so isso que funcionou, o gallery-dl pode ter um extrator proprio
        para o site (ex.: galerias de imagens) e da um resultado melhor.
        """
        from .registry import adapter_for

        if self.engine_order(url)[0] != YTDLP:
            return super().analyze(url, ctx)

        generic_result: MediaInfo | None = None
        try:
            generic_result = adapter_for(YTDLP).analyze(url, ctx)
        except UMDError as exc:
            if not exc.fallthrough:
                raise
        if generic_result is not None and (generic_result.extractor or "").lower() != "generic":
            return self.finalize(generic_result, url)

        try:
            return self.finalize(adapter_for(GALLERYDL).analyze(url, ctx), url)
        except UMDError as exc:
            if generic_result is not None:
                return self.finalize(generic_result, url)
            if not exc.fallthrough:
                raise
        try:
            return self.finalize(adapter_for(DIRECT).analyze(url, ctx), url)
        except UMDError as exc:
            if not exc.fallthrough:
                raise
        raise UMDError(ErrorCode.UNSUPPORTED, details="yt-dlp, gallery-dl e download direto não reconheceram a URL")

    def finalize(self, info: MediaInfo, url: str) -> MediaInfo:
        info = super().finalize(info, url)
        extractor = info.extractor or ""
        if info.engine == YTDLP and extractor and extractor.lower() != "generic":
            # site sem provider proprio, mas com extrator dedicado no yt-dlp
            info.platform_name = extractor
        elif info.engine == GALLERYDL and info.platform_name in ("", "Site"):
            info.platform_name = extractor or "Site"
        elif info.engine == DIRECT:
            info.platform_name = "Link direto"
        return info
