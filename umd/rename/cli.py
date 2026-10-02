r"""
umd rename - renomeador em lote (linha de comando).

Sem --apply so mostra a previa; nada e renomeado:
    umd rename "C:\Fotos\Viagem" --exif
    umd rename "C:\Fotos\Viagem" --exif --apply

Modelo do nome (-p), com os campos:
    {name}     nome atual, sem a extensao (ja com o localizar/substituir aplicado)
    {n}        contador; {n:3} = 001, 002... (comeca em --start, anda de --step)
    {date}     data da foto (EXIF); {date:%Y-%m-%d %H.%M.%S} escolhe o formato
    {parent}   nome da pasta
    {ext}      extensao, sem o ponto

A extensao e sempre mantida e nenhum arquivo e sobrescrito (nome repetido
ganha " (1)"). A ultima renomeacao pode ser desfeita com: umd rename --undo

Exemplos:
    umd rename *.jpg -p "Ferias {n:3}"                       Ferias 001.jpg, Ferias 002.jpg...
    umd rename pasta --find "IMG_" --replace "foto-"         troca um trecho do nome
    umd rename pasta --find "(\d+)-(\d+)" --replace "\2-\1" --regex
    umd rename pasta --exif                                  2024-03-15 14.30.22.jpg
    umd rename pasta -p "{date:%Y-%m} {name}" --exif-only    pula quem nao tem data EXIF
    umd rename pasta --case lower --ext-case lower -r        tudo em minusculas, com subpastas
"""

from __future__ import annotations

import argparse
import glob
from pathlib import Path

from ..convert.core.errors import ConversionError
from ..convert.core.paths import clean_path_arg
from . import engine

_SKIP_REASON = {engine.NOTE_NO_DATE: "sem data EXIF"}
_NOTES = {engine.NOTE_CONFLICT: "nome ja existia", engine.NOTE_MTIME: "sem EXIF: data de modificacao"}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="umd rename", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("paths", nargs="*", metavar="ARQUIVO|PASTA", help="arquivos e/ou pastas")
    parser.add_argument("-p", "--pattern", help="modelo do nome novo (padrao: {name})")
    parser.add_argument("--exif", nargs="?", const="%Y-%m-%d %H.%M.%S", metavar="FORMATO",
                        help="nome = data da foto (atalho para -p \"{date:FORMATO}\")")
    parser.add_argument("--exif-only", action="store_true",
                        help="sem data EXIF, pula o arquivo (padrao: usa a data de modificacao)")
    parser.add_argument("--find", metavar="TEXTO", help="trecho a localizar no nome")
    parser.add_argument("--replace", metavar="TEXTO", default="", help="texto que entra no lugar (padrao: nada)")
    parser.add_argument("--regex", action="store_true", help="--find e uma expressao regular (\\1, \\2 em --replace)")
    parser.add_argument("-i", "--ignore-case", action="store_true", help="--find ignora maiusculas/minusculas")
    parser.add_argument("--case", choices=engine.CASES[1:], help="caixa do nome")
    parser.add_argument("--ext-case", choices=engine.EXT_CASES[1:], help="caixa da extensao")
    parser.add_argument("--start", type=int, default=1, metavar="N", help="primeiro numero do contador (padrao 1)")
    parser.add_argument("--step", type=int, default=1, metavar="N", help="passo do contador (padrao 1)")
    parser.add_argument("--sort", choices=("name", "date", "none"), default="name",
                        help="ordem dos arquivos para o contador (padrao: name)")
    parser.add_argument("-r", "--recursive", action="store_true", help="inclui as subpastas")
    parser.add_argument("-y", "--apply", action="store_true", help="renomeia de verdade (sem isso, so mostra a previa)")
    parser.add_argument("--undo", action="store_true", help="desfaz a ultima renomeacao")
    return parser


def options_from_args(args: argparse.Namespace) -> engine.RenameOptions:
    if args.pattern and args.exif:
        raise ConversionError("Use --pattern ou --exif, nao os dois.",
                              hint="Para juntar data e nome: -p \"{date:%Y-%m-%d} {name}\"")
    pattern = args.pattern or (f"{{date:{args.exif}}}" if args.exif else "{name}")
    return engine.RenameOptions(
        pattern=pattern, find=args.find or "", replace=args.replace, regex=args.regex,
        ignore_case=args.ignore_case, case=args.case or "keep", ext_case=args.ext_case or "keep",
        start=args.start, step=args.step, sort="list" if args.sort == "none" else args.sort,
        date_fallback=not args.exif_only)


def _undo() -> int:
    restored = engine.undo_last()
    for current, original in restored:
        print(f"{current.name} -> {original.name}")
    print(f"[OK] {len(restored)} arquivo(s) voltaram ao nome anterior.")
    return 0


def run(args: argparse.Namespace) -> int:
    if args.undo:
        return _undo()
    paths: list[Path] = []
    for raw in map(clean_path_arg, args.paths):
        # o terminal do Windows nao expande *.jpg: quem expande e o programa
        matches = sorted(glob.glob(raw)) if glob.has_magic(raw) and not Path(raw).exists() else []
        paths += [Path(m) for m in matches] or [Path(raw)]
    files = engine.collect_files(paths, recursive=args.recursive)
    if not files:
        raise ConversionError("Nenhum arquivo para renomear.")
    plan = engine.build_plan(files, options_from_args(args))

    changes = [item for item in plan if item.status == engine.RENAME]
    for item in plan:
        if item.status == engine.RENAME:
            note = f"   ({_NOTES[item.note]})" if item.note in _NOTES else ""
            print(f"{item.source.name} -> {item.target.name}{note}")
        elif item.status == engine.SKIP:
            print(f"[PULADO] {item.source.name}: {_SKIP_REASON.get(item.note, 'sem nome novo')}")
    unchanged = sum(item.status == engine.SAME for item in plan)
    if unchanged:
        print(f"{unchanged} arquivo(s) ja estao com o nome certo.")

    if not changes:
        print("Nada para renomear.")
        return 0
    if not args.apply:
        print(f"\nPrevia: {len(changes)} de {len(plan)} arquivo(s) seriam renomeados. "
              "Nada foi alterado; repita o comando com --apply para renomear.")
        return 0
    done = engine.apply_plan(plan)
    print(f"\n[OK] {len(done)} arquivo(s) renomeados. Para desfazer: umd rename --undo")
    return 0


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not argv:
        parser.print_help()
        return 1
    if not args.paths and not args.undo:
        print("[ERRO] Informe os arquivos ou a pasta. Veja os exemplos em: umd rename --help")
        return 1
    try:
        return run(args)
    except ConversionError as exc:
        print(f"[ERRO] {exc.user_message()}")
        return 1
