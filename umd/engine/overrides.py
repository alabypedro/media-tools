"""Versoes atualizadas das engines, instaladas pelo proprio app.

Plataformas mudam o tempo todo e o yt-dlp/gallery-dl precisam de
atualizacao frequente. Em vez de substituir arquivos do programa (o que
no .exe nem e possivel), o app instala a versao nova numa pasta de dados:

    engines/
        yt-dlp-2026.9.1/yt_dlp/...
        gallery-dl-1.33.0/gallery_dl/...
        active.json          {"yt-dlp": "yt-dlp-2026.9.1", ...}

O worker coloca as pastas ativas no inicio do sys.path ANTES de importar
qualquer engine. A troca de versao e atomica (so o active.json muda,
via os.replace) e apagar a entrada volta para a versao embutida.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

# nome no PyPI -> pacote Python importavel
ENGINE_PACKAGES = {
    "yt-dlp": "yt_dlp",
    "gallery-dl": "gallery_dl",
    "yt-dlp-ejs": "yt_dlp_ejs",
}

_SAFE_DIRNAME = re.compile(r"^[A-Za-z0-9._-]{1,80}$")


def active_file(engines_dir: Path) -> Path:
    return engines_dir / "active.json"


def read_active(engines_dir: Path) -> dict[str, str]:
    try:
        data = json.loads(active_file(engines_dir).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        name: dirname
        for name, dirname in data.items()
        if name in ENGINE_PACKAGES and isinstance(dirname, str) and _SAFE_DIRNAME.match(dirname)
    }


def write_active(engines_dir: Path, mapping: dict[str, str]) -> None:
    engines_dir.mkdir(parents=True, exist_ok=True)
    target = active_file(engines_dir)
    tmp = target.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(mapping, indent=2), encoding="utf-8")
    os.replace(tmp, target)


def override_paths(engines_dir: Path) -> list[Path]:
    paths = []
    for name, dirname in read_active(engines_dir).items():
        candidate = engines_dir / dirname
        if (candidate / ENGINE_PACKAGES[name] / "__init__.py").is_file():
            paths.append(candidate)
    return paths


def activate(engines_dir: str | Path | None) -> list[str]:
    """Poe as versoes atualizadas na frente do sys.path. Chamar antes de importar engines."""
    if not engines_dir:
        return []
    added = []
    for path in override_paths(Path(engines_dir)):
        entry = str(path)
        if entry not in sys.path:
            sys.path.insert(0, entry)
            added.append(entry)
    return added
