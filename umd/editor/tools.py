"""As ferramentas do editor: e isto que a tela Editor e o `umd edit` chamam.

Todas recebem caminhos e um dict de opcoes (ver clip.py) e nunca alteram o
arquivo de origem. Quem chama escolhe o caminho de saida; `output_path()`
monta um nome que nao sobrescreve nada ("gato (redimensionado).gif").
"""

from __future__ import annotations

import math
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Any

from ..convert.core.errors import ConversionError
from ..convert.core.paths import sanitize_filename, unique_path
from .clip import IMAGE_EXTS, Clip, apply_ops, check_budget, check_cancel, load_clip, save_clip
from .video import DEFAULT_GIF_FPS, VIDEO_EXTS, clip_to_video, extract_clip, is_video, merge_videos, probe, transcode

FRAME_FORMATS = ("png", "jpg", "webp", "bmp", "gif")
# o FFmpeg ja aplicou estas opcoes ao tirar os quadros do video
_DONE_BY_FFMPEG = ("start", "end", "crop", "resize")


def output_path(source: Path, suffix: str, ext: str, out_dir: Path | None = None) -> Path:
    name = sanitize_filename(f"{source.stem} ({suffix}).{ext.lower().lstrip('.')}")
    return unique_path((out_dir or source.parent) / name)


def _save(clip: Clip, output: Path, options: dict[str, Any], cancel_event: threading.Event | None) -> Path:
    if is_video(output):
        return clip_to_video(clip, output, options, cancel_event)
    return save_clip(clip, output, options, cancel_event)


def _load(source: Path, options: dict[str, Any], cancel_event: threading.Event | None,
          fps: float | None = DEFAULT_GIF_FPS) -> tuple[Clip, dict[str, Any]]:
    """Clip de qualquer origem + as opcoes que ainda faltam aplicar."""
    if not source.is_file():
        raise ConversionError(f"Arquivo nao encontrado: {source}")
    if is_video(source):
        clip = extract_clip(source, options, cancel_event, fps=options.get("fps") or fps)
        return clip, {k: v for k, v in options.items() if k not in _DONE_BY_FFMPEG}
    return load_clip(source, cancel_event), options


def edit(input_path: Path, output: Path, options: dict[str, Any] | None = None,
         cancel_event: threading.Event | None = None) -> Path:
    """Aplica as opcoes e grava em `output`; o formato vem da extensao de `output`.

    video -> video passa inteiro pelo FFmpeg (mantem o audio); os outros casos
    (animacao/imagem de origem, ou video -> GIF/WebP/APNG/AVIF) passam pelos quadros.
    """
    options = options or {}
    try:
        if is_video(input_path) and is_video(output):
            return transcode(input_path, output, options, cancel_event)
        clip, remaining = _load(input_path, options, cancel_event)
        return _save(apply_ops(clip, remaining, cancel_event), output, options, cancel_event)
    except ConversionError:
        output.unlink(missing_ok=True)
        raise


def _fit(frame, size: tuple[int, int]):
    """Quadro inteiro dentro de `size`, centralizado, sem distorcer (sobras transparentes)."""
    from PIL import Image

    if frame.size == size:
        return frame
    scaled = frame.copy()
    scaled.thumbnail(size, Image.Resampling.LANCZOS)
    canvas = Image.new("RGBA", size, (0, 0, 0, 0))
    canvas.paste(scaled, ((size[0] - scaled.width) // 2, (size[1] - scaled.height) // 2))
    return canvas


def make_animation(sources: list[Path], output: Path, options: dict[str, Any] | None = None,
                   cancel_event: threading.Event | None = None) -> Path:
    """Imagens (na ordem dada) -> GIF/WebP/APNG/AVIF/video. Uma origem animada entra com todos os
    seus quadros e tempos (serve para juntar GIFs). Todos os quadros ficam do tamanho do primeiro."""
    options = options or {}
    if not sources:
        raise ConversionError("Escolha pelo menos uma imagem.")
    delay = max(1, int(options.get("delay") or 100))
    frames: list = []
    durations: list[int] = []
    for source in sources:
        check_cancel(cancel_event)
        part, _ = _load(source, {k: v for k, v in options.items() if k in ("fps", "ffmpeg_path")}, cancel_event)
        if frames:
            check_budget(len(frames) + len(part.frames), *frames[0].size)
        size = frames[0].size if frames else part.size
        frames += [_fit(frame, size) for frame in part.frames]
        durations += part.durations if len(part.frames) > 1 else [delay]
    clip = Clip(frames, durations, int(options.get("loop") or 0))
    try:
        return _save(apply_ops(clip, {k: v for k, v in options.items() if k != "delay"}, cancel_event),
                     output, options, cancel_event)
    except ConversionError:
        output.unlink(missing_ok=True)
        raise


def split_frames(input_path: Path, out_dir: Path | None = None, options: dict[str, Any] | None = None,
                 cancel_event: threading.Event | None = None) -> list[Path]:
    """Cada quadro vira um arquivo numa pasta "<nome> (quadros)" -- ou, com options["zip"], num ZIP.
    Para video, options["fps"] limita os quadros por segundo (padrao: todos)."""
    options = options or {}
    fmt = str(options.get("format") or "png").lower()
    if fmt not in FRAME_FORMATS:
        raise ConversionError(f"Formato de quadro nao suportado: '{fmt}'.", hint="Use " + ", ".join(FRAME_FORMATS) + ".")
    clip, remaining = _load(input_path, options, cancel_event, fps=None)
    clip = apply_ops(clip, remaining, cancel_event)
    base = out_dir or input_path.parent
    name = sanitize_filename(f"{input_path.stem} (quadros)")
    digits = max(3, len(str(len(clip.frames))))

    def write(folder: Path) -> list[Path]:
        written = []
        for index, frame in enumerate(clip.frames, start=1):
            check_cancel(cancel_event)
            written.append(save_clip(Clip([frame], [100]), folder / f"frame_{index:0{digits}d}.{fmt}", options))
        return written

    if not options.get("zip"):
        folder = unique_path(base / name)
        try:
            return write(folder)
        except ConversionError:
            shutil.rmtree(folder, ignore_errors=True)
            raise
    with tempfile.TemporaryDirectory(prefix="umd_edit_") as tmp:
        write(Path(tmp))
        archive = unique_path(base / f"{name}.zip")
        base.mkdir(parents=True, exist_ok=True)
        shutil.make_archive(str(archive.with_suffix("")), "zip", tmp)
    return [archive]


def make_sprite_sheet(input_path: Path, output: Path, options: dict[str, Any] | None = None,
                      cancel_event: threading.Event | None = None) -> Path:
    """Todos os quadros lado a lado numa imagem so (grade de options["columns"] colunas)."""
    from PIL import Image

    options = options or {}
    clip, remaining = _load(input_path, options, cancel_event)
    clip = apply_ops(clip, remaining, cancel_event)
    count = len(clip.frames)
    columns = min(count, int(options.get("columns") or math.ceil(math.sqrt(count))))
    rows = math.ceil(count / columns)
    width, height = clip.size
    check_budget(1, width * columns, height * rows)
    sheet = Image.new("RGBA", (width * columns, height * rows), (0, 0, 0, 0))
    for index, frame in enumerate(clip.frames):
        sheet.paste(frame, ((index % columns) * width, (index // columns) * height))
    return save_clip(Clip([sheet], [100]), output, options, cancel_event)


def cut_sprite_sheet(input_path: Path, output: Path, options: dict[str, Any] | None = None,
                     cancel_event: threading.Event | None = None) -> Path:
    """Sprite sheet (grade de columns x rows) -> animacao. Celulas vazias no fim da grade sao ignoradas."""
    options = options or {}
    sheet = load_clip(input_path, cancel_event).frames[0]
    columns, rows = max(1, int(options.get("columns") or 1)), max(1, int(options.get("rows") or 1))
    width, height = sheet.width // columns, sheet.height // rows
    if not width or not height:
        raise ConversionError(f"A grade {columns}x{rows} nao cabe na imagem ({sheet.width}x{sheet.height}).")
    frames = [sheet.crop((col * width, row * height, (col + 1) * width, (row + 1) * height))
              for row in range(rows) for col in range(columns)]
    while len(frames) > 1 and frames[-1].getextrema()[3][1] == 0:
        frames.pop()
    clip = Clip(frames, [max(1, int(options.get("delay") or 100))] * len(frames), int(options.get("loop") or 0))
    rest = {k: v for k, v in options.items() if k not in ("delay", "columns", "rows")}
    return _save(apply_ops(clip, rest, cancel_event), output, options, cancel_event)


__all__ = ["FRAME_FORMATS", "IMAGE_EXTS", "VIDEO_EXTS", "cut_sprite_sheet", "edit", "make_animation",
           "make_sprite_sheet", "merge_videos", "output_path", "probe", "split_frames"]
