"""Listas de URLs para download em lote: texto colado, TXT ou CSV."""

from __future__ import annotations

import csv
import io
from dataclasses import dataclass, field
from pathlib import Path

from ..core.exceptions import UMDError
from ..core.security import extract_urls, normalize_url, validate_url

MAX_LIST_BYTES = 5 * 1024 * 1024
MAX_URLS = 5000


@dataclass
class BatchParseResult:
    urls: list[str] = field(default_factory=list)
    invalid: list[str] = field(default_factory=list)
    duplicates: int = 0
    truncated: bool = False


def _add(result: BatchParseResult, seen: set[str], candidate: str, allow_local: bool) -> None:
    try:
        url = validate_url(candidate, allow_local=allow_local)
    except UMDError:
        result.invalid.append(candidate[:200])
        return
    if url in seen:
        result.duplicates += 1
        return
    if len(result.urls) >= MAX_URLS:
        result.truncated = True
        return
    seen.add(url)
    result.urls.append(url)


def parse_url_text(text: str, *, allow_local: bool = False) -> BatchParseResult:
    """Uma URL por linha (linhas vazias e comentarios '#' sao ignorados).

    Linhas com texto em volta tambem funcionam: todas as URLs da linha
    sao aproveitadas.
    """
    result = BatchParseResult()
    seen: set[str] = set()
    for raw_line in (text or "").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        found = extract_urls(line)
        if found:
            for url in found:
                _add(result, seen, url, allow_local)
            continue
        candidate = normalize_url(line)
        if candidate.startswith(("http://", "https://")):
            _add(result, seen, candidate, allow_local)
        else:
            result.invalid.append(line[:200])
    return result


def parse_csv_text(text: str, *, allow_local: bool = False) -> BatchParseResult:
    """Qualquer celula de qualquer coluna que contenha uma URL."""
    result = BatchParseResult()
    seen: set[str] = set()
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel
    for row in csv.reader(io.StringIO(text), dialect):
        for cell in row:
            for url in extract_urls(cell):
                _add(result, seen, url, allow_local)
    return result


def load_url_file(path: Path, *, allow_local: bool = False) -> BatchParseResult:
    size = path.stat().st_size
    if size > MAX_LIST_BYTES:
        raise ValueError(f"O arquivo é grande demais ({size // 1024} KB). Limite: {MAX_LIST_BYTES // 1024 // 1024} MB.")
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = raw.decode("latin-1")
    if path.suffix.lower() == ".csv":
        return parse_csv_text(text, allow_local=allow_local)
    return parse_url_text(text, allow_local=allow_local)
