"""Fila ordenada de jobs (thread-safe)."""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Iterable

from .job import DownloadJob, JobStatus


class JobQueue:
    def __init__(self) -> None:
        self._jobs: OrderedDict[str, DownloadJob] = OrderedDict()
        self._lock = threading.RLock()

    def add(self, job: DownloadJob) -> None:
        with self._lock:
            self._jobs[job.id] = job

    def get(self, job_id: str) -> DownloadJob | None:
        with self._lock:
            return self._jobs.get(job_id)

    def remove(self, job_id: str) -> DownloadJob | None:
        with self._lock:
            return self._jobs.pop(job_id, None)

    def all(self) -> list[DownloadJob]:
        with self._lock:
            return list(self._jobs.values())

    def next_ready(self, now: float) -> DownloadJob | None:
        """Primeiro job que pode comecar agora (ordem de chegada)."""
        with self._lock:
            for job in self._jobs.values():
                if job.status == JobStatus.QUEUED:
                    return job
                if job.status == JobStatus.WAITING_RETRY and (job.next_retry_at or 0) <= now:
                    return job
            return None

    def next_retry_time(self) -> float | None:
        with self._lock:
            times = [j.next_retry_at for j in self._jobs.values()
                     if j.status == JobStatus.WAITING_RETRY and j.next_retry_at is not None]
            return min(times) if times else None

    def count(self, statuses: Iterable[JobStatus]) -> int:
        wanted = set(statuses)
        with self._lock:
            return sum(1 for job in self._jobs.values() if job.status in wanted)

    def move(self, job_id: str, to_front: bool) -> None:
        with self._lock:
            if job_id in self._jobs:
                self._jobs.move_to_end(job_id, last=not to_front)

    def __len__(self) -> int:
        with self._lock:
            return len(self._jobs)
