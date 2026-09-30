"""Atualizacao segura das engines: hash, extracao protegida, validacao e ativacao."""

from __future__ import annotations

import hashlib
import zipfile
from pathlib import Path

import pytest

from umd.core.config import SettingsStore
from umd.core.exceptions import ErrorCode, UMDError
from umd.engine import overrides
from umd.engine.runner import EngineRunner
from umd.services import engine_service
from umd.services.engine_service import EngineService, UpdateInfo, WheelInfo, version_tuple


def make_wheel(path: Path, package: str, version: str, extra: dict[str, str] | None = None) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{package}/__init__.py", f"__version__ = '{version}'\n")
        zf.writestr(f"{package}/version.py", f"__version__ = '{version}'\n")
        zf.writestr(f"{package}-{version}.dist-info/METADATA", "Name: x\n")
        zf.writestr(f"{package}-{version}.data/scripts/ferramenta", "#!/bin/sh\n")  # nao deve ser extraido
        for name, content in (extra or {}).items():
            zf.writestr(name, content)
    return path


def test_version_tuple() -> None:
    assert version_tuple("2026.08.19") == (2026, 8, 19)
    assert version_tuple("2026.9.1") > version_tuple("2026.08.19")
    assert version_tuple("1.32.13") < version_tuple("1.33.0")
    assert version_tuple(None) == (0,)


def test_pinned_requirement() -> None:
    requires = ["yt-dlp-ejs==0.8.0; extra == \"default\"", "requests>=2"]
    assert engine_service._pinned_requirement(requires, "yt-dlp-ejs") == "0.8.0"
    assert engine_service._pinned_requirement(["requests"], "yt-dlp-ejs") is None


def test_only_pypi_hosts_are_allowed() -> None:
    engine_service._check_host("https://files.pythonhosted.org/packages/x.whl")
    for bad in ("http://files.pythonhosted.org/x.whl", "https://evil.example/x.whl", "https://pypi.org.evil.com/x"):
        with pytest.raises(UMDError):
            engine_service._check_host(bad)


def test_extract_wheel_filters_members(tmp_path: Path) -> None:
    wheel = make_wheel(tmp_path / "w.whl", "yt_dlp", "9.9.9")
    dest = tmp_path / "out"
    engine_service._extract_wheel(wheel, dest, "yt_dlp")
    assert (dest / "yt_dlp" / "__init__.py").exists()
    assert (dest / "yt_dlp-9.9.9.dist-info" / "METADATA").exists()
    assert not (dest / "yt_dlp-9.9.9.data").exists()


@pytest.mark.parametrize("evil", ["../fora.py", "yt_dlp/../../fora.py", "/abs.py", "C:/abs.py"])
def test_extract_wheel_rejects_path_traversal(tmp_path: Path, evil: str) -> None:
    wheel = make_wheel(tmp_path / "w.whl", "yt_dlp", "9.9.9", {evil: "print('pwned')"})
    with pytest.raises(UMDError):
        engine_service._extract_wheel(wheel, tmp_path / "out", "yt_dlp")
    assert not (tmp_path / "fora.py").exists()


def test_download_rejects_wrong_hash(tmp_path: Path, media_server, monkeypatch) -> None:
    base, root = media_server
    make_wheel(root / "yt_dlp-9.9.9-py3-none-any.whl", "yt_dlp", "9.9.9")
    monkeypatch.setattr(engine_service, "_check_host", lambda url: None)  # servidor local no lugar do PyPI
    wheel = WheelInfo("yt-dlp", "9.9.9", f"{base}/yt_dlp-9.9.9-py3-none-any.whl", "0" * 64)
    target = tmp_path / "w.whl"
    with pytest.raises(UMDError) as info:
        engine_service._download_verified(wheel, target)
    assert info.value.code == ErrorCode.ACCESS_DENIED and not target.exists()


def test_apply_update_validates_activates_and_resets(tmp_path: Path, media_server, monkeypatch, umd_home) -> None:
    base, root = media_server
    wheel_path = make_wheel(root / "gallery_dl-99.0.0-py3-none-any.whl", "gallery_dl", "99.0.0")
    sha = hashlib.sha256(wheel_path.read_bytes()).hexdigest()
    monkeypatch.setattr(engine_service, "_check_host", lambda url: None)

    engines_dir = umd_home / "engines"
    service = EngineService(EngineRunner(engines_dir), engines_dir, SettingsStore(tmp_path / "s.json"))
    update = UpdateInfo("gallery-dl", "1.0.0", "99.0.0",
                        WheelInfo("gallery-dl", "99.0.0", f"{base}/{wheel_path.name}", sha))
    messages = []
    assert service.apply_update(update, messages.append) == "99.0.0"
    assert messages and overrides.read_active(engines_dir) == {"gallery-dl": "gallery-dl-99.0.0"}
    status = service.versions()["gallery-dl"]
    assert status.version == "99.0.0" and status.override  # o worker ja usa a versao nova

    service.reset("gallery-dl")
    assert overrides.read_active(engines_dir) == {}
    assert not (engines_dir / "gallery-dl-99.0.0").exists()
    assert not service.versions()["gallery-dl"].override


def test_failed_validation_keeps_current_version(tmp_path: Path, media_server, monkeypatch, umd_home) -> None:
    base, root = media_server
    # pacote que declara uma versao, mas o import reporta outra -> validacao falha
    wheel_path = make_wheel(root / "gallery_dl-50.0.0-py3-none-any.whl", "gallery_dl", "1.0.0")
    sha = hashlib.sha256(wheel_path.read_bytes()).hexdigest()
    monkeypatch.setattr(engine_service, "_check_host", lambda url: None)
    engines_dir = umd_home / "engines"
    service = EngineService(EngineRunner(engines_dir), engines_dir, SettingsStore(tmp_path / "s.json"))
    update = UpdateInfo("gallery-dl", "1.32.0", "50.0.0",
                        WheelInfo("gallery-dl", "50.0.0", f"{base}/{wheel_path.name}", sha))
    with pytest.raises(UMDError):
        service.apply_update(update)
    assert overrides.read_active(engines_dir) == {}
    assert not (engines_dir / "_staging").exists()


def test_active_file_ignores_unsafe_entries(tmp_path: Path) -> None:
    (tmp_path / "active.json").write_text('{"yt-dlp": "../../etc", "gallery-dl": "gallery-dl-1.0", "outro": "x"}')
    assert overrides.read_active(tmp_path) == {"gallery-dl": "gallery-dl-1.0"}
