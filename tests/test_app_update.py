"""Atualizacao do programa pelo GitHub Releases (sem internet: respostas simuladas)."""

from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path

import pytest

from umd.core import app_update

INSTALLER = b"MZ" + b"x" * 5000
SHA = hashlib.sha256(INSTALLER).hexdigest()
REPO = "fulano/media-tools"
API = f"https://api.github.com/repos/{REPO}/releases/latest"


class FakeResponse(io.BytesIO):
    def __init__(self, data: bytes, url: str):
        super().__init__(data)
        self._url = url

    def geturl(self) -> str:
        return self._url


def release(version="1.2.0", digest=True, extra_assets=()):
    name = f"UniversalMediaTools-Setup-{version}.exe"
    asset = {"name": name, "size": len(INSTALLER),
             "browser_download_url": f"https://github.com/{REPO}/releases/download/v{version}/{name}"}
    if digest:
        asset["digest"] = f"sha256:{SHA}"
    return {"tag_name": f"v{version}", "body": "- novidade A", "html_url": f"https://github.com/{REPO}/releases/tag/v{version}",
            "assets": [asset, *extra_assets]}


@pytest.fixture
def routes(monkeypatch):
    table: dict[str, tuple[bytes, str]] = {}

    def opener(request, timeout=None):
        data, final = table[request.full_url]
        return FakeResponse(data, final)

    monkeypatch.setattr(app_update.urllib.request, "urlopen", opener)
    return table


def serve_release(routes, payload) -> None:
    routes[API] = (json.dumps(payload).encode(), API)


@pytest.mark.parametrize(("candidate", "current", "newer"), [
    ("1.0.1", "1.0.0", True), ("v1.10.0", "1.9.9", True), ("2", "1.9", True),
    ("1.0.0", "1.0.0", False), ("1.0", "1.0.0", False), ("0.9.9", "1.0.0", False),
])
def test_version_compare(candidate, current, newer) -> None:
    assert app_update.is_newer(candidate, current) is newer


def test_reads_release_with_github_digest(routes) -> None:
    serve_release(routes, release("1.2.0"))
    info = app_update.check_for_update(REPO)
    assert (info.version, info.sha256, info.available) == ("1.2.0", SHA, True)


def test_same_version_is_not_available(routes, monkeypatch) -> None:
    serve_release(routes, release("1.0.0"))
    monkeypatch.setattr(app_update, "__version__", "1.0.0")
    assert not app_update.check_for_update(REPO).available


def test_falls_back_to_sha256_file(routes) -> None:
    url = f"https://github.com/{REPO}/releases/download/v1.2.0/UniversalMediaTools-Setup-1.2.0.exe.sha256"
    serve_release(routes, release(digest=False, extra_assets=[
        {"name": "UniversalMediaTools-Setup-1.2.0.exe.sha256", "browser_download_url": url}]))
    routes[url] = (f"{SHA}  UniversalMediaTools-Setup-1.2.0.exe\n".encode(), url)
    assert app_update.check_for_update(REPO).sha256 == SHA


def test_refuses_release_without_hash(routes) -> None:
    serve_release(routes, release(digest=False))
    with pytest.raises(app_update.UpdateError, match="SHA-256"):
        app_update.check_for_update(REPO)


def test_refuses_release_without_installer(routes) -> None:
    payload = release()
    payload["assets"][0]["name"] = "outra-coisa.zip"
    serve_release(routes, payload)
    with pytest.raises(app_update.UpdateError, match="instalador"):
        app_update.check_for_update(REPO)


def test_redirect_outside_github_is_refused(routes) -> None:
    routes[API] = (json.dumps(release()).encode(), "https://evil.example.com/x")
    with pytest.raises(app_update.UpdateError, match="não permitido"):
        app_update.check_for_update(REPO)


def test_offline_gives_friendly_message() -> None:  # a fixture do conftest desliga a rede
    with pytest.raises(app_update.UpdateError, match="internet"):
        app_update.check_for_update(REPO)


def _info(sha=SHA) -> app_update.UpdateInfo:
    name = "UniversalMediaTools-Setup-1.2.0.exe"
    return app_update.UpdateInfo("1.2.0", "", "https://github.com/x", name,
                                 f"https://github.com/{REPO}/releases/download/v1.2.0/{name}", len(INSTALLER), sha)


def test_download_verifies_hash(routes) -> None:
    info = _info()
    routes[info.installer_url] = (INSTALLER, "https://objects.githubusercontent.com/asset/1")
    seen = []
    path = app_update.download_installer(info, lambda received, total: seen.append((received, total)))
    try:
        assert path.read_bytes() == INSTALLER and seen[-1] == (len(INSTALLER), len(INSTALLER))
    finally:
        path.unlink()
        path.parent.rmdir()


def test_tampered_download_is_discarded(routes, monkeypatch, tmp_path) -> None:
    info = _info()
    routes[info.installer_url] = (INSTALLER + b"virus", info.installer_url)
    folder = tmp_path / "update"
    folder.mkdir()
    monkeypatch.setattr(app_update.tempfile, "mkdtemp", lambda **_kw: str(folder))
    with pytest.raises(app_update.UpdateError, match="não confere"):
        app_update.download_installer(info)
    assert not folder.exists()


def test_source_run_cannot_self_update() -> None:
    assert app_update.installed_app_dir() is None
    with pytest.raises(app_update.UpdateError, match="instalada"):
        app_update.launch_installer(Path("x.exe"))


# ---------------------------------------------------------------- interface

@pytest.fixture
def window(qtbot, tmp_path):
    pytest.importorskip("pytestqt")
    from PySide6.QtWidgets import QApplication

    from umd.services.context import create_context
    from umd.ui.main_window import MainWindow
    from umd.ui.theme import apply_theme

    ctx = create_context()
    ctx.settings_store.update(download_dir=str(tmp_path / "downloads"))
    apply_theme(QApplication.instance(), "light")
    win = MainWindow(ctx)
    qtbot.addWidget(win)
    yield win
    ctx.close()


def test_silent_check_shows_badge_without_dialogs(window, qtbot, routes, monkeypatch) -> None:
    monkeypatch.setattr(app_update, "UPDATE_REPO", REPO)
    serve_release(routes, release("9.9.9"))
    offered = []
    monkeypatch.setattr(window.updater, "offer", lambda info=None: offered.append(info))
    window.updater.check(manual=False)
    qtbot.waitUntil(lambda: window.update_badge.isVisibleTo(window), timeout=5000)
    assert "9.9.9" in window.update_badge.text()
    assert offered == []  # a checagem silenciosa nunca abre dialogo


def test_manual_check_up_to_date_informs_user(window, qtbot, routes, monkeypatch) -> None:
    monkeypatch.setattr(app_update, "UPDATE_REPO", REPO)
    serve_release(routes, release("0.0.1"))
    shown = []
    monkeypatch.setattr("umd.ui.app_update.info_box", lambda *args: shown.append(args))
    window.updater.check(manual=True)
    qtbot.waitUntil(lambda: bool(shown), timeout=5000)
    assert "mais recente" in shown[0][2]
    assert not window.update_badge.isVisibleTo(window)


def test_manual_check_error_is_shown(window, qtbot, monkeypatch) -> None:
    warned = []
    monkeypatch.setattr("umd.ui.app_update.QMessageBox.warning", lambda *args: warned.append(args))
    window.updater.check(manual=True)  # rede desligada pela fixture
    qtbot.waitUntil(lambda: bool(warned), timeout=5000)
    assert "internet" in warned[0][2]


def test_source_copy_offers_download_page_instead_of_installing(window, monkeypatch) -> None:
    opened, asked = [], []
    monkeypatch.setattr("umd.ui.app_update.confirm", lambda *args: asked.append(args) or True)
    monkeypatch.setattr("umd.ui.app_update.QDesktopServices.openUrl", lambda url: opened.append(url.toString()))
    downloads = []
    monkeypatch.setattr(window.updater, "_download", lambda info: downloads.append(info))
    info = _info()
    window.updater.offer(info)
    assert opened == [info.page_url] and downloads == []
    assert "instalador" in asked[0][2]


def test_setting_to_check_on_start_roundtrip(window) -> None:
    page = window.settings_page
    assert page.check_app_on_start.isChecked()  # padrao: avisar
    page.check_app_on_start.setChecked(False)
    page.save()
    assert window.ctx.settings_store.settings.check_app_updates_on_start is False
