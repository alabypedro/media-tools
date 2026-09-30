"""Configuracao de logging (item 25).

Regra: o usuario comum nunca ve traceback. INFO/WARNING/ERROR sao
mensagens curtas e amigaveis; DEBUG (--debug na CLI) inclui detalhes
tecnicos (nome da excecao original, stderr de subprocessos, etc.).
"""

from __future__ import annotations

import logging

LOGGER_NAME = "universal_converter"


def setup_logging(*, verbose: bool = False, debug: bool = False, quiet: bool = False) -> logging.Logger:
    logger = logging.getLogger(LOGGER_NAME)
    logger.handlers.clear()

    if quiet:
        level = logging.ERROR
    elif debug:
        level = logging.DEBUG
    elif verbose:
        level = logging.INFO
    else:
        level = logging.WARNING

    handler = logging.StreamHandler()
    fmt = "%(levelname)s: %(message)s" if not debug else "%(asctime)s %(levelname)s [%(name)s] %(message)s"
    handler.setFormatter(logging.Formatter(fmt))
    logger.addHandler(handler)
    logger.setLevel(level)
    logger.propagate = False
    return logger


def get_logger() -> logging.Logger:
    return logging.getLogger(LOGGER_NAME)
