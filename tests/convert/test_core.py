from __future__ import annotations

from pathlib import Path

import pytest

from umd.convert.core import registry
from umd.convert.core.errors import ConversionError
from umd.convert.core.paths import (
    ConflictPolicy,
    page_range_to_indices,
    resolve_conflict,
    safe_member_path,
    sanitize_filename,
    unique_path,
)
from umd.convert.core.queue import ConversionQueue, JobStatus, run_queue
from umd.convert.core.config import Settings


# -------------------------------------------------------------- registry
@pytest.mark.parametrize(
    "source,target,expected",
    [
        ("docx", "pdf", "office"),
        ("pdf", "docx", "office"),
        ("png", "jpg", "image"),
        ("png", "pdf", "image"),
        ("pdf", "png", "pdf_to_image"),
        ("pdf", "webp", "pdf_to_image"),
        ("txt", "pdf", "text_to_pdf"),
        ("md", "pdf", "markdown_to_pdf"),
        ("mp4", "mp3", "media"),
        ("mp3", "mp4", None),  # audio nao vira video
        ("zip", "tar.gz", "archive"),
        ("jpg", "mp3", None),
        ("xlsx", "pdf", "office"),
    ],
)
def test_resolve_handler(source, target, expected):
    assert registry.resolve_handler(source, target) == expected


def test_compatible_targets_excludes_same_format():
    targets = registry.compatible_targets("png")
    assert "png" not in targets
    assert "pdf" in targets
    assert "jpg" in targets


def test_adding_a_format_is_localized():
    """HEIC ja foi adicionado como exemplo do item 23: deve aparecer no
    registro sem precisar tocar em nenhum outro modulo."""
    assert "heic" in registry.IMAGE.extensions
    assert registry.category_of("heic") is registry.IMAGE


# ----------------------------------------------------------------- paths
def test_unique_path_avoids_overwrite(tmp_path: Path):
    p = tmp_path / "documento.pdf"
    p.write_text("x")
    p1 = unique_path(p)
    assert p1 == tmp_path / "documento (1).pdf"
    p1.write_text("y")
    p2 = unique_path(p)
    assert p2 == tmp_path / "documento (2).pdf"


def test_resolve_conflict_policies(tmp_path: Path):
    p = tmp_path / "a.txt"
    p.write_text("x")
    assert resolve_conflict(p, ConflictPolicy.OVERWRITE) == p
    assert resolve_conflict(p, ConflictPolicy.SKIP) is None
    assert resolve_conflict(p, ConflictPolicy.RENAME) == tmp_path / "a (1).txt"
    # arquivo que nao existe: qualquer politica devolve o proprio caminho
    q = tmp_path / "novo.txt"
    assert resolve_conflict(q, ConflictPolicy.SKIP) == q


def test_sanitize_filename_strips_invalid_chars():
    assert sanitize_filename('relatorio:final?.pdf') == "relatorio_final_.pdf"
    assert sanitize_filename("   ") == "arquivo"
    assert sanitize_filename("CON.txt").upper().startswith("_CON")


def test_page_range_parsing():
    assert page_range_to_indices("1-3,5", 10) == [0, 1, 2, 4]
    assert page_range_to_indices("", 3) == [0, 1, 2]
    assert page_range_to_indices("99", 3) == []  # fora do intervalo, ignorado
    with pytest.raises(ValueError):
        page_range_to_indices("abc", 3)


def test_safe_member_path_blocks_traversal(tmp_path: Path):
    dest = tmp_path / "extraida"
    dest.mkdir()
    with pytest.raises(ValueError):
        safe_member_path(dest, "../../evil.txt")
    ok = safe_member_path(dest, "sub/arquivo.txt")
    assert ok == (dest / "sub" / "arquivo.txt").resolve()

    # caminho com letra de unidade (ex.: "C:/windows/evil.txt") e
    # neutralizado (o prefixo de unidade/barra e removido antes de juntar
    # com a pasta destino) em vez de escapar -- ainda assim tem que
    # continuar dentro da pasta destino.
    from umd.convert.core.paths import is_within_directory

    neutralized = safe_member_path(dest, "C:/windows/evil.txt")
    assert is_within_directory(dest, neutralized)


# ----------------------------------------------------------------- queue
def test_queue_deduplicates(tmp_path: Path):
    f = tmp_path / "a.png"
    f.write_bytes(b"fake")
    q = ConversionQueue()
    job1 = q.add(f, "jpg")
    job2 = q.add(f, "jpg")
    assert job1 is not None
    assert job2 is None
    assert len(q) == 1


def test_queue_remove_and_clear(tmp_path: Path):
    f = tmp_path / "a.png"
    f.write_bytes(b"fake")
    q = ConversionQueue()
    job = q.add(f, "jpg")
    assert q.remove(job.id) is True
    assert len(q) == 0
    q.add(f, "jpg")
    q.clear()
    assert len(q) == 0


def test_run_queue_continues_after_error(tmp_path: Path, sample_png: Path):
    missing = tmp_path / "nao_existe.png"
    q = ConversionQueue()
    q.add(missing, "jpg")
    q.add(sample_png, "jpg")

    settings = Settings(default_output_dir=str(tmp_path))
    statuses = []
    run_queue(q, settings, on_progress=lambda job: statuses.append((job.source.name, job.status)))

    by_name = dict(statuses)
    assert by_name[missing.name] == JobStatus.ERROR
    assert by_name[sample_png.name] == JobStatus.DONE


def test_run_queue_respects_cancel(tmp_path: Path, sample_png: Path):
    import threading

    other = tmp_path / "outra.png"
    from PIL import Image
    Image.new("RGB", (10, 10)).save(other)

    q = ConversionQueue()
    q.add(sample_png, "jpg")
    q.add(other, "jpg")

    cancel_event = threading.Event()
    cancel_event.set()  # cancela antes mesmo de comecar
    settings = Settings(default_output_dir=str(tmp_path))
    run_queue(q, settings, cancel_event=cancel_event)

    assert all(job.status == JobStatus.CANCELLED for job in q.jobs)


def test_cancel_while_paused_converts_nothing(tmp_path: Path, sample_png: Path):
    """Cancelar com a fila pausada nao pode deixar converter "mais um" arquivo."""
    import threading

    q = ConversionQueue()
    q.add(sample_png, "jpg")
    pause_event = threading.Event()
    pause_event.set()
    cancel_event = threading.Event()
    threading.Timer(0.3, cancel_event.set).start()  # cancela durante a pausa
    run_queue(q, Settings(default_output_dir=str(tmp_path)), cancel_event=cancel_event, pause_event=pause_event)

    assert [job.status for job in q.jobs] == [JobStatus.CANCELLED]
    assert not (tmp_path / "foto de teste.jpg").exists()
