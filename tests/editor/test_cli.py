from __future__ import annotations

from pathlib import Path

import pytest

from umd.editor import cli

from .conftest import make_png, read


def test_help_lists_the_modes(capsys) -> None:
    with pytest.raises(SystemExit) as info:
        cli.main(["--help"])
    assert info.value.code == 0
    out = capsys.readouterr().out
    assert "--make" in out and "--resize" in out and "--split" in out


def test_main_cli_dispatches_edit(gif: Path, capsys) -> None:
    from umd.cli import main

    assert main(["edit", str(gif), "--resize", "50%"]) == 0
    assert "[OK]" in capsys.readouterr().out
    assert read(gif.with_name("anim (editado).gif"))[0][0].size == (20, 15)


def test_no_arguments_prints_help(capsys) -> None:
    assert cli.main([]) == 1
    assert "umd edit" in capsys.readouterr().out


def test_explicit_output_and_conflict(gif: Path, tmp_path: Path, capsys) -> None:
    out = tmp_path / "saida.webp"
    assert cli.main([str(gif), str(out), "--reverse", "--speed", "2"]) == 0
    assert len(read(out)[0]) == 4
    assert cli.main([str(gif), str(out)]) == 0
    assert (tmp_path / "saida (1).webp").exists()  # nao sobrescreve
    assert cli.main([str(gif), str(out), "--overwrite"]) == 0
    assert not (tmp_path / "saida (2).webp").exists()


def test_options_are_parsed(gif: Path) -> None:
    args = cli.build_parser().parse_args([
        str(gif), "--crop", "1,2,30,20", "--resize", "20x", "--stretch", "--rotate", "90", "--flip", "hv",
        "--text", "a\\nb", "--text-position", "top", "--censor", "0,0,5,5", "--censor-mode", "black",
        "--colors", "16", "--loop", "0", "--grayscale", "--brightness", "1.2"])
    options = cli.options_from_args(args)
    assert options["crop"] == {"x": 1, "y": 2, "width": 30, "height": 20}
    assert options["resize"] == {"width": 20, "height": 0, "keep_aspect": False}
    assert options["flip_h"] and options["flip_v"] and options["rotate"] == 90
    assert options["text"]["text"] == "a\nb" and options["text"]["position"] == "top"
    assert options["censor"]["mode"] == "black" and options["colors"] == 16 and options["loop"] == 0
    assert options["grayscale"] is True and options["brightness"] == 1.2 and "sepia" not in options


def test_bad_values_are_usage_errors(gif: Path) -> None:
    for bad in (["--crop", "1,2"], ["--resize", "grande"], ["--text-position", "lua"]):
        with pytest.raises(SystemExit) as info:
            cli.main([str(gif), *bad])
        assert info.value.code == 2


def test_missing_file_and_bad_color(gif: Path, tmp_path: Path, capsys) -> None:
    assert cli.main([str(tmp_path / "nao_existe.gif"), "--reverse"]) == 1
    assert "[ERRO]" in capsys.readouterr().out
    assert cli.main([str(gif), "--background", "cor-que-nao-existe"]) == 1
    assert "[ERRO]" in capsys.readouterr().out


def test_make_split_sprite_modes(gif: Path, tmp_path: Path, capsys) -> None:
    images = [str(make_png(tmp_path / "1.png", (255, 0, 0))), str(make_png(tmp_path / "2.png", (0, 0, 255)))]
    made = tmp_path / "feito.gif"
    assert cli.main(["--make", *images, "-o", str(made), "--delay", "150"]) == 0
    assert read(made)[1] == [150, 150]
    assert cli.main(["--split", str(gif), "--format", "jpg"]) == 0
    assert "4 quadros" in capsys.readouterr().out.splitlines()[-1]
    assert len(list((tmp_path / "anim (quadros)").glob("*.jpg"))) == 4
    sheet = tmp_path / "folha.png"
    assert cli.main(["--sprite", str(gif), "-o", str(sheet), "--columns", "4"]) == 0
    assert read(sheet)[0][0].size == (160, 30)
    assert cli.main(["--unsprite", str(sheet), "--columns", "4", "--rows", "1", "--to", "webp"]) == 0
    assert len(read(tmp_path / "folha (animacao).webp")[0]) == 4


def test_video_to_gif_and_merge(sample_video: Path, tmp_path: Path) -> None:
    out = tmp_path / "v.gif"
    assert cli.main([str(sample_video), str(out), "--end", "1", "--fps", "3", "--resize", "80x"]) == 0
    frames, _ = read(out)
    assert len(frames) == 3 and frames[0].size == (80, 60)
    merged = tmp_path / "m.mp4"
    assert cli.main(["--merge", str(sample_video), str(sample_video), "-o", str(merged)]) == 0
    assert merged.exists()
