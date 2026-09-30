"""Utilitarios de caminho: nomes unicos, politica de conflito e
protecao contra path traversal (zip slip e afins).
"""

from __future__ import annotations

import re
from enum import Enum
from pathlib import Path


def clean_path_arg(raw: str) -> str:
    """Remove aspas/espacos de um caminho colado do Explorer do Windows."""
    return raw.strip().strip('"').strip("'").strip()


def unique_path(path: Path) -> Path:
    """Se `path` ja existe, devolve 'nome (1).ext', 'nome (2).ext' etc.
    (nunca sobrescreve por acidente)."""
    if not path.exists():
        return path
    n = 1
    while True:
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not candidate.exists():
            return candidate
        n += 1


class ConflictPolicy(str, Enum):
    ASK = "ask"          # so faz sentido na GUI; CLI/headless trata como RENAME
    RENAME = "rename"    # padrao seguro: "arquivo (1).ext"
    OVERWRITE = "overwrite"
    SKIP = "skip"


def resolve_conflict(path: Path, policy: ConflictPolicy) -> Path | None:
    """Aplica a politica de conflito a um caminho de destino.

    Devolve o caminho final a usar, ou None se o arquivo deve ser pulado.
    """
    if not path.exists():
        return path
    if policy in (ConflictPolicy.RENAME, ConflictPolicy.ASK):
        return unique_path(path)
    if policy is ConflictPolicy.OVERWRITE:
        return path
    if policy is ConflictPolicy.SKIP:
        return None
    raise ValueError(f"Politica de conflito desconhecida: {policy}")


_INVALID_WINDOWS_CHARS = '<>:"/\\|?*'
_WINDOWS_RESERVED_NAMES = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def sanitize_filename(name: str) -> str:
    """Troca caracteres invalidos no Windows por '_' e evita nomes reservados."""
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    stem = "".join("_" if c in _INVALID_WINDOWS_CHARS else c for c in stem)
    stem = " ".join(stem.split()).strip(" .")
    if not stem:
        stem = "arquivo"
    if stem.upper() in _WINDOWS_RESERVED_NAMES:
        stem = f"_{stem}"
    return f"{stem}.{ext}" if ext else stem


def is_within_directory(directory: Path, target: Path) -> bool:
    """True se `target` esta dentro de `directory` (protege contra
    '../../etc/passwd' e caminhos absolutos vindos de dentro de um
    arquivo compactado malicioso)."""
    try:
        directory = directory.resolve()
        target = target.resolve()
    except OSError:
        return False
    return directory == target or directory in target.parents


def safe_member_path(destination: Path, member_name: str) -> Path:
    """Resolve o caminho de destino de um membro de arquivo compactado
    (zip/tar/7z) garantindo que ele fique dentro de `destination`.

    Levanta ValueError se o nome do membro tentar escapar da pasta
    destino (path traversal / "zip slip").
    """
    # normaliza separadores e remove tentativas de caminho absoluto/drive
    cleaned = member_name.replace("\\", "/")
    cleaned = re.sub(r"^([A-Za-z]:)?/+", "", cleaned)
    candidate = (destination / cleaned).resolve()
    if not is_within_directory(destination, candidate):
        raise ValueError(f"Nome de arquivo suspeito dentro do compactado: {member_name!r}")
    return candidate


def page_range_to_indices(spec: str, page_count: int) -> list[int]:
    """Converte uma especificacao de paginas tipo '1-5,10,20-25' numa
    lista de indices 0-based, na ordem em que foram pedidos, sem
    duplicatas. Paginas fora do intervalo do documento sao ignoradas
    silenciosamente na ponta (o chamador pode avisar se quiser)."""
    if not spec or not spec.strip():
        return list(range(page_count))

    indices: list[int] = []
    seen: set[int] = set()
    for raw_part in spec.split(","):
        part = raw_part.strip()
        if not part:
            continue
        if "-" in part:
            start_s, _, end_s = part.partition("-")
            try:
                start, end = int(start_s), int(end_s)
            except ValueError as exc:
                raise ValueError(f"Intervalo de paginas invalido: {part!r}") from exc
            if start > end:
                start, end = end, start
            page_numbers = range(start, end + 1)
        else:
            try:
                page_numbers = [int(part)]
            except ValueError as exc:
                raise ValueError(f"Numero de pagina invalido: {part!r}") from exc

        for page_number in page_numbers:
            idx = page_number - 1
            if idx < 0 or idx >= page_count:
                continue
            if idx not in seen:
                seen.add(idx)
                indices.append(idx)
    return indices
