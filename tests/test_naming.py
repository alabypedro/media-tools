from __future__ import annotations

from pathlib import Path, PurePath

import pytest

from umd.core.naming import (
    DEFAULT_TEMPLATE,
    TemplateError,
    build_fields,
    fit_path,
    format_date,
    render_template,
    safe_join,
    sanitize_component,
    sanitize_extension,
    unique_path,
    validate_template,
)


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("Meu vídeo", "Meu vídeo"),
        ('Título: parte 1/2 <final> "oficial"?', "Título - parte 1-2 final 'oficial'"),
        ("a\\b|c*d", "a-b-cd"),
        ("  espaços    demais  ", "espaços demais"),
        ("termina com ponto...", "termina com ponto"),
        ("...começa oculto", "começa oculto"),
        ("CON", "_CON"),
        ("nul.txt", "_nul.txt"),
        ("com1", "_com1"),
        ("linha\nquebrada\ttab", "linhaquebradatab"),
        ("", "sem_titulo"),
        ("???", "sem_titulo"),
        ("emoji 🎬 ok", "emoji 🎬 ok"),
    ],
)
def test_sanitize_component(raw: str, expected: str) -> None:
    assert sanitize_component(raw) == expected


def test_sanitize_component_limits_length() -> None:
    result = sanitize_component("x" * 500, max_len=100)
    assert len(result) == 100


def test_sanitize_extension() -> None:
    assert sanitize_extension(".MP4") == "mp4"
    assert sanitize_extension("../exe") == "exe"
    assert sanitize_extension("") == "bin"


@pytest.mark.parametrize("template", [DEFAULT_TEMPLATE, "{title}.{ext}", "{date} - {title}", "Músicas/{author}/{title}"])
def test_valid_templates(template: str) -> None:
    assert validate_template(template) == template


@pytest.mark.parametrize(
    "template",
    ["", "/abs/{title}.{ext}", "C:/x/{title}", "../{title}.{ext}", "{platform}//{title}", "{nope}.{ext}",
     "{title", "pasta/{ext}", "a/./{title}"],
)
def test_invalid_templates(template: str) -> None:
    with pytest.raises(TemplateError):
        validate_template(template)


def _fields(**overrides: str) -> dict[str, str]:
    base = dict(title="Meu vídeo", author="Canal", platform="YouTube", date="2026-09-20", id="abc", ext="mp4")
    base.update(overrides)
    return build_fields(**base)


def test_render_default_template() -> None:
    assert render_template(DEFAULT_TEMPLATE, _fields()) == Path("YouTube", "Canal", "Meu vídeo.mp4")


def test_render_without_subfolders() -> None:
    assert render_template(DEFAULT_TEMPLATE, _fields(), create_subfolders=False) == Path("Meu vídeo.mp4")


def test_render_appends_extension_and_date_parts() -> None:
    assert render_template("{year}/{month}/{title}", _fields()) == Path("2026", "09", "Meu vídeo.mp4")


def test_field_values_never_create_folders_or_escape() -> None:
    evil = _fields(title="../../Windows/System32/evil", author="..\\..\\x", platform="a/b")
    relative = render_template(DEFAULT_TEMPLATE, evil)
    assert ".." not in relative.parts
    assert len(relative.parts) == 3  # so as barras do proprio template criam pastas
    root = Path("C:/downloads") if Path("C:/").exists() else Path("/tmp/downloads")
    assert safe_join(root, relative).is_relative_to(root.resolve())


def test_missing_fields_use_fallbacks() -> None:
    relative = render_template(DEFAULT_TEMPLATE, build_fields(title="", ext="jpg"))
    assert relative == Path("Outros", "desconhecido", "sem_titulo.jpg")


def test_safe_join_blocks_traversal(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        safe_join(tmp_path, PurePath("..") / "fora.txt")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        safe_join(tmp_path, Path(tmp_path.anchor) / "abs.txt")
    assert safe_join(tmp_path, Path("a/b.txt")) == (tmp_path / "a" / "b.txt").resolve()


def test_unique_path_never_overwrites(tmp_path: Path) -> None:
    target = tmp_path / "video.mp4"
    assert unique_path(target) == target
    target.write_text("x")
    assert unique_path(target) == tmp_path / "video (1).mp4"
    (tmp_path / "video (1).mp4").write_text("x")
    assert unique_path(target) == tmp_path / "video (2).mp4"


def test_fit_path_respects_windows_limit() -> None:
    result = fit_path(["YouTube", "C" * 120, "T" * 200], "mp4", root_len=60)
    assert 60 + len(str(result)) + 1 <= 250 + 5
    assert result.suffix == ".mp4"


def test_directories_are_created_for_rendered_path(tmp_path: Path) -> None:
    destination = safe_join(tmp_path, render_template(DEFAULT_TEMPLATE, _fields()))
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(b"ok")
    assert (tmp_path / "YouTube" / "Canal" / "Meu vídeo.mp4").read_bytes() == b"ok"


@pytest.mark.parametrize("upload, ts, expected", [
    ("20240131", None, "2024-01-31"),
    ("2024-01-31T10:00:00", None, "2024-01-31"),
    (None, 0, ""),
    (None, 1700000000, "2023-11-14"),
    ("lixo", None, ""),
])
def test_format_date(upload, ts, expected) -> None:
    assert format_date(upload, ts) == expected
