"""Varias imagens -> um PDF, uma pagina por imagem (item 8).

Usa reportlab para ter controle real de tamanho de pagina, orientacao,
margem e ajuste da imagem -- coisa que o `Image.save(...,"PDF")` do
Pillow (usado para conversao 1-para-1 em converters/image.py) nao
oferece.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.errors import ConversionError, missing_dependency

PAGE_SIZES_MM = {
    "a4": (210, 297),
    "letter": (215.9, 279.4),
    "a3": (297, 420),
    "original": None,  # usa o tamanho da propria imagem
}

FIT_MODES = ("contain", "cover", "stretch")


def _mm_to_pt(mm: float) -> float:
    return mm * 72.0 / 25.4


def convert(
    input_paths: list[Path],
    output_path: Path,
    options: dict[str, Any] | None = None,
) -> Path:
    """options:
        page_size: "a4" | "letter" | "a3" | "original"
        orientation: "portrait" | "landscape"
        fit: "contain" | "cover" | "stretch"
        margin_mm: float
        quality: int (reencode como JPEG interno para reduzir tamanho; None = sem recompressao)
    """
    options = options or {}
    if not input_paths:
        raise ConversionError("Nenhuma imagem selecionada para gerar o PDF.")

    try:
        from PIL import Image
    except ModuleNotFoundError as exc:
        raise missing_dependency("pillow", "ler as imagens", exc) from exc
    try:
        from reportlab.pdfgen import canvas
    except ModuleNotFoundError as exc:
        raise missing_dependency("reportlab", "gerar o PDF", exc) from exc

    page_size_key = options.get("page_size", "a4").lower()
    orientation = options.get("orientation", "portrait").lower()
    fit = options.get("fit", "contain").lower()
    if fit not in FIT_MODES:
        raise ConversionError(f"Modo de ajuste invalido: {fit!r}. Use um destes: {', '.join(FIT_MODES)}")
    margin_pt = _mm_to_pt(float(options.get("margin_mm", 10)))
    quality = options.get("quality")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(output_path))

    for image_path in input_paths:
        try:
            with Image.open(image_path) as img:
                img.load()
                if img.mode in ("RGBA", "LA", "P"):
                    img = img.convert("RGB")

                fixed_size_mm = PAGE_SIZES_MM.get(page_size_key)
                if fixed_size_mm is None:
                    page_w_pt, page_h_pt = img.width, img.height
                    box_w_pt, box_h_pt = page_w_pt, page_h_pt
                    m = 0.0
                else:
                    w_mm, h_mm = fixed_size_mm
                    if orientation == "landscape":
                        w_mm, h_mm = h_mm, w_mm
                    page_w_pt, page_h_pt = _mm_to_pt(w_mm), _mm_to_pt(h_mm)
                    m = margin_pt
                    box_w_pt, box_h_pt = page_w_pt - 2 * m, page_h_pt - 2 * m

                img_ratio = img.width / img.height
                box_ratio = box_w_pt / box_h_pt

                if fit == "stretch":
                    draw_w, draw_h = box_w_pt, box_h_pt
                elif fit == "cover":
                    if img_ratio > box_ratio:
                        draw_h = box_h_pt
                        draw_w = draw_h * img_ratio
                    else:
                        draw_w = box_w_pt
                        draw_h = draw_w / img_ratio
                else:  # contain
                    if img_ratio > box_ratio:
                        draw_w = box_w_pt
                        draw_h = draw_w / img_ratio
                    else:
                        draw_h = box_h_pt
                        draw_w = draw_h * img_ratio

                x = m + (box_w_pt - draw_w) / 2
                y = m + (box_h_pt - draw_h) / 2

                c.setPageSize((page_w_pt, page_h_pt))

                from reportlab.lib.utils import ImageReader

                if quality is not None:
                    import io
                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=int(quality))
                    buf.seek(0)
                    image_source = ImageReader(buf)
                else:
                    image_source = ImageReader(img)

                if fit == "cover":
                    # "cover" pode estourar a caixa (isso e o objetivo: sem
                    # sobras); recorta pra nao vazar sobre a margem/pagina.
                    c.saveState()
                    clip = c.beginPath()
                    clip.rect(m, m, box_w_pt, box_h_pt)
                    c.clipPath(clip, stroke=0, fill=0)
                    c.drawImage(image_source, x, y, width=draw_w, height=draw_h)
                    c.restoreState()
                else:
                    c.drawImage(image_source, x, y, width=draw_w, height=draw_h)

                c.showPage()
        except ConversionError:
            raise
        except OSError as exc:
            raise ConversionError(
                f"Nao foi possivel ler a imagem '{image_path.name}'.",
                detail=exc,
            ) from exc

    c.save()
    return output_path
