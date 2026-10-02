"""Plano de renomeacao: calcula os nomes novos sem tocar no disco (a previa),
aplica o plano e desfaz a ultima aplicacao.

Regras que valem sempre:
* a extensao e mantida (so a caixa dela pode mudar);
* o arquivo nunca muda de pasta;
* nenhum arquivo e sobrescrito: nome repetido ganha " (1)", " (2)"...;
* se um arquivo falhar no meio, os que ja foram renomeados voltam ao nome antigo.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..convert.core.errors import ConversionError
from ..convert.core.paths import sanitize_filename
from ..core.paths import app_data_dir

TOKENS = ("name", "n", "date", "parent", "ext")
DEFAULT_DATE_FORMAT = "%Y-%m-%d"
EXIF_DATE_PATTERN = "{date:%Y-%m-%d %H.%M.%S}"
CASES = ("keep", "lower", "upper", "title")
EXT_CASES = ("keep", "lower", "upper")
SORTS = ("list", "name", "date")

# situacao de cada arquivo no plano
RENAME, SAME, SKIP = "rename", "same", "skip"
# observacao: nome ajustado por conflito / sem data nenhuma / data veio da modificacao do arquivo
NOTE_CONFLICT, NOTE_NO_DATE, NOTE_MTIME = "conflict", "no_date", "mtime"

_TOKEN_RE = re.compile(r"\{(\w+)(?::([^{}]*))?\}")
_EXIF_IFD, _EXIF_ORIGINAL, _EXIF_DIGITIZED, _EXIF_DATETIME = 0x8769, 0x9003, 0x9004, 0x0132
_EXIF_EXTS = {"jpg", "jpeg", "jpe", "tif", "tiff", "heic", "heif", "png", "webp", "avif"}


@dataclass
class RenameOptions:
    pattern: str = "{name}"
    find: str = ""
    replace: str = ""
    regex: bool = False
    ignore_case: bool = False
    case: str = "keep"
    ext_case: str = "keep"
    start: int = 1
    step: int = 1
    sort: str = "list"
    date_fallback: bool = True  # foto sem EXIF (ou arquivo que nao e foto): usa a data de modificacao


@dataclass
class RenameItem:
    source: Path
    target: Path
    status: str
    note: str = ""


# ---------------------------------------------------------------- arquivos e datas

def natural_key(name: str) -> list:
    """Ordem "humana": foto2 vem antes de foto10."""
    return [int(part) if part.isdigit() else part.casefold() for part in re.split(r"(\d+)", name)]


def collect_files(paths: Iterable[Path], recursive: bool = False) -> list[Path]:
    """Arquivos informados + os de dentro das pastas informadas, sem repetir."""
    found: dict[str, Path] = {}
    for path in paths:
        if path.is_dir():
            children = path.rglob("*") if recursive else path.iterdir()
            files = sorted((p for p in children if p.is_file()), key=lambda p: (str(p.parent), natural_key(p.name)))
        elif path.is_file():
            files = [path]
        else:
            raise ConversionError(f"Caminho nao encontrado: {path}")
        for file in files:
            found.setdefault(os.path.normcase(os.path.abspath(file)), file)
    return list(found.values())


def exif_date(path: Path) -> datetime | None:
    """Data em que a foto foi tirada (EXIF), ou None se o arquivo nao tem."""
    if path.suffix.lower().lstrip(".") not in _EXIF_EXTS:
        return None
    try:
        from PIL import Image

        with Image.open(path) as img:
            exif = img.getexif()
            detail = exif.get_ifd(_EXIF_IFD)
            values = [detail.get(_EXIF_ORIGINAL), detail.get(_EXIF_DIGITIZED), exif.get(_EXIF_DATETIME)]
    except Exception:  # noqa: BLE001 - arquivo ilegivel ou sem EXIF: simplesmente nao tem data
        return None
    for value in values:
        try:
            return datetime.strptime(str(value).strip().rstrip("\x00"), "%Y:%m:%d %H:%M:%S")
        except ValueError:
            continue
    return None


def _file_date(path: Path, options: RenameOptions, date_of: Callable[[Path], datetime | None]) -> tuple[datetime | None, str]:
    found = date_of(path)
    if found is not None:
        return found, ""
    if not options.date_fallback:
        return None, NOTE_NO_DATE
    try:
        return datetime.fromtimestamp(path.stat().st_mtime), NOTE_MTIME
    except OSError:
        return None, NOTE_NO_DATE


# ---------------------------------------------------------------- modelo do nome

def check_pattern(pattern: str) -> set[str]:
    """Valida o modelo e devolve os campos usados (ConversionError se estiver errado)."""
    if not pattern.strip():
        raise ConversionError("O modelo do nome esta vazio.", hint="Use, por exemplo: {name} {n:3}")
    used = set()
    for name, spec in _TOKEN_RE.findall(pattern):
        if name not in TOKENS:
            raise ConversionError(f"Campo desconhecido no modelo: {{{name}}}.",
                                  hint="Campos validos: " + ", ".join(f"{{{t}}}" for t in TOKENS))
        if name == "n" and spec and not spec.isdigit():
            raise ConversionError(f"Numero de digitos invalido em {{n:{spec}}}.", hint="Use, por exemplo: {n:3}")
        used.add(name)
    if "{" in _TOKEN_RE.sub("", pattern) or "}" in _TOKEN_RE.sub("", pattern):
        raise ConversionError("Chave { ou } sobrando no modelo.", hint="Os campos sao escritos assim: {name}, {n:3}")
    return used


def _compile_find(options: RenameOptions) -> re.Pattern[str] | None:
    if not options.find:
        return None
    flags = re.IGNORECASE if options.ignore_case else 0
    try:
        return re.compile(options.find if options.regex else re.escape(options.find), flags)
    except re.error as exc:
        raise ConversionError(f"Expressao regular invalida: {exc}") from exc


def _apply_case(text: str, case: str) -> str:
    if case == "lower":
        return text.lower()
    if case == "upper":
        return text.upper()
    if case == "title":
        return text.title()
    return text


# ---------------------------------------------------------------- plano (previa)

def build_plan(files: list[Path], options: RenameOptions,
               date_of: Callable[[Path], datetime | None] = exif_date) -> list[RenameItem]:
    """Calcula o nome novo de cada arquivo. Nao altera nada no disco."""
    used = check_pattern(options.pattern)
    finder = _compile_find(options)
    replacement = options.replace if options.regex else options.replace.replace("\\", "\\\\")

    dates: dict[Path, tuple[datetime | None, str]] = {}
    if "date" in used or options.sort == "date":
        dates = {path: _file_date(path, options, date_of) for path in files}

    if options.sort == "name":
        files = sorted(files, key=lambda p: natural_key(p.name))
    elif options.sort == "date":
        files = sorted(files, key=lambda p: (dates[p][0] or datetime.max, natural_key(p.name)))

    # {n} sem tamanho: tantos digitos quantos o maior numero do lote precisar
    last = options.start + options.step * max(0, len(files) - 1)
    auto_width = max(len(str(abs(options.start))), len(str(abs(last))))

    items: list[RenameItem] = []
    for index, source in enumerate(files):
        stem, ext = source.stem, source.suffix
        if finder is not None:
            try:
                stem = finder.sub(replacement, stem)
            except (re.error, IndexError) as exc:
                raise ConversionError(f"Substituicao invalida: {exc}",
                                      hint=r"Grupos da expressao sao escritos como \1, \2...") from exc
        when, note = dates.get(source, (None, ""))
        if "date" in used and when is None:
            items.append(RenameItem(source, source, SKIP, NOTE_NO_DATE))
            continue
        number = options.start + options.step * index

        def render(match: re.Match[str]) -> str:
            name, spec = match.group(1), match.group(2)
            if name == "name":
                return stem
            if name == "n":
                return str(number).zfill(int(spec) if spec else auto_width)
            if name == "parent":
                return source.parent.name
            if name == "ext":
                return ext.lstrip(".")
            assert when is not None
            return when.strftime(spec or DEFAULT_DATE_FORMAT)

        try:
            new_stem = _TOKEN_RE.sub(render, options.pattern)
        except ValueError as exc:  # formato de data que o sistema nao aceita
            raise ConversionError(f"Formato de data invalido no modelo: {exc}",
                                  hint="Exemplo: {date:%Y-%m-%d %H.%M.%S}") from exc
        new_stem = _apply_case(new_stem, options.case)
        new_ext = _apply_case(ext, options.ext_case) if options.ext_case in ("lower", "upper") else ext
        # sem extensao no meio: sanitize_filename trataria o ultimo ponto do nome como extensao
        new_name = sanitize_filename(new_stem + ".x")[:-2] + new_ext
        target = source.with_name(new_name)
        items.append(RenameItem(source, target, SAME if new_name == source.name else RENAME,
                                note if "date" in used else ""))

    _resolve_conflicts(items)
    return items


def _key(path: Path) -> str:
    return os.path.normcase(str(path))


def _resolve_conflicts(items: list[RenameItem]) -> None:
    """Dois arquivos nao podem acabar com o mesmo nome, nem em cima de um que ja existe na pasta."""
    leaving = {_key(item.source) for item in items if item.status == RENAME}
    taken: dict[Path, set[str]] = {}
    for item in items:
        folder = item.source.parent
        if folder not in taken:
            try:
                existing = {os.path.normcase(name) for name in os.listdir(folder)}
            except OSError:
                existing = set()
            # o nome de quem vai ser renomeado fica livre para outro arquivo do lote
            taken[folder] = {name for name in existing if _key(folder / name) not in leaving}
    for item in items:
        if item.status != RENAME:
            continue
        names = taken[item.source.parent]
        target, counter = item.target, 0
        while os.path.normcase(target.name) in names:
            counter += 1
            target = item.target.with_name(f"{item.target.stem} ({counter}){item.target.suffix}")
        if counter:
            item.note = NOTE_CONFLICT
            item.target = target
            if target.name == item.source.name:  # o nome livre era o que ele ja tinha
                item.status = SAME
        names.add(os.path.normcase(item.target.name))


# ---------------------------------------------------------------- aplicar e desfazer

def _undo_path() -> Path:
    return app_data_dir() / "rename_undo.json"


def _rename_pairs(pairs: list[tuple[Path, Path]]) -> None:
    """Renomeia origem -> destino, tudo ou nada. Trocas e cadeias (a->b, b->c) passam por um nome temporario."""
    sources = {_key(src) for src, _ in pairs}
    for src, dst in pairs:
        if not src.exists():
            raise ConversionError(f"O arquivo nao existe mais: {src.name}",
                                  hint="A pasta mudou depois da previa. Atualize a lista e tente de novo.")
        if _key(dst) not in sources and dst.exists():
            raise ConversionError(f"Ja existe um arquivo chamado {dst.name}.",
                                  hint="A pasta mudou depois da previa. Atualize a lista e tente de novo.")

    via_temp = any(_key(dst) in sources for _, dst in pairs)
    tag = uuid.uuid4().hex[:8]
    temps = [src.with_name(f".umd-rename-{tag}-{i}.tmp") for i, (src, _) in enumerate(pairs)]
    moved: list[tuple[Path, Path]] = []  # (onde esta agora, nome original), para voltar atras
    try:
        if via_temp:
            for (src, _), temp in zip(pairs, temps):
                os.rename(src, temp)
                moved.append((temp, src))
            for i, ((src, dst), temp) in enumerate(zip(pairs, temps)):
                os.rename(temp, dst)
                moved[i] = (dst, src)
        else:
            for src, dst in pairs:
                os.rename(src, dst)
                moved.append((dst, src))
    except OSError as exc:
        _roll_back(moved, temps if via_temp else None)
        raise ConversionError(
            f"Nao foi possivel renomear '{exc.filename or ''}': o arquivo esta aberto em outro programa "
            "ou sem permissao de acesso.",
            hint="Nenhum arquivo foi renomeado. Feche o arquivo e tente de novo.", detail=exc) from exc


def _roll_back(moved: list[tuple[Path, Path]], temps: list[Path] | None) -> None:
    if temps is not None:  # primeiro todo mundo para o temporario, para os nomes originais ficarem livres
        for i, (current, original) in enumerate(moved):
            if current != temps[i]:
                try:
                    os.rename(current, temps[i])
                    moved[i] = (temps[i], original)
                except OSError:
                    pass
    for current, original in reversed(moved):
        try:
            os.rename(current, original)
        except OSError:
            pass


def apply_plan(items: list[RenameItem]) -> list[tuple[Path, Path]]:
    """Renomeia os arquivos do plano e guarda a operacao para o desfazer. Devolve os pares (antes, depois)."""
    pairs = [(item.source, item.target) for item in items if item.status == RENAME]
    if not pairs:
        return []
    _rename_pairs(pairs)
    try:
        _undo_path().write_text(json.dumps([[str(src), str(dst)] for src, dst in pairs], ensure_ascii=False),
                                encoding="utf-8")
    except OSError:
        pass  # sem registro nao da para desfazer depois, mas a renomeacao esta feita
    return pairs


def last_operation() -> list[tuple[Path, Path]]:
    """Pares (antes, depois) da ultima renomeacao que ainda pode ser desfeita."""
    try:
        data = json.loads(_undo_path().read_text(encoding="utf-8"))
        return [(Path(src), Path(dst)) for src, dst in data]
    except (OSError, ValueError, TypeError):
        return []


def undo_last() -> list[tuple[Path, Path]]:
    """Devolve os nomes antigos da ultima renomeacao. Devolve os pares (nome atual, nome restaurado)."""
    pairs = last_operation()
    if not pairs:
        raise ConversionError("Nao ha renomeacao para desfazer.")
    # arquivo apagado ou movido depois da renomeacao fica de fora; os demais voltam
    back = [(dst, src) for src, dst in pairs if dst.exists()]
    if not back:
        _undo_path().unlink(missing_ok=True)
        raise ConversionError("Os arquivos renomeados nao estao mais na pasta.")
    _rename_pairs(back)
    _undo_path().unlink(missing_ok=True)
    return back
