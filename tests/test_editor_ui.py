"""Tela Editor (pytest-qt, tela offscreen): ferramentas rodando de verdade em segundo plano."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("pytestqt")

from tests.editor.conftest import make_gif, make_png, read  # noqa: E402


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
    monkeypatch.setattr("umd.ui.editor.info_box", lambda *args: shown.append(args))
    monkeypatch.setattr("umd.ui.editor.QMessageBox.warning", lambda *args: shown.append(args))
    window.editor.shown = shown
    return window.editor


def _run(page, qtbot) -> Path:
    page.apply()
    qtbot.waitUntil(lambda: not page.running and page.progress.maximum() == 100, timeout=30_000)
    assert not page.shown, page.shown
    return page.result


def test_editor_page_in_navigation(window) -> None:
    window.navigate("editor")
    assert window.nav_buttons["editor"].isChecked() and window.stack.currentWidget() is window.editor
    for key in ("home", "downloads", "converter", "history", "library", "settings"):  # ordem das paginas intacta
        window.navigate(key)
        assert window.stack.currentWidget() is {
            "home": window.home, "downloads": window.downloads, "converter": window.converter,
            "history": window.history, "library": window.library, "settings": window.settings_page}[key]


def test_every_tool_has_a_form_and_unique_key(page) -> None:
    from umd.ui.editor import TOOL_FORMS

    keys = [form.key for form in page.forms]
    assert len(keys) == len(set(keys)) == len(TOOL_FORMS) == page.tool_list.count()
    assert all(form.title and form.hint for form in page.forms)


def test_apply_disabled_until_a_file_is_open(page, tmp_path) -> None:
    page.select_tool("resize")
    assert not page.apply_btn.isEnabled() and page.format_combo.count() == 0
    assert page.open_file(make_gif(tmp_path / "a.gif"))
    assert page.apply_btn.isEnabled() and page.format_combo.currentData() == "gif"
    assert "4 quadros" in page.info_label.text() and "40×30" in page.info_label.text()
    page.select_tool("make")  # ferramenta com lista propria nao precisa de arquivo aberto
    assert page.apply_btn.isEnabled()


def test_unreadable_file_is_refused_with_message(page, tmp_path) -> None:
    bad = tmp_path / "bad.gif"
    bad.write_bytes(b"x")
    assert not page.open_file(bad)
    assert page.shown and page.source is None


def test_resize_runs_in_background_and_result_can_be_edited_again(page, qtbot, tmp_path) -> None:
    src = make_gif(tmp_path / "a.gif")
    page.open_file(src)
    form = page.select_tool("resize")
    assert form.width_spin.value() == 40
    form.width_spin.setValue(20)
    out = _run(page, qtbot)
    assert out == tmp_path / "a (redimensionado).gif" and read(out)[0][0].size == (20, 15)
    assert "Salvo" in page.status_label.text() and page.open_folder_btn.isEnabled()
    assert page.history.recent()[0].target_path == str(out)

    page.use_result()  # encadeia outra ferramenta, como no ezgif
    assert page.source == out and page.info.size == (20, 15)
    form = page.select_tool("rotate")
    form.angle.setValue(90)
    assert read(_run(page, qtbot))[0][0].size == (15, 20)
    assert src.exists() and read(src)[0][0].size == (40, 30)  # original intacto


def test_output_folder_and_format_choice(page, qtbot, tmp_path) -> None:
    page.open_file(make_gif(tmp_path / "a.gif"))
    page.select_tool("convert")
    page.format_combo.setCurrentIndex(page.format_combo.findData("webp"))
    page.output_dir.setText(str(tmp_path / "saida"))
    out = _run(page, qtbot)
    assert out == tmp_path / "saida" / "a (convertido).webp" and len(read(out)[0]) == 4
    page.select_tool("resize")
    assert page.format_combo.currentData() == "gif"  # trocar de ferramenta volta ao formato do arquivo


def test_forms_build_the_expected_options(page, tmp_path) -> None:
    page.open_file(make_gif(tmp_path / "a.gif"))
    crop = page.select_tool("crop")
    assert crop.options() == {"crop": {"x": 0, "y": 0, "width": 40, "height": 30}}
    censor = page.select_tool("censor")
    assert censor.options()["censor"] == {"x": 10, "y": 7, "width": 20, "height": 15, "mode": "blur"}
    speed = page.select_tool("speed")
    speed.speed.setValue(200)
    speed.reverse.setChecked(True)
    assert speed.options() == {"speed": 2.0, "reverse": True, "boomerang": False}
    cut = page.select_tool("cut")
    assert cut.end.value() == pytest.approx(0.4)
    with pytest.raises(ValueError):
        cut.options()  # nada mudou: nao ha trecho
    cut.start.setValue(0.1)
    cut.end.setValue(0.3)
    assert cut.options() == {"start": pytest.approx(0.1), "end": pytest.approx(0.3)}
    effects = page.select_tool("effects")
    with pytest.raises(ValueError):
        effects.options()
    effects.checks["sepia"].setChecked(True)
    effects.levels["brightness"].setValue(150)
    assert effects.options() == {"sepia": True, "brightness": 1.5}
    optimize = page.select_tool("optimize")
    assert "colors" not in optimize.options()
    optimize.colors.setValue(32)
    optimize.drop.setCurrentIndex(optimize.drop.findData(2))
    assert optimize.options()["colors"] == 32 and optimize.options()["drop_every"] == 2


def test_invalid_form_shows_message_and_does_not_start(page, tmp_path) -> None:
    page.open_file(make_gif(tmp_path / "a.gif"))
    page.select_tool("text")
    page.apply()
    assert page.shown and not page.running and page.result is None


def test_text_tool(page, qtbot, tmp_path) -> None:
    page.open_file(make_png(tmp_path / "preto.png", (0, 0, 0), size=(200, 120)))
    form = page.select_tool("text")
    form.text.setPlainText("OLA")
    form.position.setCurrentIndex(form.position.findData("top"))
    out = _run(page, qtbot)
    assert out.name == "preto (texto).png"
    frame = read(out)[0][0]
    assert frame.crop((0, 0, 200, 60)).convert("L").getextrema()[1] == 255
    assert frame.crop((0, 80, 200, 120)).convert("L").getextrema()[1] == 0


def test_make_animation_tool_needs_no_open_file(page, qtbot, tmp_path) -> None:
    form = page.select_tool("make")
    page.apply()
    assert page.shown  # lista vazia
    page.shown.clear()
    files = [make_png(tmp_path / "1.png", (255, 0, 0)), make_png(tmp_path / "2.png", (0, 0, 255)),
             tmp_path / "notas.txt"]
    form.files.add(files)
    assert form.files.files == files[:2]  # so imagens
    form.files.list.setCurrentRow(1)
    form.files.move(-1)
    form.delay.setValue(300)
    out = _run(page, qtbot)
    frames, durations = read(out)
    assert out.name == "2 (animação).gif" and durations == [300, 300]
    assert frames[0].getpixel((5, 5))[2] > 200  # a azul foi para a frente
    assert page.use_result_btn.isEnabled()


def test_split_and_sprite_tools(page, qtbot, tmp_path) -> None:
    page.open_file(make_gif(tmp_path / "a.gif"))
    page.select_tool("split")
    assert page.format_combo.currentData() == "png"
    _run(page, qtbot)
    assert len(list((tmp_path / "a (quadros)").glob("frame_*.png"))) == 4
    assert "4 quadros" in page.status_label.text()

    form = page.select_tool("sprite")
    assert page.format_combo.currentData() == "png" and not form.rows.isEnabled()
    form.columns.setValue(2)
    sheet = _run(page, qtbot)
    assert read(sheet)[0][0].size == (80, 60)
    page.use_result()
    form.mode.setCurrentIndex(form.mode.findData("cut"))
    assert page.format_combo.currentData() == "gif" and form.rows.isEnabled()
    form.rows.setValue(2)
    assert len(read(_run(page, qtbot))[0]) == 4


def test_video_to_gif_tool(page, qtbot, sample_video, tmp_path) -> None:
    import shutil

    video = Path(shutil.copy(sample_video, tmp_path / "clipe.mp4"))
    assert page.open_file(video)
    assert "com áudio" in page.info_label.text() and page.preview.pixmap() is not None
    page.select_tool("resize")
    assert page.format_combo.currentData() == "mp4"
    form = page.select_tool("video2gif")
    assert page.format_combo.currentData() == "gif" and form.end.value() == pytest.approx(2.0, abs=0.2)
    form.end.setValue(1.0)
    form.fps.setValue(4)
    form.width_spin.setValue(160)
    out = _run(page, qtbot)
    frames, _ = read(out)
    assert out.name == "clipe (convertido).gif" and len(frames) == 4 and frames[0].size == (160, 120)


def test_cancel_reenables_the_page(page, qtbot, sample_video, tmp_path) -> None:
    page.open_file(sample_video)
    page.select_tool("speed").speed.setValue(50)
    page.output_dir.setText(str(tmp_path))
    page.apply()
    assert not page.apply_btn.isEnabled() and not page.tool_list.isEnabled()
    page.cancel()
    qtbot.waitUntil(lambda: not page.running and page.apply_btn.isEnabled(), timeout=20_000)
    assert not page.shown  # cancelar nao e erro


def test_drop_goes_to_the_list_of_the_current_tool(page, tmp_path) -> None:
    from PySide6.QtCore import QMimeData, QPoint, Qt, QUrl
    from PySide6.QtGui import QDropEvent

    gif = make_gif(tmp_path / "a.gif")
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(gif))])

    def drop() -> None:
        page.dropEvent(QDropEvent(QPoint(5, 5), Qt.DropAction.CopyAction, mime, Qt.MouseButton.LeftButton,
                                  Qt.KeyboardModifier.NoModifier))

    page.select_tool("resize")
    drop()
    assert page.source == gif
    form = page.select_tool("make")
    drop()
    assert form.files.files == [gif]
