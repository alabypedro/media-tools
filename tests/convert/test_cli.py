from __future__ import annotations

from pathlib import Path

import pytest

from umd.convert import cli
from umd.convert.converters import office


def test_cli_formatos(capsys):
    code = cli.main(["--formatos"])
    out = capsys.readouterr().out
    assert code == 0
    assert "Imagem" in out and "PDF" in out


def test_cli_single_file_conversion(tmp_path: Path, sample_png: Path, capsys):
    code = cli.main([str(sample_png), "webp"])
    out = capsys.readouterr().out
    assert code == 0
    assert "[OK]" in out
    assert (sample_png.parent / "foto de teste.webp").exists()


def test_cli_missing_file(tmp_path: Path, capsys):
    code = cli.main([str(tmp_path / "nao_existe.png"), "jpg"])
    assert code == 1


def test_cli_unsupported_format(tmp_path: Path, sample_png: Path, capsys):
    code = cli.main([str(sample_png), "mp3"])
    out = capsys.readouterr().out
    assert code == 1
    assert "ERRO" in out


def test_cli_name_conflict_renames_instead_of_overwrite(tmp_path: Path, sample_png: Path, capsys):
    cli.main([str(sample_png), "jpg"])
    capsys.readouterr()
    cli.main([str(sample_png), "jpg"])
    out = capsys.readouterr().out
    assert "(1)" in out


def test_cli_overwrite_flag(tmp_path: Path, sample_png: Path, capsys):
    cli.main([str(sample_png), "jpg"])
    capsys.readouterr()
    code = cli.main([str(sample_png), "jpg", "--overwrite"])
    out = capsys.readouterr().out
    assert code == 0
    assert "(1)" not in out


def test_cli_folder_batch_recursive(tmp_path: Path):
    (tmp_path / "sub1").mkdir()
    (tmp_path / "sub2").mkdir()
    (tmp_path / "sub1" / "a.txt").write_text("conteudo a", encoding="utf-8")
    (tmp_path / "sub2" / "b.txt").write_text("conteudo b", encoding="utf-8")

    code = cli.main([str(tmp_path), "pdf", "--recursive"])
    assert code == 0
    assert (tmp_path / "sub1" / "a.pdf").exists()
    assert (tmp_path / "sub2" / "b.pdf").exists()


def test_cli_folder_batch_preserves_structure_with_output(tmp_path: Path):
    (tmp_path / "lote" / "p1").mkdir(parents=True)
    (tmp_path / "lote" / "p1" / "doc.txt").write_text("ola", encoding="utf-8")
    out_dir = tmp_path / "convertidos"

    code = cli.main([str(tmp_path / "lote"), "pdf", "--recursive", "--output", str(out_dir)])
    assert code == 0
    assert (out_dir / "p1" / "doc.pdf").exists()
    assert not (tmp_path / "lote" / "p1" / "doc.pdf").exists()


def test_cli_images_to_pdf(tmp_path: Path, sample_png: Path):
    output = tmp_path / "album.pdf"
    code = cli.main(["--images-to-pdf", str(sample_png), str(output)])
    assert code == 0
    assert output.exists()


def test_cli_explicit_output_name(tmp_path: Path, sample_png: Path):
    output = tmp_path / "resultado_final.jpg"
    code = cli.main([str(sample_png), "jpg", str(output)])
    assert code == 0
    assert output.exists()


@pytest.mark.skipif(office.is_available(), reason="teste do caminho SEM LibreOffice instalado")
def test_cli_office_missing_gives_friendly_message(tmp_path: Path, capsys):
    docx_like = tmp_path / "fake.docx"
    docx_like.write_bytes(b"nao e um docx de verdade, so para testar a mensagem de erro")
    code = cli.main([str(docx_like), "pdf"])
    out = capsys.readouterr().out
    assert code == 1
    assert "LibreOffice" in out
    assert "Traceback" not in out
