"""Traducao simples da interface.

O texto-fonte e o proprio portugues (pt_BR); outros idiomas sao
dicionarios "texto original -> traducao". Texto sem traducao cai no
original, entao nunca aparece uma chave crua para o usuario.

A troca de idioma vale a partir da proxima abertura do aplicativo
(os widgets sao montados uma vez so).
"""

from __future__ import annotations

from datetime import date, datetime

LANGUAGES = {
    "pt_BR": "Português (Brasil)",
    "en": "English",
}

_current = "pt_BR"
_catalogs: dict[str, dict[str, str]] = {}


def _catalog(lang: str) -> dict[str, str]:
    if lang not in _catalogs:
        if lang == "en":
            from .i18n_en import CATALOG

            _catalogs[lang] = CATALOG
        else:
            _catalogs[lang] = {}
    return _catalogs[lang]


def set_language(lang: str) -> None:
    global _current
    _current = lang if lang in LANGUAGES else "pt_BR"


def current_language() -> str:
    return _current


def format_number(value: int) -> str:
    """1234567 -> '1.234.567' (pt_BR) / '1,234,567' (en)."""
    text = f"{value:,}"
    return text.replace(",", ".") if _current == "pt_BR" else text


def format_day(value: "date | datetime", with_time: bool = False) -> str:
    """Data no formato do idioma: 31/01/2024 (pt_BR) / 2024-01-31 (en)."""
    fmt = "%d/%m/%Y" if _current == "pt_BR" else "%Y-%m-%d"
    if with_time:
        fmt += " %H:%M"
    return value.strftime(fmt)


def tr(text: str, **kwargs: object) -> str:
    translated = _catalog(_current).get(text, text) if _current != "pt_BR" else text
    if kwargs:
        try:
            return translated.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return text.format(**kwargs)
    return translated
