"""Linha de comando (usa exatamente os mesmos servicos da interface grafica).

Exemplos:
    umd "https://exemplo.com/video"
    umd URL --quality 1080p
    umd URL --audio mp3
    umd URL --output "C:\\Downloads"
    umd URL1 URL2 URL3
    umd --file urls.txt
    umd URL --info
    umd convert video.mov mp4       (conversor de arquivos: umd convert --help)
    umd edit gato.gif --resize 50%  (editor de GIF/animacoes/video: umd edit --help)
    umd rename pasta --exif         (renomeador em lote, com previa: umd rename --help)
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from . import APP_NAME, __version__
from .core.config import AUDIO_FORMATS, VIDEO_CONTAINERS, QualityPreset
from .core.exceptions import UMDError
from .core.i18n import tr
from .downloader.job import FINISHED_STATUSES, STAGE_LABELS, JobStatus
from .downloader.progress import human_duration, human_size, human_speed
from .media.formats import SelectionMode, audio_format_choices, estimate_size, quality_choices, selection_from_settings
from .media.metadata import CONTENT_TYPE_LABELS

EXIT_OK, EXIT_FAILED, EXIT_USAGE, EXIT_INTERRUPTED = 0, 1, 2, 130


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="umd",
        description=f"{APP_NAME} {__version__} — baixa vídeos, áudios, imagens e posts a partir de URLs "
                    "(e converte arquivos: umd convert).",
        epilog=(
            "Para converter arquivos (vídeo, áudio, imagem, PDF, documentos, compactados): umd convert --help. "
            "Para editar GIFs, animações e vídeos (redimensionar, cortar, texto, vídeo → GIF...): umd edit --help. "
            "Para renomear vários arquivos de uma vez (modelo, regex, data EXIF): umd rename --help. "
            "Use apenas para conteúdo que você tem autorização para baixar. O programa não contorna DRM, "
            "paywalls ou controles de acesso."
        ),
    )
    parser.add_argument("urls", nargs="*", metavar="URL", help="uma ou mais URLs")
    parser.add_argument("-f", "--file", type=Path, help="arquivo TXT/CSV com URLs (uma por linha)")
    parser.add_argument("-q", "--quality", choices=[p.value for p in QualityPreset], help="preset de qualidade")
    parser.add_argument("-a", "--audio", choices=list(AUDIO_FORMATS), help="baixar apenas o áudio neste formato")
    parser.add_argument("--bitrate", choices=["320", "256", "192", "128", "original"], help="bitrate do áudio")
    parser.add_argument("--format", dest="container", choices=list(VIDEO_CONTAINERS), help="formato do vídeo")
    parser.add_argument("-o", "--output", type=Path, help="pasta de destino")
    parser.add_argument("-t", "--template", help="template de nome, ex.: \"{platform}/{author}/{title}.{ext}\"")
    parser.add_argument("--no-subfolders", action="store_true", help="salvar tudo direto na pasta de destino")
    parser.add_argument("--items", help="itens de playlist/galeria, ex.: \"1-3,7\"")
    parser.add_argument("-j", "--concurrent", type=int, metavar="N", help="downloads simultâneos (1-10)")
    parser.add_argument("--rate-limit", type=int, metavar="KBPS", help="limite de velocidade por download, em KB/s")
    parser.add_argument("--retries", type=int, metavar="N", help="tentativas automáticas em falhas temporárias")
    parser.add_argument("--cookies-from-browser", metavar="NAVEGADOR[:PERFIL]",
                        help="usar a sessão do seu navegador (firefox, chrome, edge...) para conteúdo que você já acessa")
    parser.add_argument("--cookies", type=Path, metavar="ARQUIVO", help="arquivo cookies.txt (formato Netscape)")
    parser.add_argument("-i", "--info", action="store_true", help="só analisar e mostrar informações/formatos")
    parser.add_argument("--json", action="store_true", help="com --info, imprime o resultado em JSON")
    parser.add_argument("--check-engines", action="store_true", help="mostra versões do yt-dlp, gallery-dl e FFmpeg")
    parser.add_argument("--update-engines", action="store_true", help="verifica e instala atualizações do yt-dlp/gallery-dl")
    parser.add_argument("--platforms", action="store_true", help="lista as plataformas e o suporte real das engines")
    parser.add_argument("--quiet", action="store_true", help="mostra só erros e o resumo final")
    parser.add_argument("--verbose", action="store_true", help="mostra avisos técnicos no terminal")
    parser.add_argument("--debug", action="store_true", help="log detalhado (arquivos de log e terminal)")
    parser.add_argument("--version", action="version", version=f"{APP_NAME} {__version__}")
    return parser


def _out(text: str = "", end: str = "\n") -> None:
    try:
        sys.stdout.write(text + end)
    except UnicodeEncodeError:
        sys.stdout.write(text.encode("ascii", "replace").decode("ascii") + end)
    sys.stdout.flush()


def _err(error: UMDError, prefix: str = "") -> None:
    message = error.user_message()
    _out(f"[ERRO] {prefix}{message}")


def parse_items(spec: str) -> list[int]:
    items: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, _, end = part.partition("-")
            a, b = int(start), int(end)
            items.update(range(min(a, b), max(a, b) + 1))
        else:
            items.add(int(part))
    if not items or min(items) < 1:
        raise ValueError(spec)
    return sorted(items)


def _collect_urls(args: argparse.Namespace, allow_local: bool) -> tuple[list[str], list[str]]:
    from .services.batch import load_url_file, parse_url_text

    urls: list[str] = []
    invalid: list[str] = []
    if args.file:
        result = load_url_file(args.file, allow_local=allow_local)
        urls.extend(result.urls)
        invalid.extend(result.invalid)
    if args.urls:
        result = parse_url_text("\n".join(args.urls), allow_local=allow_local)
        urls.extend(u for u in result.urls if u not in urls)
        invalid.extend(result.invalid)
    return urls, invalid


def _overrides(args: argparse.Namespace) -> dict:
    changes: dict = {}
    if args.template:
        changes["filename_template"] = args.template
    if args.no_subfolders:
        changes["create_subfolders"] = False
    if args.concurrent is not None:
        changes["max_concurrent_downloads"] = args.concurrent
    if args.rate_limit is not None:
        changes["rate_limit_kbps"] = args.rate_limit
    if args.retries is not None:
        changes["retries"] = args.retries
    if args.bitrate:
        changes["default_audio_bitrate"] = args.bitrate
    if args.cookies_from_browser:
        browser, _, profile = args.cookies_from_browser.partition(":")
        changes["cookies_browser"] = browser.strip().lower()
        changes["cookies_browser_profile"] = profile.strip()
    if args.cookies:
        changes["cookies_file"] = str(args.cookies)
    return changes


# ---------------------------------------------------------------- comandos

def cmd_info(ctx, urls: list[str], as_json: bool) -> int:
    code = EXIT_OK
    for url in urls:
        try:
            info = ctx.media.analyze(url)
        except UMDError as exc:
            _err(exc, f"{url}\n")
            code = EXIT_FAILED
            continue
        if as_json:
            _out(info.model_dump_json(indent=2))
            continue
        _out(f"Título:      {info.title}")
        _out(f"Autor:       {info.author or '-'}")
        _out(f"Plataforma:  {info.platform_name}  (engine: {info.engine}, extrator: {info.extractor or '-'})")
        _out(f"Tipo:        {tr(CONTENT_TYPE_LABELS.get(info.content_type, info.content_type.value))}")
        if info.duration:
            _out(f"Duração:     {human_duration(info.duration)}")
        if info.view_count is not None:
            _out(f"Visualiz.:   {info.view_count}")
        if info.upload_date:
            _out(f"Data:        {info.upload_date}")
        if info.support_note:
            _out(f"Aviso:       {info.support_note}")
        for warning in info.warnings:
            _out(f"Aviso:       {tr(warning)}")
        if info.video_heights:
            _out("Vídeo:       " + ", ".join(f"{h}p" for h in info.video_heights))
        if info.audio_bitrates:
            _out("Áudio:       " + ", ".join(f"{b} kbps" for b in info.audio_bitrates))
        if info.formats:
            qualities = [f"{c.label}{' ~' + human_size(c.size) if c.size else ''}" for c in quality_choices(info)]
            _out("Qualidades:  " + " | ".join(qualities))
            _out("Áudio em:    " + ", ".join(c.key for c in audio_format_choices(info)))
            _out(f"Formatos:    {len(info.formats)} (use --json para a lista completa)")
        if info.entries:
            total = info.total_entries or len(info.entries)
            _out(f"Itens:       {total}" + (" (lista truncada)" if info.truncated else ""))
            for entry in info.entries[:15]:
                _out(f"  {entry.index:>3}. {entry.title}")
            if len(info.entries) > 15:
                _out(f"  ... e mais {len(info.entries) - 15}")
        estimate = estimate_size(info, selection_from_settings(ctx.settings_store.settings, info))
        if estimate:
            _out(f"Tamanho est.: ~{human_size(estimate)}")
        _out()
    return code


def cmd_check_engines(ctx) -> int:
    versions = ctx.engines.versions()
    for name in ("yt-dlp", "gallery-dl", "yt-dlp-ejs"):
        status = versions.get(name)
        if status and status.ok:
            origin = "atualizado pelo app" if status.override else "embutido"
            _out(f"{name:<12} OK  {status.version}  ({origin})")
        else:
            _out(f"{name:<12} FALTANDO  {status.error if status else ''}")
    ffmpeg = ctx.engines.ffmpeg()
    _out(f"{'FFmpeg':<12} {'OK  ' + ffmpeg.version + '  (' + (ffmpeg.path or '') + ')' if ffmpeg.ok else 'FALTANDO'}")
    runtimes = ctx.engines.js_runtimes()
    _out(f"{'JavaScript':<12} {'OK  ' + ', '.join(runtimes) if runtimes else 'não encontrado (alguns formatos do YouTube podem faltar)'}")
    return EXIT_OK if ffmpeg.ok and all(versions.get(n) and versions[n].ok for n in ("yt-dlp", "gallery-dl")) else EXIT_FAILED


def cmd_update_engines(ctx) -> int:
    from .services.engine_service import UPDATABLE

    versions = ctx.engines.versions()
    code = EXIT_OK
    for name in UPDATABLE:
        current = versions.get(name).version if versions.get(name) else None
        try:
            update = ctx.engines.check_update(name, current)
            if not update.available:
                _out(f"{name}: já está na versão mais recente ({current}).")
                continue
            _out(f"{name}: versão atual {current} -> nova versão {update.latest}")
            ctx.engines.apply_update(update, lambda msg: _out(f"  {msg}"))
            _out(f"{name}: atualizado para {update.latest}.")
        except UMDError as exc:
            _err(exc, f"{name}: ")
            code = EXIT_FAILED
    return code


def cmd_platforms(ctx) -> int:
    rows = ctx.engines.platform_support()
    _out(f"{'Plataforma':<16} {'yt-dlp':<28} {'gallery-dl':<30} Situação")
    for row in rows:
        yt = ", ".join(row["yt-dlp"]) or "-"
        gallery = row["gallery-dl"] or "-"
        situation = "suportado" if (row["yt-dlp"] or row["gallery-dl"]) else "sem extrator dedicado (só tentativa genérica)"
        _out(f"{row['name']:<16} {yt:<28} {gallery:<30} {situation}")
    return EXIT_OK


class _Printer:
    """Progresso no terminal: uma linha de status atualizada + uma linha por job concluido."""

    def __init__(self, quiet: bool):
        self.quiet = quiet
        self.last_status = ""
        self.interactive = sys.stdout.isatty()

    def status(self, jobs: list) -> None:
        if self.quiet or not self.interactive:
            return
        active = [j for j in jobs if j.status in (JobStatus.DOWNLOADING, JobStatus.PROCESSING, JobStatus.STARTING)]
        pending = sum(1 for j in jobs if j.status in (JobStatus.QUEUED, JobStatus.WAITING_RETRY))
        parts = []
        for job in active[:2]:
            p = job.progress
            title = (job.title or job.url)[:32]
            if job.status == JobStatus.PROCESSING:
                parts.append(f"{title}: {tr(STAGE_LABELS.get(p.stage_detail, 'Processando…'))}")
                continue
            pct = f"{int(p.fraction * 100)}%" if p.fraction is not None else human_size(p.downloaded)
            speed = human_speed(p.speed)
            items = f" [{p.item}/{p.items}]" if p.items and p.items > 1 else ""
            parts.append(f"{title}{items} {pct} {speed}".strip())
        if pending:
            parts.append(f"+{pending} na fila")
        line = " | ".join(parts)[:150]
        padding = " " * max(0, len(self.last_status) - len(line))
        _out("\r" + line + padding, end="")
        self.last_status = line

    def line(self, text: str) -> None:
        if self.last_status:
            _out("\r" + " " * len(self.last_status) + "\r", end="")
            self.last_status = ""
        _out(text)


def cmd_download(ctx, args: argparse.Namespace, urls: list[str]) -> int:
    printer = _Printer(args.quiet)
    preset = QualityPreset(args.quality) if args.quality else None
    items = parse_items(args.items) if args.items else None
    job_ids: list[str] = []
    for url in urls:
        try:
            request = ctx.download.quick_request(
                url, preset=preset, output_dir=args.output, audio_format=args.audio, container=args.container
            )
        except UMDError as exc:
            _err(exc, f"{url}\n")
            continue
        if items:
            request.selection.items = items
            request.expected_items = len(items)
            request.is_collection = True
        if args.audio:
            request.selection.mode = SelectionMode.AUDIO
        job_ids.append(ctx.download.submit(request).id)
        if not args.quiet:
            printer.line(f"[fila] {url}")

    if not job_ids:
        return EXIT_FAILED

    reported: set[str] = set()
    failures = 0
    try:
        while True:
            jobs = [ctx.manager.get(job_id) for job_id in job_ids]
            jobs = [j for j in jobs if j is not None]
            for job in jobs:
                if job.id in reported or job.status not in FINISHED_STATUSES:
                    continue
                reported.add(job.id)
                if job.status == JobStatus.COMPLETED:
                    media = [f for f in job.files if not f.endswith(".txt")]
                    printer.line(f"[ok] {job.title}")
                    for path in media[:10]:
                        printer.line(f"     -> {path}")
                    if len(media) > 10:
                        printer.line(f"     ... e mais {len(media) - 10} arquivo(s)")
                    if job.warning:
                        printer.line(f"     aviso: {job.warning}")
                else:
                    failures += 1
                    if job.error is not None:
                        printer.line(f"[erro] {job.title or job.url}")
                        printer.line("       " + job.error.user_message().replace("\n", "\n       "))
                        if args.verbose or args.debug:
                            printer.line("       detalhes: " + (job.error.details or "")[-800:].replace("\n", "\n       "))
                    else:
                        printer.line(f"[{tr(job.status.value)}] {job.title}")
            if len(reported) == len(jobs):
                break
            printer.status(jobs)
            time.sleep(0.3)
    except KeyboardInterrupt:
        printer.line("\nInterrompido: cancelando downloads em andamento…")
        for job_id in job_ids:
            ctx.manager.cancel(job_id)
        ctx.manager.wait_all(timeout=15)
        return EXIT_INTERRUPTED

    done = len(job_ids) - failures
    printer.line(f"\nConcluídos: {done}  •  Falhas: {failures}")
    return EXIT_OK if failures == 0 else EXIT_FAILED


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if hasattr(sys.stdout, "reconfigure"):
        try:
            if sys.stdout.isatty():
                sys.stdout.reconfigure(errors="replace")
            else:  # pipe/arquivo: UTF-8 em vez da pagina de codigo legada do Windows
                sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass
    if argv and argv[0] == "convert":  # conversor de arquivos: parser proprio
        from .convert.cli import main as convert_main

        return convert_main(argv[1:])
    if argv and argv[0] == "edit":  # editor de GIF/animacoes/video: parser proprio
        from .editor.cli import main as edit_main

        return edit_main(argv[1:])
    if argv and argv[0] == "rename":  # renomeador em lote: parser proprio
        from .rename.cli import main as rename_main

        return rename_main(argv[1:])
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.info and args.json:
        args.quiet = True

    if not (args.urls or args.file or args.check_engines or args.update_engines or args.platforms):
        parser.print_help()
        return EXIT_USAGE
    if args.items:
        try:
            parse_items(args.items)
        except ValueError:
            parser.error(f"--items inválido: {args.items!r} (use algo como 1-3,7)")

    from .services.context import create_context

    ctx = create_context(restore_jobs=False, start_manager=False, debug=args.debug, console_log=args.verbose or args.debug)
    try:
        try:
            changes = _overrides(args)
            if changes:
                ctx.settings_store.apply_temporary(**changes)
        except ValueError as exc:
            parser.error(str(exc))
        if args.check_engines:
            return cmd_check_engines(ctx)
        if args.update_engines:
            return cmd_update_engines(ctx)
        if args.platforms:
            return cmd_platforms(ctx)

        allow_local = ctx.settings_store.settings.allow_local_network_urls
        try:
            urls, invalid = _collect_urls(args, allow_local)
        except (OSError, ValueError) as exc:
            _out(f"[ERRO] Não foi possível ler a lista: {exc}")
            return EXIT_USAGE
        for line in invalid:
            _out(f"[ignorado] não é uma URL válida: {line}")
        if not urls:
            _out("[ERRO] Nenhuma URL válida informada.")
            return EXIT_USAGE
        if args.info:
            return cmd_info(ctx, urls, args.json)
        ctx.manager.start()
        return cmd_download(ctx, args, urls)
    except UMDError as exc:
        _err(exc)
        return EXIT_FAILED
    finally:
        ctx.close()


if __name__ == "__main__":
    raise SystemExit(main())
