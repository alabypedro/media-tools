"""Animacao em memoria (GIF, WebP, APNG, AVIF ou imagem parada) e as
operacoes sobre os quadros, todas com Pillow.

As opcoes sao um dict simples (como nos conversores de umd.convert). As
operacoes sao aplicadas sempre nesta ordem, nao importa a ordem das chaves:

    start/end (s)     recorta o tempo
    drop_every (n)    remove 1 a cada n quadros
    crop              {x, y, width, height}
    resize            {width, height, keep_aspect} ou {percent}
    rotate (graus, sentido horario), flip_h, flip_v
    grayscale, sepia, invert, brightness, contrast, saturation (1.0 = igual),
    blur (raio), sharpen
    censor            {x, y, width, height, mode: blur|pixelate|black}
    overlay           {path, position, scale (% da largura), opacity, x, y}
    text              {text, font, size, color, stroke_color, stroke_width, position, margin, x, y}
    background        cor que substitui a transparencia
    dedupe            junta quadros repetidos
    speed (2 = dobro da velocidade), reverse, boomerang (vai e volta)
    delay (ms)        mesmo tempo para todos os quadros
    loop              0 = infinito, 1 = toca uma vez, n = n vezes

Na gravacao: colors (2-256, GIF/PNG), quality (1-100), lossless (WebP).
"""

from __future__ import annotations

import math
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..convert.core.errors import ConversionError

CANCELLED_MESSAGE = "Conversao cancelada pelo usuario."  # mesma mensagem que a fila do conversor reconhece
MIN_GIF_DELAY = 20  # ms; abaixo disso os navegadores tocam o GIF devagar (100 ms)
MAX_CLIP_PIXELS = 400_000_000  # quadros x largura x altura (~1,6 GB em RGBA)

POSITIONS = ("top-left", "top", "top-right", "left", "center", "right", "bottom-left", "bottom", "bottom-right")
FONTS = {"impact": "impact.ttf", "arial": "arialbd.ttf", "times": "timesbd.ttf", "courier": "courbd.ttf",
         "comic": "comicbd.ttf"}
CENSOR_MODES = ("blur", "pixelate", "black")

_STATIC_FORMATS = {"jpg": "JPEG", "jpeg": "JPEG", "bmp": "BMP", "tif": "TIFF", "tiff": "TIFF", "ico": "ICO"}
ANIMATED_EXTS = ("gif", "webp", "apng", "png", "avif")
IMAGE_EXTS = ANIMATED_EXTS + tuple(_STATIC_FORMATS)
_SEPIA = (0.393, 0.769, 0.189, 0, 0.349, 0.686, 0.168, 0, 0.272, 0.534, 0.131, 0)


@dataclass
class Clip:
    frames: list  # PIL.Image em RGBA, todos do mesmo tamanho
    durations: list[int]  # ms por quadro
    loop: int = 0

    @property
    def size(self) -> tuple[int, int]:
        return self.frames[0].size

    @property
    def duration(self) -> float:
        return sum(self.durations) / 1000


def check_cancel(cancel_event: threading.Event | None) -> None:
    if cancel_event is not None and cancel_event.is_set():
        raise ConversionError(CANCELLED_MESSAGE)


def check_budget(frames: float, width: int, height: int) -> None:
    if frames * width * height > MAX_CLIP_PIXELS:
        raise ConversionError(
            f"A animacao ficaria grande demais para a memoria ({int(frames)} quadros de {width}x{height}).",
            hint="Reduza o tamanho, a duracao ou os quadros por segundo.",
        )


# ---------------------------------------------------------------- leitura e gravacao

def load_clip(path: Path, cancel_event: threading.Event | None = None) -> Clip:
    from PIL import Image, ImageOps, ImageSequence

    try:
        with Image.open(path) as img:
            count = getattr(img, "n_frames", 1)
            check_budget(count, img.width, img.height)
            if count == 1:
                frames = [ImageOps.exif_transpose(img).convert("RGBA")]
                frames[0].info = {}
                return Clip(frames, [100])
            frames, durations = [], []
            for frame in ImageSequence.Iterator(img):
                check_cancel(cancel_event)
                durations.append(int(frame.info.get("duration") or 100))
                frames.append(frame.convert("RGBA"))
                frames[-1].info = {}  # senao o Pillow regrava loop/transparencia do arquivo de origem
            # GIF sem a extensao de repeticao toca uma vez so
            loop = int(img.info.get("loop", 1 if img.format == "GIF" else 0))
    except OSError as exc:
        raise ConversionError(
            f"Nao foi possivel ler a imagem '{path.name}'.",
            hint="Verifique se o arquivo nao esta corrompido e se e uma imagem (GIF, WebP, APNG, AVIF, PNG, JPG...).",
            detail=exc,
        ) from exc
    return Clip(frames, durations, loop)


def _has_alpha(frame) -> bool:
    return frame.getextrema()[3][0] < 255


def _flatten(frame, color: str | None):
    from PIL import Image, ImageColor

    base = Image.new("RGBA", frame.size, ImageColor.getrgb(color or "#ffffff")[:3] + (255,))
    return Image.alpha_composite(base, frame)


def _gif_frame(frame, colors: int | None, transparent: bool):
    """Quadro pronto para GIF: transparencia binaria e, se pedido, paleta reduzida."""
    from PIL import Image

    if not transparent:
        frame = frame.convert("RGB")
        return frame.quantize(colors, method=Image.Quantize.MEDIANCUT) if colors else frame
    frame = frame.copy()
    frame.putalpha(frame.getchannel("A").point(lambda v: 255 if v >= 128 else 0))
    if not colors:
        return frame
    quantized = frame.quantize(colors, method=Image.Quantize.FASTOCTREE)
    for rgba, index in quantized.palette.colors.items():
        if len(rgba) == 4 and rgba[3] == 0:
            quantized.info["transparency"] = index
            break
    return quantized


def min_delay(frames: list, durations: list[int], minimum: int = MIN_GIF_DELAY) -> tuple[list, list[int]]:
    """Junta quadros curtos demais para o GIF (o tempo total nao muda)."""
    out_frames: list = []
    out_durations: list[int] = []
    for frame, duration in zip(frames, durations):
        if out_durations and out_durations[-1] < minimum:
            out_durations[-1] += duration
        else:
            out_frames.append(frame)
            out_durations.append(duration)
    return out_frames, [max(minimum, round(d / 10) * 10) for d in out_durations]


def save_clip(clip: Clip, output_path: Path, options: dict[str, Any] | None = None,
              cancel_event: threading.Event | None = None) -> Path:
    from PIL import features

    options = options or {}
    ext = output_path.suffix.lower().lstrip(".")
    frames, durations = clip.frames, clip.durations
    quality = options.get("quality")
    colors = options.get("colors")
    colors = min(256, max(2, int(colors))) if colors else None
    kwargs: dict[str, Any] = {}

    if ext == "gif":
        frames, durations = min_delay(frames, durations)
        transparent = any(_has_alpha(f) for f in frames)
        frames = [_gif_frame(f, colors, transparent) for f in frames]
        kwargs = {"format": "GIF", "optimize": True}
        if transparent:
            kwargs["disposal"] = 2
        if clip.loop != 1:
            kwargs["loop"] = clip.loop
    elif ext == "webp":
        kwargs = {"format": "WEBP", "quality": int(quality or 80), "lossless": bool(options.get("lossless")),
                  "method": 4, "loop": clip.loop}
    elif ext in ("png", "apng"):
        kwargs = {"format": "PNG", "loop": clip.loop}
        if len(frames) == 1:
            kwargs = {"format": "PNG", "optimize": True}
            if colors:
                frames = [_gif_frame(frames[0], colors, _has_alpha(frames[0]))]
    elif ext == "avif":
        if not features.check("avif"):
            raise ConversionError("Esta instalacao do Pillow nao grava AVIF.", hint="Atualize com: pip install -U pillow")
        kwargs = {"format": "AVIF", "quality": int(quality or 75)}
    elif ext in _STATIC_FORMATS:
        frames = frames[:1]
        kwargs = {"format": _STATIC_FORMATS[ext]}
        if ext in ("jpg", "jpeg", "bmp"):
            frames = [_flatten(frames[0], options.get("background")).convert("RGB")]
        if ext in ("jpg", "jpeg"):
            kwargs.update(quality=min(95, int(quality or 90)), optimize=True)
    else:
        raise ConversionError(
            f"Nao sei gravar animacao ou imagem em '.{ext}'.",
            hint="Use gif, webp, apng, avif, png, jpg ou um formato de video (mp4, webm...).",
        )

    if len(frames) > 1:
        kwargs.update(save_all=True, append_images=frames[1:], duration=durations)
    else:
        kwargs.pop("loop", None)
        kwargs.pop("disposal", None)
    check_cancel(cancel_event)
    try:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        frames[0].save(output_path, **kwargs)
    except (OSError, ValueError) as exc:
        output_path.unlink(missing_ok=True)
        raise ConversionError(
            f"Nao foi possivel gravar '{output_path.name}'.",
            hint="Verifique se voce tem permissao na pasta de destino.",
            detail=exc,
        ) from exc
    return output_path


# ---------------------------------------------------------------- geometria

def clamp_box(box: Any, size: tuple[int, int]) -> tuple[int, int, int, int] | None:
    """(x, y, largura, altura) dentro da imagem; largura/altura 0 = ate a borda."""
    if not box:
        return None
    if isinstance(box, dict):
        x, y, w, h = (int(box.get(k) or 0) for k in ("x", "y", "width", "height"))
    else:
        x, y, w, h = (int(v) for v in box)
    width, height = size
    x, y = max(0, x), max(0, y)
    w, h = min(w or width, width - x), min(h or height, height - y)
    if w <= 0 or h <= 0:
        raise ConversionError(f"A area escolhida fica fora da imagem ({width}x{height}).")
    return x, y, w, h


def resize_size(size: tuple[int, int], resize: dict[str, Any] | None) -> tuple[int, int]:
    if not resize:
        return size
    w, h = size
    percent = resize.get("percent")
    if percent:
        return max(1, round(w * percent / 100)), max(1, round(h * percent / 100))
    tw, th = int(resize.get("width") or 0), int(resize.get("height") or 0)
    if not tw and not th:
        return size
    if resize.get("keep_aspect", True):
        ratio = min(tw / w if tw else math.inf, th / h if th else math.inf)
        return max(1, round(w * ratio)), max(1, round(h * ratio))
    return tw or w, th or h


def rotated_size(size: tuple[int, int], angle: float) -> tuple[int, int]:
    angle = angle % 360
    w, h = size
    if angle in (0, 180):
        return size
    if angle in (90, 270):
        return h, w
    rad = math.radians(angle)
    cos, sin = abs(math.cos(rad)), abs(math.sin(rad))
    return max(1, round(w * cos + h * sin)), max(1, round(w * sin + h * cos))


def plan_size(size: tuple[int, int], options: dict[str, Any]) -> tuple[int, int]:
    """Tamanho final depois de crop -> resize -> rotate (o video precisa saber antes de processar)."""
    crop = clamp_box(options.get("crop"), size)
    if crop:
        size = crop[2:]
    size = resize_size(size, options.get("resize"))
    return rotated_size(size, options.get("rotate") or 0)


def place(container: tuple[int, int], item: tuple[int, int], position: str = "center", margin: int = 0,
          x: int | None = None, y: int | None = None) -> tuple[int, int]:
    if x is not None and y is not None:
        return int(x), int(y)
    cw, ch = container
    iw, ih = item
    left = margin if "left" in position else cw - iw - margin if "right" in position else (cw - iw) // 2
    top = margin if "top" in position else ch - ih - margin if "bottom" in position else (ch - ih) // 2
    return left, top


# ---------------------------------------------------------------- por quadro

def _rotate(frame, angle: float, flip_h: bool, flip_v: bool):
    from PIL import Image

    angle = angle % 360
    if angle == 90:
        frame = frame.transpose(Image.Transpose.ROTATE_270)  # Pillow gira no sentido anti-horario
    elif angle == 180:
        frame = frame.transpose(Image.Transpose.ROTATE_180)
    elif angle == 270:
        frame = frame.transpose(Image.Transpose.ROTATE_90)
    elif angle:
        frame = frame.rotate(-angle, expand=True, resample=Image.Resampling.BICUBIC)
    if flip_h:
        frame = frame.transpose(Image.Transpose.FLIP_LEFT_RIGHT)
    if flip_v:
        frame = frame.transpose(Image.Transpose.FLIP_TOP_BOTTOM)
    return frame


def _factor(options: dict[str, Any], key: str) -> float | None:
    value = options.get(key)
    return None if value is None or float(value) == 1.0 else max(0.0, float(value))


def has_effects(options: dict[str, Any]) -> bool:
    return bool(any(options.get(k) for k in ("grayscale", "sepia", "invert", "blur", "sharpen"))
                or any(_factor(options, k) is not None for k in ("brightness", "contrast", "saturation")))


def _effects(frame, options: dict[str, Any]):
    from PIL import ImageEnhance, ImageFilter, ImageOps

    alpha = frame.getchannel("A")
    rgb = frame.convert("RGB")
    if options.get("grayscale"):
        rgb = ImageOps.grayscale(rgb).convert("RGB")
    if options.get("sepia"):
        rgb = rgb.convert("RGB", _SEPIA)
    if options.get("invert"):
        rgb = ImageOps.invert(rgb)
    for key, enhancer in (("brightness", ImageEnhance.Brightness), ("contrast", ImageEnhance.Contrast),
                          ("saturation", ImageEnhance.Color)):
        factor = _factor(options, key)
        if factor is not None:
            rgb = enhancer(rgb).enhance(factor)
    if options.get("sharpen"):
        rgb = rgb.filter(ImageFilter.SHARPEN)
    rgb.putalpha(alpha)
    if options.get("blur"):
        rgb = rgb.filter(ImageFilter.GaussianBlur(float(options["blur"])))
    return rgb


def _censor(frame, box: tuple[int, int, int, int], mode: str):
    from PIL import Image, ImageFilter

    x, y, w, h = box
    region = frame.crop((x, y, x + w, y + h))
    if mode == "black":
        region = Image.new("RGBA", (w, h), (0, 0, 0, 255))
    elif mode == "pixelate":
        small = (max(1, w // 12), max(1, h // 12))
        region = region.resize(small, Image.Resampling.BILINEAR).resize((w, h), Image.Resampling.NEAREST)
    else:
        region = region.filter(ImageFilter.GaussianBlur(max(4, min(w, h) / 8)))
    frame.paste(region, (x, y))
    return frame


def _font(name: str, size: int):
    from PIL import ImageFont

    for candidate in (FONTS.get(name, name), "arial.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default(size)


def render_layer(size: tuple[int, int], options: dict[str, Any]):
    """Marca-d'agua e texto numa camada transparente do tamanho do quadro (None se nao ha nenhum).
    A mesma camada serve para todos os quadros e, no video, vira uma entrada do filtro overlay."""
    from PIL import Image, ImageDraw

    overlay = options.get("overlay")
    text = options.get("text")
    caption = (text or {}).get("text", "").strip()
    if not overlay and not caption:
        return None
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    default_margin = max(4, round(size[1] * 0.03))

    if overlay:
        try:
            with Image.open(overlay["path"]) as img:
                mark = img.convert("RGBA")
        except (OSError, KeyError) as exc:
            raise ConversionError("Nao foi possivel ler a imagem de sobreposicao.", detail=exc) from exc
        if overlay.get("scale"):
            width = max(1, round(size[0] * overlay["scale"] / 100))
            mark = mark.resize((width, max(1, round(mark.height * width / mark.width))), Image.Resampling.LANCZOS)
        opacity = overlay.get("opacity", 100)
        if opacity < 100:
            mark.putalpha(mark.getchannel("A").point(lambda v: round(v * opacity / 100)))
        stamp = Image.new("RGBA", size, (0, 0, 0, 0))
        stamp.paste(mark, place(size, mark.size, overlay.get("position", "bottom-right"),
                                overlay.get("margin", default_margin), overlay.get("x"), overlay.get("y")))
        layer = Image.alpha_composite(layer, stamp)

    if caption:
        font_size = int(text.get("size") or max(12, size[1] // 10))
        font = _font(text.get("font", "impact"), font_size)
        stroke = int(text.get("stroke_width", max(1, font_size // 15)))
        stamp = Image.new("RGBA", size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(stamp)
        box = draw.multiline_textbbox((0, 0), caption, font=font, stroke_width=stroke, align="center")
        x, y = place(size, (box[2] - box[0], box[3] - box[1]), text.get("position", "bottom"),
                     text.get("margin", default_margin), text.get("x"), text.get("y"))
        draw.multiline_text((x - box[0], y - box[1]), caption, font=font, fill=text.get("color", "#ffffff"),
                            stroke_width=stroke, stroke_fill=text.get("stroke_color", "#000000"), align="center")
        layer = Image.alpha_composite(layer, stamp)
    return layer


# ---------------------------------------------------------------- linha do tempo

def _cut(clip: Clip, start: float | None, end: float | None) -> None:
    if start is None and end is None:
        return
    start_ms = (start or 0) * 1000
    end_ms = math.inf if end is None else end * 1000
    kept, elapsed = [], 0
    for index, duration in enumerate(clip.durations):
        if start_ms - 0.5 <= elapsed < end_ms:
            kept.append(index)
        elapsed += duration
    if not kept:
        raise ConversionError(f"O trecho escolhido nao tem nenhum quadro (a animacao dura {clip.duration:.2f} s).")
    clip.frames = [clip.frames[i] for i in kept]
    clip.durations = [clip.durations[i] for i in kept]


def _merge_frames(clip: Clip, drop) -> None:
    """Remove os quadros em que drop(indice, quadro, anterior_mantido) e verdadeiro; o tempo vai para o anterior."""
    frames: list = []
    durations: list[int] = []
    for index, (frame, duration) in enumerate(zip(clip.frames, clip.durations)):
        if frames and drop(index, frame, frames[-1]):
            durations[-1] += duration
        else:
            frames.append(frame)
            durations.append(duration)
    clip.frames, clip.durations = frames, durations


def apply_ops(clip: Clip, options: dict[str, Any] | None = None,
              cancel_event: threading.Event | None = None) -> Clip:
    from PIL import Image

    options = options or {}
    _cut(clip, options.get("start"), options.get("end"))
    every = int(options.get("drop_every") or 0)
    if every >= 2:
        _merge_frames(clip, lambda index, _frame, _previous: (index + 1) % every == 0)

    crop = clamp_box(options.get("crop"), clip.size)
    target = resize_size(crop[2:] if crop else clip.size, options.get("resize"))
    angle = float(options.get("rotate") or 0)
    flip_h, flip_v = bool(options.get("flip_h")), bool(options.get("flip_v"))
    effects = has_effects(options)
    censor = options.get("censor")
    background = options.get("background")
    wants_layer = bool(options.get("overlay") or (options.get("text") or {}).get("text", "").strip())
    layer = None

    for index, frame in enumerate(clip.frames):
        check_cancel(cancel_event)
        if crop:
            x, y, w, h = crop
            frame = frame.crop((x, y, x + w, y + h))
        if frame.size != target:
            frame = frame.resize(target, Image.Resampling.LANCZOS)
        frame = _rotate(frame, angle, flip_h, flip_v)
        if effects:
            frame = _effects(frame, options)
        if censor:
            frame = _censor(frame.copy(), clamp_box(censor, frame.size), censor.get("mode", "blur"))
        if wants_layer:
            if layer is None:
                layer = render_layer(frame.size, options)
            frame = Image.alpha_composite(frame, layer)
        if background:
            frame = _flatten(frame, background)
        clip.frames[index] = frame

    if options.get("dedupe"):
        _merge_frames(clip, lambda _index, frame, previous: frame.tobytes() == previous.tobytes())
    speed = float(options.get("speed") or 1)
    if speed != 1:
        if speed <= 0:
            raise ConversionError("A velocidade precisa ser maior que zero.")
        clip.durations = [max(1, round(d / speed)) for d in clip.durations]
    if options.get("reverse"):
        clip.frames.reverse()
        clip.durations.reverse()
    if options.get("boomerang") and len(clip.frames) > 2:
        clip.frames += clip.frames[-2:0:-1]
        clip.durations += clip.durations[-2:0:-1]
    if options.get("delay"):
        clip.durations = [max(1, int(options["delay"]))] * len(clip.frames)
    if options.get("loop") is not None:
        clip.loop = max(0, int(options["loop"]))
    return clip
