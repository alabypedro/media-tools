"""Um download na fila: o pedido (persistido) e o estado em tempo real."""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from pydantic import BaseModel

from ..core.exceptions import UMDError
from ..database.models import now_iso
from ..media.formats import DownloadSelection
from .progress import ProgressInfo

STAGING_DIRNAME = ".umd-staging"


class JobStatus(str, Enum):
    QUEUED = "queued"
    STARTING = "starting"
    DOWNLOADING = "downloading"
    PROCESSING = "processing"
    WAITING_RETRY = "waiting_retry"
    PAUSED = "paused"
    INTERRUPTED = "interrupted"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


ACTIVE_STATUSES = {JobStatus.STARTING, JobStatus.DOWNLOADING, JobStatus.PROCESSING}
PENDING_STATUSES = {JobStatus.QUEUED, JobStatus.WAITING_RETRY}
FINISHED_STATUSES = {JobStatus.COMPLETED, JobStatus.FAILED, JobStatus.CANCELLED}
RESUMABLE_STATUSES = {JobStatus.PAUSED, JobStatus.INTERRUPTED}

STATUS_LABELS = {
    JobStatus.QUEUED: "Na fila",
    JobStatus.STARTING: "Iniciando…",
    JobStatus.DOWNLOADING: "Baixando",
    JobStatus.PROCESSING: "Processando",
    JobStatus.WAITING_RETRY: "Aguardando nova tentativa",
    JobStatus.PAUSED: "Pausado",
    JobStatus.INTERRUPTED: "Interrompido",
    JobStatus.COMPLETED: "Concluído",
    JobStatus.FAILED: "Falhou",
    JobStatus.CANCELLED: "Cancelado",
}

STAGE_LABELS = {
    "merge": "Juntando áudio e vídeo…",
    "extract_audio": "Extraindo áudio…",
    "remux": "Ajustando o formato do arquivo…",
    "convert": "Convertendo…",
    "metadata": "Gravando metadados…",
    "thumbnail": "Incorporando capa…",
    "fixup": "Corrigindo o arquivo…",
    "move": "Organizando arquivos…",
    "salvage": "Salvando a gravação…",
}


class DownloadRequest(BaseModel):
    """Tudo que e preciso para (re)executar um download. Salvo no banco."""

    url: str
    provider_key: str = "generic"
    platform_name: str = "Site"
    engines: list[str]
    selection: DownloadSelection
    output_dir: str
    filename_template: str
    create_subfolders: bool = True
    title: str | None = None
    author: str | None = None
    thumbnail: str | None = None
    content_type: str | None = None
    upload_date: str | None = None
    media_id: str | None = None
    description: str | None = None
    duration: float | None = None
    expected_items: int | None = None
    is_collection: bool = False
    is_live: bool = False


@dataclass(frozen=True)
class JobSnapshot:
    """Copia imutavel do estado de um job (segura para a interface ler)."""

    id: str
    url: str
    title: str
    platform: str
    status: JobStatus
    progress: ProgressInfo
    error: UMDError | None
    warning: str | None
    files: tuple[str, ...]
    attempts: int
    created_at: str
    thumbnail: str | None
    quality_label: str
    output_dir: str
    is_live: bool
    item_title: str | None
    next_retry_in: float | None = None

    @property
    def is_active(self) -> bool:
        return self.status in ACTIVE_STATUSES

    @property
    def is_finished(self) -> bool:
        return self.status in FINISHED_STATUSES


class DownloadJob:
    def __init__(
        self,
        request: DownloadRequest,
        *,
        job_id: str | None = None,
        staging_dir: Path | None = None,
        created_at: str | None = None,
    ):
        self.id = job_id or uuid.uuid4().hex
        self.request = request
        self.status = JobStatus.QUEUED
        self.progress = ProgressInfo(items=request.expected_items)
        self.title = request.title or request.url
        self.item_title: str | None = None
        self.thumbnail = request.thumbnail
        self.error: UMDError | None = None
        self.warning: str | None = None
        self.files: list[Path] = []
        self.attempts = 0
        self.created_at = created_at or now_iso()
        self.staging_dir = staging_dir or Path(request.output_dir) / STAGING_DIRNAME / self.id
        self.cancel_event = threading.Event()
        self.stop_reason: str | None = None  # "pause" | "cancel" | "stop" (live) | "shutdown"
        self.next_retry_at: float | None = None
        self.lock = threading.RLock()

    def request_stop(self, reason: str) -> None:
        self.stop_reason = reason
        self.cancel_event.set()

    def reset_for_run(self) -> None:
        self.cancel_event = threading.Event()
        self.stop_reason = None
        self.error = None
        self.warning = None
        self.next_retry_at = None
        self.progress = ProgressInfo(items=self.request.expected_items)

    def snapshot(self, now: float | None = None) -> JobSnapshot:
        with self.lock:
            retry_in = None
            if self.next_retry_at is not None and now is not None:
                retry_in = max(0.0, self.next_retry_at - now)
            return JobSnapshot(
                id=self.id,
                url=self.request.url,
                title=self.title,
                platform=self.request.platform_name,
                status=self.status,
                progress=self.progress.copy(),
                error=self.error,
                warning=self.warning,
                files=tuple(str(p) for p in self.files),
                attempts=self.attempts,
                created_at=self.created_at,
                thumbnail=self.thumbnail,
                quality_label=self.request.selection.quality_label(),
                output_dir=self.request.output_dir,
                is_live=self.request.is_live,
                item_title=self.item_title,
                next_retry_in=retry_in,
            )
