from __future__ import annotations

import logging
from pathlib import Path

import pytest

from umd.core.logger import get_logger, redact, setup_logging


@pytest.mark.parametrize(
    "text, secret",
    [
        ("Cookie: sessionid=abc123; csrftoken=zzz", "abc123"),
        ("authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig", "eyJhbGciOiJIUzI1NiJ9"),
        ("https://cdn.site/v.mp4?token=SEGREDO123&x=1", "SEGREDO123"),
        ("https://api.site/x?access_token=AAAA&sig=BBBB", "AAAA"),
        ("https://usuario:senha123@site.com/x", "senha123"),
        ("password=hunter2 enviado", "hunter2"),
        (".instagram.com\tTRUE\t/\tTRUE\t1790000000\tsessionid\tVALORSECRETO", "VALORSECRETO"),
    ],
)
def test_redact_removes_secrets(text: str, secret: str) -> None:
    cleaned = redact(text)
    assert secret not in cleaned
    assert "[REDACTED]" in cleaned


def test_redact_keeps_normal_text() -> None:
    text = "Download concluído: https://www.youtube.com/watch?v=abc (320 kbps)"
    assert redact(text) == text


def test_log_files_and_redaction_on_disk(tmp_path: Path) -> None:
    setup_logging(tmp_path)
    get_logger("app").info("iniciando com Cookie: sessionid=NAOPODE")
    get_logger("downloads").info("download https://x.com/v?token=TOKENX")
    get_logger("app").error("falhou password=hunter2")
    for handler in logging.getLogger("umd").handlers + logging.getLogger("umd.downloads").handlers:
        handler.flush()
    app_log = (tmp_path / "application.log").read_text(encoding="utf-8")
    downloads_log = (tmp_path / "downloads.log").read_text(encoding="utf-8")
    errors_log = (tmp_path / "errors.log").read_text(encoding="utf-8")
    assert "iniciando" in app_log and "download" in app_log  # downloads tambem vao para o application.log
    assert "download" in downloads_log and "iniciando" not in downloads_log
    assert "falhou" in errors_log and "iniciando" not in errors_log
    for content in (app_log, downloads_log, errors_log):
        assert "NAOPODE" not in content and "TOKENX" not in content and "hunter2" not in content
    for handler in logging.getLogger("umd").handlers + logging.getLogger("umd.downloads").handlers:
        handler.close()
