"""Versoes das engines, FFmpeg, runtimes JS e atualizacao segura.

Atualizacao (yt-dlp / gallery-dl):
1. consulta a versao mais recente na API JSON do PyPI (HTTPS);
2. baixa o wheel oficial (so de pypi.org / files.pythonhosted.org);
3. confere o SHA-256 publicado pelo PyPI -- arquivo diferente e descartado;
4. extrai so os pacotes esperados, recusando caminhos absolutos/'..';
5. valida a versao nova rodando o worker com ela ANTES de ativar;
6. ativa trocando o engines/active.json de forma atomica.
Nada do programa instalado e sobrescrito; "Restaurar versao embutida"
desfaz a qualquer momento.
"""

from __future__ import annotations

import hashlib
import re
import shutil
import zipfile
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from ..core.config import SettingsStore
from ..core.exceptions import ErrorCode, UMDError
from ..core.logger import get_logger
from ..engine import overrides
from ..engine.environment import find_js_runtimes
from ..engine.runner import EngineRunner
from ..media.ffmpeg import FFmpegInfo, ffmpeg_info
from ..providers.registry import all_providers

log = get_logger("engines")

UPDATABLE = ("yt-dlp", "gallery-dl")
PYPI_JSON = "https://pypi.org/pypi/{name}/json"
PYPI_VERSION_JSON = "https://pypi.org/pypi/{name}/{version}/json"
_ALLOWED_HOSTS = ("pypi.org", "files.pythonhosted.org")
MAX_WHEEL_BYTES = 60 * 1024 * 1024


@dataclass
class EngineStatus:
    name: str
    version: str | None
    location: str | None = None
    override: bool = False
    error: str | None = None

    @property
    def ok(self) -> bool:
        return bool(self.version)


@dataclass
class WheelInfo:
    name: str
    version: str
    url: str
    sha256: str
    size: int | None = None


@dataclass
class UpdateInfo:
    name: str
    current: str | None
    latest: str
    wheel: WheelInfo
    companions: list[WheelInfo] = field(default_factory=list)  # ex.: yt-dlp-ejs fixado pelo yt-dlp

    @property
    def available(self) -> bool:
        return self.current is None or version_tuple(self.latest) > version_tuple(self.current)


def version_tuple(version: str | None) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", version or "")) or (0,)


class EngineService:
    def __init__(self, runner: EngineRunner, engines_dir: Path, settings_store: SettingsStore):
        self.runner = runner
        self.engines_dir = engines_dir
        self._store = settings_store

    # ------------------------------------------------------------ estado

    def versions(self) -> dict[str, EngineStatus]:
        try:
            result = self.runner.run({"action": "versions"}, timeout=90).result or {}
        except UMDError as exc:
            return {name: EngineStatus(name, None, error=exc.message) for name in overrides.ENGINE_PACKAGES}
        statuses = {}
        for name, data in (result.get("engines") or {}).items():
            statuses[name] = EngineStatus(
                name=name,
                version=data.get("version"),
                location=data.get("location"),
                override=bool(data.get("override")),
                error=data.get("error"),
            )
        return statuses

    def ffmpeg(self) -> FFmpegInfo:
        return ffmpeg_info(self._store.settings.ffmpeg_path)

    @staticmethod
    def js_runtimes() -> dict[str, str]:
        return find_js_runtimes()

    def platform_support(self) -> list[dict[str, Any]]:
        """Para cada plataforma cadastrada: quais engines tem extrator dedicado de verdade."""
        providers = [p for p in all_providers() if getattr(p, "sample_url", None)]
        urls = [p.sample_url for p in providers]
        result = self.runner.run({"action": "check_support", "urls": urls}, timeout=120).result or {}
        rows = []
        for provider in providers:
            entry = result.get(provider.sample_url) or {}
            rows.append(
                {
                    "key": provider.key,
                    "name": provider.name,
                    "yt-dlp": entry.get("yt-dlp") or [],
                    "yt-dlp-broken": entry.get("yt-dlp-broken") or [],
                    "gallery-dl": entry.get("gallery-dl"),
                }
            )
        return rows

    # ------------------------------------------------------------ atualizacao

    def check_update(self, name: str, current: str | None = None) -> UpdateInfo:
        if name not in UPDATABLE:
            raise ValueError(name)
        data = _get_json(PYPI_JSON.format(name=name))
        latest = str(data["info"]["version"])
        wheel = _pick_wheel(name, latest, data.get("urls") or [])
        companions: list[WheelInfo] = []
        if name == "yt-dlp":
            pinned = _pinned_requirement(data["info"].get("requires_dist") or [], "yt-dlp-ejs")
            if pinned:
                ejs = _get_json(PYPI_VERSION_JSON.format(name="yt-dlp-ejs", version=pinned))
                companions.append(_pick_wheel("yt-dlp-ejs", pinned, ejs.get("urls") or []))
        return UpdateInfo(name=name, current=current, latest=latest, wheel=wheel, companions=companions)

    def apply_update(self, update: UpdateInfo, progress: Callable[[str], None] | None = None) -> str:
        notify = progress or (lambda _msg: None)
        staging_root = self.engines_dir / "_staging"
        shutil.rmtree(staging_root, ignore_errors=True)
        staging_root.mkdir(parents=True, exist_ok=True)
        wheels = [update.wheel, *update.companions]
        try:
            mapping: dict[str, str] = {}
            for wheel in wheels:
                notify(f"Baixando {wheel.name} {wheel.version}…")
                archive = staging_root / f"{wheel.name}-{wheel.version}.whl"
                _download_verified(wheel, archive)
                dirname = f"{wheel.name}-{wheel.version}"
                notify(f"Instalando {wheel.name} {wheel.version}…")
                _extract_wheel(archive, staging_root / dirname, overrides.ENGINE_PACKAGES[wheel.name])
                archive.unlink(missing_ok=True)
                mapping[wheel.name] = dirname

            notify("Validando a nova versão…")
            overrides.write_active(staging_root, mapping)
            check = self.runner.run({"action": "versions", "engines_dir": str(staging_root)}, timeout=120).result or {}
            reported = (check.get("engines") or {}).get(update.name) or {}
            if not reported.get("override") or version_tuple(reported.get("version")) != version_tuple(update.latest):
                raise UMDError(ErrorCode.ENGINE_CRASH, details=f"Validation failed: {reported}")

            active = overrides.read_active(self.engines_dir)
            previous = {name: active.get(name) for name in mapping}
            for dirname in mapping.values():
                target = self.engines_dir / dirname
                shutil.rmtree(target, ignore_errors=True)
                (staging_root / dirname).replace(target)
            active.update(mapping)
            overrides.write_active(self.engines_dir, active)
            for name, old in previous.items():
                if old and old != mapping[name]:
                    shutil.rmtree(self.engines_dir / old, ignore_errors=True)
            log.info("engine updated: %s -> %s", update.name, update.latest)
            return update.latest
        finally:
            shutil.rmtree(staging_root, ignore_errors=True)

    def reset(self, name: str) -> None:
        """Volta para a versao embutida no app."""
        active = overrides.read_active(self.engines_dir)
        names = [name] + (["yt-dlp-ejs"] if name == "yt-dlp" else [])
        for item in names:
            dirname = active.pop(item, None)
            if dirname:
                shutil.rmtree(self.engines_dir / dirname, ignore_errors=True)
        overrides.write_active(self.engines_dir, active)


# ---------------------------------------------------------------- helpers

def _check_host(url: str) -> None:
    parts = urlsplit(url)
    if parts.scheme != "https" or (parts.hostname or "") not in _ALLOWED_HOSTS:
        raise UMDError(ErrorCode.ACCESS_DENIED, details=f"Refusing to download engine from {url!r}")


def _get_json(url: str) -> dict[str, Any]:
    import requests

    _check_host(url)
    try:
        response = requests.get(url, timeout=(10, 30), headers={"Accept": "application/json"})
        response.raise_for_status()
        return response.json()
    except requests.RequestException as exc:
        raise UMDError(ErrorCode.NETWORK, details=f"{url}: {exc}") from None
    except ValueError as exc:
        raise UMDError(ErrorCode.UNKNOWN, details=f"Invalid JSON from {url}: {exc}") from None


def _pick_wheel(name: str, version: str, files: list[dict[str, Any]]) -> WheelInfo:
    for item in files:
        filename = str(item.get("filename") or "")
        if item.get("packagetype") == "bdist_wheel" and filename.endswith("-py3-none-any.whl") and not item.get("yanked"):
            sha = (item.get("digests") or {}).get("sha256")
            if not sha:
                continue
            _check_host(str(item.get("url")))
            return WheelInfo(name, version, str(item["url"]), sha, item.get("size"))
    raise UMDError(ErrorCode.ENGINE_MISSING, details=f"No universal wheel found for {name} {version}")


def _pinned_requirement(requires: list[str], package: str) -> str | None:
    for requirement in requires:
        match = re.match(rf"^{re.escape(package)}\s*==\s*([\w.]+)", requirement.strip(), re.I)
        if match:
            return match.group(1)
    return None


def _download_verified(wheel: WheelInfo, target: Path) -> None:
    import requests

    _check_host(wheel.url)
    digest = hashlib.sha256()
    written = 0
    try:
        with requests.get(wheel.url, stream=True, timeout=(10, 60)) as response:
            response.raise_for_status()
            with open(target, "wb") as fh:
                for chunk in response.iter_content(256 * 1024):
                    written += len(chunk)
                    if written > MAX_WHEEL_BYTES:
                        raise UMDError(ErrorCode.FILE_TOO_LARGE, details="Engine package too large")
                    digest.update(chunk)
                    fh.write(chunk)
    except requests.RequestException as exc:
        raise UMDError(ErrorCode.NETWORK, details=str(exc)) from None
    if digest.hexdigest().lower() != wheel.sha256.lower():
        target.unlink(missing_ok=True)
        raise UMDError(ErrorCode.ACCESS_DENIED, "O arquivo baixado não confere com a assinatura publicada. A atualização foi cancelada.",
                       details=f"SHA-256 mismatch for {wheel.url}")


def _extract_wheel(archive: Path, destination: Path, package: str) -> None:
    """Extrai so '<pacote>/' e '<pacote>-*.dist-info/', sem path traversal."""
    destination.mkdir(parents=True, exist_ok=True)
    root = destination.resolve()
    with zipfile.ZipFile(archive) as zf:
        for member in zf.infolist():
            name = member.filename.replace("\\", "/")
            if name.startswith("/") or re.match(r"^[A-Za-z]:", name) or ".." in name.split("/"):
                raise UMDError(ErrorCode.ACCESS_DENIED, details=f"Unsafe path in engine package: {name!r}")
            top = name.split("/", 1)[0]
            if not (top == package or (top.startswith(package + "-") and top.endswith(".dist-info"))):
                continue  # scripts, .data, etc. nao sao necessarios
            target = (root / name).resolve()
            if root not in target.parents:
                raise UMDError(ErrorCode.ACCESS_DENIED, details=f"Unsafe path in engine package: {name!r}")
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)
    if not (destination / package / "__init__.py").is_file():
        raise UMDError(ErrorCode.ENGINE_MISSING, details=f"Package {package} not found in wheel")
