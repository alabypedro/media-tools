"""Achatar PDF (antigo scripts/flatten_pdf.py): formularios e anotacoes viram conteudo fixo."""

from __future__ import annotations

from pathlib import Path

import pytest

from umd.convert import cli
from umd.convert.converters import pdf_flatten
from umd.convert.core import registry
from umd.convert.core.config import Settings
from umd.convert.core.errors import ConversionError
from umd.convert.core.queue import ConversionQueue, JobStatus, run_queue


def make_form_pdf(path: Path, value: str = "Maria da Silva") -> Path:
    """PDF com um campo de texto preenchido e uma caixa de selecao marcada."""
    from reportlab.pdfgen import canvas

    c = canvas.Canvas(str(path))
    c.drawString(72, 760, "Formulario de teste")
    c.acroForm.textfield(name="nome", value=value, x=72, y=700, width=250, height=24, borderWidth=1)
    c.acroForm.checkbox(name="aceito", checked=True, x=72, y=660, size=18)
    c.save()
    return path


def _widgets(path: Path) -> list:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    found = []
    for page in reader.pages:
        for ref in page.get("/Annots") or []:
            annot = ref.get_object()
            if annot.get("/Subtype") == "/Widget":
                found.append(annot)
    return found


def test_flatten_removes_form_but_keeps_filled_text(tmp_path: Path) -> None:
    from pypdf import PdfReader

    source = make_form_pdf(tmp_path / "formulario.pdf")
    assert _widgets(source) and "/AcroForm" in PdfReader(str(source)).trailer["/Root"]

    out = pdf_flatten.convert(source, tmp_path / "saida.pdf")

    reader = PdfReader(str(out))
    assert "/AcroForm" not in reader.trailer["/Root"]
    assert _widgets(out) == []
    assert not reader.get_fields()
    import pymupdf

    with pymupdf.open(out) as doc:
        text = doc[0].get_text()
    assert "Maria da Silva" in text and "Formulario de teste" in text


def test_links_are_kept(tmp_path: Path) -> None:
    from pypdf import PdfReader, PdfWriter
    from pypdf.annotations import Link

    source = make_form_pdf(tmp_path / "com_link.pdf")
    writer = PdfWriter(clone_from=str(source))
    writer.add_annotation(0, Link(rect=(72, 600, 200, 620), url="https://example.com"))
    writer.write(str(source))

    out = pdf_flatten.convert(source, tmp_path / "saida.pdf")
    subtypes = [ref.get_object().get("/Subtype") for ref in PdfReader(str(out)).pages[0].get("/Annots") or []]
    assert subtypes == ["/Link"]


def test_password_protected_pdf_gives_friendly_error(tmp_path: Path) -> None:
    from pypdf import PdfWriter

    source = make_form_pdf(tmp_path / "secreto.pdf")
    writer = PdfWriter(clone_from=str(source))
    writer.encrypt("senha")
    writer.write(str(source))
    with pytest.raises(ConversionError, match="senha"):
        pdf_flatten.convert(source, tmp_path / "saida.pdf")
    assert not (tmp_path / "saida.pdf").exists()


def test_invalid_pdf_gives_friendly_error(tmp_path: Path) -> None:
    bad = tmp_path / "quebrado.pdf"
    bad.write_bytes(b"isto nao e um pdf")
    with pytest.raises(ConversionError, match="achatar"):
        pdf_flatten.convert(bad, tmp_path / "saida.pdf")


def test_flatten_is_a_pdf_target_in_registry() -> None:
    assert registry.FLATTEN_TARGET in registry.compatible_targets("pdf")
    assert registry.FLATTEN_TARGET not in registry.compatible_targets("png")
    assert registry.resolve_handler("pdf", registry.FLATTEN_TARGET) == "pdf_flatten"


def test_queue_names_output_and_never_overwrites(tmp_path: Path) -> None:
    source = make_form_pdf(tmp_path / "ficha.pdf")
    settings = Settings()
    for expected in ("ficha (achatado).pdf", "ficha (achatado) (1).pdf"):
        q = ConversionQueue()
        q.add(source, registry.FLATTEN_TARGET)
        run_queue(q, settings)
        job = q.jobs[0]
        assert job.status == JobStatus.DONE, job.error
        assert job.output_paths == [tmp_path / expected]
    assert source.exists()  # o original nunca e alterado


def test_cli_single_file_with_alias_and_explicit_output(tmp_path: Path, capsys) -> None:
    source = make_form_pdf(tmp_path / "doc.pdf")
    assert cli.main([str(source), "achatar"]) == 0
    assert (tmp_path / "doc (achatado).pdf").exists()
    assert cli.main([str(source), "flatten", str(tmp_path / "final.pdf")]) == 0
    assert (tmp_path / "final.pdf").exists() and _widgets(tmp_path / "final.pdf") == []


def test_cli_folder_skips_already_flattened(tmp_path: Path, capsys) -> None:
    folder = tmp_path / "lote"
    folder.mkdir()
    make_form_pdf(folder / "a.pdf")
    make_form_pdf(folder / "b.pdf")
    make_form_pdf(folder / "c (achatado).pdf")
    assert cli.main([str(folder), "flatten"]) == 0
    names = sorted(p.name for p in folder.iterdir())
    assert names == ["a (achatado).pdf", "a.pdf", "b (achatado).pdf", "b.pdf", "c (achatado).pdf"]
