"""Interface (pytest-qt, tela offscreen) com servicos reais e worker falso."""

from __future__ import annotations

import pytest

pytest.importorskip("pytestqt")

from umd.core.exceptions import ErrorCode, UMDError  # noqa: E402
from umd.downloader.job import JobStatus  # noqa: E402
from umd.media.formats import SelectionMode  # noqa: E402
from umd.media.metadata import ContentType, MediaEntry, MediaFormat, MediaInfo, MediaKind  # noqa: E402


@pytest.fixture
def ctx(tmp_path):
    from umd.services.context import create_context

    context = create_context()
    context.settings_store.update(download_dir=str(tmp_path / "downloads"))
    yield context
    context.close()


@pytest.fixture
def window(qtbot, ctx):
    from umd.ui.main_window import MainWindow
    from umd.ui.theme import apply_theme
    from PySide6.QtWidgets import QApplication

    apply_theme(QApplication.instance(), "light")
    win = MainWindow(ctx)
    qtbot.addWidget(win)
    win.show()
    return win


def _video_info() -> MediaInfo:
    return MediaInfo(
        source_url="https://www.youtube.com/watch?v=abc", platform_key="youtube", platform_name="YouTube",
        engine="yt-dlp", title="Vídeo de teste", author="Canal", duration=90, content_type=ContentType.VIDEO,
        formats=[
            MediaFormat(format_id="140", ext="m4a", vcodec="none", acodec="mp4a", abr=128, filesize=1_000_000),
            MediaFormat(format_id="137", ext="mp4", width=1920, height=1080, vcodec="avc1", acodec="none", filesize=9_000_000),
            MediaFormat(format_id="136", ext="mp4", width=1280, height=720, vcodec="avc1", acodec="none", filesize=5_000_000),
        ],
    )


def test_navigation_between_pages(window) -> None:
    for key in ("downloads", "history", "library", "settings", "home"):
        window.navigate(key)
        assert window.nav_buttons[key].isChecked()


def test_home_shows_only_available_options(window) -> None:
    home = window.home
    home._show_info(_video_info())
    assert not home.result_card.isHidden()
    keys = [home.quality_combo.itemData(i) for i in range(home.quality_combo.count())]
    assert keys == ["best", "1080", "720", "original"]
    home.mode_combo.setCurrentIndex(home.mode_combo.findData(SelectionMode.AUDIO.value))
    audio = [home.format_combo.itemData(i) for i in range(home.format_combo.count())]
    assert "flac" not in audio and "mp3" in audio
    selection = home.current_selection()
    assert selection.mode == SelectionMode.AUDIO


def test_home_manual_item_selection(window) -> None:
    info = MediaInfo(source_url="https://www.youtube.com/playlist?list=PL", engine="yt-dlp", title="Playlist",
                     content_type=ContentType.PLAYLIST,
                     entries=[MediaEntry(index=i, title=f"Item {i}", kind=MediaKind.VIDEO) for i in range(1, 5)])
    home = window.home
    home._show_info(info)
    assert home.entries_list.count() == 4 and home.current_selection().items is None
    home.manual_radio.setChecked(True)
    home._check_all(False)
    home.entries_list.item(1).setCheckState(home.entries_list.item(1).checkState().__class__.Checked)
    assert home.current_selection().items == [2]
    home._check_all(False)
    assert not home.download_btn.isEnabled()


def test_home_download_enqueues_job(window, ctx, monkeypatch) -> None:
    ran = []
    monkeypatch.setattr(ctx.manager._executor, "execute", lambda job, report: ran.append(job.request.url))
    window.home._show_info(_video_info())
    window.home.start_download()
    assert not window.home.banner.isHidden()
    assert ctx.manager.wait_all(timeout=10)
    assert ran == ["https://www.youtube.com/watch?v=abc"]
    snapshot = ctx.manager.jobs()[0]
    assert snapshot.status == JobStatus.COMPLETED


def test_batch_counts_urls(window, qtbot) -> None:
    home = window.home
    home.tabs.setCurrentIndex(1)
    home.batch_edit.setPlainText("https://a.com/1\nhttps://b.com/2\nlixo\nhttps://a.com/1")
    home._update_batch_count()
    assert home.batch_count.text() == "2 URLs encontradas"
    assert home.batch_btn.isEnabled()


def test_downloads_page_shows_failed_job_with_friendly_error(window, ctx, monkeypatch, qtbot) -> None:
    def fail(job, report):
        raise UMDError(ErrorCode.PRIVATE, details="Private video")

    monkeypatch.setattr(ctx.manager._executor, "execute", fail)
    request = ctx.download.quick_request("https://www.youtube.com/watch?v=abc")
    job = ctx.download.submit(request)
    qtbot.waitUntil(lambda: ctx.manager.get(job.id).status == JobStatus.FAILED, timeout=10_000)
    qtbot.waitUntil(lambda: job.id in window.downloads._widgets
                    and window.downloads._widgets[job.id].snapshot.status == JobStatus.FAILED, timeout=5_000)
    widget = window.downloads._widgets[job.id]
    assert widget.message.text() == "Este conteúdo é privado."
    assert widget.primary.text() == "Tentar novamente"


def test_settings_save_roundtrip(window, ctx) -> None:
    page = window.settings_page
    page.concurrent.setValue(6)
    page.template.setText("{author}/{title}.{ext}")
    page.save()
    assert ctx.settings_store.settings.max_concurrent_downloads == 6
    assert ctx.settings_store.settings.filename_template == "{author}/{title}.{ext}"


def test_settings_rejects_invalid_template(window, ctx, monkeypatch) -> None:
    shown = []
    monkeypatch.setattr("umd.ui.settings.info_box", lambda *args: shown.append(args))
    page = window.settings_page
    page.template.setText("../{title}")
    page.save()
    assert shown and ctx.settings_store.settings.filename_template != "../{title}"


def test_history_and_library_pages_load(window, ctx) -> None:
    window.navigate("history")
    window.history.reload()
    window.navigate("library")
    window.library.reload()
    window.library._set_view(False)
    assert ctx.settings_store.settings.library_view == "list"
