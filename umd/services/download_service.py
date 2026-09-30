"""Monta pedidos de download (a partir da analise ou direto da URL, em
lote) e entrega ao DownloadManager."""

from __future__ import annotations

from pathlib import Path

from ..core.config import QualityPreset, Settings, SettingsStore
from ..core.exceptions import ErrorCode, UMDError
from ..core.naming import validate_template
from ..core.security import validate_url
from ..database.models import DownloadRecord
from ..downloader.job import DownloadJob, DownloadRequest
from ..downloader.manager import DownloadManager
from ..media.formats import DownloadSelection, SelectionMode, selection_from_settings
from ..media.metadata import COLLECTION_TYPES, MediaInfo
from ..providers.base import YTDLP
from ..providers.registry import detect_provider


class DownloadService:
    def __init__(self, manager: DownloadManager, settings_store: SettingsStore):
        self.manager = manager
        self._store = settings_store

    @property
    def settings(self) -> Settings:
        return self._store.settings

    def _output_dir(self, output_dir: str | Path | None) -> str:
        target = Path(output_dir) if output_dir else Path(self.settings.download_dir)
        try:
            target.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise UMDError(ErrorCode.PERMISSION, details=f"{target}: {exc}") from None
        return str(target.resolve())

    def request_from_info(
        self, info: MediaInfo, selection: DownloadSelection, output_dir: str | Path | None = None
    ) -> DownloadRequest:
        settings = self.settings
        empty_items = selection.items is not None and not selection.items
        empty_urls = selection.item_urls is not None and not selection.item_urls
        if (empty_items or empty_urls) and not (selection.items or selection.item_urls):
            # selecao explicitamente vazia nunca pode virar "baixar tudo"
            raise UMDError(ErrorCode.NO_MEDIA, "Selecione pelo menos um item para baixar.", reasons=[])
        if info.entries_are_urls:
            # itens sao POSTS (cada um pode ter varios arquivos): o total de arquivos so e conhecido no download
            expected = None
        elif selection.items:
            expected = len(selection.items)
        elif info.entries:
            expected = len(info.entries)
        else:
            expected = None
        return DownloadRequest(
            url=info.source_url,
            provider_key=info.platform_key,
            platform_name=info.platform_name,
            engines=[info.engine],
            selection=selection,
            output_dir=self._output_dir(output_dir),
            filename_template=validate_template(settings.filename_template),
            create_subfolders=settings.create_subfolders,
            title=info.title or None,
            author=info.author,
            thumbnail=info.thumbnail,
            content_type=info.content_type.value,
            upload_date=info.upload_date,
            media_id=info.id,
            description=(info.description or "")[:10000] or None,
            duration=info.duration,
            expected_items=expected,
            is_collection=info.is_collection or (info.engine == YTDLP and bool(info.entries)),
            is_live=info.is_live,
        )

    def quick_request(
        self,
        raw_url: str,
        *,
        preset: QualityPreset | None = None,
        output_dir: str | Path | None = None,
        audio_format: str | None = None,
        container: str | None = None,
    ) -> DownloadRequest:
        """Pedido direto da URL, sem analise previa (lote, CLI)."""
        settings = self.settings
        url = validate_url(raw_url, allow_local=settings.allow_local_network_urls)
        provider = detect_provider(url)
        selection = selection_from_settings(settings, None, preset)
        if audio_format:
            selection.mode = SelectionMode.AUDIO
            selection.audio_format = audio_format
        if container:
            selection.container = container
        hinted = provider.classify(url)
        return DownloadRequest(
            url=url,
            provider_key=provider.key,
            platform_name=provider.name if provider.key != "generic" else "Site",
            engines=provider.engine_order(url),
            selection=selection,
            output_dir=self._output_dir(output_dir),
            filename_template=validate_template(settings.filename_template),
            create_subfolders=settings.create_subfolders,
            content_type=hinted.value if hinted else None,
            is_collection=hinted in COLLECTION_TYPES if hinted else False,
        )

    def submit(self, request: DownloadRequest) -> DownloadJob:
        return self.manager.enqueue(request)

    def redownload(self, record: DownloadRecord) -> DownloadJob:
        """'Baixar novamente' do historico: mesmo pedido, job novo."""
        if record.request_json:
            try:
                request = DownloadRequest.model_validate_json(record.request_json)
            except ValueError:
                request = self.quick_request(record.url)
            else:
                request.output_dir = self._output_dir(request.output_dir)
        else:
            request = self.quick_request(record.url)
        return self.submit(request)
