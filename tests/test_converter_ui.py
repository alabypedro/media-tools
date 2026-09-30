"""Tela Converter (pytest-qt, tela offscreen): fila, formatos, conversao real em thread."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pytestqt")

from umd.convert.core.queue import JobStatus  # noqa: E402


@pytest.fixture
def ctx(tmp_path):
    from umd.services.context import create_context

    context = create_context()
    context.settings_store.update(download_dir=str(tmp_path / "downloads"))
    yield context
    context.close()


@pytest.fixture
def window(qtbot, ctx):
    from PySide6.QtWidgets import QApplication

    from umd.ui.main_window import MainWindow
    from umd.ui.theme import apply_theme

    apply_theme(QApplication.instance(), "light")
    win = MainWindow(ctx)
    qtbot.addWidget(win)
    win.show()
    yield win
    win.converter.shutdown()


def _png(path: Path, color=(10, 120, 200)) -> Path:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", (40, 30), color).save(path)
    return path


def test_converter_page_in_navigation(window) -> None:
    window.navigate("converter")
    assert window.nav_buttons["converter"].isChecked()
    assert window.stack.currentWidget() is window.converter


def test_add_paths_dedupes_and_skips_unsupported(window, tmp_path) -> None:
    page = window.converter
    img = _png(tmp_path / "a.png")
    other = tmp_path / "notas.xyz"
    other.write_text("?", encoding="utf-8")
    assert page.add_paths([img, img, other]) == 1
    assert page.table.rowCount() == 1
    assert "ignorado" in page.current_label.text()
    formats = [page.format_combo.itemData(i) for i in range(page.format_combo.count())]
    assert "jpg" in formats and "pdf" in formats and "png" not in formats
    assert page.format_combo.currentData() == "jpg"  # sugestao comum, nao a primeira em ordem alfabetica


def test_folder_keeps_only_convertible_files(window, tmp_path) -> None:
    folder = tmp_path / "lote"
    _png(folder / "x.png")
    _png(folder / "sub" / "y.png")
    (folder / "leia.xyz").write_text("?", encoding="utf-8")
    page = window.converter
    assert page.add_paths([folder]) == 2
    assert all(e.root == folder for e in page.entries)


def test_common_formats_for_mixed_queue(window, tmp_path) -> None:
    page = window.converter
    txt = tmp_path / "t.txt"
    txt.write_text("ola", encoding="utf-8")
    page.add_paths([_png(tmp_path / "a.png"), txt])
    formats = [page.format_combo.itemData(i) for i in range(page.format_combo.count())]
    assert formats == ["pdf"]


def test_dpi_override_only_applies_to_pdf(window, tmp_path) -> None:
    page = window.converter
    page.add_paths([_png(tmp_path / "a.png")])
    page.options = {"dpi": 300, "quality": 70}
    opts = page.job_options(page.entries[0])
    assert "dpi" not in opts and opts["quality"] == 70
    pdf_entry = type(page.entries[0])(tmp_path / "doc.pdf")
    assert page.job_options(pdf_entry)["dpi"] == 300


def test_real_conversion_runs_in_background(window, qtbot, tmp_path) -> None:
    page = window.converter
    out_dir = tmp_path / "convertidos"
    page.add_paths([_png(tmp_path / "a.png"), _png(tmp_path / "b.png", (200, 0, 0))])
    page.format_combo.setCurrentIndex(page.format_combo.findData("jpg"))
    page.output_dir.setText(str(out_dir))
    page.start_conversion()
    qtbot.waitUntil(lambda: not page.running and all(e.status != JobStatus.WAITING for e in page.entries),
                    timeout=20_000)
    assert [e.status for e in page.entries] == [JobStatus.DONE, JobStatus.DONE]
    assert (out_dir / "a.jpg").exists() and (out_dir / "b.jpg").exists()
    assert page.progress.value() == 100
    assert page.open_folder_btn.isEnabled()
    assert len(page.history.recent()) == 2


def test_cancel_finishes_queue_and_reenables_ui(window, qtbot, tmp_path) -> None:
    page = window.converter
    page.add_paths([_png(tmp_path / f"{i}.png") for i in range(5)])
    page.format_combo.setCurrentIndex(page.format_combo.findData("jpg"))
    page.start_conversion()
    assert not page.clear_btn.isEnabled()  # fila travada durante a conversao
    page.cancel_conversion()
    qtbot.waitUntil(lambda: not page.running, timeout=10_000)
    qtbot.waitUntil(lambda: page.clear_btn.isEnabled(), timeout=5_000)
    finished = (JobStatus.DONE, JobStatus.CANCELLED)
    assert all(e.status in finished for e in page.entries)


def test_flatten_pdf_from_converter_page(window, qtbot, tmp_path) -> None:
    from tests.convert.test_pdf_flatten import make_form_pdf

    page = window.converter
    page.add_paths([make_form_pdf(tmp_path / "ficha.pdf")])
    index = page.format_combo.findData("flatten")
    assert index >= 0 and page.format_combo.itemText(index) == "PDF achatado"
    page.format_combo.setCurrentIndex(index)
    assert page.table.item(0, 3).text() == "PDF achatado"
    page.start_conversion()
    qtbot.waitUntil(lambda: not page.running and page.entries[0].status != JobStatus.WAITING, timeout=20_000)
    assert page.entries[0].status == JobStatus.DONE, page.entries[0].error
    assert (tmp_path / "ficha (achatado).pdf").exists()


def test_merge_images_dialog_generates_pdf(window, tmp_path, monkeypatch) -> None:
    from umd.ui.converter import MergeImagesDialog

    monkeypatch.setattr("umd.ui.converter.confirm", lambda *a: False)
    files = [_png(tmp_path / "1.png"), _png(tmp_path / "2.png", (0, 200, 0))]
    dialog = MergeImagesDialog(files, window.converter)
    dialog.list.setCurrentRow(1)
    dialog.move(-1)
    assert dialog.files == list(reversed(files))
    result = dialog.generate(tmp_path / "album")
    assert result == tmp_path / "album.pdf" and result.exists()


def test_library_sends_files_to_converter(window, tmp_path) -> None:
    img = _png(tmp_path / "baixado.png")
    window.library.convert_requested.emit([str(img)])
    assert window.stack.currentWidget() is window.converter
    assert [e.path for e in window.converter.entries] == [img]


def test_converter_settings_dialog_saves(window) -> None:
    from umd.convert.core.config import load_settings
    from umd.ui.converter import ConverterSettingsDialog

    page = window.converter
    dialog = ConverterSettingsDialog(page.settings, page)
    dialog.pdf_dpi.setValue(300)
    dialog.conflict.setCurrentIndex(dialog.conflict.findData("skip"))
    dialog.save()
    from umd.convert.core.config import save_settings

    save_settings(page.settings)
    saved = load_settings()
    assert saved.pdf_default_dpi == 300 and saved.conflict_policy == "skip"
