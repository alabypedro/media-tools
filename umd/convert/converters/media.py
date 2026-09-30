"""Conversao de video/audio via ffmpeg. Mesma ideia do antigo
convert_media(), agora cancelavel e sem deixar processo ffmpeg
abandonado (item 18).

O ffmpeg e localizado como no resto do app (umd.media.ffmpeg): o
caminho configurado em Configuracoes > Engines, o empacotado junto do
.exe, o do PATH ou o do pacote imageio-ffmpeg."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from ..core.errors import ConversionError
from ..core.procutil import run_cancellable
from ..core.registry import AUDIO


def _ffmpeg_exe(custom_path: str = "") -> str:
    from ...media.ffmpeg import find_ffmpeg

    path = find_ffmpeg(custom_path)
    if not path:
        raise ConversionError(
            "O FFmpeg nao foi encontrado; ele e necessario para converter video/audio.",
            hint="Indique o ffmpeg.exe em Configuracoes > Engines, ou instale com: pip install imageio-ffmpeg",
        )
    return path


def convert(
    input_path: Path,
    output_path: Path,
    options: dict[str, Any] | None = None,
    cancel_event: threading.Event | None = None,
) -> Path:
    options = options or {}
    target_ext = output_path.suffix.lower().lstrip(".")

    args = [_ffmpeg_exe(options.get("ffmpeg_path", "")), "-y", "-i", str(input_path)]
    if target_ext in AUDIO.extensions:
        args.append("-vn")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    args.append(str(output_path))

    result = run_cancellable(args, cancel_event=cancel_event, timeout=options.get("timeout", 1800))

    if result.cancelled:
        output_path.unlink(missing_ok=True)
        raise ConversionError("Conversao cancelada pelo usuario.")
    if result.timed_out:
        output_path.unlink(missing_ok=True)
        raise ConversionError(
            f"A conversao de '{input_path.name}' demorou demais e foi interrompida.",
            hint="Arquivos de video muito grandes podem precisar de mais tempo; tente novamente.",
        )
    if result.returncode != 0 or not output_path.exists():
        raise ConversionError(
            f"O ffmpeg nao conseguiu converter '{input_path.name}'.",
            hint="Verifique se o arquivo de origem nao esta corrompido.",
            detail=RuntimeError(result.stderr[-2000:]),
        )
    return output_path
