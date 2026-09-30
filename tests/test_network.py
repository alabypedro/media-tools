"""Testes com a internet de verdade (opcionais): pytest --run-network

Usam conteudo publico e estavel (o primeiro video do YouTube, arquivos de
exemplo da Wikimedia). Podem falhar se a plataforma mudar -- e justamente
para isso existem: avisam quando as engines precisam de atualizacao.
"""

from __future__ import annotations

import pytest

from umd.downloader.job import JobStatus
from umd.media.formats import SelectionMode, selection_from_settings
from umd.media.metadata import ContentType

pytestmark = pytest.mark.network


@pytest.fixture
def ctx(tmp_path):
    from umd.services.context import create_context

    context = create_context(restore_jobs=False)
    context.settings_store.update(download_dir=str(tmp_path / "downloads"))
    yield context
    context.close()


def _download(ctx, info, selection):
    job = ctx.download.submit(ctx.download.request_from_info(info, selection))
    assert ctx.manager.wait_all(timeout=300)
    return ctx.manager.get(job.id)


def test_youtube_video_and_audio(ctx) -> None:
    info = ctx.media.analyze("https://www.youtube.com/watch?v=jNQXAC9IVRw")
    assert info.title == "Me at the zoo" and info.video_heights
    snap = _download(ctx, info, selection_from_settings(ctx.settings_store.settings, info))
    assert snap.status == JobStatus.COMPLETED and snap.files[0].endswith(".mp4")
    selection = selection_from_settings(ctx.settings_store.settings, info)
    selection.mode = SelectionMode.AUDIO
    snap = _download(ctx, info, selection)
    assert snap.status == JobStatus.COMPLETED and snap.files[0].endswith(".mp3")


def test_youtube_playlist_selection(ctx) -> None:
    info = ctx.media.analyze("https://www.youtube.com/playlist?list=PL6B3937A5D230E335")
    assert info.content_type == ContentType.PLAYLIST and len(info.entries) > 3


def test_wikimedia_via_gallery_dl(ctx) -> None:
    info = ctx.media.analyze("https://commons.wikimedia.org/wiki/File:Example.jpg")
    assert info.engine == "gallery-dl"


def test_unsupported_page(ctx) -> None:
    from umd.core.exceptions import ErrorCode, UMDError

    with pytest.raises(UMDError) as error:
        ctx.media.analyze("https://example.com/")
    assert error.value.code in (ErrorCode.UNSUPPORTED, ErrorCode.NO_MEDIA)
