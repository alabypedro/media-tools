"""Logs em arquivo:

    logs/application.log   tudo que o app registra (INFO+)
    logs/downloads.log     so o ciclo de vida dos downloads
    logs/errors.log        so erros (de qualquer parte)

Todo registro passa por `redact()` antes de ir para o disco: cookies,
tokens, senhas, cabecalhos de autorizacao e parametros sensiveis de URL
nunca ficam em texto puro nos logs.
"""

from __future__ import annotations

import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT_LOGGER = "umd"
DOWNLOADS_LOGGER = "umd.downloads"

_SENSITIVE_PARAMS = (
    "access_token|refresh_token|id_token|token|auth|authorization|key|api_key|apikey|sig|signature|lsig|"
    "password|passwd|pass|pwd|secret|client_secret|session|sessionid|session_id|sid|oauth_token|"
    "oauth_verifier|csrf|csrftoken|xsrf|jwt|bearer|cookie"
)
_REDACTIONS: list[tuple[re.Pattern[str], str]] = [
    # cabecalhos HTTP inteiros
    (re.compile(r"(?im)\b(cookie|set-cookie|authorization|proxy-authorization|x-csrf-token|x-auth-token|x-ig-www-claim)"
                r"(\s*[:=]\s*)[^\r\n]+"), r"\1\2[REDACTED]"),
    # "Bearer abc..."
    (re.compile(r"(?i)\b(bearer|basic)\s+[a-z0-9._~+/=-]{8,}"), r"\1 [REDACTED]"),
    # usuario:senha@ em URLs
    (re.compile(r"(?i)(https?://)[^/\s:@]+:[^/\s@]+@"), r"\1[REDACTED]@"),
    # parametros sensiveis em query strings / formularios
    (re.compile(rf"(?i)([?&;](?:{_SENSITIVE_PARAMS})=)[^&\s\"'#]+"), r"\1[REDACTED]"),
    # "password=..." / "token: ..." soltos em texto
    (re.compile(r"(?i)\b(password|passwd|pwd|secret|api[_-]?key|access[_-]?token)(\s*[=:]\s*)\S+"), r"\1\2[REDACTED]"),
    # linhas de cookies.txt (formato Netscape: 7 campos separados por TAB)
    (re.compile(r"(?m)^([^\t\n]+\t(?:TRUE|FALSE)\t[^\t\n]*\t(?:TRUE|FALSE)\t\d+\t[^\t\n]+\t)[^\n]+$"), r"\1[REDACTED]"),
]


def redact(text: str) -> str:
    if not text:
        return text
    for pattern, replacement in _REDACTIONS:
        text = pattern.sub(replacement, text)
    return text


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


_FMT = "%(asctime)s %(levelname)-7s [%(name)s] %(message)s"
_configured_dir: Path | None = None


def setup_logging(log_dir: Path, *, debug: bool = False, console: bool = False) -> None:
    """Configura os handlers (idempotente: chamar de novo reconfigura)."""
    global _configured_dir
    log_dir.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger(ROOT_LOGGER)
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()
    downloads = logging.getLogger(DOWNLOADS_LOGGER)
    for handler in list(downloads.handlers):
        downloads.removeHandler(handler)
        handler.close()

    formatter = RedactingFormatter(_FMT)

    def file_handler(name: str, level: int) -> RotatingFileHandler:
        handler = RotatingFileHandler(log_dir / name, maxBytes=2_000_000, backupCount=3, encoding="utf-8", delay=True)
        handler.setLevel(level)
        handler.setFormatter(formatter)
        return handler

    root.setLevel(logging.DEBUG if debug else logging.INFO)
    root.propagate = False
    root.addHandler(file_handler("application.log", logging.DEBUG if debug else logging.INFO))
    root.addHandler(file_handler("errors.log", logging.ERROR))
    # downloads.log recebe so o logger de downloads (e continua propagando
    # para application.log / errors.log pelo logger raiz "umd")
    downloads.addHandler(file_handler("downloads.log", logging.INFO))

    if console:
        stream = logging.StreamHandler(sys.stderr)
        stream.setLevel(logging.DEBUG if debug else logging.WARNING)
        stream.setFormatter(RedactingFormatter("%(levelname)s: %(message)s"))
        root.addHandler(stream)
    _configured_dir = log_dir


def configured_log_dir() -> Path | None:
    return _configured_dir


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(f"{ROOT_LOGGER}.{name}" if name else ROOT_LOGGER)
