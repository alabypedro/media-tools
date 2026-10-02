"""Publica uma versao nova do Universal Media Tools com um comando so.

    python release.py "O que mudou"            # 1.2.0 -> 1.2.1 (correcao)
    python release.py --minor "O que mudou"    # 1.2.0 -> 1.3.0 (recurso novo)
    python release.py --major "O que mudou"    # 1.2.0 -> 2.0.0
    python release.py --version 1.4.2 "..."    # versao exata

O que ele faz, nesta ordem (para no primeiro erro):
  1. confere o GitHub CLI e se a versao ainda nao existe;
  2. aumenta __version__ em umd/__init__.py;
  3. roda os testes (--skip-tests pula);
  4. gera o executavel e o instalador (build.py --installer);
  5. faz commit de tudo e push (--no-git pula);
  6. publica no GitHub Releases (publish_release.py);
  7. atualiza a lista de builds (builds.json) a partir do GitHub;
  8. apaga de dist/ os arquivos das versoes antigas (elas continuam no GitHub).

Se algo falhar antes do commit, a versao em umd/__init__.py volta ao que era.

Builds antigas:
    python release.py --list             # todas as versoes publicadas
    python release.py --install 1.1.0    # baixa do GitHub, confere o SHA-256 e abre o instalador
    python release.py --sync             # refaz builds.json a partir do GitHub
    python release.py --clean            # so apaga de dist/ os arquivos de versoes antigas

Requer o GitHub CLI autenticado (winget install GitHub.cli; gh auth login) e o Inno Setup 6.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

from publish_release import find_gh

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from umd import UPDATE_REPO  # noqa: E402

VERSION_FILE = ROOT / "umd" / "__init__.py"
BUILDS_FILE = ROOT / "builds.json"
DIST = ROOT / "dist"
_VERSION_LINE = re.compile(r'^(__version__\s*=\s*")([^"]+)(")', re.MULTILINE)
# arquivos de build com a versao no nome (zip, instalador e .sha256)
_BUILD_FILE = re.compile(r"^UniversalMediaTools-(?:Setup-)?(\d+(?:\.\d+)*)(?:-win64\.zip|\.exe(?:\.sha256)?)$")


def step(message: str) -> None:
    print(f"\n==> {message}", flush=True)


def fail(message: str) -> SystemExit:
    return SystemExit(f"[ERRO] {message}")


def current_version() -> str:
    match = _VERSION_LINE.search(VERSION_FILE.read_text(encoding="utf-8"))
    if not match:
        raise fail(f"__version__ nao encontrado em {VERSION_FILE}")
    return match.group(2)


def next_version(current: str, part: str) -> str:
    major, minor, patch = (int(n) for n in (current.split(".") + ["0", "0"])[:3])
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def version_key(version: str) -> tuple[int, ...]:
    return tuple(int(n) for n in re.findall(r"\d+", version))


def write_version(version: str) -> None:
    text = VERSION_FILE.read_bytes().decode("utf-8")  # bytes: preserva as quebras de linha do arquivo
    VERSION_FILE.write_bytes(_VERSION_LINE.sub(rf"\g<1>{version}\g<3>", text, count=1).encode("utf-8"))


def run(args: list[str], what: str) -> None:
    if subprocess.run(args, cwd=ROOT).returncode != 0:
        raise fail(f"Falhou: {what}")


def gh_exe() -> str:
    gh = find_gh()
    if not gh:
        raise fail("GitHub CLI nao encontrado. Instale com: winget install GitHub.cli  (depois: gh auth login)")
    return gh


# ---------------------------------------------------------------- lista de builds

def fetch_builds(gh: str) -> list[dict]:
    """Versoes publicadas no GitHub (a fonte da verdade), da mais nova para a mais antiga."""
    result = subprocess.run([gh, "api", f"repos/{UPDATE_REPO}/releases", "--paginate"],
                            capture_output=True, text=True, encoding="utf-8")
    if result.returncode != 0:
        raise fail(f"Nao foi possivel consultar os Releases do GitHub:\n{result.stderr.strip()}")
    builds = []
    for release in json.loads(result.stdout):
        installer = next((a for a in release.get("assets", [])
                          if a["name"].startswith("UniversalMediaTools-Setup-") and a["name"].endswith(".exe")), None)
        if installer is None or release.get("draft"):
            continue
        builds.append({
            "version": release["tag_name"].lstrip("vV"),
            "date": (release.get("published_at") or "")[:10],
            "notes": (release.get("body") or "").strip(),
            "installer": installer["name"],
            "size_mb": round(installer["size"] / 1024 / 1024),
            "sha256": (installer.get("digest") or "").removeprefix("sha256:"),
            "download": installer["browser_download_url"],
            "page": release["html_url"],
        })
    return sorted(builds, key=lambda b: version_key(b["version"]), reverse=True)


def sync_builds(gh: str) -> list[dict]:
    builds = fetch_builds(gh)
    BUILDS_FILE.write_text(json.dumps(builds, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return builds


def load_builds() -> list[dict]:
    return json.loads(BUILDS_FILE.read_text(encoding="utf-8")) if BUILDS_FILE.exists() else []


def print_builds(builds: list[dict]) -> None:
    if not builds:
        print("Nenhuma build registrada. Rode: python release.py --sync")
        return
    for index, build in enumerate(builds):
        mark = "  (atual)" if index == 0 else ""
        print(f"{build['version']:<8} {build['date']}  {build['size_mb']} MB{mark}")
        if build["notes"]:
            print(f"         {build['notes'].splitlines()[0]}")
        print(f"         {build['download']}")
    print("\nInstalar uma delas: python release.py --install <versao>")


def install_build(gh: str, version: str) -> None:
    version = version.lstrip("vV")
    build = next((b for b in load_builds() or fetch_builds(gh) if b["version"] == version), None)
    if build is None:  # a lista local pode estar atrasada
        build = next((b for b in fetch_builds(gh) if b["version"] == version), None)
    if build is None:
        raise fail(f"Versao {version} nao encontrada. Veja as disponiveis com: python release.py --list")
    folder = Path(tempfile.mkdtemp(prefix="umt_build_"))
    step(f"Baixando {build['installer']} ({build['size_mb']} MB)")
    run([gh, "release", "download", f"v{version}", "--repo", UPDATE_REPO, "--pattern", build["installer"],
         "--dir", str(folder)], "download do instalador")
    installer = folder / build["installer"]
    digest = hashlib.sha256()
    with open(installer, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    if not build["sha256"] or digest.hexdigest() != build["sha256"]:
        installer.unlink(missing_ok=True)
        raise fail("O arquivo baixado nao confere com o SHA-256 publicado; ele foi descartado.")
    print(f"    SHA-256 conferido. Abrindo o instalador: {installer}")
    print("    (feche o Universal Media Tools antes de continuar a instalacao)")
    os.startfile(installer)  # noqa: S606 - instalador baixado do proprio repositorio, com hash conferido


# ---------------------------------------------------------------- limpeza

def clean_dist(keep_version: str, published: set[str]) -> None:
    """Apaga de dist/ os arquivos de outras versoes -- so das que estao publicadas no GitHub
    (de la da para baixar de novo). A pasta dist/UniversalMediaTools e sempre a do ultimo build."""
    if not DIST.is_dir():
        return
    freed = 0
    for path in sorted(DIST.iterdir()):
        match = _BUILD_FILE.match(path.name)
        if not path.is_file() or not match or match.group(1) == keep_version:
            continue
        if match.group(1) not in published:
            print(f"    mantido (versao {match.group(1)} nao esta publicada): {path.name}")
            continue
        freed += path.stat().st_size
        path.unlink()
        print(f"    apagado: {path.name}")
    print(f"    {freed / 1024 / 1024:.0f} MB liberados em dist/")


# ---------------------------------------------------------------- publicar

def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8")


def release(args: argparse.Namespace) -> int:
    gh = gh_exe()
    if subprocess.run([gh, "auth", "status"], capture_output=True).returncode != 0:
        raise fail("GitHub CLI sem login. Rode: gh auth login")
    old = current_version()
    new = args.version or next_version(old, "major" if args.major else "minor" if args.minor else "patch")
    if not re.fullmatch(r"\d+\.\d+\.\d+", new):
        raise fail(f"Versao invalida: {new} (use o formato 1.2.3)")
    tag = f"v{new}"
    if subprocess.run([gh, "release", "view", tag, "--repo", UPDATE_REPO], capture_output=True).returncode == 0:
        raise fail(f"A versao {new} ja esta publicada. Escolha outra com --version.")
    notes = args.notes or f"Universal Media Tools {new}"
    print(f"Universal Media Tools {old} -> {new}\nNotas: {notes}")

    step(f"Versao {new} em umd/__init__.py")
    write_version(new)
    try:
        if not args.skip_tests:
            step("Rodando os testes (use --skip-tests para pular)")
            run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider"], "testes")
        run([sys.executable, str(ROOT / "build.py"), "--installer"], "build")
    except BaseException:
        write_version(old)
        print(f"\nA versao em umd/__init__.py voltou para {old}.")
        raise

    if not args.no_git:
        step("Commit e push")
        git("add", "-A")
        if git("diff", "--cached", "--quiet").returncode != 0:
            run(["git", "commit", "-q", "-m", f"Universal Media Tools {new}"], "git commit")
        run(["git", "push", "-q"], "git push")

    step("Publicando no GitHub Releases")
    run([sys.executable, str(ROOT / "publish_release.py"), notes], "publicacao (o build ja esta pronto: "
        f'corrija o problema e rode  python publish_release.py "{notes}")')

    step("Atualizando a lista de builds (builds.json)")
    builds = sync_builds(gh)
    if not args.no_git and git("status", "--porcelain", "--", BUILDS_FILE.name).stdout.strip():
        git("add", BUILDS_FILE.name)
        run(["git", "commit", "-q", "-m", f"builds.json: {new}"], "git commit")
        run(["git", "push", "-q"], "git push")

    step("Apagando de dist/ os arquivos das versoes antigas")
    clean_dist(new, {b["version"] for b in builds})
    print(f"\n[OK] Versao {new} publicada. O app instalado ja encontra em \"Verificar atualizacoes\".")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("notes", nargs="?", help="o que mudou nesta versao (aparece na tela de atualizacao do app)")
    bump = parser.add_mutually_exclusive_group()
    bump.add_argument("--minor", action="store_true", help="recurso novo: 1.2.0 -> 1.3.0")
    bump.add_argument("--major", action="store_true", help="mudanca grande: 1.2.0 -> 2.0.0")
    bump.add_argument("--version", metavar="X.Y.Z", help="versao exata")
    parser.add_argument("--skip-tests", action="store_true", help="nao roda os testes antes do build")
    parser.add_argument("--no-git", action="store_true", help="nao faz commit nem push")
    parser.add_argument("--list", action="store_true", help="mostra as builds publicadas")
    parser.add_argument("--install", metavar="VERSAO", help="baixa e abre o instalador de uma build publicada")
    parser.add_argument("--sync", action="store_true", help="refaz builds.json a partir do GitHub")
    parser.add_argument("--clean", action="store_true", help="apaga de dist/ os arquivos de versoes antigas")
    args = parser.parse_args(argv)

    if args.list:
        print_builds(load_builds())
    elif args.install:
        install_build(gh_exe(), args.install)
    elif args.sync:
        print_builds(sync_builds(gh_exe()))
    elif args.clean:
        clean_dist(current_version(), {b["version"] for b in fetch_builds(gh_exe())})
    else:
        return release(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
