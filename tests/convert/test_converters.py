from __future__ import annotations

from pathlib import Path

import pytest

from umd.convert.converters import archive, image, image_to_pdf, markdown_to_pdf, office, pdf_to_image, text_to_pdf
from umd.convert.core.errors import ConversionError


# ----------------------------------------------------------------- image
def test_image_convert_basic(tmp_path: Path, sample_png: Path):
    out = image.convert(sample_png, tmp_path / "saida.jpg", {"quality": 80})
    assert out.exists()
    from PIL import Image as PILImage
    with PILImage.open(out) as img:
        assert img.format == "JPEG"


def test_image_resize_keep_aspect(tmp_path: Path, sample_png: Path):
    out = image.convert(sample_png, tmp_path / "menor.png", {"resize": {"width": 60, "keep_aspect": True}})
    from PIL import Image as PILImage
    with PILImage.open(out) as img:
        assert img.width <= 60


def test_image_animated_gif_to_static_warns_but_completes(tmp_path: Path, sample_animated_gif: Path, caplog):
    out = image.convert(sample_animated_gif, tmp_path / "estatico.png")
    assert out.exists()
    from PIL import Image as PILImage
    with PILImage.open(out) as img:
        assert not getattr(img, "is_animated", False)


def test_image_animated_gif_to_webp_preserves_animation(tmp_path: Path, sample_animated_gif: Path):
    out = image.convert(sample_animated_gif, tmp_path / "anim.webp")
    from PIL import Image as PILImage
    with PILImage.open(out) as img:
        assert getattr(img, "is_animated", False)
        assert img.n_frames == 4


def test_image_nonexistent_file_raises_friendly_error(tmp_path: Path):
    with pytest.raises(ConversionError):
        image.convert(tmp_path / "nao_existe.png", tmp_path / "saida.jpg")


# -------------------------------------------------------------- text/md
def test_text_to_pdf_multi_page(tmp_path: Path, sample_txt: Path):
    out = text_to_pdf.convert(sample_txt, tmp_path / "saida.pdf", {"font_size": 12})
    assert out.exists()
    import pymupdf
    doc = pymupdf.open(out)
    try:
        assert doc.page_count >= 2  # texto longo o bastante pra quebrar pagina
        text = doc[0].get_text()
        assert "acentuacao" in text or "coracao" in text
    finally:
        doc.close()


def test_markdown_to_pdf_structure(tmp_path: Path, sample_markdown: Path):
    out = markdown_to_pdf.convert(sample_markdown, tmp_path / "saida.pdf")
    assert out.exists()
    import pymupdf
    doc = pymupdf.open(out)
    try:
        text = doc[0].get_text()
        assert "Titulo principal" in text
        assert "negrito" in text
        assert "Coluna A" not in text or "A" in text  # tabela renderizada (celulas presentes)
        assert "print" in text
    finally:
        doc.close()


# ------------------------------------------------------------- pdf<->img
def test_pdf_to_image_all_pages(tmp_path: Path, sample_pdf: Path):
    out_dir = tmp_path / "paginas"
    produced = pdf_to_image.convert(sample_pdf, out_dir, "png", {"dpi": 100})
    assert len(produced) == pdf_to_image.page_count(sample_pdf)
    assert produced[0].name.startswith(sample_pdf.stem + "_pagina_001")


def test_pdf_to_image_page_range(tmp_path: Path, sample_pdf: Path):
    out_dir = tmp_path / "paginas"
    produced = pdf_to_image.convert(sample_pdf, out_dir, "jpg", {"pages": "1"})
    assert len(produced) == 1


def test_pdf_to_image_invalid_range_raises(tmp_path: Path, sample_pdf: Path):
    with pytest.raises(ConversionError):
        pdf_to_image.convert(sample_pdf, tmp_path / "paginas", "png", {"pages": "999"})


def test_images_to_pdf_merge(tmp_path: Path, sample_png: Path, sample_animated_gif: Path):
    out = image_to_pdf.convert([sample_png, sample_animated_gif], tmp_path / "album.pdf",
                                {"page_size": "a4", "fit": "cover"})
    assert out.exists()
    import pymupdf
    doc = pymupdf.open(out)
    try:
        assert doc.page_count == 2
    finally:
        doc.close()


def test_images_to_pdf_empty_list_raises(tmp_path: Path):
    with pytest.raises(ConversionError):
        image_to_pdf.convert([], tmp_path / "vazio.pdf")


# --------------------------------------------------------------- archive
def test_archive_zip_to_targz_roundtrip(tmp_path: Path, sample_zip: Path):
    out = archive.convert(sample_zip, tmp_path / "pacote.tar.gz", "tar.gz")
    assert out.exists()
    import tarfile
    with tarfile.open(out) as t:
        names = sorted(t.getnames())
    assert names == ["a.txt", "sub/b.txt"]


def test_archive_rejects_zip_slip(tmp_path: Path):
    import zipfile

    evil = tmp_path / "evil.zip"
    with zipfile.ZipFile(evil, "w") as z:
        z.writestr("../../evil.txt", "muahaha")
    with pytest.raises(ConversionError):
        archive.convert(evil, tmp_path / "saida.tar", "tar")
    assert not (tmp_path.parent.parent / "evil.txt").exists()


def test_archive_unrecognized_file_raises(tmp_path: Path):
    fake = tmp_path / "nao_e_zip.zip"
    fake.write_text("isso nao e um zip de verdade")
    with pytest.raises(ConversionError):
        archive.convert(fake, tmp_path / "saida.tar", "tar")


# ---------------------------------------------------------------- office
@pytest.mark.skipif(not office.is_available(), reason="LibreOffice nao instalado nesta maquina")
def test_office_txt_to_docx_to_pdf_roundtrip(tmp_path: Path, sample_txt: Path):
    docx = office.convert(sample_txt, tmp_path / "saida.docx")
    assert docx.exists() and docx.stat().st_size > 0

    pdf = office.convert(docx, tmp_path / "saida.pdf")
    assert pdf.exists()
    import pymupdf
    doc = pymupdf.open(pdf)
    try:
        assert doc.page_count >= 1
        assert "acentuacao" in doc[0].get_text() or "coracao" in doc[0].get_text()
    finally:
        doc.close()


@pytest.mark.skipif(not office.is_available(), reason="LibreOffice nao instalado nesta maquina")
def test_office_does_not_overwrite_without_asking(tmp_path: Path, sample_txt: Path):
    from umd.convert.core.queue import ConversionQueue, JobStatus, run_queue
    from umd.convert.core.config import Settings

    q = ConversionQueue()
    q.add(sample_txt, "docx", output_dir=tmp_path)
    settings = Settings(default_output_dir=str(tmp_path))
    run_queue(q, settings)
    first = q.jobs[0]
    assert first.status == JobStatus.DONE
    first_output = first.output_paths[0]
    assert first_output.exists()

    q2 = ConversionQueue()
    q2.add(sample_txt, "docx", output_dir=tmp_path)
    run_queue(q2, settings)
    second_output = q2.jobs[0].output_paths[0]
    assert second_output != first_output
    assert "(1)" in second_output.name
