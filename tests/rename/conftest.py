from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

import pytest


def make_files(folder: Path, *names: str) -> list[Path]:
    folder.mkdir(parents=True, exist_ok=True)
    paths = []
    for name in names:
        path = folder / name
        path.write_text(name, encoding="utf-8")  # o conteudo identifica o arquivo depois de renomeado
        paths.append(path)
    return paths


def make_photo(path: Path, taken: str | None = None, modified: datetime | None = None) -> Path:
    """JPEG pequeno; `taken` ("2024:03:15 14:30:22") vira a data EXIF em que a foto foi tirada."""
    from PIL import Image

    img = Image.new("RGB", (8, 8), (200, 120, 10))
    exif = Image.Exif()
    if taken:
        exif.get_ifd(0x8769)[0x9003] = taken
    img.save(path, exif=exif)
    if modified:
        os.utime(path, (modified.timestamp(), modified.timestamp()))
    return path


def names(folder: Path) -> list[str]:
    return sorted(p.name for p in folder.iterdir())


@pytest.fixture
def folder(tmp_path: Path) -> Path:
    path = tmp_path / "arquivos"
    path.mkdir()
    return path
