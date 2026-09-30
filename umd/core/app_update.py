"""Atualizacao do proprio programa pelos Releases do GitHub.

(As engines yt-dlp/gallery-dl tem atualizacao propria: services/engine_service.py.)

Fluxo (nada acontece sem o usuario confirmar):
  1. :func:`check_for_update` consulta o ultimo Release de ``UPDATE_REPO`` (somente leitura);
  2. :func:`download_installer` baixa ``UniversalMediaTools-Setup-<versao>.exe`` e confere o
     SHA-256 publicado no Release (sem hash conferido, nao instala);
  3. :func:`launch_installer` abre o instalador em modo silencioso; o programa fecha, o
     instalador espera ele terminar, instala por cima e reabre o programa.

Seguranca: so HTTPS e so hosts do GitHub (inclusive depois de redirecionamentos),
tamanho maximo, hash obrigatorio e o instalador fica numa pasta temporaria propria.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from .. import UPDATE_REPO, __version__
from .logger import get_logger

log = get_logger("app_update")

INSTALLER_PATTERN = re.compile(r"^UniversalMediaTools-Setup-[\w.\-]+\.exe$", re.IGNORECASE)
ALLOWED_HOSTS = {"api.github.com", "github.com", "objects.githubusercontent.com",
                 "release-assets.githubusercontent.com"}
MAX_DOWNLOAD_BYTES = 600 * 1024 * 1024
TIMEOUT = 20
USER_AGENT = f"UniversalMediaTools/{__version__} (+updater)"

ProgressFn = Callable[[int, int], None]  # (recebidos, total)


class UpdateError(Exception):
    """Erro com mensagem pronta para o usuario."""


@dataclass
class UpdateInfo:
    version: str
    notes: str
    page_url: str
    installer_name: str
    installer_url: str
    installer_size: int
    sha256: str  # hex, minusculo

    @property
    def available(self) -> bool:
        return is_newer(self.version, __version__)


def parse_version(text: str) -> tuple[int, ...]:
    """'v1.2.10' -> (1, 2, 10). Sufixos como '-beta' sao ignorados."""
    numbers = re.findall(r"\d+", (text or "").split("-")[0])
    return tuple(int(n) for n in numbers[:4]) or (0,)


def is_newer(candidate: str, current: str) -> bool:
    a, b = parse_version(candidate), parse_version(current)
    width = max(len(a), len(b))
    return a + (0,) * (width - len(a)) > b + (0,) * (width - len(b))


# ---------------------------------------------------------------- rede

def _check_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise UpdateError(f"Endereço de atualização não permitido: {parsed.hostname or url}")


def _open(url: str, accept: str):
    _check_url(url)
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    try:
        response = urllib.request.urlopen(request, timeout=TIMEOUT)  # noqa: S310 - URL validada acima
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            raise UpdateError("Nenhuma versão publicada foi encontrada.") from exc
        if exc.code in (403, 429):
            raise UpdateError("O GitHub limitou as consultas por agora. Tente de novo em alguns minutos.") from exc
        raise UpdateError(f"O servidor de atualizações respondeu com erro {exc.code}.") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise UpdateError("Sem conexão com a internet (ou o GitHub não respondeu).") from exc
    _check_url(response.geturl())  # redirecionamentos tambem precisam ficar no GitHub
    return response


def check_for_update(repo: str = "") -> UpdateInfo:
    """Consulta o ultimo Release publicado. Nao baixa nada."""
    repo = repo or UPDATE_REPO
    if not re.fullmatch(r"[\w.\-]+/[\w.\-]+", repo or ""):
        raise UpdateError("A origem das atualizações não foi configurada.")
    with _open(f"https://api.github.com/repos/{repo}/releases/latest", "application/vnd.github+json") as response:
        try:
            release = json.loads(response.read(2 * 1024 * 1024))
        except json.JSONDecodeError as exc:
            raise UpdateError("Resposta inválida do servidor de atualizações.") from exc
    assets = release.get("assets") or []
    installer = next((a for a in assets if INSTALLER_PATTERN.match(a.get("name", ""))), None)
    if installer is None:
        raise UpdateError("A versão publicada não tem instalador para Windows.")

    sha256 = ""
    digest = installer.get("digest") or ""  # o GitHub publica "sha256:<hex>" de cada arquivo
    if digest.lower().startswith("sha256:"):
        sha256 = digest.split(":", 1)[1]
    else:
        hash_asset = next((a for a in assets if a.get("name") == installer["name"] + ".sha256"), None)
        if hash_asset is not None:
            with _open(hash_asset["browser_download_url"], "application/octet-stream") as response:
                parts = response.read(4096).decode("utf-8", errors="replace").split()
            sha256 = parts[0] if parts else ""
    sha256 = sha256.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise UpdateError("A versão publicada não tem o código de verificação (SHA-256) do instalador; "
                          "por segurança ela não será instalada.")

    return UpdateInfo(
        version=str(release.get("tag_name") or "").lstrip("vV"),
        notes=str(release.get("body") or "").strip(),
        page_url=str(release.get("html_url") or f"https://github.com/{repo}/releases/latest"),
        installer_name=installer["name"],
        installer_url=installer["browser_download_url"],
        installer_size=int(installer.get("size") or 0),
        sha256=sha256,
    )


def download_installer(info: UpdateInfo, progress: ProgressFn | None = None,
                       cancel: threading.Event | None = None) -> Path:
    """Baixa o instalador numa pasta temporaria e confere o SHA-256."""
    if info.installer_size > MAX_DOWNLOAD_BYTES:
        raise UpdateError("O instalador publicado é grande demais; download recusado.")
    folder = Path(tempfile.mkdtemp(prefix="umt_update_"))
    target = folder / Path(info.installer_name).name
    digest = hashlib.sha256()
    received = 0
    log.info("app update download started: %s", info.installer_url)
    try:
        with _open(info.installer_url, "application/octet-stream") as response, open(target, "wb") as handle:
            while True:
                if cancel is not None and cancel.is_set():
                    raise UpdateError("Download cancelado.")
                chunk = response.read(256 * 1024)
                if not chunk:
                    break
                received += len(chunk)
                if received > MAX_DOWNLOAD_BYTES:
                    raise UpdateError("O download passou do tamanho máximo permitido.")
                digest.update(chunk)
                handle.write(chunk)
                if progress:
                    progress(received, info.installer_size)
        if digest.hexdigest() != info.sha256:
            raise UpdateError("O arquivo baixado não confere com o código de verificação (SHA-256). "
                              "Ele foi descartado; tente novamente mais tarde.")
    except BaseException:
        target.unlink(missing_ok=True)
        try:
            folder.rmdir()
        except OSError:
            pass
        raise
    log.info("app update downloaded and verified: %s (%d bytes)", target, received)
    return target


# ---------------------------------------------------------------- instalacao

def installed_app_dir() -> Path | None:
    """Pasta do programa instalado pelo instalador (tem o desinstalador ao lado), ou None."""
    if not getattr(sys, "frozen", False):
        return None
    folder = Path(sys.executable).resolve().parent
    return folder if (folder / "unins000.exe").exists() else None


def can_self_update() -> bool:
    return sys.platform == "win32" and installed_app_dir() is not None


def launch_installer(installer: Path) -> None:
    """Abre o instalador (silencioso, com barra de progresso) e deixa ele reabrir o programa.

    O chamador deve fechar o programa logo em seguida; o instalador espera este
    processo (/WAITPID) terminar antes de substituir os arquivos.
    """
    if installed_app_dir() is None:
        raise UpdateError("A atualização automática só funciona na versão instalada pelo instalador.")
    # Mesmo AppId: o instalador reaproveita a pasta e o modo (usuario/todos) da instalacao atual.
    args = [str(installer), "/SILENT", "/SUPPRESSMSGBOXES", "/NORESTART", "/CLOSEAPPLICATIONS",
            "/RELAUNCH=1", f"/WAITPID={os.getpid()}"]
    log.info("launching app update installer: %s", installer)
    subprocess.Popen(args, close_fds=True,  # noqa: S603 - sem shell; caminho do instalador verificado
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0)
                     | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))


def release_page(info: UpdateInfo | None = None) -> str:
    return info.page_url if info else f"https://github.com/{UPDATE_REPO}/releases/latest"
