r"""
umd convert - conversor de arquivos (linha de comando).

Uso basico:
    umd convert entrada.ext formato_destino [saida.ext]
    umd convert "C:\pasta" formato_destino     (converte os arquivos
                                                compativeis da pasta)
    umd convert                                (modo interativo)
    umd convert --formatos                     (lista formatos suportados)

Operacao especial (varias imagens -> um PDF so, item 8):
    umd convert --images-to-pdf foto1.jpg foto2.png ... saida.pdf

Achatar PDF (formularios e anotacoes viram conteudo fixo, nao editavel):
    umd convert formulario.pdf flatten        (gera "formulario (achatado).pdf")
    umd convert "C:\pasta" flatten             (todos os PDFs da pasta)
    ("achatar" tambem funciona no lugar de "flatten")

Opcoes:
    --output PASTA     pasta de destino (padrao: ao lado do arquivo de origem)
    --overwrite        sobrescreve arquivos existentes (padrao: cria "nome (1).ext")
    --recursive        ao converter uma pasta, entra nas subpastas tambem
    --quiet            so mostra erros
    --verbose          mostra mais detalhes (nivel INFO)
    --debug            mostra detalhes tecnicos completos dos erros
    --pages "1-5,10"   (PDF -> imagem) paginas a exportar; padrao = todas
    --dpi N            (PDF -> imagem) resolucao; padrao 200
    --quality N        qualidade 1-95 (jpg/webp/pdf->imagem); padrao 90

Exemplos:
    umd convert video.mov mp4
    umd convert relatorio.docx pdf
    umd convert manual.pdf png --pages "1-3" --dpi 300
    umd convert notas.md pdf
    umd convert "C:\Fotos" jpg --recursive --output "C:\Fotos convertidas"

Requisitos:
    documentos/planilhas/apresentacoes exigem o LibreOffice instalado
"""

from __future__ import annotations

import sys
from pathlib import Path

from .core import registry
from .core.config import Settings, load_settings
from .core.errors import ConversionError, path_not_found
from .core.history import History
from .core.logging_setup import get_logger, setup_logging
from .core.paths import ConflictPolicy, clean_path_arg
from .core.queue import ConversionQueue, JobStatus, merge_images_to_pdf, run_queue

_FLAGS_WITH_VALUE = {
    "--output", "--pages", "--dpi", "--quality",
}
_FLAGS_BOOL = {
    "--overwrite", "--recursive", "--quiet", "--verbose", "--debug",
    "--formatos", "-f", "--help", "-h",
}


_FLATTEN_ALIASES = {"flatten", "achatar", "achatado"}


def _parse_argv(argv: list[str]) -> tuple[list[str], dict]:
    positional: list[str] = []
    flags: dict = {}
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in _FLAGS_WITH_VALUE:
            if i + 1 >= len(argv):
                raise ConversionError(f"{arg} precisa de um valor depois.")
            flags[arg.lstrip("-")] = argv[i + 1]
            i += 2
        elif arg in _FLAGS_BOOL:
            flags[arg.lstrip("-")] = True
            i += 1
        else:
            positional.append(arg)
            i += 1
    return positional, flags


def _compatible_files_in_folder(folder: Path, target_fmt: str, recursive: bool) -> list[Path]:
    from .converters.archive import detect_archive_kind

    target_fmt = target_fmt.lower().lstrip(".")
    iterator = folder.rglob("*") if recursive else folder.iterdir()
    files = []
    for path in sorted(iterator):
        if not path.is_file():
            continue
        ext = path.suffix.lower().lstrip(".")
        if ext == target_fmt:
            continue  # ja esta no formato de destino; nao faz sentido reconverter
        if target_fmt == registry.FLATTEN_TARGET and path.stem.endswith(registry.FLATTEN_SUFFIX):
            continue  # resultado de um achatamento anterior
        is_archive_src = detect_archive_kind(path) is not None
        if target_fmt in registry.ARCHIVE.extensions and is_archive_src:
            files.append(path)
        elif target_fmt not in registry.ARCHIVE.extensions and not is_archive_src:
            if registry.resolve_handler(ext, target_fmt) is not None:
                files.append(path)
    return files


def run(
    input_arg: str,
    target_fmt: str,
    output_arg: str | None,
    *,
    output_dir: str | None,
    overwrite: bool,
    recursive: bool,
    pages: str | None,
    dpi: str | None,
    quality: str | None,
    logger,
) -> int:
    input_path = Path(clean_path_arg(input_arg))
    if target_fmt.lower() in _FLATTEN_ALIASES:
        target_fmt = registry.FLATTEN_TARGET
    if not input_path.exists():
        logger.error(path_not_found(str(input_path)).user_message())
        return 1

    settings = load_settings()
    history = History()
    if overwrite:
        settings.conflict_policy = ConflictPolicy.OVERWRITE.value
    if output_dir:
        settings.default_output_dir = str(Path(clean_path_arg(output_dir)))

    options: dict = {}
    if pages:
        options["pages"] = pages
    if dpi:
        options["dpi"] = int(dpi)
    if quality:
        options["quality"] = int(quality)

    queue = ConversionQueue()

    if input_path.is_dir():
        files = _compatible_files_in_folder(input_path, target_fmt, recursive)
        if not files:
            logger.error(f"Nenhum arquivo compativel com '{target_fmt}' encontrado em {input_path}")
            return 1
        for f in files:
            queue.add(f, target_fmt, relative_root=input_path, options=options)
    else:
        if output_arg:
            # 3o argumento posicional: caminho de saida exato, igual ao
            # comportamento do convert.py original (usado como veio,
            # relativo ao diretorio atual se nao for absoluto). Tem
            # prioridade sobre --output.
            out_arg_path = Path(clean_path_arg(output_arg))
            job = queue.add(input_path, target_fmt, output_dir=out_arg_path.parent, options=options)
            if job is not None:
                job.explicit_name = out_arg_path.name
        else:
            job_output_dir = Path(clean_path_arg(output_dir)) if output_dir else None
            queue.add(input_path, target_fmt, output_dir=job_output_dir, options=options)

    exit_code = 0

    def on_progress(job) -> None:
        nonlocal exit_code
        if job.status == JobStatus.DONE:
            names = ", ".join(p.name for p in job.output_paths)
            print(f"[OK] {job.source.name} -> {names}")
        elif job.status == JobStatus.ERROR:
            print(f"[ERRO] {job.source.name}: {job.error}")
            exit_code = 1
        elif job.status == JobStatus.SKIPPED:
            print(f"[IGNORADO] {job.source.name} (ja existe)")
        elif job.status == JobStatus.CANCELLED:
            print(f"[CANCELADO] {job.source.name}")

    run_queue(queue, settings, history=history, on_progress=on_progress)
    return exit_code


def run_interactive() -> int:
    print("umd convert - conversor de arquivos")
    print("Formatos suportados: umd convert --formatos\n")
    raw_path = input("Caminho do arquivo ou pasta: ").strip()
    if not raw_path:
        print(__doc__)
        return 1
    raw_fmt = input("Converter para (ex.: mp4, mp3, png, pdf, zip): ").strip()
    if not raw_fmt:
        print(__doc__)
        return 1
    logger = get_logger()
    return run(
        raw_path, raw_fmt, None,
        output_dir=None, overwrite=False, recursive=False,
        pages=None, dpi=None, quality=None, logger=logger,
    )


def run_images_to_pdf(argv: list[str], logger) -> int:
    if len(argv) < 2:
        print("Uso: umd convert --images-to-pdf foto1.jpg foto2.png ... saida.pdf")
        return 1
    *input_args, output_arg = argv
    inputs = [Path(clean_path_arg(a)) for a in input_args]
    missing = [str(p) for p in inputs if not p.exists()]
    if missing:
        for m in missing:
            logger.error(path_not_found(m).user_message())
        return 1

    settings = load_settings()
    history = History()
    output_path = Path(clean_path_arg(output_arg))
    try:
        out = merge_images_to_pdf(inputs, output_path, settings=settings, history=history)
        print(f"[OK] {len(inputs)} imagem(ns) -> {out}")
        return 0
    except ConversionError as exc:
        print(f"[ERRO] {exc.user_message()}")
        return 1


def main(argv: list[str]) -> int:
    try:
        if argv and argv[0] == "--images-to-pdf":
            logger = setup_logging()
            return run_images_to_pdf(argv[1:], logger)

        positional, flags = _parse_argv(argv)
    except ConversionError as exc:
        print(f"[ERRO] {exc.user_message()}")
        return 1

    logger = setup_logging(
        verbose=bool(flags.get("verbose")), debug=bool(flags.get("debug")), quiet=bool(flags.get("quiet")),
    )

    if flags.get("help") or flags.get("h"):
        print(__doc__)
        return 0

    if flags.get("formatos") or flags.get("f"):
        print(registry.describe_supported_formats())
        return 0

    if not positional:
        return run_interactive()

    if len(positional) == 1:
        raw_fmt = input(f"Converter '{positional[0]}' para (ex.: mp4, mp3, png, pdf, zip): ").strip()
        if not raw_fmt:
            print(__doc__)
            return 1
        positional.append(raw_fmt)

    input_arg, target_fmt = positional[0], positional[1]
    output_arg = positional[2] if len(positional) >= 3 else None

    try:
        return run(
            input_arg, target_fmt, output_arg,
            output_dir=flags.get("output"),
            overwrite=bool(flags.get("overwrite")),
            recursive=bool(flags.get("recursive")),
            pages=flags.get("pages"),
            dpi=flags.get("dpi"),
            quality=flags.get("quality"),
            logger=logger,
        )
    except ConversionError as exc:
        logger.error(exc.user_message())
        return 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
