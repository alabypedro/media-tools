"""PDF -> PNG/JPG/WEBP, pagina por pagina (item 7).

Usa PyMuPDF (pymupdf) para renderizar cada pagina num bitmap no DPI
pedido -- nao depende de instalar Poppler/Ghostscript no sistema, o que
importa bastante para o empacotamento em .exe (item 31).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.errors import ConversionError, missing_dependency
from ..core.paths import ConflictPolicy, page_range_to_indices, resolve_conflict

DEFAULT_DPI = 200
_PIL_FORMAT = {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG", "webp": "WEBP"}


def page_count(input_path: Path) -> int:
    doc = _open(input_path)
    try:
        return doc.page_count
    finally:
        doc.close()


def _open(input_path: Path):
    try:
        import pymupdf
    except ModuleNotFoundError as exc:
        raise missing_dependency("pymupdf", "abrir/renderizar PDFs", exc) from exc
    try:
        return pymupdf.open(input_path)
    except Exception as exc:
        raise ConversionError(
            f"Nao foi possivel abrir '{input_path.name}' como PDF.",
            hint="Verifique se o arquivo nao esta corrompido ou protegido por senha.",
            detail=exc,
        ) from exc


def convert(
    input_path: Path,
    output_dir: Path,
    target_ext: str,
    options: dict[str, Any] | None = None,
) -> list[Path]:
    """Renderiza as paginas pedidas do PDF como imagens.

    options:
        pages: str  -- ex. "1-5,10,20-25" (vazio/None = todas as paginas)
        dpi: int
        quality: int  -- 1-95, so para jpg/webp
        conflict_policy: ConflictPolicy
    """
    options = options or {}
    dpi = int(options.get("dpi", DEFAULT_DPI))
    quality = options.get("quality")
    conflict_policy = ConflictPolicy(options.get("conflict_policy", ConflictPolicy.RENAME.value))
    pil_format = _PIL_FORMAT.get(target_ext.lower().lstrip("."))
    if pil_format is None:
        raise ConversionError(f"Formato de imagem de destino nao suportado para PDF: {target_ext}")

    import pymupdf

    doc = _open(input_path)
    try:
        total_pages = doc.page_count
        indices = page_range_to_indices(options.get("pages", ""), total_pages)
        if not indices:
            raise ConversionError(
                f"Nenhuma das paginas pedidas existe em '{input_path.name}' ({total_pages} pagina(s))."
            )

        zoom = dpi / 72.0
        matrix = pymupdf.Matrix(zoom, zoom)
        digits = max(3, len(str(total_pages)))
        stem = input_path.stem

        output_dir.mkdir(parents=True, exist_ok=True)
        produced: list[Path] = []
        for idx in indices:
            page = doc.load_page(idx)
            pixmap = page.get_pixmap(matrix=matrix)
            name = f"{stem}_pagina_{idx + 1:0{digits}d}.{target_ext.lstrip('.')}"
            candidate = output_dir / name
            final_path = resolve_conflict(candidate, conflict_policy)
            if final_path is None:
                continue  # SKIP

            if pil_format == "PNG":
                pixmap.save(final_path)
            else:
                from PIL import Image

                img = Image.frombytes("RGB", (pixmap.width, pixmap.height), pixmap.samples)
                save_kwargs: dict[str, Any] = {}
                if quality is not None:
                    save_kwargs["quality"] = int(quality)
                img.save(final_path, format=pil_format, **save_kwargs)
            produced.append(final_path)
        return produced
    finally:
        doc.close()
