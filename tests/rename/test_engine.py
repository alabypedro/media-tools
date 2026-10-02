from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

import pytest

from umd.convert.core.errors import ConversionError
from umd.rename import engine
from umd.rename.engine import RENAME, SAME, SKIP, RenameOptions

from .conftest import make_files, make_photo, names


def _targets(plan: list[engine.RenameItem]) -> list[str]:
    return [item.target.name for item in plan]


def test_pattern_with_counter_keeps_extension_and_pads(folder: Path) -> None:
    files = make_files(folder, "b.JPG", "a.png", "c.txt")
    plan = engine.build_plan(files, RenameOptions(pattern="Ferias {n:3}"))
    assert _targets(plan) == ["Ferias 001.JPG", "Ferias 002.png", "Ferias 003.txt"]
    assert all(item.status == RENAME for item in plan)
    assert names(folder) == ["a.png", "b.JPG", "c.txt"]  # a previa nao toca no disco


def test_counter_auto_width_start_step_and_sort(folder: Path) -> None:
    files = make_files(folder, *[f"foto{i}.jpg" for i in (10, 2, 1)], *[f"x{i}.jpg" for i in range(9)])
    plan = engine.build_plan(files, RenameOptions(pattern="{n}", sort="name"))
    assert [item.source.name for item in plan][:3] == ["foto1.jpg", "foto2.jpg", "foto10.jpg"]  # ordem natural
    assert _targets(plan)[0] == "01.jpg" and _targets(plan)[-1] == "12.jpg"  # digitos conforme o maior numero
    plan = engine.build_plan(files[:3], RenameOptions(pattern="{name}_{n}", start=5, step=5))
    assert _targets(plan) == ["foto10_05.jpg", "foto2_10.jpg", "foto1_15.jpg"]


def test_parent_and_ext_fields(folder: Path) -> None:
    (file,) = make_files(folder, "nota.md")
    assert _targets(engine.build_plan([file], RenameOptions(pattern="{parent}-{ext}-{name}"))) == ["arquivos-md-nota.md"]


def test_find_replace_text_and_regex(folder: Path) -> None:
    files = make_files(folder, "IMG_001.jpg", "img_002.jpg", "outro.jpg")
    plan = engine.build_plan(files, RenameOptions(find="IMG_", replace="foto-"))
    assert _targets(plan) == ["foto-001.jpg", "img_002.jpg", "outro.jpg"]
    assert [item.status for item in plan] == [RENAME, SAME, SAME]
    plan = engine.build_plan(files, RenameOptions(find="img_", replace="foto-", ignore_case=True))
    assert _targets(plan)[:2] == ["foto-001.jpg", "foto-002.jpg"]
    plan = engine.build_plan(files, RenameOptions(find=r"([a-z]+)_(\d+)", replace=r"\2-\1", regex=True, ignore_case=True))
    assert _targets(plan) == ["001-IMG.jpg", "002-img.jpg", "outro.jpg"]


def test_text_mode_treats_special_characters_literally(folder: Path) -> None:
    (file,) = make_files(folder, "a.b (1).txt")
    plan = engine.build_plan([file], RenameOptions(find=".b (1)", replace=r"\1"))
    assert _targets(plan) == ["a_1.txt"]  # "\" nao pode ficar no nome: vira "_"


def test_find_runs_before_the_pattern(folder: Path) -> None:
    (file,) = make_files(folder, "Cópia de relatorio.pdf")
    plan = engine.build_plan([file], RenameOptions(find="Cópia de ", pattern="2026 {name}"))
    assert _targets(plan) == ["2026 relatorio.pdf"]


def test_case_options(folder: Path) -> None:
    (file,) = make_files(folder, "Minha FOTO.JPG")
    assert _targets(engine.build_plan([file], RenameOptions(case="lower", ext_case="lower"))) == ["minha foto.jpg"]
    assert _targets(engine.build_plan([file], RenameOptions(case="upper"))) == ["MINHA FOTO.JPG"]
    assert _targets(engine.build_plan([file], RenameOptions(case="title", ext_case="lower"))) == ["Minha Foto.jpg"]


def test_invalid_characters_are_cleaned(folder: Path) -> None:
    (file,) = make_files(folder, "a.txt")
    plan = engine.build_plan([file], RenameOptions(pattern='x: y/z?  "w" v1.2'))
    assert _targets(plan) == ["x_ y_z_ _w_ v1.2.txt"]
    assert _targets(engine.build_plan([file], RenameOptions(pattern="CON"))) == ["_CON.txt"]


@pytest.mark.parametrize("pattern", ["", "   ", "{nome}", "{n:x}", "{name", "name}"])
def test_bad_pattern_is_a_friendly_error(folder: Path, pattern: str) -> None:
    with pytest.raises(ConversionError):
        engine.build_plan(make_files(folder, "a.txt"), RenameOptions(pattern=pattern))


def test_bad_regex_and_bad_group_are_friendly_errors(folder: Path) -> None:
    files = make_files(folder, "a.txt")
    with pytest.raises(ConversionError):
        engine.build_plan(files, RenameOptions(find="(", regex=True))
    with pytest.raises(ConversionError):
        engine.build_plan(files, RenameOptions(find="a", replace=r"\3", regex=True))


# ---------------------------------------------------------------- data EXIF

def test_exif_date_names_the_photo(folder: Path) -> None:
    photo = make_photo(folder / "IMG_1.jpg", "2024:03:15 14:30:22")
    assert engine.exif_date(photo) == datetime(2024, 3, 15, 14, 30, 22)
    plan = engine.build_plan([photo], RenameOptions(pattern=engine.EXIF_DATE_PATTERN))
    assert _targets(plan) == ["2024-03-15 14.30.22.jpg"] and plan[0].note == ""
    assert _targets(engine.build_plan([photo], RenameOptions(pattern="{date} {name}"))) == ["2024-03-15 IMG_1.jpg"]


def test_without_exif_falls_back_to_modification_date_or_skips(folder: Path) -> None:
    modified = datetime(2023, 12, 25, 8, 0, 0)
    photo = make_photo(folder / "sem_exif.jpg", modified=modified)
    (text,) = make_files(folder, "nota.txt")
    os.utime(text, (modified.timestamp(), modified.timestamp()))
    assert engine.exif_date(photo) is None and engine.exif_date(text) is None

    plan = engine.build_plan([photo, text], RenameOptions(pattern="{date:%Y%m%d} {name}"))
    assert _targets(plan) == ["20231225 sem_exif.jpg", "20231225 nota.txt"]
    assert {item.note for item in plan} == {engine.NOTE_MTIME}

    plan = engine.build_plan([photo, text], RenameOptions(pattern="{date} {name}", date_fallback=False))
    assert [item.status for item in plan] == [SKIP, SKIP] and plan[0].note == engine.NOTE_NO_DATE
    assert plan[0].target == photo


def test_same_second_photos_do_not_collide_and_sort_by_date(folder: Path) -> None:
    late = make_photo(folder / "a.jpg", "2024:01:02 10:00:00")
    first = make_photo(folder / "b.jpg", "2024:01:01 09:00:00")
    twin = make_photo(folder / "c.jpg", "2024:01:01 09:00:00")
    plan = engine.build_plan([late, first, twin], RenameOptions(pattern="{date:%Y-%m-%d %H.%M.%S}", sort="date"))
    assert [item.source.name for item in plan] == ["b.jpg", "c.jpg", "a.jpg"]
    assert _targets(plan) == ["2024-01-01 09.00.00.jpg", "2024-01-01 09.00.00 (1).jpg", "2024-01-02 10.00.00.jpg"]
    assert plan[1].note == engine.NOTE_CONFLICT


def test_bad_date_format_is_a_friendly_error(folder: Path) -> None:
    photo = make_photo(folder / "a.jpg", "2024:01:02 10:00:00")
    try:
        plan = engine.build_plan([photo], RenameOptions(pattern="{date:%Q}"))
    except ConversionError:
        return
    assert plan[0].target.suffix == ".jpg"  # ha sistemas que so copiam a diretiva desconhecida


# ---------------------------------------------------------------- conflitos, aplicar e desfazer

def test_never_overwrites_a_file_outside_the_batch(folder: Path) -> None:
    a, _other = make_files(folder, "a.txt", "novo.txt")
    plan = engine.build_plan([a], RenameOptions(pattern="novo"))
    assert _targets(plan) == ["novo (1).txt"] and plan[0].note == engine.NOTE_CONFLICT
    engine.apply_plan(plan)
    assert names(folder) == ["novo (1).txt", "novo.txt"]
    assert (folder / "novo.txt").read_text(encoding="utf-8") == "novo.txt"


def test_apply_and_undo(folder: Path) -> None:
    files = make_files(folder, "a.txt", "b.txt", "fica.txt")
    plan = engine.build_plan(files, RenameOptions(find="a", replace="z", pattern="{name}{name}"))
    assert [item.status for item in plan] == [RENAME, RENAME, RENAME]
    plan = engine.build_plan(files[:2], RenameOptions(pattern="doc {n}"))
    done = engine.apply_plan(plan)
    assert names(folder) == ["doc 1.txt", "doc 2.txt", "fica.txt"] and len(done) == 2
    assert engine.last_operation() == done
    restored = engine.undo_last()
    assert names(folder) == ["a.txt", "b.txt", "fica.txt"] and len(restored) == 2
    assert (folder / "a.txt").read_text(encoding="utf-8") == "a.txt"
    assert engine.last_operation() == []
    with pytest.raises(ConversionError):
        engine.undo_last()


def test_swap_and_chain_inside_the_batch(folder: Path) -> None:
    files = make_files(folder, "1.txt", "2.txt", "3.txt")  # 1->2, 2->3, 3->4: cada nome novo ainda esta ocupado
    plan = engine.build_plan(files, RenameOptions(pattern="{n}", start=2))
    assert _targets(plan) == ["2.txt", "3.txt", "4.txt"] and all(item.note == "" for item in plan)
    engine.apply_plan(plan)
    assert names(folder) == ["2.txt", "3.txt", "4.txt"]
    assert [(folder / f"{n}.txt").read_text(encoding="utf-8") for n in (2, 3, 4)] == ["1.txt", "2.txt", "3.txt"]
    engine.undo_last()
    assert [(folder / f"{n}.txt").read_text(encoding="utf-8") for n in (1, 2, 3)] == ["1.txt", "2.txt", "3.txt"]


def test_case_only_rename(folder: Path) -> None:
    (file,) = make_files(folder, "Foto.JPG")
    plan = engine.build_plan([file], RenameOptions(case="lower", ext_case="lower"))
    assert plan[0].status == RENAME and plan[0].note == ""
    engine.apply_plan(plan)
    assert names(folder) == ["foto.jpg"]


def test_unchanged_files_are_not_touched(folder: Path) -> None:
    files = make_files(folder, "a.txt")
    plan = engine.build_plan(files, RenameOptions())
    assert plan[0].status == SAME and engine.apply_plan(plan) == []
    assert engine.last_operation() == []


def test_apply_is_all_or_nothing(folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    files = make_files(folder, "a.txt", "b.txt", "c.txt")
    plan = engine.build_plan(files, RenameOptions(pattern="x{n}"))
    real = os.rename

    def flaky(src, dst):
        if Path(dst).name == "x3.txt":
            raise PermissionError(13, "em uso", str(src))
        real(src, dst)

    monkeypatch.setattr(engine.os, "rename", flaky)
    with pytest.raises(ConversionError) as info:
        engine.apply_plan(plan)
    assert "Nenhum arquivo foi renomeado" in info.value.user_message()
    monkeypatch.undo()
    assert names(folder) == ["a.txt", "b.txt", "c.txt"] and engine.last_operation() == []


def test_chain_rolls_back_through_the_temporary_names(folder: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    files = make_files(folder, "1.txt", "2.txt", "3.txt")
    plan = engine.build_plan(files, RenameOptions(pattern="{n}", start=2))
    real = os.rename

    def flaky(src, dst):
        if Path(dst).name == "4.txt":
            raise PermissionError(13, "em uso", str(src))
        real(src, dst)

    monkeypatch.setattr(engine.os, "rename", flaky)
    with pytest.raises(ConversionError):
        engine.apply_plan(plan)
    monkeypatch.undo()
    assert names(folder) == ["1.txt", "2.txt", "3.txt"]
    assert [(folder / f"{n}.txt").read_text(encoding="utf-8") for n in (1, 2, 3)] == ["1.txt", "2.txt", "3.txt"]


def test_stale_plan_is_refused(folder: Path) -> None:
    files = make_files(folder, "a.txt", "b.txt")
    plan = engine.build_plan(files, RenameOptions(pattern="x{n}"))
    make_files(folder, "x2.txt")  # apareceu depois da previa
    with pytest.raises(ConversionError):
        engine.apply_plan(plan)
    assert names(folder) == ["a.txt", "b.txt", "x2.txt"]
    plan = engine.build_plan(files, RenameOptions(pattern="y{n}"))
    files[0].unlink()
    with pytest.raises(ConversionError):
        engine.apply_plan(plan)
    assert names(folder) == ["b.txt", "x2.txt"]


def test_undo_skips_files_that_are_gone(folder: Path) -> None:
    files = make_files(folder, "a.txt", "b.txt")
    engine.apply_plan(engine.build_plan(files, RenameOptions(pattern="x{n}")))
    (folder / "x1.txt").unlink()
    assert len(engine.undo_last()) == 1 and names(folder) == ["b.txt"]


def test_collect_files(folder: Path, tmp_path: Path) -> None:
    make_files(folder, "b10.txt", "b2.txt")
    make_files(folder / "sub", "dentro.txt")
    assert [p.name for p in engine.collect_files([folder])] == ["b2.txt", "b10.txt"]
    assert [p.name for p in engine.collect_files([folder], recursive=True)] == ["b2.txt", "b10.txt", "dentro.txt"]
    assert len(engine.collect_files([folder, folder / "b2.txt"])) == 2  # sem repetir
    with pytest.raises(ConversionError):
        engine.collect_files([tmp_path / "nao_existe"])


@pytest.mark.skipif(sys.platform != "win32", reason="nomes sem diferenca de maiusculas so no Windows")
def test_conflict_check_ignores_case_on_windows(folder: Path) -> None:
    a, _other = make_files(folder, "a.txt", "NOVO.txt")
    assert _targets(engine.build_plan([a], RenameOptions(pattern="novo"))) == ["novo (1).txt"]
