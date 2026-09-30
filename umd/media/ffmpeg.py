"""Localizacao e uso basico do FFmpeg.

Ordem de busca:
1. caminho configurado pelo usuario (Configuracoes > Engines);
2. ffmpeg empacotado junto com o app (pasta ffmpeg/ do .exe);
3. ffmpeg do PATH do sistema;
4. binario do pacote imageio-ffmpeg (instalado com o requirements.txt).
"""

from __future__ import annotations

import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path

from ..core.paths import resource_path
from ..core.process import run_quiet

_EXE = "ffmpeg.exe" if sys.platform == "win32" else "ffmpeg"


@dataclass
class FFmpegInfo:
    path: str | None
    version: str | None
    source: str  # "custom" | "bundled" | "system" | "imageio" | "missing"
    has_ffprobe: bool = False

    @property
    def ok(self) -> bool:
        return bool(self.path and self.version)


def _candidates(custom_path: str = "") -> list[tuple[str, Path]]:
    found: list[tuple[str, Path]] = []
    if custom_path:
        custom = Path(custom_path)
        found.append(("custom", custom / _EXE if custom.is_dir() else custom))
    found.append(("bundled", resource_path("ffmpeg", _EXE)))
    system = shutil.which("ffmpeg")
    if system:
        found.append(("system", Path(system)))
    try:
        import imageio_ffmpeg

        found.append(("imageio", Path(imageio_ffmpeg.get_ffmpeg_exe())))
    except Exception:  # noqa: BLE001 - pacote ausente ou sem binario para esta plataforma
        pass
    return found


def find_ffmpeg(custom_path: str = "") -> str | None:
    for _source, path in _candidates(custom_path):
        if path.is_file():
            return str(path)
    return None


def ffmpeg_version(path: str) -> str | None:
    code, output = run_quiet([path, "-hide_banner", "-version"], timeout=15)
    if code != 0:
        return None
    match = re.search(r"ffmpeg version (\S+)", output)
    return match.group(1) if match else "?"


def ffmpeg_info(custom_path: str = "") -> FFmpegInfo:
    for source, path in _candidates(custom_path):
        if path.is_file():
            version = ffmpeg_version(str(path))
            if version:
                probe = path.with_name(path.name.replace("ffmpeg", "ffprobe", 1))
                return FFmpegInfo(str(path), version, source, has_ffprobe=probe.is_file() and probe != path)
    return FFmpegInfo(None, None, "missing")


def extract_frame(ffmpeg: str, video: Path, output: Path, at_seconds: float = 1.0, width: int = 360) -> bool:
    """Gera uma miniatura (jpg) a partir de um video. Devolve True se criou."""
    output.parent.mkdir(parents=True, exist_ok=True)
    args = [
        ffmpeg, "-hide_banner", "-loglevel", "error", "-y",
        "-ss", f"{max(0.0, at_seconds):.2f}", "-i", str(video.resolve()),
        "-frames:v", "1", "-vf", f"scale={width}:-2", "-q:v", "4", str(output.resolve()),
    ]
    code, _ = run_quiet(args, timeout=30)
    if code != 0 or not output.exists():
        if at_seconds > 0:  # video muito curto: tenta o primeiro quadro
            return extract_frame(ffmpeg, video, output, 0.0, width)
        return False
    return True
