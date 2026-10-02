from __future__ import annotations

from pathlib import Path

import pytest

from umd.rename import cli

from .conftest import make_files, make_photo, names


def test_help_lists_the_fields(capsys) -> None:
    with pytest.raises(SystemExit) as info:
        cli.main(["--help"])
    assert info.value.code == 0
    out = capsys.readouterr().out
    assert "--exif" in out and "{n:3}" in out and "--undo" in out


def test_no_arguments_prints_help(capsys) -> None:
    assert cli.main([]) == 1
    assert "umd rename" in capsys.readouterr().out


def test_preview_by_default_changes_nothing(folder: Path, capsys) -> None:
    make_files(folder, "a.txt", "b.txt")
    assert cli.main([str(folder), "-p", "doc {n:2}"]) == 0
    out = capsys.readouterr().out
    assert "a.txt -> doc 01.txt" in out and "--apply" in out and "2 de 2" in out
    assert names(folder) == ["a.txt", "b.txt"]


def test_main_cli_dispatches_rename_apply_and_undo(folder: Path, capsys) -> None:
    from umd.cli import main

    make_files(folder, "a.txt", "b.txt")
    assert main(["rename", str(folder), "-p", "doc {n:2}", "--apply"]) == 0
    assert "[OK] 2" in capsys.readouterr().out and names(folder) == ["doc 01.txt", "doc 02.txt"]
    assert main(["rename", "--undo"]) == 0
    assert "[OK] 2" in capsys.readouterr().out and names(folder) == ["a.txt", "b.txt"]
    assert main(["rename", "--undo"]) == 1
    assert "[ERRO]" in capsys.readouterr().out


def test_exif_shortcut_and_exif_only(folder: Path, capsys) -> None:
    make_photo(folder / "com.jpg", "2024:03:15 14:30:22")
    make_photo(folder / "sem.jpg")
    assert cli.main([str(folder), "--exif", "--exif-only", "-y"]) == 0
    out = capsys.readouterr().out
    assert "[PULADO] sem.jpg: sem data EXIF" in out
    assert names(folder) == ["2024-03-15 14.30.22.jpg", "sem.jpg"]
    assert cli.main([str(folder), "--exif", "%Y", "-p", "x"]) == 1  # um ou outro
    assert "[ERRO]" in capsys.readouterr().out


def test_find_replace_regex_case_and_wildcard(folder: Path, capsys) -> None:
    make_files(folder, "IMG_01.JPG", "IMG_02.JPG", "nota.txt")
    assert cli.main([str(folder / "*.JPG"), "--find", r"IMG_(\d+)", "--replace", r"foto \1", "--regex",
                     "--ext-case", "lower", "-y"]) == 0
    assert names(folder) == ["foto 01.jpg", "foto 02.jpg", "nota.txt"]
    assert cli.main([str(folder), "--case", "upper"]) == 0
    assert "foto 01.jpg -> FOTO 01.jpg" in capsys.readouterr().out


def test_nothing_to_rename_and_errors(folder: Path, tmp_path: Path, capsys) -> None:
    make_files(folder, "a.txt")
    assert cli.main([str(folder)]) == 0
    assert "Nada para renomear" in capsys.readouterr().out
    assert cli.main([str(tmp_path / "nao_existe"), "-p", "x"]) == 1
    assert cli.main([str(folder), "-p", "{campo}"]) == 1
    assert cli.main(["-p", "x"]) == 1
    assert capsys.readouterr().out.count("[ERRO]") == 3
    with pytest.raises(SystemExit) as info:
        cli.main([str(folder), "--case", "gritando"])
    assert info.value.code == 2


def test_recursive_and_sort(folder: Path) -> None:
    make_files(folder, "b.txt", "a.txt")
    make_files(folder / "sub", "c.txt")
    assert cli.main([str(folder), "-r", "-p", "{parent} {n}", "-y"]) == 0
    assert names(folder) == ["arquivos 1.txt", "arquivos 2.txt", "sub"]
    assert (folder / "arquivos 1.txt").read_text(encoding="utf-8") == "a.txt"
    assert names(folder / "sub") == ["sub 3.txt"]
