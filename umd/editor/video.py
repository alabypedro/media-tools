"""A parte do editor que passa pelo FFmpeg: ler informacoes do arquivo,
editar video -> video (com audio), tirar quadros de um video, montar um
video a partir de quadros e juntar videos.

O FFmpeg e localizado como no resto do app e roda por run_cancellable
(cancelar nunca deixa processo orfao).
"""

from __future__ import annotations

import re
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..convert.converters.media import _ffmpeg_exe
from ..convert.core.errors import ConversionError
from ..convert.core.procutil import run_cancellable
from ..convert.core.registry import VIDEO
from ..core.process import run_quiet
from .clip import (
    CANCELLED_MESSAGE,
    Clip,
    _flatten,
    check_budget,
    check_cancel,
    clamp_box,
    render_layer,
    resize_size,
    rotated_size,
)

VIDEO_EXTS = VIDEO.extensions
DEFAULT_GIF_FPS = 10
_EVEN = "pad=ceil(iw/2)*2:ceil(ih/2)*2"  # H.264/VP9 em yuv420p exigem largura e altura pares


@dataclass
class MediaInfo:
    path: Path
    kind: str  # "video" | "animation" | "image"
    width: int
    height: int
    duration: float = 0.0  # segundos
    frames: int | None = None
    fps: float | None = None
    has_audio: bool = False

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height


def is_video(path: Path) -> bool:
    return path.suffix.lower().lstrip(".") in VIDEO_EXTS


def probe(path: Path, ffmpeg_path: str = "") -> MediaInfo:
    if not path.is_file():
        raise ConversionError(f"Arquivo nao encontrado: {path}")
    if is_video(path):
        return _probe_video(path, ffmpeg_path)
    from PIL import Image

    try:
        with Image.open(path) as img:
            frames = getattr(img, "n_frames", 1)
            duration = 0
            if frames > 1:
                for index in range(frames):
                    img.seek(index)
                    duration += int(img.info.get("duration") or 100)
            return MediaInfo(path, "animation" if frames > 1 else "image", img.width, img.height,
                             duration / 1000, frames, frames / (duration / 1000) if duration else None)
    except OSError as exc:
        raise ConversionError(
            f"Nao foi possivel ler '{path.name}'.",
            hint="O editor abre GIF, WebP, APNG, AVIF, PNG, JPG e videos (MP4, WebM, MKV, MOV...).",
            detail=exc,
        ) from exc


def _probe_video(path: Path, ffmpeg_path: str) -> MediaInfo:
    # sem ffprobe (o imageio-ffmpeg so traz o ffmpeg): le o cabecalho que o proprio ffmpeg imprime
    _code, output = run_quiet([_ffmpeg_exe(ffmpeg_path), "-hide_banner", "-i", str(path)], timeout=30)
    video = re.search(r"Stream #.*Video:.*", output)
    size = re.search(r"[ ,](\d{2,5})x(\d{2,5})[ ,\n\r]", video.group(0) + "\n") if video else None
    if not size:
        raise ConversionError(
            f"Nao foi possivel ler o video '{path.name}'.",
            hint="Verifique se o arquivo nao esta corrompido.",
            detail=RuntimeError(output[-2000:]),
        )
    width, height = int(size.group(1)), int(size.group(2))
    if re.search(r"rotation of -?(90|270)(\.0+)? degrees", output):
        width, height = height, width  # video de celular gravado em pe: o ffmpeg ja entrega girado
    duration = 0.0
    match = re.search(r"Duration: (\d+):(\d+):(\d+(?:\.\d+)?)", output)
    if match:
        duration = int(match.group(1)) * 3600 + int(match.group(2)) * 60 + float(match.group(3))
    rate = re.search(r"([\d.]+) fps", video.group(0)) or re.search(r"([\d.]+) tbr", video.group(0))
    fps = float(rate.group(1)) if rate else None
    return MediaInfo(path, "video", width, height, duration, round(duration * fps) if fps and duration else None,
                     fps, has_audio=bool(re.search(r"Stream #.*Audio:", output)))


def _run(args: list[str], what: str, output: Path | None, cancel_event: threading.Event | None,
         timeout: float = 3600) -> None:
    result = run_cancellable(args, cancel_event=cancel_event, timeout=timeout)
    if result.cancelled or result.timed_out or result.returncode != 0:
        if output is not None:
            output.unlink(missing_ok=True)
    if result.cancelled:
        raise ConversionError(CANCELLED_MESSAGE)
    if result.timed_out:
        raise ConversionError(f"O FFmpeg demorou demais ao {what} e foi interrompido.",
                              hint="Tente um trecho menor ou um tamanho menor.")
    if result.returncode != 0 or (output is not None and not output.exists()):
        raise ConversionError(f"O FFmpeg nao conseguiu {what}.",
                              hint="Verifique se o arquivo de origem nao esta corrompido.",
                              detail=RuntimeError(result.stderr[-2000:]))


def _seek_args(options: dict[str, Any]) -> list[str]:
    start, end = options.get("start"), options.get("end")
    args: list[str] = []
    if start:
        args += ["-ss", f"{float(start):.3f}"]
    if end is not None:
        length = float(end) - float(start or 0)
        if length <= 0:
            raise ConversionError("O fim do trecho precisa vir depois do inicio.")
        args += ["-t", f"{length:.3f}"]
    return args


def _geometry(size: tuple[int, int], options: dict[str, Any]) -> tuple[list[str], tuple[int, int]]:
    filters: list[str] = []
    crop = clamp_box(options.get("crop"), size)
    if crop:
        x, y, w, h = crop
        filters.append(f"crop={w}:{h}:{x}:{y}")
        size = (w, h)
    target = resize_size(size, options.get("resize"))
    if target != size:
        filters.append(f"scale={target[0]}:{target[1]}:flags=lanczos")
    return filters, target


def codec_args(ext: str, quality: Any) -> tuple[list[str], list[str]]:
    """(argumentos de video, argumentos de audio) para o conteiner; quality 1-100 vira CRF."""
    q = min(100, max(1, int(quality or 80)))
    if ext == "webm":
        return ["-c:v", "libvpx-vp9", "-crf", str(round(52 - q * 0.25)), "-b:v", "0"], ["-c:a", "libopus"]
    if ext in ("mp4", "m4v", "mov", "mkv"):
        video = ["-c:v", "libx264", "-preset", "medium", "-crf", str(round(40 - q * 0.22))]
        if ext != "mkv":
            video += ["-movflags", "+faststart"]
        return video, ["-c:a", "aac", "-b:a", "160k"]
    return [], []


def _atempo(speed: float) -> list[str]:
    filters = []
    while speed > 2:
        filters.append("atempo=2.0")
        speed /= 2
    while speed < 0.5:
        filters.append("atempo=0.5")
        speed /= 0.5
    return filters + [f"atempo={speed:.4f}"]


def video_filters(info: MediaInfo, options: dict[str, Any], has_layer: bool) -> tuple[str, list[str]]:
    """Grafo de filtros de video (termina em [vout]) e os filtros de audio, na mesma ordem de clip.apply_ops."""
    chain, size = _geometry(info.size, options)
    angle = float(options.get("rotate") or 0) % 360
    if angle == 90:
        chain.append("transpose=1")
    elif angle == 180:
        chain += ["hflip", "vflip"]
    elif angle == 270:
        chain.append("transpose=2")
    elif angle:
        w, h = rotated_size(size, angle)
        chain.append(f"rotate={angle}*PI/180:ow={w}:oh={h}:c=black")
    size = rotated_size(size, angle)
    if options.get("flip_h"):
        chain.append("hflip")
    if options.get("flip_v"):
        chain.append("vflip")

    if options.get("grayscale"):
        chain.append("hue=s=0")
    if options.get("sepia"):
        chain.append("colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131")
    if options.get("invert"):
        chain.append("negate")
    brightness = options.get("brightness")
    if brightness is not None and float(brightness) != 1:
        expr = f"'clip(val*{float(brightness):.3f},0,255)'"
        chain.append(f"lutrgb=r={expr}:g={expr}:b={expr}")
    eq = [f"{name}={float(options[key]):.3f}" for key, name in (("contrast", "contrast"), ("saturation", "saturation"))
          if options.get(key) is not None and float(options[key]) != 1]
    if eq:
        chain.append("eq=" + ":".join(eq))
    if options.get("sharpen"):
        chain.append("unsharp=5:5:1.0")
    if options.get("blur"):
        chain.append(f"gblur=sigma={float(options['blur']):.2f}")

    censor = options.get("censor")
    if censor:
        x, y, w, h = clamp_box(censor, size)
        mode = censor.get("mode", "blur")
        if mode == "black":
            chain.append(f"drawbox=x={x}:y={y}:w={w}:h={h}:color=black:t=fill")
        else:
            effect = (f"scale={max(1, w // 12)}:{max(1, h // 12)},scale={w}:{h}:flags=neighbor" if mode == "pixelate"
                      else f"gblur=sigma={max(4, min(w, h) / 8):.1f}")
            chain.append(f"split[cm][cc];[cc]crop={w}:{h}:{x}:{y},{effect}[cb];[cm][cb]overlay={x}:{y}")
    if has_layer:
        chain.append("null[vm];[vm][1:v]overlay=0:0")

    speed = float(options.get("speed") or 1)
    if speed <= 0:
        raise ConversionError("A velocidade precisa ser maior que zero.")
    audio: list[str] = []
    if speed != 1:
        chain.append(f"setpts=PTS/{speed:.4f}")
        if info.fps:
            chain.append(f"fps={info.fps}")
        audio += _atempo(speed)
    if options.get("reverse"):
        chain.append("reverse")
        audio.append("areverse")
    if options.get("boomerang"):
        chain.append("split[bf][bb];[bb]reverse[br];[bf][br]concat=n=2:v=1:a=0")
    chain += [_EVEN, "format=yuv420p"]
    return "[0:v]" + ",".join(chain) + "[vout]", audio


def transcode(input_path: Path, output_path: Path, options: dict[str, Any] | None = None,
              cancel_event: threading.Event | None = None) -> Path:
    """Video -> video com as mesmas opcoes do editor; o audio acompanha (velocidade, inverter, sem som)."""
    options = options or {}
    ffmpeg = _ffmpeg_exe(options.get("ffmpeg_path", ""))
    info = probe(input_path, options.get("ffmpeg_path", ""))
    ext = output_path.suffix.lower().lstrip(".")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="umd_edit_") as tmp:
        final_size = rotated_size(_geometry(info.size, options)[1], float(options.get("rotate") or 0))
        layer = render_layer(final_size, options)
        args = [ffmpeg, "-hide_banner", "-y", *_seek_args(options), "-i", str(input_path)]
        if layer is not None:
            layer_path = Path(tmp) / "layer.png"
            layer.save(layer_path)
            args += ["-i", str(layer_path)]
        graph, audio_filters = video_filters(info, options, layer is not None)
        video_codec, audio_codec = codec_args(ext, options.get("quality"))
        keep_audio = info.has_audio and not options.get("mute") and not options.get("boomerang")
        if keep_audio and audio_filters:
            graph += ";[0:a]" + ",".join(audio_filters) + "[aout]"
        args += ["-filter_complex", graph, "-map", "[vout]"]
        if keep_audio:
            args += ["-map", "[aout]" if audio_filters else "0:a?", *audio_codec]
        else:
            args.append("-an")
        args += [*video_codec, str(output_path)]
        _run(args, f"editar '{input_path.name}'", output_path, cancel_event, options.get("timeout", 3600))
    return output_path


def extract_clip(input_path: Path, options: dict[str, Any] | None = None,
                 cancel_event: threading.Event | None = None, fps: float | None = DEFAULT_GIF_FPS) -> Clip:
    """Quadros de um video como Clip. O FFmpeg ja aplica start/end, crop e resize (as opcoes
    que reduzem o que vai para a memoria); o resto fica para clip.apply_ops. fps=None = todos os quadros."""
    from PIL import Image

    options = options or {}
    ffmpeg = _ffmpeg_exe(options.get("ffmpeg_path", ""))
    info = probe(input_path, options.get("ffmpeg_path", ""))
    filters, size = _geometry(info.size, options)
    rate = float(fps or info.fps or 25)
    if fps:
        filters.insert(0, f"fps={rate:g}")
    start = float(options.get("start") or 0)
    end = options.get("end")
    length = (float(end) if end is not None else info.duration) - start
    check_budget(max(1.0, length * rate), *size)

    with tempfile.TemporaryDirectory(prefix="umd_edit_") as tmp:
        args = [ffmpeg, "-hide_banner", "-y", *_seek_args(options), "-i", str(input_path), "-an"]
        if filters:
            args += ["-vf", ",".join(filters)]
        args.append(str(Path(tmp) / "%06d.png"))
        _run(args, f"ler os quadros de '{input_path.name}'", None, cancel_event, options.get("timeout", 3600))
        frames = []
        for path in sorted(Path(tmp).glob("*.png")):
            check_cancel(cancel_event)
            with Image.open(path) as img:
                frames.append(img.convert("RGBA"))
    if not frames:
        raise ConversionError(f"O trecho escolhido de '{input_path.name}' nao tem nenhum quadro.")
    return Clip(frames, [max(1, round(1000 / rate))] * len(frames))


def clip_to_video(clip: Clip, output_path: Path, options: dict[str, Any] | None = None,
                  cancel_event: threading.Event | None = None) -> Path:
    """Animacao -> video (ex.: GIF -> MP4), respeitando o tempo de cada quadro. A transparencia vira cor de fundo."""
    options = options or {}
    ffmpeg = _ffmpeg_exe(options.get("ffmpeg_path", ""))
    ext = output_path.suffix.lower().lstrip(".")
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="umd_edit_") as tmp:
        lines = ["ffconcat version 1.0"]
        for index, (frame, duration) in enumerate(zip(clip.frames, clip.durations)):
            check_cancel(cancel_event)
            name = f"{index:06d}.png"
            _flatten(frame, options.get("background")).convert("RGB").save(Path(tmp) / name, compress_level=1)
            lines += [f"file '{name}'", f"duration {duration / 1000:.3f}"]
        lines.append(f"file '{name}'")  # o demuxer concat so respeita a duracao do ultimo se ele for repetido
        list_path = Path(tmp) / "frames.txt"
        list_path.write_text("\n".join(lines), encoding="utf-8")
        video_codec, _audio = codec_args(ext, options.get("quality"))
        args = [ffmpeg, "-hide_banner", "-y", "-f", "concat", "-safe", "0", "-i", str(list_path),
                "-vf", f"{_EVEN},format=yuv420p", "-an", *video_codec, str(output_path)]
        _run(args, f"gravar '{output_path.name}'", output_path, cancel_event, options.get("timeout", 3600))
    return output_path


def merge_videos(inputs: list[Path], output_path: Path, options: dict[str, Any] | None = None,
                 cancel_event: threading.Event | None = None) -> Path:
    """Junta videos em sequencia. Todos sao ajustados ao tamanho e ao fps do primeiro (com barras, sem distorcer)."""
    options = options or {}
    if len(inputs) < 2:
        raise ConversionError("Escolha pelo menos dois videos para juntar.")
    ffmpeg = _ffmpeg_exe(options.get("ffmpeg_path", ""))
    infos = [probe(path, options.get("ffmpeg_path", "")) for path in inputs]
    width, height = (v + v % 2 for v in infos[0].size)
    fps = infos[0].fps or 25
    with_audio = all(info.has_audio for info in infos) and not options.get("mute")
    ext = output_path.suffix.lower().lstrip(".")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    args = [ffmpeg, "-hide_banner", "-y"]
    parts, labels = [], ""
    for index, path in enumerate(inputs):
        args += ["-i", str(path)]
        parts.append(f"[{index}:v]scale={width}:{height}:force_original_aspect_ratio=decrease,"
                     f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},format=yuv420p[v{index}]")
        labels += f"[v{index}]"
        if with_audio:
            parts.append(f"[{index}:a]aresample=44100,aformat=channel_layouts=stereo[a{index}]")
            labels += f"[a{index}]"
    parts.append(f"{labels}concat=n={len(inputs)}:v=1:a={int(with_audio)}[vout]" + ("[aout]" if with_audio else ""))
    video_codec, audio_codec = codec_args(ext, options.get("quality"))
    args += ["-filter_complex", ";".join(parts), "-map", "[vout]"]
    args += ["-map", "[aout]", *audio_codec] if with_audio else ["-an"]
    args += [*video_codec, str(output_path)]
    _run(args, "juntar os videos", output_path, cancel_event, options.get("timeout", 3600))
    return output_path
