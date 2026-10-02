r"""
umd edit - editor de GIF, animacoes e video (linha de comando).

Uso basico (o original nunca e alterado):
    umd edit entrada.gif [saida.ext] [opcoes]
    umd edit entrada.mp4 --to gif --start 2 --end 6 --fps 12 --resize 480x

Sem saida, o arquivo vai para o lado do original como "nome (editado).ext"
(--to escolhe o formato: gif, webp, apng, avif, png, jpg, mp4, webm, mkv, mov).

Outros modos:
    umd edit --make a.png b.png c.gif -o anim.gif --delay 200   imagens (e GIFs) -> animacao
    umd edit --split anim.gif [--format png] [--zip]            um arquivo por quadro
    umd edit --sprite anim.gif -o folha.png [--columns 4]       quadros -> sprite sheet
    umd edit --unsprite folha.png -o anim.gif --columns 4 --rows 2 [--delay 80]
    umd edit --merge a.mp4 b.mp4 -o junto.mp4                   juntar videos

Exemplos:
    umd edit gato.gif --resize 50%
    umd edit gato.gif --crop 10,10,200,150 --rotate 90
    umd edit gato.gif --speed 2 --reverse
    umd edit gato.gif --text "bom dia" --text-position top
    umd edit gato.gif --colors 64 --drop-every 2                GIF menor
    umd edit gato.gif --to mp4
    umd edit video.mp4 saida.mp4 --start 5 --end 12 --mute
"""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

from ..convert.core.errors import ConversionError
from ..convert.core.paths import clean_path_arg, unique_path
from . import tools
from .clip import CENSOR_MODES, FONTS, POSITIONS


def _box(value: str) -> dict[str, int]:
    try:
        x, y, width, height = (int(v) for v in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("use X,Y,LARGURA,ALTURA (ex.: 10,10,200,150)") from exc
    return {"x": x, "y": y, "width": width, "height": height}


def _resize(value: str) -> dict[str, Any]:
    try:
        if value.endswith("%"):
            return {"percent": float(value[:-1])}
        width, _, height = value.lower().partition("x")
        return {"width": int(width or 0), "height": int(height or 0)}
    except ValueError as exc:
        raise argparse.ArgumentTypeError("use LARGURAxALTURA, LARGURAx, xALTURA ou 50%") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="umd edit", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="*", metavar="ARQUIVO", help="entrada (e, opcionalmente, saida)")
    parser.add_argument("-o", "--output", help="arquivo de saida (ou pasta, com --split)")
    parser.add_argument("--to", metavar="EXT", help="formato de saida quando a saida nao e informada")
    parser.add_argument("--overwrite", action="store_true", help="sobrescreve a saida em vez de criar 'nome (1).ext'")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--make", action="store_true", help="imagens -> animacao")
    mode.add_argument("--split", action="store_true", help="um arquivo por quadro")
    mode.add_argument("--sprite", action="store_true", help="quadros -> sprite sheet")
    mode.add_argument("--unsprite", action="store_true", help="sprite sheet -> animacao")
    mode.add_argument("--merge", action="store_true", help="juntar videos")

    group = parser.add_argument_group("tempo")
    group.add_argument("--start", type=float, metavar="S", help="inicio do trecho, em segundos")
    group.add_argument("--end", type=float, metavar="S", help="fim do trecho, em segundos")
    group.add_argument("--speed", type=float, metavar="X", help="2 = dobro da velocidade; 0.5 = metade")
    group.add_argument("--reverse", action="store_true", help="de tras para frente")
    group.add_argument("--boomerang", action="store_true", help="vai e volta")
    group.add_argument("--delay", type=int, metavar="MS", help="tempo de cada quadro")
    group.add_argument("--loop", type=int, metavar="N", help="0 = infinito, 1 = toca uma vez")
    group.add_argument("--fps", type=float, help="quadros por segundo ao ler um video (padrao 10)")

    group = parser.add_argument_group("tamanho e posicao")
    group.add_argument("--resize", type=_resize, metavar="LxA|N%%")
    group.add_argument("--stretch", action="store_true", help="com --resize LxA, nao manter a proporcao")
    group.add_argument("--crop", type=_box, metavar="X,Y,L,A")
    group.add_argument("--rotate", type=float, metavar="GRAUS", help="sentido horario")
    group.add_argument("--flip", choices=("h", "v", "hv"), help="espelhar na horizontal/vertical")

    group = parser.add_argument_group("efeitos")
    for name in ("grayscale", "sepia", "invert", "sharpen"):
        group.add_argument(f"--{name}", action="store_true")
    for name in ("brightness", "contrast", "saturation"):
        group.add_argument(f"--{name}", type=float, metavar="F", help="1.0 = sem mudanca")
    group.add_argument("--blur", type=float, metavar="RAIO")
    group.add_argument("--background", metavar="COR", help="cor no lugar da transparencia (ex.: #ffffff)")

    group = parser.add_argument_group("texto, marca-d'agua e censura")
    group.add_argument("--text")
    group.add_argument("--text-size", type=int)
    group.add_argument("--text-color", default="#ffffff")
    group.add_argument("--text-outline", default="#000000", metavar="COR")
    group.add_argument("--text-position", choices=POSITIONS, default="bottom")
    group.add_argument("--font", default="impact", help=f"{', '.join(FONTS)} ou o caminho de um .ttf")
    group.add_argument("--overlay", metavar="IMAGEM")
    group.add_argument("--overlay-position", choices=POSITIONS, default="bottom-right")
    group.add_argument("--overlay-scale", type=float, metavar="%%", help="largura em %% da largura do quadro")
    group.add_argument("--overlay-opacity", type=float, default=100, metavar="%%")
    group.add_argument("--censor", type=_box, metavar="X,Y,L,A")
    group.add_argument("--censor-mode", choices=CENSOR_MODES, default="blur")

    group = parser.add_argument_group("gravacao")
    group.add_argument("--colors", type=int, metavar="N", help="GIF/PNG: no maximo N cores (2-256)")
    group.add_argument("--quality", type=int, metavar="N", help="1-100 (WebP, AVIF, JPG e video)")
    group.add_argument("--lossless", action="store_true", help="WebP sem perdas")
    group.add_argument("--drop-every", type=int, metavar="N", help="remove 1 a cada N quadros")
    group.add_argument("--dedupe", action="store_true", help="junta quadros repetidos")
    group.add_argument("--mute", action="store_true", help="video sem audio")
    group.add_argument("--format", default="png", choices=tools.FRAME_FORMATS, help="formato dos quadros (--split)")
    group.add_argument("--zip", action="store_true", help="--split: grava um ZIP em vez de uma pasta")
    group.add_argument("--columns", type=int)
    group.add_argument("--rows", type=int)
    return parser


def options_from_args(args: argparse.Namespace) -> dict[str, Any]:
    options: dict[str, Any] = {}
    for key in ("start", "end", "speed", "delay", "loop", "fps", "crop", "rotate", "brightness", "contrast",
                "saturation", "blur", "background", "colors", "quality", "drop_every", "columns", "rows"):
        value = getattr(args, key)
        if value is not None:
            options[key] = value
    for key in ("reverse", "boomerang", "grayscale", "sepia", "invert", "sharpen", "lossless", "dedupe", "mute",
                "zip"):
        if getattr(args, key):
            options[key] = True
    if args.resize:
        options["resize"] = {**args.resize, "keep_aspect": not args.stretch}
    if args.flip:
        options["flip_h"], options["flip_v"] = "h" in args.flip, "v" in args.flip
    if args.text:
        options["text"] = {"text": args.text.replace("\\n", "\n"), "size": args.text_size, "color": args.text_color,
                           "stroke_color": args.text_outline, "position": args.text_position, "font": args.font}
    if args.overlay:
        options["overlay"] = {"path": clean_path_arg(args.overlay), "position": args.overlay_position,
                              "scale": args.overlay_scale, "opacity": args.overlay_opacity}
    if args.censor:
        options["censor"] = {**args.censor, "mode": args.censor_mode}
    options["format"] = args.format
    return options


def _target(args: argparse.Namespace, explicit: str | None, source: Path, suffix: str, default_ext: str) -> Path:
    if explicit:
        path = Path(clean_path_arg(explicit))
        return path if args.overwrite else unique_path(path)
    return tools.output_path(source, suffix, args.to or default_ext)


def run(args: argparse.Namespace) -> list[Path]:
    files = [Path(clean_path_arg(f)) for f in args.files]
    options = options_from_args(args)
    if not files:
        raise ConversionError("Informe o arquivo de entrada.", hint="Veja os exemplos em: umd edit --help")
    if args.make:
        return [tools.make_animation(files, _target(args, args.output, files[0], "animacao", "gif"), options)]
    if args.merge:
        return [tools.merge_videos(files, _target(args, args.output, files[0], "junto", "mp4"), options)]
    source = files[0]
    if args.split:
        return tools.split_frames(source, Path(clean_path_arg(args.output)) if args.output else None, options)
    explicit = args.output or (str(files[1]) if len(files) > 1 else None)
    if args.sprite:
        return [tools.make_sprite_sheet(source, _target(args, explicit, source, "sprite", "png"), options)]
    if args.unsprite:
        return [tools.cut_sprite_sheet(source, _target(args, explicit, source, "animacao", "gif"), options)]
    return [tools.edit(source, _target(args, explicit, source, "editado", source.suffix), options)]


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not argv:
        parser.print_help()
        return 1
    try:
        outputs = run(args)
    except ConversionError as exc:
        print(f"[ERRO] {exc.user_message()}")
        return 1
    except ValueError as exc:  # ex.: cor invalida
        print(f"[ERRO] {exc}")
        return 1
    if len(outputs) == 1:
        print(f"[OK] {args.files[0]} -> {outputs[0]}")
    else:
        print(f"[OK] {len(outputs)} quadros em {outputs[0].parent}")
    return 0
