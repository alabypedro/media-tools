"""Conversao de documentos/planilhas/apresentacoes via LibreOffice
headless. Evolucao de convert_office() do antigo convert.py: deteccao
melhor, timeout/cancelamento, mensagens de erro claras e tratamento de
arquivo bloqueado (itens 9 e 18)."""

from __future__ import annotations

import shutil
import threading
from pathlib import Path
from typing import Any

from ..core.errors import ConversionError, file_locked, libreoffice_missing, libreoffice_timeout
from ..core.procutil import run_cancellable

DEFAULT_TIMEOUT = 300.0

_SOFFICE_CANDIDATES = [
    "soffice",
    r"C:\Program Files\LibreOffice\program\soffice.exe",
    r"C:\Program Files (x86)\LibreOffice\program\soffice.exe",
]

_cached_soffice: str | None = None
_cache_checked = False


def find_soffice() -> str | None:
    global _cached_soffice, _cache_checked
    if _cache_checked:
        return _cached_soffice
    _cache_checked = True
    for candidate in _SOFFICE_CANDIDATES:
        resolved = shutil.which(candidate) if not candidate.endswith(".exe") else candidate
        if resolved and Path(resolved).exists():
            _cached_soffice = resolved
            return resolved
    return None


def is_available() -> bool:
    return find_soffice() is not None


def convert(
    input_path: Path,
    output_path: Path,
    options: dict[str, Any] | None = None,
    cancel_event: threading.Event | None = None,
) -> Path:
    options = options or {}
    soffice = find_soffice()
    if soffice is None:
        raise libreoffice_missing()

    target_ext = output_path.suffix.lower().lstrip(".")
    timeout = float(options.get("timeout", DEFAULT_TIMEOUT))

    import tempfile

    with tempfile.TemporaryDirectory(prefix="uc_office_") as tmp:
        # cada conversao usa seu proprio --outdir e um user profile isolado
        # (-env:UserInstallation) para nao colidir com outra instancia do
        # LibreOffice rodando ao mesmo tempo (lote em paralelo/GUI + CLI).
        profile_dir = Path(tmp) / "profile"
        cmd = [
            soffice,
            "--headless", "--norestore", "--nolockcheck", "--nodefault",
            f"-env:UserInstallation={profile_dir.as_uri()}",
            "--convert-to", target_ext,
            "--outdir", tmp, str(input_path),
        ]
        result = run_cancellable(cmd, cancel_event=cancel_event, timeout=timeout)

        if result.cancelled:
            raise ConversionError("Conversao cancelada pelo usuario.")
        if result.timed_out:
            raise libreoffice_timeout(timeout)

        produced = Path(tmp) / f"{input_path.stem}.{target_ext}"
        if not produced.exists():
            stderr = (result.stderr or "").lower()
            if "source file could not be loaded" in stderr and _looks_locked(input_path):
                raise file_locked(input_path.name)
            raise ConversionError(
                f"O LibreOffice nao conseguiu converter '{input_path.name}' para '.{target_ext}'.",
                hint="Confira se o arquivo nao esta corrompido, protegido por senha ou aberto em outro programa.",
                detail=RuntimeError(f"{result.stdout}\n{result.stderr}"),
            )

        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(produced), str(output_path))
        except PermissionError as exc:
            raise file_locked(output_path.name) from exc

    return output_path


def _looks_locked(path: Path) -> bool:
    try:
        with open(path, "rb"):
            return False
    except PermissionError:
        return True
    except OSError:
        return False
