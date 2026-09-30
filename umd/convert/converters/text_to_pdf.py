"""TXT -> PDF (item 10). Nao depende do LibreOffice: gera o PDF
diretamente com reportlab, com quebra de linha automatica, controle de
fonte/tamanho/margem e paginacao. As fontes base do reportlab
(Helvetica/Times/Courier) usam WinAnsiEncoding, que cobre os acentos e
caracteres especiais do portugues (a, e, c, etc.)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.errors import ConversionError, missing_dependency

PAGE_SIZES = {"a4": None, "letter": None}  # preenchido de forma preguicosa (ver _page_size)

FONT_MAP = {
    "helvetica": "Helvetica",
    "times": "Times-Roman",
    "courier": "Courier",
}


def _page_size(name: str):
    from reportlab.lib.pagesizes import A4, LETTER

    return {"a4": A4, "letter": LETTER}.get(name.lower(), A4)


def convert(
    input_path: Path,
    output_path: Path,
    options: dict[str, Any] | None = None,
) -> Path:
    """options:
        font: "helvetica" | "times" | "courier"
        font_size: int
        margin_mm: float
        page_size: "a4" | "letter"
        encoding: str (padrao utf-8)
    """
    options = options or {}
    try:
        from reportlab.lib.units import mm
        from reportlab.pdfbase.pdfmetrics import stringWidth
        from reportlab.pdfgen import canvas
    except ModuleNotFoundError as exc:
        raise missing_dependency("reportlab", "gerar PDF de texto", exc) from exc

    encoding = options.get("encoding", "utf-8")
    try:
        text = input_path.read_text(encoding=encoding)
    except UnicodeDecodeError:
        text = input_path.read_text(encoding="latin-1")
    except OSError as exc:
        raise ConversionError(f"Nao foi possivel ler '{input_path.name}'.", detail=exc) from exc

    font = FONT_MAP.get(options.get("font", "helvetica").lower(), "Helvetica")
    font_size = int(options.get("font_size", 11))
    margin = float(options.get("margin_mm", 20)) * mm
    page_w, page_h = _page_size(options.get("page_size", "a4"))
    leading = font_size * 1.3
    max_width = page_w - 2 * margin

    output_path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(output_path), pagesize=(page_w, page_h))
    c.setFont(font, font_size)

    y = page_h - margin
    for paragraph in text.splitlines() or [""]:
        for line in _wrap_line(paragraph, font, font_size, max_width, stringWidth):
            if y < margin:
                c.showPage()
                c.setFont(font, font_size)
                y = page_h - margin
            c.drawString(margin, y, line)
            y -= leading

    c.save()
    return output_path


def _wrap_line(text: str, font: str, font_size: int, max_width: float, stringWidth) -> list[str]:
    if text == "":
        return [""]
    words = text.split(" ")
    lines: list[str] = []
    current = ""
    for word in words:
        candidate = f"{current} {word}".strip()
        if stringWidth(candidate, font, font_size) <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines
