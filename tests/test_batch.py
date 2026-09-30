from __future__ import annotations

from pathlib import Path

import pytest

from umd.services import batch
from umd.services.batch import load_url_file, parse_csv_text, parse_url_text


def test_parse_lines_with_comments_duplicates_and_invalid() -> None:
    text = """
    # minha lista
    https://site.com/video1
    https://site.com/video2

    https://site.com/video1
    youtube.com/watch?v=abc
    isto não é url
    ftp://site.com/arquivo
    """
    result = parse_url_text(text)
    assert result.urls == ["https://site.com/video1", "https://site.com/video2", "https://youtube.com/watch?v=abc"]
    assert result.duplicates == 1
    assert len(result.invalid) == 2


def test_parse_text_with_urls_inside_sentences() -> None:
    result = parse_url_text("veja https://a.com/1 e https://b.com/2 hoje")
    assert result.urls == ["https://a.com/1", "https://b.com/2"]


def test_parse_csv_any_column() -> None:
    text = "titulo;link;obs\nVideo 1;https://a.com/1;ok\nVideo 2;https://b.com/2;\n"
    assert parse_csv_text(text).urls == ["https://a.com/1", "https://b.com/2"]


def test_load_txt_with_bom_and_csv(tmp_path: Path) -> None:
    txt = tmp_path / "urls.txt"
    txt.write_text("﻿https://a.com/1\nhttps://b.com/2\n", encoding="utf-8")
    assert load_url_file(txt).urls == ["https://a.com/1", "https://b.com/2"]
    csv_file = tmp_path / "urls.csv"
    csv_file.write_text("url,nome\nhttps://c.com/3,x\n", encoding="utf-8")
    assert load_url_file(csv_file).urls == ["https://c.com/3"]


def test_file_size_limit(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(batch, "MAX_LIST_BYTES", 10)
    big = tmp_path / "grande.txt"
    big.write_text("https://a.com/1\n" * 5)
    with pytest.raises(ValueError):
        load_url_file(big)


def test_local_urls_filtered_unless_allowed() -> None:
    assert parse_url_text("http://127.0.0.1/a.mp4").urls == []
    assert parse_url_text("http://127.0.0.1/a.mp4", allow_local=True).urls == ["http://127.0.0.1/a.mp4"]
