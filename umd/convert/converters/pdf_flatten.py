"""Achata (flatten) PDFs: campos de formulario preenchiveis e anotacoes
(comentarios, carimbos, destaques...) viram conteudo estatico da pagina,
e o PDF deixa de ser editavel -- como o https://www.sejda.com/flatten-pdf.

Veio do antigo scripts/flatten_pdf.py. Usa o pypdf.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..core.errors import ConversionError, missing_dependency

# Anotacoes que nunca sao achatadas: nao sao conteudo visual fixo (links
# continuam clicaveis; popups sao so a janelinha de um comentario).
SUBTYPES_TO_SKIP = {"/Link", "/Popup"}
# Icones de nota ("post-it"): sem aparencia impressa relevante, so removidos.
SUBTYPES_TO_DROP_SILENTLY = {"/Text"}


def _pypdf():
    try:
        import pypdf
        from pypdf import generic
    except ModuleNotFoundError as exc:
        raise missing_dependency("pypdf", "achatar PDFs", exc) from exc
    return pypdf, generic


def _resolve_normal_appearance(annotation, generic):
    """Aparencia ativa (/AP /N) da anotacao, resolvendo o estado (/AS) quando
    /N e um dicionario de estados (caixas de selecao, radio buttons)."""
    ap = annotation.get("/AP")
    if ap is None:
        return None
    normal = ap.get_object().get("/N")
    if normal is None:
        return None
    normal = normal.get_object()
    if isinstance(normal, generic.StreamObject):
        return normal
    if isinstance(normal, generic.DictionaryObject):
        state = annotation.get("/AS")
        chosen = normal.get(state) if state is not None else None
        if chosen is None:
            return None
        chosen = chosen.get_object()
        return chosen if isinstance(chosen, generic.StreamObject) else None
    return None


def convert(input_path: Path, output_path: Path, options: dict[str, Any] | None = None) -> Path:
    pypdf, generic = _pypdf()
    NameObject, DictionaryObject, ArrayObject = generic.NameObject, generic.DictionaryObject, generic.ArrayObject

    try:
        reader = pypdf.PdfReader(str(input_path))
        if reader.is_encrypted:
            raise ConversionError(
                f"'{input_path.name}' esta protegido por senha.",
                hint="Remova a senha do PDF antes de achatar.",
            )
        writer = pypdf.PdfWriter(clone_from=reader)

        # 1) regenera as aparencias (/AP) dos campos com os valores atuais
        if "/AcroForm" in writer._root_object:
            values = {name: f.value for name, f in (reader.get_fields() or {}).items() if f.value is not None}
            if values:
                try:
                    writer.update_page_form_field_values(None, values, auto_regenerate=False, flatten=False)
                except Exception:  # noqa: BLE001 - PDF malformado: segue com as aparencias existentes
                    pass

        # 2) desenha a aparencia ativa de cada anotacao no conteudo da pagina e remove a anotacao
        for page in writer.pages:
            annots = page.get("/Annots")
            if not annots:
                continue
            kept = []
            for ref in annots.get_object():
                annotation = ref.get_object()
                subtype = annotation.get("/Subtype")
                if subtype in SUBTYPES_TO_SKIP:
                    kept.append(ref)
                    continue
                if subtype in SUBTYPES_TO_DROP_SILENTLY:
                    continue
                appearance = _resolve_normal_appearance(annotation, generic)
                if appearance is None:
                    kept.append(ref)  # sem aparencia segura para desenhar: preserva, nao perde conteudo
                    continue
                rect = annotation.get("/Rect")
                name = str(annotation.get("/T", "") or annotation.indirect_reference.idnum)
                if "/Resources" not in page:
                    page[NameObject("/Resources")] = DictionaryObject()
                writer._add_apstream_object(page, appearance, name, float(rect[0]), float(rect[1]))
            if kept:
                page[NameObject("/Annots")] = ArrayObject(kept)
            elif "/Annots" in page:
                del page[NameObject("/Annots")]

        # 3) nenhum campo preenchivel pode sobrar no documento final
        if "/AcroForm" in writer._root_object:
            del writer._root_object[NameObject("/AcroForm")]

        output_path.parent.mkdir(parents=True, exist_ok=True)
        with open(output_path, "wb") as handle:
            writer.write(handle)
    except ConversionError:
        raise
    except Exception as exc:  # noqa: BLE001 - PDF corrompido/incomum vira mensagem amigavel
        output_path.unlink(missing_ok=True)
        raise ConversionError(
            f"Nao foi possivel achatar '{input_path.name}'.",
            hint="Verifique se o arquivo e um PDF valido e nao esta aberto em outro programa.",
            detail=exc,
        ) from exc
    return output_path
