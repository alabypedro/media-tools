"""Nome novo (Universal Media Tools) sem perder os dados de quem usava o nome antigo."""

from __future__ import annotations

from pathlib import Path

import pytest

from umd.core import paths


@pytest.fixture
def local_appdata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.delenv("UMD_HOME", raising=False)
    monkeypatch.setattr(paths.sys, "platform", "win32")
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    monkeypatch.setattr(paths.Path, "home", lambda: tmp_path / "home")
    return tmp_path


def test_new_install_uses_new_name(local_appdata: Path) -> None:
    assert paths.app_data_dir() == local_appdata / "UniversalMediaTools"
    assert paths.default_download_dir() == local_appdata / "home" / "Downloads" / "Universal Media Tools"


def test_existing_install_keeps_legacy_folders(local_appdata: Path) -> None:
    (local_appdata / "UniversalMediaDownloader").mkdir()
    (local_appdata / "home" / "Downloads" / "Universal Media Downloader").mkdir(parents=True)
    assert paths.app_data_dir() == local_appdata / "UniversalMediaDownloader"
    assert paths.default_download_dir() == local_appdata / "home" / "Downloads" / "Universal Media Downloader"
    assert not (local_appdata / "UniversalMediaTools").exists()


def test_new_folder_wins_when_both_exist(local_appdata: Path) -> None:
    (local_appdata / "UniversalMediaDownloader").mkdir()
    (local_appdata / "UniversalMediaTools").mkdir()
    assert paths.app_data_dir() == local_appdata / "UniversalMediaTools"
