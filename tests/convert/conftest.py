"""Fixtures compartilhadas. Tudo roda em cima de `tmp_path` (pastas
temporarias criadas e apagadas pelo proprio pytest) -- nenhum teste toca
em arquivo pessoal do usuario (item 28)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))


@pytest.fixture(autouse=True)
def isolate_app_data_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Config e historico do conversor moram na pasta de dados do app.
    Nenhum teste pode escrever ali -- redireciona para uma pasta
    temporaria isolada em toda a suite (item 28: nao usar arquivos
    pessoais do usuario)."""
    from umd.convert.core import config, history

    app_dir = tmp_path / ".universal_converter_test"
    monkeypatch.setattr(config, "APP_DIR", app_dir)
    monkeypatch.setattr(config, "CONFIG_PATH", app_dir / "config.json")
    monkeypatch.setattr(history, "DB_PATH", app_dir / "history.db")


@pytest.fixture
def sample_png(tmp_path: Path) -> Path:
    from PIL import Image

    path = tmp_path / "foto de teste.png"
    Image.new("RGB", (120, 80), (10, 120, 200)).save(path)
    return path


@pytest.fixture
def sample_animated_gif(tmp_path: Path) -> Path:
    from PIL import Image

    frames = [Image.new("RGB", (40, 40), (i * 40 % 255, 10, 10)) for i in range(4)]
    path = tmp_path / "animado.gif"
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=80, loop=0)
    return path


@pytest.fixture
def sample_txt(tmp_path: Path) -> Path:
    path = tmp_path / "notas com espaço e acentuação.txt"
    path.write_text(
        "Relatorio de teste\n\n"
        "Este texto tem acentuacao: acao, coracao, nao, voce, ate, orgao, publico.\n\n"
        + ("Linha de conteudo repetida para forcar quebra de pagina. " * 300),
        encoding="utf-8",
    )
    return path


@pytest.fixture
def sample_markdown(tmp_path: Path) -> Path:
    path = tmp_path / "documento.md"
    path.write_text(
        "# Titulo principal\n\n"
        "## Subtitulo\n\n"
        "Paragrafo com **negrito**, *italico* e `codigo inline`, "
        "e um [link](https://example.com).\n\n"
        "- item um\n- item dois\n- item tres\n\n"
        "1. primeiro\n2. segundo\n\n"
        "> uma citacao\n\n"
        "```python\nprint('ola')\n```\n\n"
        "| A | B |\n|---|---|\n| 1 | 2 |\n\n"
        "---\n\nFim.\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def sample_pdf(tmp_path: Path, sample_markdown: Path) -> Path:
    from umd.convert.converters import markdown_to_pdf

    out = tmp_path / "documento.pdf"
    return markdown_to_pdf.convert(sample_markdown, out)


@pytest.fixture
def sample_zip(tmp_path: Path) -> Path:
    import zipfile

    path = tmp_path / "pacote.zip"
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("a.txt", "conteudo a")
        z.writestr("sub/b.txt", "conteudo b")
    return path
