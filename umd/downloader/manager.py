"""DownloadManager: fila, downloads simultaneos, pausa, cancelamento e retry.

Nao conhece Qt: a GUI e a CLI assinam eventos com `add_listener` e cada
uma exibe do seu jeito. A execucao de um download em si fica no
`executor` (injetado), o que permite testar toda a logica de fila com
um executor falso, sem rede.

Semantica dos controles:
* Pausar    -> encerra o worker mas MANTEM os arquivos parciais; "Retomar"
               continua de onde parou quando a engine/servidor permitem.
* Cancelar  -> encerra o worker e APAGA os parciais.
* Parar     -> (lives) encerra a gravacao e salva o que ja foi gravado.
* Retry     -> falhas temporarias (rede, timeout, limite de acessos) sao
               tentadas de novo automaticamente, com espera crescente.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from typing import Protocol

from ..core.config import SettingsStore
from ..core.exceptions import ErrorCode, UMDError, to_user_error
from ..core.logger import DOWNLOADS_LOGGER, get_logger
from ..database.models import DownloadRecord, now_iso
from ..database.repository import DownloadRepository
from ..media.metadata import kind_from_ext
from .job import (
    ACTIVE_STATUSES,
    FINISHED_STATUSES,
    PENDING_STATUSES,
    RESUMABLE_STATUSES,
    DownloadJob,
    DownloadRequest,
    JobSnapshot,
    JobStatus,
)
from .queue import JobQueue

log = get_logger("downloader")
dlog = get_logger(DOWNLOADS_LOGGER.split(".", 1)[1])

Listener = Callable[[str, JobSnapshot], None]  # evento: "added" | "updated" | "removed"


class Executor(Protocol):
    def execute(self, job: DownloadJob, report: Callable[[], None]) -> None: ...

    def discard(self, job: DownloadJob) -> None: ...


class DownloadManager:
    def __init__(
        self,
        executor: Executor,
        settings_store: SettingsStore,
        repository: DownloadRepository | None = None,
        *,
        clock: Callable[[], float] = time.monotonic,
    ):
        self._executor = executor
        self._settings = settings_store
        self._repo = repository
        self._clock = clock
        self._queue = JobQueue()
        self._listeners: list[Listener] = []
        self._cond = threading.Condition(threading.RLock())
        self._running: dict[str, threading.Thread] = {}
        self._last_start = float("-inf")
        self._stopped = False
        self._scheduler: threading.Thread | None = None
        settings_store.subscribe(lambda _settings: self._wake())

    # ------------------------------------------------------------ ciclo de vida

    def start(self) -> None:
        with self._cond:
            if self._scheduler is None:
                self._stopped = False
                self._scheduler = threading.Thread(target=self._loop, name="umd-scheduler", daemon=True)
                self._scheduler.start()

    def shutdown(self, timeout: float = 15.0) -> None:
        """Fecha o app: interrompe o que esta rodando, mantendo os parciais para retomar depois."""
        with self._cond:
            self._stopped = True
            running = [self._queue.get(job_id) for job_id in self._running]
            for job in running:
                if job is not None:
                    job.request_stop("shutdown")
            for job in self._queue.all():
                if job.status in PENDING_STATUSES:
                    self._set_status(job, JobStatus.INTERRUPTED)
            threads = list(self._running.values())
            self._cond.notify_all()
        deadline = time.monotonic() + timeout
        for thread in threads:
            thread.join(max(0.1, deadline - time.monotonic()))
        if self._scheduler is not None:
            self._scheduler.join(timeout=2)

    # ------------------------------------------------------------ eventos

    def add_listener(self, listener: Listener) -> None:
        self._listeners.append(listener)

    def _emit(self, event: str, job: DownloadJob) -> None:
        snapshot = job.snapshot(self._clock())
        for listener in list(self._listeners):
            try:
                listener(event, snapshot)
            except Exception:  # noqa: BLE001 - um listener com defeito nao pode travar os downloads
                log.exception("listener failed")

    # ------------------------------------------------------------ comandos

    def enqueue(self, request: DownloadRequest) -> DownloadJob:
        job = DownloadJob(request)
        with self._cond:
            self._queue.add(job)
            self._persist(job)
            self._cond.notify_all()
        dlog.info("queued %s (%s) %s", job.id, request.platform_name, request.url)
        self._emit("added", job)
        return job

    def restore(self, record: DownloadRecord) -> DownloadJob | None:
        """Recoloca na lista um download interrompido/pausado de uma sessao anterior."""
        if not record.request_json or self._queue.get(record.id):
            return None
        try:
            request = DownloadRequest.model_validate_json(record.request_json)
        except ValueError:
            log.warning("could not restore job %s: invalid request", record.id)
            return None
        from pathlib import Path

        job = DownloadJob(
            request,
            job_id=record.id,
            staging_dir=Path(record.staging_dir) if record.staging_dir else None,
            created_at=record.created_at,
        )
        job.status = JobStatus(record.status) if record.status in {s.value for s in RESUMABLE_STATUSES} else JobStatus.INTERRUPTED
        job.title = record.title or job.title
        job.attempts = record.attempts or 0
        with self._cond:
            self._queue.add(job)
        self._emit("added", job)
        return job

    def pause(self, job_id: str) -> None:
        with self._cond:
            job = self._queue.get(job_id)
            if job is None:
                return
            if job.status in ACTIVE_STATUSES:
                job.request_stop("pause")
            elif job.status in PENDING_STATUSES:
                self._set_status(job, JobStatus.PAUSED)

    def resume(self, job_id: str) -> None:
        with self._cond:
            job = self._queue.get(job_id)
            if job is None or job.status not in RESUMABLE_STATUSES | {JobStatus.FAILED, JobStatus.CANCELLED}:
                return
            job.error = None
            job.attempts = 0  # acao manual: renova o limite de tentativas automaticas
            self._set_status(job, JobStatus.QUEUED)
            self._cond.notify_all()

    retry = resume

    def cancel(self, job_id: str) -> None:
        with self._cond:
            job = self._queue.get(job_id)
            if job is None or job.status in FINISHED_STATUSES:
                return
            if job.status in ACTIVE_STATUSES:
                job.request_stop("cancel")
                return
            self._executor.discard(job)
            self._set_status(job, JobStatus.CANCELLED)

    def stop_live(self, job_id: str) -> None:
        """Lives: para a gravacao e salva o que ja foi gravado."""
        with self._cond:
            job = self._queue.get(job_id)
            if job is not None and job.status in ACTIVE_STATUSES:
                job.request_stop("stop")

    def remove(self, job_id: str) -> None:
        """Tira da lista (so jobs parados). Nao apaga arquivos concluidos."""
        with self._cond:
            job = self._queue.get(job_id)
            if job is None or job.status in ACTIVE_STATUSES:
                return
            if job.status in RESUMABLE_STATUSES | PENDING_STATUSES:
                self._executor.discard(job)
                if job.status != JobStatus.CANCELLED:
                    self._set_status(job, JobStatus.CANCELLED, emit=False)
            self._queue.remove(job_id)
        self._emit("removed", job)

    def clear_finished(self) -> None:
        for job in self._queue.all():
            if job.status in FINISHED_STATUSES:
                self.remove(job.id)

    def pause_all(self) -> None:
        for job in self._queue.all():
            self.pause(job.id)

    def resume_all(self) -> None:
        for job in self._queue.all():
            if job.status in RESUMABLE_STATUSES:
                self.resume(job.id)

    # ------------------------------------------------------------ consultas

    def get(self, job_id: str) -> JobSnapshot | None:
        job = self._queue.get(job_id)
        return job.snapshot(self._clock()) if job else None

    def jobs(self) -> list[JobSnapshot]:
        now = self._clock()
        return [job.snapshot(now) for job in self._queue.all()]

    def counts(self) -> dict[str, int]:
        return {
            "active": self._queue.count(ACTIVE_STATUSES),
            "pending": self._queue.count(PENDING_STATUSES),
            "paused": self._queue.count(RESUMABLE_STATUSES),
            "completed": self._queue.count({JobStatus.COMPLETED}),
            "failed": self._queue.count({JobStatus.FAILED}),
        }

    def is_idle(self) -> bool:
        return self._queue.count(ACTIVE_STATUSES | PENDING_STATUSES) == 0

    def wait_all(self, timeout: float | None = None) -> bool:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._cond:
            while not self.is_idle():
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    return False
                self._cond.wait(0.2 if remaining is None else min(0.2, remaining))
        return True

    # ------------------------------------------------------------ escalonador

    def _wake(self) -> None:
        with self._cond:
            self._cond.notify_all()

    def _loop(self) -> None:
        with self._cond:
            while not self._stopped:
                settings = self._settings.settings
                now = self._clock()
                timeout = 1.0
                if len(self._running) < settings.max_concurrent_downloads:
                    job = self._queue.next_ready(now)
                    if job is not None:
                        wait = self._last_start + settings.download_interval_seconds - now
                        if wait <= 0:
                            self._start(job)
                            continue
                        timeout = min(timeout, wait)
                    else:
                        retry_at = self._queue.next_retry_time()
                        if retry_at is not None:
                            timeout = min(timeout, max(0.05, retry_at - now))
                self._cond.wait(timeout)

    def _start(self, job: DownloadJob) -> None:
        job.reset_for_run()
        job.attempts += 1
        self._last_start = self._clock()
        self._set_status(job, JobStatus.STARTING)
        thread = threading.Thread(target=self._run_job, args=(job,), name=f"umd-job-{job.id[:8]}", daemon=True)
        self._running[job.id] = thread
        thread.start()

    def _run_job(self, job: DownloadJob) -> None:
        dlog.info("start %s attempt=%d url=%s", job.id, job.attempts, job.request.url)
        final_status = JobStatus.COMPLETED
        try:
            # se o executor terminou normalmente, o download esta concluido mesmo
            # que um "pausar/cancelar" tenha chegado no ultimo instante
            self._executor.execute(job, lambda: self._emit("updated", job))
        except BaseException as exc:  # noqa: BLE001 - qualquer falha vira status, nunca derruba o app
            error = to_user_error(exc)
            final_status = self._resolve_failure(job, error)
        finally:
            with self._cond:
                self._running.pop(job.id, None)
                if final_status == JobStatus.CANCELLED:
                    self._executor.discard(job)
                self._set_status(job, final_status)
                self._cond.notify_all()
        if final_status == JobStatus.COMPLETED:
            dlog.info("completed %s files=%d", job.id, len(job.files))
        elif final_status == JobStatus.FAILED and job.error is not None:
            dlog.error("failed %s code=%s details=%s", job.id, job.error.code.value, job.error.details[-1500:])

    def _resolve_failure(self, job: DownloadJob, error: UMDError) -> JobStatus:
        if error.code == ErrorCode.CANCELLED or job.cancel_event.is_set():
            reason = job.stop_reason
            if reason == "pause":
                dlog.info("paused %s", job.id)
                return JobStatus.PAUSED
            if reason == "shutdown":
                return JobStatus.INTERRUPTED
            dlog.info("cancelled %s", job.id)
            return JobStatus.CANCELLED
        settings = self._settings.settings
        if error.retryable and job.attempts <= settings.retries and not self._stopped:
            factor = 3 if error.code == ErrorCode.RATE_LIMITED else 1
            delay = settings.retry_delay_seconds * job.attempts * factor
            job.next_retry_at = self._clock() + delay
            job.error = error
            dlog.warning("retry %s in %.0fs (attempt %d, %s)", job.id, delay, job.attempts, error.code.value)
            return JobStatus.WAITING_RETRY
        job.error = error
        return JobStatus.FAILED

    # ------------------------------------------------------------ estado/persistencia

    def _set_status(self, job: DownloadJob, status: JobStatus, *, emit: bool = True) -> None:
        with job.lock:
            job.status = status
        self._persist(job)
        if emit:
            self._emit("updated", job)

    def _persist(self, job: DownloadJob) -> None:
        if self._repo is None:
            return
        try:
            self._repo.save(record_from_job(job))
        except Exception:  # noqa: BLE001 - falha no historico nao pode parar o download
            log.exception("could not persist job %s", job.id)


def record_from_job(job: DownloadJob) -> DownloadRecord:
    request = job.request
    files = list(job.files)
    kinds = {kind_from_ext(p.suffix).value for p in files if kind_from_ext(p.suffix).value != "document"}
    total = 0
    for path in files:
        try:
            total += path.stat().st_size
        except OSError:
            pass
    error = job.error
    finished = job.status in FINISHED_STATUSES
    quality = request.selection.quality_label()
    fmt = request.selection.format_label()
    media_exts = sorted({p.suffix.lower().lstrip(".") for p in files if p.suffix.lower() != ".txt"})
    if media_exts:
        fmt = "/".join(media_exts)[:30]  # o formato que realmente saiu (ex.: jpg de uma galeria)
        if kinds == {"image"}:
            quality = "Original" if request.selection.image_format == "original" else request.selection.image_format.upper()
    return DownloadRecord(
        id=job.id,
        url=request.url,
        status=job.status.value,
        title=job.title,
        author=request.author,
        platform=request.platform_name,
        provider=request.provider_key,
        content_type=request.content_type,
        media_kind=(next(iter(kinds)) if len(kinds) == 1 else ("mixed" if kinds else None)),
        quality=quality,
        format=fmt,
        output_dir=request.output_dir,
        file_path=str(files[0]) if files else None,
        total_size=total or None,
        file_count=len(files),
        thumbnail_url=job.thumbnail,
        error_code=error.code.value if error else None,
        error_message=error.message if error else None,
        error_details=error.details[-8000:] if error and error.details else None,
        request_json=request.model_dump_json(),
        staging_dir=str(job.staging_dir),
        attempts=job.attempts,
        created_at=job.created_at,
        completed_at=now_iso() if finished else None,
    )
