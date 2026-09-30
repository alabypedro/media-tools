"""Deteccao de dependencias externas usadas pelas engines.

O YouTube passou a exigir um runtime JavaScript para o yt-dlp resolver
os desafios do player; sem ele, parte dos formatos some. Detectamos os
runtimes suportados pelo yt-dlp que estiverem instalados.
"""

from __future__ import annotations

import shutil

# chave do yt-dlp -> nomes de executavel
_JS_RUNTIMES = {
    "deno": ("deno",),
    "node": ("node",),
    "bun": ("bun",),
    "quickjs": ("qjs", "quickjs"),
}


def find_js_runtimes() -> dict[str, str]:
    found = {}
    for key, names in _JS_RUNTIMES.items():
        for name in names:
            path = shutil.which(name)
            if path:
                found[key] = path
                break
    return found


def ytdlp_js_runtimes(found: dict[str, str] | None = None) -> dict[str, dict[str, str]]:
    found = find_js_runtimes() if found is None else found
    if not found:
        return {"deno": {}}  # padrao do yt-dlp (ele avisa se nao achar)
    return {key: {"path": path} for key, path in found.items()}
