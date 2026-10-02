"""Conversao de imagens (Pillow). Evolucao de convert_image() do antigo
convert.py: agora com qualidade, redimensionamento, DPI, metadados e
tratamento explicito de GIF animado (item 12/13).
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from ..core.errors import ConversionError, missing_dependency
from ..core.logging_setup import get_logger

_EXIF_CAPABLE_FORMATS = {"JPEG", "TIFF", "WEBP", "AVIF"}


def _pil_format(target_ext: str) -> str:
    return {"jpg": "JPEG", "tif": "TIFF"}.get(target_ext, target_ext.upper())


def _resize(img, options: dict[str, Any]):
    resize = options.get("resize")
    if not resize:
        return img
    width = resize.get("width")
    height = resize.get("height")
    if not width and not height:
        return img
    keep_aspect = resize.get("keep_aspect", True)

    from PIL import Image

    if keep_aspect:
        box = (width or img.width, height or img.height)
        img = img.copy()
        img.thumbnail(box, Image.LANCZOS)
        return img
    return img.resize((width or img.width, height or img.height), Image.LANCZOS)


def _prepare_frame(frame, pil_format: str):
    if pil_format in ("JPEG", "PDF") and frame.mode in ("RGBA", "LA", "P"):
        frame = frame.convert("RGB")
    return frame


def convert(
    input_path: Path,
    output_path: Path,
    options: dict[str, Any] | None = None,
    cancel_event: threading.Event | None = None,
) -> Path:
    options = options or {}
    logger = get_logger()

    try:
        from PIL import Image, ImageSequence
    except ModuleNotFoundError as exc:
        raise missing_dependency("pillow", "converter imagens", exc) from exc

    target_ext = output_path.suffix.lower().lstrip(".")
    pil_format = _pil_format(target_ext)

    if target_ext in ("heic", "heif"):
        try:
            import pillow_heif

            pillow_heif.register_heif_opener()
        except ModuleNotFoundError as exc:
            raise missing_dependency(
                "pillow-heif", "converter para/de HEIC/HEIF (formato opcional)", exc
            ) from exc

    try:
        with Image.open(input_path) as img:
            img.load()
            is_animated = bool(getattr(img, "is_animated", False))

            save_kwargs: dict[str, Any] = {}
            dpi = options.get("dpi")
            if dpi:
                save_kwargs["dpi"] = (dpi, dpi)

            quality = options.get("quality")
            if quality is not None and pil_format in ("JPEG", "WEBP", "AVIF"):
                save_kwargs["quality"] = int(quality)
            if pil_format == "PNG":
                save_kwargs["optimize"] = True

            keep_metadata = options.get("keep_metadata", True)
            if keep_metadata and pil_format in _EXIF_CAPABLE_FORMATS and img.info.get("exif"):
                save_kwargs["exif"] = img.info["exif"]

            if is_animated and pil_format in ("GIF", "WEBP"):
                frames = [
                    _resize(_prepare_frame(f.convert(img.mode), pil_format), options)
                    for f in ImageSequence.Iterator(img)
                ]
                first, rest = frames[0], frames[1:]
                save_kwargs["save_all"] = True
                save_kwargs["append_images"] = rest
                save_kwargs["duration"] = img.info.get("duration", 100)
                save_kwargs["loop"] = img.info.get("loop", 0)
                output_path.parent.mkdir(parents=True, exist_ok=True)
                first.save(output_path, format=pil_format, **save_kwargs)
                return output_path

            if is_animated and pil_format not in ("GIF", "WEBP"):
                logger.warning(
                    "'%s' e uma animacao (GIF); como '.%s' nao suporta animacao, "
                    "apenas o primeiro quadro sera salvo.",
                    input_path.name, target_ext,
                )

            frame = _prepare_frame(img, pil_format)
            frame = _resize(frame, options)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            frame.save(output_path, format=pil_format, **save_kwargs)
            return output_path
    except ConversionError:
        raise
    except OSError as exc:
        raise ConversionError(
            f"Nao foi possivel ler ou gravar a imagem '{input_path.name}'.",
            hint="Verifique se o arquivo nao esta corrompido e se voce tem permissao na pasta de destino.",
            detail=exc,
        ) from exc
