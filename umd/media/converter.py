"""Conversoes pos-download para arquivos que NAO passaram pelos
post-processors do yt-dlp (imagens de galerias, links diretos, gravacoes
de live interrompidas).

Politica: nunca recomprimir sem necessidade. Troca de container tenta
primeiro "remux" (-c copy, sem perda e rapido); so recodifica se o
container de destino nao aceitar os codecs originais.
"""

from __future__ import annotations

import re
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable
from pathlib import Path

from ..core.exceptions import ErrorCode, UMDError
from ..core.process import kill_process_tree, popen_kwargs

ProgressCallback = Callable[[float], None]  # fracao 0..1

AUDIO_TARGETS = {"mp3", "m4a", "opus", "wav", "flac"}
VIDEO_TARGETS = {"mp4", "mkv", "webm"}
IMAGE_TARGETS = {"jpg", "png", "webp"}

_AUDIO_ENCODERS = {
    "mp3": ["-c:a", "libmp3lame"],
    "m4a": ["-c:a", "aac"],
    "opus": ["-c:a", "libopus"],
    "wav": ["-c:a", "pcm_s16le"],
    "flac": ["-c:a", "flac"],
}
_VIDEO_ENCODERS = {
    "mp4": ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart"],
    "webm": ["-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-row-mt", "1", "-c:a", "libopus", "-b:a", "128k"],
    "mkv": ["-c:v", "libx264", "-preset", "medium", "-crf", "20", "-c:a", "aac", "-b:a", "192k"],
}


def run_ffmpeg(
    args: list[str],
    *,
    cancel_event: threading.Event | None = None,
    timeout: float | None = None,
    duration: float | None = None,
    on_progress: ProgressCallback | None = None,
) -> None:
    """Roda ffmpeg (lista de argumentos, sem shell), cancelavel, com progresso."""
    full = [args[0], "-hide_banner", "-nostdin", "-progress", "pipe:1", "-nostats", *args[1:]]
    with tempfile.TemporaryFile() as err_file:
        proc = subprocess.Popen(full, stdout=subprocess.PIPE, stderr=err_file, stdin=subprocess.DEVNULL, **popen_kwargs())
        started = time.monotonic()
        lines: list[str] = []

        def pump() -> None:
            assert proc.stdout is not None
            for raw in iter(proc.stdout.readline, b""):
                line = raw.decode("ascii", errors="ignore").strip()
                if line.startswith("out_time_us=") and duration and on_progress:
                    try:
                        seconds = int(line.split("=", 1)[1]) / 1_000_000
                        on_progress(max(0.0, min(1.0, seconds / duration)))
                    except ValueError:
                        pass
                lines.append(line)
                del lines[:-50]

        reader = threading.Thread(target=pump, daemon=True)
        reader.start()
        while proc.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                kill_process_tree(proc)
                raise UMDError(ErrorCode.CANCELLED)
            if timeout is not None and time.monotonic() - started > timeout:
                kill_process_tree(proc)
                raise UMDError(ErrorCode.TIMEOUT, details="ffmpeg timeout")
            time.sleep(0.1)
        reader.join(timeout=2)
        err_file.seek(0)
        stderr = err_file.read().decode("utf-8", errors="replace")
    if proc.returncode != 0:
        raise UMDError(ErrorCode.FFMPEG_FAILED, details=stderr[-3000:])


def _output_for(source: Path, target_ext: str) -> Path:
    out = source.with_suffix(f".{target_ext}")
    if out == source:
        out = source.with_name(f"{source.stem}.converted.{target_ext}")
    return out


def convert_media(
    ffmpeg: str,
    source: Path,
    target_ext: str,
    *,
    audio_bitrate: str = "192",
    cancel_event: threading.Event | None = None,
    duration: float | None = None,
    on_progress: ProgressCallback | None = None,
) -> Path:
    """Converte video/audio para target_ext. Devolve o novo arquivo (o original e removido)."""
    target_ext = target_ext.lower()
    if source.suffix.lower().lstrip(".") == target_ext:
        return source
    out = _output_for(source, target_ext)
    src = str(source.resolve())
    dst = str(out.resolve())

    attempts: list[list[str]] = []
    if target_ext in AUDIO_TARGETS:
        bitrate = [] if audio_bitrate == "original" or target_ext in ("wav", "flac") else ["-b:a", f"{audio_bitrate}k"]
        if target_ext in ("m4a", "opus"):
            attempts.append([ffmpeg, "-y", "-i", src, "-vn", "-c:a", "copy", dst])  # mesmo codec: sem perda
        quality = ["-q:a", "0"] if target_ext == "mp3" and not bitrate else []
        attempts.append([ffmpeg, "-y", "-i", src, "-vn", *_AUDIO_ENCODERS[target_ext], *bitrate, *quality, dst])
    elif target_ext in VIDEO_TARGETS:
        attempts.append([ffmpeg, "-y", "-i", src, "-map", "0", "-c", "copy", dst])  # remux
        attempts.append([ffmpeg, "-y", "-i", src, *_VIDEO_ENCODERS[target_ext], dst])
    else:
        raise UMDError(ErrorCode.FFMPEG_FAILED, details=f"Unsupported target format: {target_ext}")

    last_error: UMDError | None = None
    for args in attempts:
        try:
            run_ffmpeg(args, cancel_event=cancel_event, duration=duration, on_progress=on_progress)
        except UMDError as exc:
            if exc.code != ErrorCode.FFMPEG_FAILED:
                out.unlink(missing_ok=True)
                raise
            last_error = exc
            out.unlink(missing_ok=True)
            continue
        if out.exists() and out.stat().st_size > 0:
            source.unlink(missing_ok=True)
            return out
    raise last_error or UMDError(ErrorCode.FFMPEG_FAILED)


def convert_image(source: Path, target_ext: str, quality: int = 92) -> Path:
    """Converte imagem com Pillow (JPG sem transparencia recebe fundo branco)."""
    target_ext = target_ext.lower()
    current = source.suffix.lower().lstrip(".")
    if current == target_ext or (current == "jpeg" and target_ext == "jpg"):
        return source
    try:
        from PIL import Image
    except ImportError as exc:
        raise UMDError(ErrorCode.ENGINE_MISSING, details=f"Pillow not installed: {exc}") from None
    out = _output_for(source, target_ext)
    try:
        with Image.open(source) as img:
            img.seek(0)  # animadas (gif/webp): usa o primeiro quadro
            frame = img.convert("RGBA") if img.mode in ("P", "LA", "RGBA") or "transparency" in img.info else img.convert("RGB")
            if target_ext == "jpg":
                if frame.mode == "RGBA":
                    background = Image.new("RGB", frame.size, (255, 255, 255))
                    background.paste(frame, mask=frame.split()[-1])
                    frame = background
                frame.save(out, "JPEG", quality=quality, optimize=True)
            elif target_ext == "png":
                frame.save(out, "PNG", optimize=True)
            elif target_ext == "webp":
                frame.save(out, "WEBP", quality=quality)
            else:
                raise UMDError(ErrorCode.FFMPEG_FAILED, details=f"Unsupported image target: {target_ext}")
    except UMDError:
        raise
    except Exception as exc:  # noqa: BLE001 - arquivo corrompido, formato nao suportado pelo Pillow...
        out.unlink(missing_ok=True)
        raise UMDError(ErrorCode.FFMPEG_FAILED, details=f"Image conversion failed: {type(exc).__name__}: {exc}") from None
    source.unlink(missing_ok=True)
    return out


def salvage_partial(ffmpeg: str, part_file: Path, target_ext: str = "mp4") -> Path | None:
    """Recupera uma gravacao de live interrompida (.part em MPEG-TS) como arquivo jogavel."""
    stem = re.sub(r"(\.f\d+)?\.[^.]+\.part$", "", part_file.name) or "gravacao"
    out = part_file.with_name(f"{stem}.{target_ext}")
    try:
        run_ffmpeg([ffmpeg, "-y", "-i", str(part_file.resolve()), "-map", "0", "-c", "copy", str(out.resolve())], timeout=3600)
    except UMDError:
        out.unlink(missing_ok=True)
        return None
    if out.exists() and out.stat().st_size > 0:
        part_file.unlink(missing_ok=True)
        return out
    return None
