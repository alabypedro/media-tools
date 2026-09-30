"""Lista os textos da interface que passam por tr() (e os catalogos de rotulos).

Uso: python tools/extract_strings.py            -> mostra textos sem traducao em ingles
     python tools/extract_strings.py --all      -> mostra todos
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def tr_literals() -> set[str]:
    found: set[str] = set()
    for path in (ROOT / "umd").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "tr" and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                    found.add(arg.value)
    return found


def catalog_strings() -> set[str]:
    from umd.core import exceptions
    from umd.core.config import QUALITY_PRESET_LABELS
    from umd.core.naming import TEMPLATE_FIELDS
    from umd.downloader.job import STAGE_LABELS, STATUS_LABELS
    from umd.media import formats
    from umd.media.metadata import CONTENT_TYPE_LABELS, MediaInfo, ContentType
    from umd.providers import yt_dlp_provider
    from umd.ui import converter, history, settings

    found: set[str] = set()
    for mapping in (converter.STATUS_LABELS, converter.CONFLICT_LABELS, converter.CATEGORY_LABELS,
                    converter.FONT_LABELS, converter.PAGE_SIZE_LABELS, converter.ORIENTATION_LABELS,
                    converter.FIT_LABELS):
        found.update(mapping.values())
    found.update(converter.ConverterPage.COLUMNS)
    for message, reasons, hint in exceptions._CATALOG.values():
        found.add(message)
        found.update(reasons)
        if hint:
            found.add(hint)
    for mapping in (QUALITY_PRESET_LABELS, STAGE_LABELS, STATUS_LABELS, CONTENT_TYPE_LABELS, TEMPLATE_FIELDS,
                    history._KIND_LABELS, settings._BROWSER_LABELS, settings._CONTAINER_LABELS, settings._CODEC_LABELS,
                    settings._AUDIO_LABELS, settings._BITRATE_LABELS, settings._IMAGE_LABELS, settings._THEME_LABELS):
        found.update(v for v in mapping.values() if isinstance(v, str))
    found.update(h for h in history.HistoryModel.HEADERS)
    for _pattern, message in yt_dlp_provider._KNOWN_WARNINGS:
        if message:
            found.add(message)
    info = MediaInfo(source_url="https://x", engine="yt-dlp", entries=[], formats=[])
    for fn in (formats.container_choices, formats.audio_format_choices, formats.bitrate_choices, formats.quality_choices):
        for choice in fn(info):
            found.add(choice.label)
            if choice.detail:
                found.add(choice.detail)
    for choice in formats.image_format_choices():
        found.add(choice.label)
    for engine, ctype in (("yt-dlp", ContentType.VIDEO), ("direct", ContentType.VIDEO), ("direct", ContentType.AUDIO),
                          ("direct", ContentType.IMAGE), ("gallery-dl", ContentType.IMAGE), ("gallery-dl", ContentType.POST)):
        for choice in formats.mode_choices(MediaInfo(source_url="x", engine=engine, content_type=ctype)):
            found.add(choice.label)
    found.update(["Apenas áudio", "Áudio", "Itens da coleção", "Arquivo original", "Até 1080p (prioridade)",
                  "Até 720p (prioridade)", "Até 480p (prioridade)", "Vários", "Documento"])
    found.update(_literal_args(("_invalid", "TemplateError")))
    found.update(FILTER_LABELS)
    return found


# rotulos passados a tr() por variavel (tuplas dos filtros das telas)
FILTER_LABELS = [
    "Todos os status", "Concluídos", "Com falha", "Cancelados", "Interrompidos", "Pausados",
    "Todos os tipos", "Vídeos", "Áudios", "Imagens", "Documentos relacionados",
    "Qualquer data", "Hoje", "Últimos 7 dias", "Últimos 30 dias", "Últimos 12 meses",
    "Original (sem conversão)",
]


def _literal_args(function_names: tuple[str, ...]) -> set[str]:
    found: set[str] = set()
    for path in (ROOT / "umd").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", None) in function_names and node.args:
                arg = node.args[0]
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and "{" not in arg.value:
                    found.add(arg.value)
    return found


def main() -> None:
    from umd.core.i18n_en import CATALOG

    strings = sorted(tr_literals() | catalog_strings())
    missing = [s for s in strings if s not in CATALOG]
    show = strings if "--all" in sys.argv else missing
    for s in show:
        print(repr(s))
    print(f"# total={len(strings)} sem_traducao={len(missing)}", file=sys.stderr)


if __name__ == "__main__":
    main()
