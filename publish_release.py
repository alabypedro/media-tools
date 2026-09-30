"""Publica uma nova versao do Universal Media Tools no GitHub Releases.

Depois disso, todo programa instalado encontra a versao nova pelo link
"Verificar atualizacoes" (ou pelo aviso ao abrir).

Passo a passo:
  1. aumente __version__ em umd/__init__.py (ex.: 1.0.0 -> 1.1.0);
  2. feche o app e rode:  python build.py --installer
  3. rode:  python publish_release.py "O que mudou nesta versao"

Requer o GitHub CLI autenticado (winget install GitHub.cli; gh auth login).
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from umd import UPDATE_REPO, __version__  # noqa: E402


def find_gh() -> str | None:
    found = shutil.which("gh")
    if found:
        return found
    default = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "GitHub CLI" / "gh.exe"
    return str(default) if default.is_file() else None


def main(argv: list[str]) -> int:
    notes = argv[0] if argv else f"Universal Media Tools {__version__}"
    gh = find_gh()
    if not gh:
        print("[ERRO] GitHub CLI nao encontrado. Instale com: winget install GitHub.cli  (depois: gh auth login)")
        return 1
    installer = ROOT / "dist" / f"UniversalMediaTools-Setup-{__version__}.exe"
    if not installer.exists():
        print(f"[ERRO] {installer.name} nao encontrado. Rode antes: python build.py --installer")
        return 1

    digest = hashlib.sha256()
    with open(installer, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    checksum = installer.with_name(installer.name + ".sha256")
    checksum.write_text(f"{digest.hexdigest()}  {installer.name}\n", encoding="ascii")
    tag = f"v{__version__}"
    print(f"Publicando {tag} em {UPDATE_REPO}")
    print(f"  {installer.name}  sha256={digest.hexdigest()}")
    result = subprocess.run([gh, "release", "create", tag, str(installer), str(checksum),
                             "--repo", UPDATE_REPO, "--title", f"Universal Media Tools {__version__}",
                             "--notes", notes, "--latest"])
    if result.returncode != 0:
        print("[ERRO] A publicacao falhou (veja a mensagem do gh acima). "
              "Se a versao ja existe, aumente __version__ em umd/__init__.py.")
        return 1
    print(f"[OK] Versao {__version__} publicada: https://github.com/{UPDATE_REPO}/releases/tag/{tag}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
