"""Onde o aplicativo guarda seus dados, e onde ficam os recursos
empacotados (ffmpeg, icones) quando rodando como .exe do PyInstaller.

A pasta de dados pode ser trocada pela variavel de ambiente UMD_HOME
(usada pelos testes e para um modo "portatil").

O app se chamava Universal Media Downloader: se a pasta de dados (ou de
downloads) com o nome antigo ja existe, ela continua sendo usada. Nao e
movida porque o banco guarda caminhos absolutos (miniaturas, engines).
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from .. import APP_ID, LEGACY_APP_ID

_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


def _prefer_legacy(new: Path, legacy: Path) -> Path:
    """Usa a pasta com o nome antigo se so ela existe (instalacao anterior ao novo nome)."""
    return legacy if not new.exists() and legacy.exists() else new


def app_data_dir() -> Path:
    override = os.environ.get("UMD_HOME")
    if override:
        base = Path(override)
    else:
        if sys.platform == "win32":
            root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
            names = (APP_ID, LEGACY_APP_ID)
        elif sys.platform == "darwin":
            root = Path.home() / "Library" / "Application Support"
            names = (APP_ID, LEGACY_APP_ID)
        else:
            xdg = os.environ.get("XDG_DATA_HOME")
            root = Path(xdg) if xdg else Path.home() / ".local" / "share"
            names = ("universal-media-tools", "universal-media-downloader")
        base = _prefer_legacy(root / names[0], root / names[1])
    base.mkdir(parents=True, exist_ok=True)
    return base


def _subdir(name: str) -> Path:
    path = app_data_dir() / name
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir() -> Path:
    return _subdir("logs")


def engines_dir() -> Path:
    return _subdir("engines")


def thumbnails_dir() -> Path:
    return _subdir("thumbnails")


def database_path() -> Path:
    return app_data_dir() / "umd.db"


def settings_path() -> Path:
    return app_data_dir() / "settings.json"


def default_download_dir() -> Path:
    downloads = Path.home() / "Downloads"
    return _prefer_legacy(downloads / "Universal Media Tools", downloads / "Universal Media Downloader")


def resource_root() -> Path:
    """Raiz dos recursos: _MEIPASS no .exe, raiz do projeto em desenvolvimento."""
    if is_frozen():
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    return _PROJECT_ROOT


def resource_path(*parts: str) -> Path:
    return resource_root().joinpath(*parts)
