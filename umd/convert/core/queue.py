"""Fila de conversao (item 5) + motor de processamento em lote (item 6).

`ConversionQueue` so guarda e organiza os Jobs (adicionar, remover,
limpar, evitar duplicata). Quem efetivamente converte e dispara os
callbacks de progresso e `JobRunner`, usado tanto pela CLI (lote) quanto
pela GUI (numa thread separada, para nao travar a interface -- item 27).
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from . import registry
from .config import Settings
from .errors import ConversionError, unsupported_conversion
from .history import History
from .paths import ConflictPolicy, resolve_conflict, sanitize_filename


class JobStatus(str, Enum):
    WAITING = "Aguardando"
    RUNNING = "Convertendo"
    DONE = "Concluido"
    ERROR = "Erro"
    CANCELLED = "Cancelado"
    SKIPPED = "Ignorado"


@dataclass
class Job:
    id: int
    source: Path
    target_ext: str
    output_dir: Path | None = None
    relative_root: Path | None = None  # p/ preservar estrutura de subpastas em lote
    explicit_name: str | None = None  # nome de saida exato pedido pelo usuario (CLI: 3o argumento)
    options: dict[str, Any] = field(default_factory=dict)
    status: JobStatus = JobStatus.WAITING
    output_paths: list[Path] = field(default_factory=list)
    error: str | None = None

    @property
    def display_target(self) -> str:
        return self.target_ext.upper()


class ConversionQueue:
    def __init__(self) -> None:
        self._jobs: list[Job] = []
        self._next_id = 1
        self._known_sources: set[Path] = set()

    def add(
        self,
        source: Path,
        target_ext: str,
        output_dir: Path | None = None,
        relative_root: Path | None = None,
        options: dict[str, Any] | None = None,
    ) -> Job | None:
        """Adiciona um job. Devolve None (sem adicionar de novo) se o
        mesmo arquivo+destino ja estiver na fila (item 4: nao duplicar)."""
        resolved = source.resolve()
        key = (resolved, target_ext.lower())
        if key in self._known_sources:
            return None
        self._known_sources.add(key)

        job = Job(
            id=self._next_id,
            source=source,
            target_ext=target_ext.lower().lstrip("."),
            output_dir=output_dir,
            relative_root=relative_root,
            options=dict(options or {}),
        )
        self._next_id += 1
        self._jobs.append(job)
        return job

    def remove(self, job_id: int) -> bool:
        for i, job in enumerate(self._jobs):
            if job.id == job_id and job.status == JobStatus.WAITING:
                del self._jobs[i]
                self._known_sources.discard((job.source.resolve(), job.target_ext))
                return True
        return False

    def clear(self) -> None:
        self._jobs.clear()
        self._known_sources.clear()

    @property
    def jobs(self) -> list[Job]:
        return list(self._jobs)

    def pending(self) -> list[Job]:
        return [j for j in self._jobs if j.status == JobStatus.WAITING]

    def __len__(self) -> int:
        return len(self._jobs)


def build_output_path(job: Job, settings: Settings) -> Path:
    # Estrutura de subpastas (item 6) so faz sentido replicar quando o
    # destino e uma pasta DIFERENTE da pasta de origem (ex.: "Convertidos/").
    # Sem pasta de destino explicita, o arquivo convertido vai para o lado
    # do original -- replicar a estrutura ali em cima dele mesmo duplicaria
    # o caminho (bug corrigido: lote/sub1/ virava lote/sub1/sub1/).
    if job.output_dir is not None:
        base_dir = job.output_dir
        preserve = settings.preserve_folder_structure
    elif settings.default_output_dir:
        base_dir = Path(settings.default_output_dir)
        preserve = settings.preserve_folder_structure
    else:
        base_dir = job.source.parent
        preserve = False

    if preserve and job.relative_root is not None:
        rel_parent = job.source.parent.resolve().relative_to(job.relative_root.resolve())
        base_dir = base_dir / rel_parent

    if job.explicit_name:
        name = job.explicit_name
    elif job.target_ext == registry.FLATTEN_TARGET:
        name = f"{job.source.stem}{registry.FLATTEN_SUFFIX}.pdf"
    else:
        name = f"{job.source.stem}.{job.target_ext}"
    return base_dir / sanitize_filename(name)


def settings_output_dir(settings: Settings, source: Path) -> Path:
    if settings.default_output_dir:
        return Path(settings.default_output_dir)
    return source.parent


HANDLERS: dict[str, Callable[..., Any]] = {}


def _load_handlers() -> None:
    if HANDLERS:
        return
    from ..converters import archive, image, media, office, pdf_flatten, pdf_to_image, text_to_pdf, markdown_to_pdf

    HANDLERS.update({
        "pdf_flatten": pdf_flatten.convert,
        "media": media.convert,
        "image": image.convert,
        "office": office.convert,
        "archive": archive.convert,
        "pdf_to_image": pdf_to_image.convert,
        "text_to_pdf": text_to_pdf.convert,
        "markdown_to_pdf": markdown_to_pdf.convert,
    })


def run_job(
    job: Job,
    settings: Settings,
    history: History | None = None,
    cancel_event: threading.Event | None = None,
    on_progress: Callable[[Job], None] | None = None,
) -> None:
    """Executa um job e atualiza seu status/output_paths/error in-place."""
    _load_handlers()
    job.status = JobStatus.RUNNING
    if on_progress:
        on_progress(job)

    try:
        if not job.source.exists():
            raise ConversionError(f"Arquivo nao encontrado: {job.source}")

        source_ext = job.source.suffix.lower().lstrip(".")
        conflict_policy = ConflictPolicy(job.options.get("conflict_policy", settings.conflict_policy))

        if job.target_ext in ("png", "jpg", "jpeg", "webp") and source_ext == "pdf":
            output_dir = job.output_dir or settings_output_dir(settings, job.source)
            options = {**job.options, "conflict_policy": conflict_policy.value}
            produced = HANDLERS["pdf_to_image"](job.source, output_dir, job.target_ext, options)
            job.output_paths = produced
        else:
            handler_name = registry.resolve_handler(source_ext, job.target_ext)
            if handler_name is None:
                raise unsupported_conversion(source_ext, job.target_ext)

            candidate = build_output_path(job, settings)
            final_path = resolve_conflict(candidate, conflict_policy)
            if final_path is None:
                job.status = JobStatus.SKIPPED
                if on_progress:
                    on_progress(job)
                return

            handler = HANDLERS[handler_name]
            if handler_name == "archive":
                out = handler(job.source, final_path, job.target_ext, cancel_event=cancel_event)
            elif handler_name in ("media", "image", "office"):
                out = handler(job.source, final_path, job.options, cancel_event=cancel_event)
            else:
                out = handler(job.source, final_path, job.options)
            job.output_paths = [out]

        job.status = JobStatus.DONE
        if history is not None:
            history.record(str(job.source), str(job.output_paths[0]) if job.output_paths else None, "Concluido")

    except ConversionError as exc:
        if exc.message == "Conversao cancelada pelo usuario.":
            job.status = JobStatus.CANCELLED
        else:
            job.status = JobStatus.ERROR
        job.error = exc.user_message()
        if history is not None:
            history.record(str(job.source), None, job.status.value, job.error)
    except Exception as exc:  # falha inesperada -- ainda assim nao derruba a fila
        job.status = JobStatus.ERROR
        job.error = f"Erro inesperado: {exc}"
        if history is not None:
            history.record(str(job.source), None, job.status.value, job.error)
    finally:
        if on_progress:
            on_progress(job)


def merge_images_to_pdf(
    sources: list[Path],
    output_path: Path,
    options: dict[str, Any] | None = None,
    settings: Settings | None = None,
    history: History | None = None,
) -> Path:
    """Junta varias imagens num unico PDF (item 8). Operacao separada da
    fila normal porque e N arquivos -> 1 arquivo, nao 1 -> 1."""
    from ..converters import image_to_pdf

    conflict_policy = ConflictPolicy(
        (options or {}).get("conflict_policy", settings.conflict_policy if settings else ConflictPolicy.RENAME.value)
    )
    final_path = resolve_conflict(output_path, conflict_policy)
    if final_path is None:
        raise ConversionError(f"'{output_path.name}' ja existe e a politica de conflito e 'ignorar'.")

    try:
        out = image_to_pdf.convert(sources, final_path, options)
        if history is not None:
            history.record(", ".join(str(p) for p in sources), str(out), "Concluido")
        return out
    except ConversionError as exc:
        if history is not None:
            history.record(", ".join(str(p) for p in sources), None, "Erro", exc.user_message())
        raise


def run_queue(
    queue: ConversionQueue,
    settings: Settings,
    history: History | None = None,
    cancel_event: threading.Event | None = None,
    pause_event: threading.Event | None = None,
    on_progress: Callable[[Job], None] | None = None,
) -> None:
    """Processa os jobs pendentes em ordem. Continua mesmo se um arquivo
    falhar (item 6). `pause_event`, quando setado, pausa ENTRE arquivos
    (nao no meio de uma conversao -- ver item 19: nao existe pausa real
    no meio de uma chamada de ffmpeg/LibreOffice ja em andamento)."""
    for job in queue.pending():
        while pause_event is not None and pause_event.is_set():
            if cancel_event is not None and cancel_event.is_set():
                break
            time.sleep(0.2)

        # checado DEPOIS da pausa: cancelar enquanto pausado nao pode
        # deixar converter "mais um" arquivo
        if cancel_event is not None and cancel_event.is_set():
            job.status = JobStatus.CANCELLED
            if on_progress:
                on_progress(job)
            continue

        run_job(job, settings, history=history, cancel_event=cancel_event, on_progress=on_progress)
