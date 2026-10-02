"""Tela Renomear (pytest-qt, tela offscreen): previa ao vivo, aplicar, desfazer e envio ao Converter."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pytestqt")

from tests.rename.conftest import make_files, make_photo, names  # noqa: E402


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
    win.editor.shutdown()


@pytest.fixture
def page(window, monkeypatch):
    shown: list = []
    monkeypatch.setattr("umd.ui.rename.QMessageBox.warning", lambda *args: shown.append(args))
    window.renamer.shown = shown
    return window.renamer


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    path = tmp_path / "arquivos"
    path.mkdir()
    return path


def _column(page, col: int) -> list[str]:
    return [page.table.item(row, col).text() for row in range(page.table.rowCount())]


def test_rename_page_in_navigation_and_other_pages_intact(window) -> None:
    from umd.ui.main_window import PAGES

    window.navigate("rename")
    assert window.nav_buttons["rename"].isChecked() and window.stack.currentWidget() is window.renamer
    pages = {"home": window.home, "downloads": window.downloads, "converter": window.converter,
             "editor": window.editor, "rename": window.renamer, "history": window.history,
             "library": window.library, "settings": window.settings_page}
    assert set(pages) == set(PAGES)
    for key, widget in pages.items():
        window.navigate(key)
        assert window.stack.currentWidget() is widget


def test_empty_page_has_nothing_to_apply(page) -> None:
    assert page.table.rowCount() == 0 and not page.apply_btn.isEnabled()
    assert not page.undo_btn.isEnabled() and not page.convert_btn.isEnabled()


def test_preview_follows_the_options_without_touching_files(page, folder) -> None:
    assert page.add_paths([folder]) == 0  # pasta vazia
    make_files(folder, "IMG_2.jpg", "IMG_10.jpg")
    assert page.add_paths([folder]) == 2 and page.add_paths([folder]) == 0  # sem repetir
    assert _column(page, 0) == ["IMG_2.jpg", "IMG_10.jpg"] and _column(page, 2) == ["Sem mudança"] * 2
    assert not page.apply_btn.isEnabled()

    page.pattern.setText("Ferias {n:3}")
    page.refresh()
    assert _column(page, 1) == ["Ferias 001.jpg", "Ferias 002.jpg"] and page.apply_btn.isEnabled()
    assert "2 de 2" in page.status_label.text()

    page.pattern.setText("{name}")
    page.find.setText(r"img_(\d+)")
    page.replace.setText(r"foto \1")
    page.regex.setChecked(True)
    page.refresh()
    assert _column(page, 1) == ["IMG_2.jpg", "IMG_10.jpg"]  # maiusculas diferentes: nao casa
    page.ignore_case.setChecked(True)
    page.ext_case.setCurrentIndex(page.ext_case.findData("upper"))
    page.refresh()
    assert _column(page, 1) == ["foto 2.JPG", "foto 10.JPG"]
    assert names(folder) == ["IMG_10.jpg", "IMG_2.jpg"]


def test_option_changes_schedule_a_refresh(page, folder, qtbot) -> None:
    make_files(folder, "a.txt")
    page.add_paths([folder])
    page.pattern.setText("novo")
    qtbot.waitUntil(lambda: _column(page, 1) == ["novo.txt"], timeout=3000)
    page.case.setCurrentIndex(page.case.findData("upper"))
    qtbot.waitUntil(lambda: _column(page, 1) == ["NOVO.txt"], timeout=3000)


def test_bad_pattern_shows_the_reason_and_blocks_apply(page, folder) -> None:
    make_files(folder, "a.txt")
    page.add_paths([folder])
    page.pattern.setText("{nome}")
    page.refresh()
    assert "{nome}" in page.status_label.text() and not page.apply_btn.isEnabled() and page.table.rowCount() == 0
    page.pattern.setText("ok {name}")
    page.refresh()
    assert page.apply_btn.isEnabled()


def test_presets_fill_the_pattern(page) -> None:
    from umd.rename import engine
    from umd.ui.rename import CUSTOM

    page.preset.setCurrentIndex(page.preset.findData(engine.EXIF_DATE_PATTERN))
    assert page.pattern.text() == engine.EXIF_DATE_PATTERN
    page.pattern.setFocus()
    page.pattern.textEdited.emit("x")  # digitar no modelo volta para "Personalizado"
    assert page.preset.currentData() == CUSTOM


def test_exif_preset_with_and_without_fallback(page, folder) -> None:
    from umd.rename import engine

    make_photo(folder / "a.jpg", "2024:03:15 14:30:22")
    make_photo(folder / "b.jpg")
    page.add_paths([folder])
    page.preset.setCurrentIndex(page.preset.findData(engine.EXIF_DATE_PATTERN))
    page.refresh()
    assert _column(page, 1)[0] == "2024-03-15 14.30.22.jpg" and "data de modificação" in _column(page, 2)[1]
    page.date_fallback.setChecked(False)
    page.refresh()
    assert _column(page, 1)[1] == "—" and _column(page, 2)[1] == "Pulado (sem data EXIF)"
    assert "1 de 2" in page.status_label.text() and "1 pulado" in page.status_label.text()


def test_apply_shows_what_was_done_then_undo(page, folder) -> None:
    make_files(folder, "a.txt", "b.txt")
    page.add_paths([folder])
    page.pattern.setText("doc {n}")
    page.apply()
    assert names(folder) == ["doc 1.txt", "doc 2.txt"] and not page.shown
    assert _column(page, 0) == ["a.txt", "b.txt"] and _column(page, 2) == ["Renomeado"] * 2
    assert not page.apply_btn.isEnabled() and page.undo_btn.isEnabled()  # nao reaplica o modelo num segundo clique
    assert [p.name for p in page.files] == ["doc 1.txt", "doc 2.txt"]
    assert "2 arquivo(s) renomeados" in page.status_label.text()

    page.undo()
    assert names(folder) == ["a.txt", "b.txt"] and [p.name for p in page.files] == ["a.txt", "b.txt"]
    assert not page.undo_btn.isEnabled() and page.apply_btn.isEnabled()  # a previa voltou


def test_remove_selected_and_clear(page, folder) -> None:
    make_files(folder, "a.txt", "b.txt", "c.txt")
    page.add_paths([folder])
    page.table.selectRow(1)
    page.remove_selected()
    assert [p.name for p in page.files] == ["a.txt", "c.txt"]
    page.pattern.setText("x{n}")
    page.apply()
    page.table.selectRow(0)  # logo depois de renomear, a linha aponta para o nome novo
    page.remove_selected()
    assert [p.name for p in page.files] == ["x2.txt"]
    page.clear()
    assert page.files == [] and page.table.rowCount() == 0


def test_failure_is_reported_and_nothing_changes(page, folder, monkeypatch) -> None:
    from umd.rename import engine

    make_files(folder, "a.txt")
    page.add_paths([folder])
    page.pattern.setText("b")

    def locked(src, dst):
        raise PermissionError(13, "em uso", str(src))

    monkeypatch.setattr(engine.os, "rename", locked)
    page.apply()
    assert page.shown and names(folder) == ["a.txt"] and [p.name for p in page.files] == ["a.txt"]


def test_send_renamed_files_to_the_converter(window, page, folder, sample_png) -> None:
    import shutil

    shutil.copy(sample_png, folder / "IMG_1.png")
    page.add_paths([folder])
    page.pattern.setText("capa")
    page.apply()
    page.convert_btn.click()
    assert window.stack.currentWidget() is window.converter
    assert window.converter.table.item(0, 0).text() == "capa.png"


def test_drop_adds_files(page, folder) -> None:
    from PySide6.QtCore import QMimeData, QPoint, Qt, QUrl
    from PySide6.QtGui import QDropEvent

    files = make_files(folder, "a.txt", "b.txt")
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(f)) for f in files])
    page.dropEvent(QDropEvent(QPoint(5, 5), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton,
                              Qt.KeyboardModifier.NoModifier))
    assert [p.name for p in page.files] == ["a.txt", "b.txt"]
