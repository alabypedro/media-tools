from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from umd.core.config import QualityPreset, Settings, SettingsStore


def test_defaults_are_sane() -> None:
    s = Settings()
    assert s.max_concurrent_downloads == 3
    assert s.default_quality == QualityPreset.BEST
    assert s.filename_template == "{platform}/{author}/{title}.{ext}"
    assert s.cookies_browser == ""  # login desligado por padrao
    assert not s.allow_local_network_urls


@pytest.mark.parametrize("field, value", [
    ("max_concurrent_downloads", 0), ("max_concurrent_downloads", 11), ("retries", -1),
    ("filename_template", "../{title}"), ("default_video_format", "avi"), ("cookies_browser", "netscape9"),
    ("theme", "rosa"), ("default_audio_bitrate", "999"),
])
def test_invalid_values_are_rejected(field: str, value) -> None:
    with pytest.raises(ValidationError):
        Settings(**{field: value})


def test_load_tolerant_keeps_valid_fields() -> None:
    s = Settings.load_tolerant({"max_concurrent_downloads": 5, "retries": "muitos", "theme": "dark", "unknown": 1})
    assert s.max_concurrent_downloads == 5
    assert s.retries == Settings().retries  # campo invalido volta ao padrao
    assert s.theme == "dark"


def test_store_roundtrip_and_listeners(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    seen = []
    store.subscribe(seen.append)
    store.update(max_concurrent_downloads=7, default_quality=QualityPreset.P720)
    assert seen and seen[-1].max_concurrent_downloads == 7
    assert json.loads(path.read_text(encoding="utf-8"))["default_quality"] == "720p"
    assert SettingsStore(path).settings.max_concurrent_downloads == 7


def test_corrupted_file_falls_back_to_defaults(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    path.write_text("{isto nao e json", encoding="utf-8")
    assert SettingsStore(path).settings == Settings()


def test_apply_temporary_does_not_touch_disk(tmp_path: Path) -> None:
    path = tmp_path / "settings.json"
    store = SettingsStore(path)
    store.update(retries=2)
    store.apply_temporary(retries=9, rate_limit_kbps=100)
    assert store.settings.retries == 9
    assert SettingsStore(path).settings.retries == 2


def test_paths_are_normalized(tmp_path: Path) -> None:
    s = Settings(download_dir=f' "{tmp_path.as_posix()}/sub" ')
    assert Path(s.download_dir) == tmp_path / "sub"
    assert Settings(download_dir="").download_dir  # vazio volta ao padrao
