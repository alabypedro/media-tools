"""Conversao entre formatos de arquivo compactado (zip/tar*/7z).

Evolucao do antigo convert.py: a extracao agora valida cada membro do
compactado contra a pasta destino (protege contra "zip slip" -- um
arquivo malicioso com nomes tipo "../../../windows/system32/..." que
escreveria fora da pasta esperada). Ver item 26 (seguranca).
"""

from __future__ import annotations

import tarfile
import threading
import zipfile
from pathlib import Path
from typing import Any

from ..core.errors import ConversionError, missing_dependency
from ..core.paths import safe_member_path

ARCHIVE_KINDS: dict[str, str] = {
    "zip": ".zip",
    "7z": ".7z",
    "tar": ".tar",
    "tar.gz": ".tar.gz",
    "tgz": ".tgz",
    "tar.bz2": ".tar.bz2",
    "tbz2": ".tbz2",
    "tar.xz": ".tar.xz",
    "txz": ".txz",
}
ARCHIVE_SUFFIXES = sorted(ARCHIVE_KINDS.values(), key=len, reverse=True)


def detect_archive_kind(path: Path) -> str | None:
    name = path.name.lower()
    for kind, suffix in sorted(ARCHIVE_KINDS.items(), key=lambda kv: len(kv[1]), reverse=True):
        if name.endswith(suffix):
            return kind
    return None


def strip_archive_suffix(name: str) -> str:
    lower = name.lower()
    for suffix in ARCHIVE_SUFFIXES:
        if lower.endswith(suffix):
            return name[: -len(suffix)]
    return name


def _require_py7zr():
    try:
        import py7zr
    except ModuleNotFoundError as exc:
        raise missing_dependency("py7zr", "converter arquivos .7z", exc) from exc
    return py7zr


def _safe_extract_zip(input_path: Path, dest: Path) -> None:
    with zipfile.ZipFile(input_path) as z:
        for info in z.infolist():
            target = safe_member_path(dest, info.filename)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with z.open(info) as src, open(target, "wb") as out:
                out.write(src.read())


def _safe_extract_7z(input_path: Path, dest: Path) -> None:
    py7zr = _require_py7zr()
    with py7zr.SevenZipFile(input_path, mode="r") as z:
        for name in z.getnames():
            safe_member_path(dest, name)  # valida antes de extrair; levanta ValueError se suspeito
        z.extractall(path=dest)


def _extract_archive(input_path: Path, kind: str, dest: Path) -> None:
    if kind == "zip":
        _safe_extract_zip(input_path, dest)
    elif kind == "7z":
        _safe_extract_7z(input_path, dest)
    elif kind.startswith("tar") or kind in ("tgz", "tbz2", "txz"):
        with tarfile.open(input_path, "r:*") as t:
            for member in t.getmembers():
                safe_member_path(dest, member.name)
            t.extractall(dest, filter="data")
    else:
        raise ConversionError(f"Formato de arquivo compactado nao suportado: {kind}")


def _iter_files(root: Path):
    for path in sorted(root.rglob("*")):
        if path.is_file():
            yield path, path.relative_to(root)


def _pack_archive(src_dir: Path, kind: str, output_path: Path) -> None:
    if kind == "zip":
        with zipfile.ZipFile(output_path, "w", zipfile.ZIP_DEFLATED) as z:
            for abs_path, rel_path in _iter_files(src_dir):
                z.write(abs_path, arcname=str(rel_path))
    elif kind == "7z":
        py7zr = _require_py7zr()
        with py7zr.SevenZipFile(output_path, "w") as z:
            for abs_path, rel_path in _iter_files(src_dir):
                z.write(abs_path, arcname=str(rel_path))
    else:
        tar_modes = {
            "tar": "w", "tar.gz": "w:gz", "tgz": "w:gz",
            "tar.bz2": "w:bz2", "tbz2": "w:bz2",
            "tar.xz": "w:xz", "txz": "w:xz",
        }
        mode = tar_modes.get(kind)
        if mode is None:
            raise ConversionError(f"Formato de arquivo compactado nao suportado: {kind}")
        with tarfile.open(output_path, mode) as t:
            for abs_path, rel_path in _iter_files(src_dir):
                t.add(abs_path, arcname=str(rel_path))


def convert(
    input_path: Path,
    output_path: Path,
    target_kind: str,
    cancel_event: threading.Event | None = None,
) -> Path:
    import tempfile

    src_kind = detect_archive_kind(input_path)
    if src_kind is None:
        raise ConversionError(f"Nao reconheco '{input_path.name}' como um arquivo compactado.")

    with tempfile.TemporaryDirectory(prefix="uc_archive_") as tmp:
        tmp_dir = Path(tmp)
        try:
            _extract_archive(input_path, src_kind, tmp_dir)
        except ValueError as exc:
            raise ConversionError(
                f"'{input_path.name}' contem caminhos suspeitos e foi rejeitado por seguranca.",
                detail=exc,
            ) from exc
        except ConversionError:
            raise
        except Exception as exc:
            raise ConversionError(
                f"'{input_path.name}' nao e um arquivo compactado valido (ou esta corrompido).",
                detail=exc,
            ) from exc
        if cancel_event is not None and cancel_event.is_set():
            raise ConversionError("Conversao cancelada pelo usuario.")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        _pack_archive(tmp_dir, target_kind, output_path)

    return output_path
