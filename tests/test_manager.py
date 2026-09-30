"""DownloadManager com executor falso: fila, limite, cancelamento, pausa, retry."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from umd.core.exceptions import ErrorCode, UMDError
from umd.core.naming import DEFAULT_TEMPLATE
from umd.database.repository import DownloadRepository
from umd.downloader.job import DownloadRequest, JobStatus
from umd.downloader.manager import DownloadManager, record_from_job
from umd.media.formats import DownloadSelection


class FakeExecutor:
    def __init__(self, behavior):
        self.behavior = behavior
        self.lock = threading.Lock()
        self.running = 0
        self.max_running = 0
        self.started: list[str] = []
        self.start_times: list[float] = []
        self.discarded: list[str] = []

    def execute(self, job, report):
        with self.lock:
            self.running += 1
            self.max_running = max(self.max_running, self.running)
            self.started.append(job.request.url)
            self.start_times.append(time.monotonic())
        try:
            self.behavior(job, report)
        finally:
            with self.lock:
                self.running -= 1

    def discard(self, job):
        self.discarded.append(job.id)


def slow(seconds: float):
    def run(job, report):
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if job.cancel_event.is_set():
                raise UMDError(ErrorCode.CANCELLED)
            job.progress.downloaded += 1
            report()
            time.sleep(0.01)
    return run


def request(tmp_path: Path, i: int) -> DownloadRequest:
    return DownloadRequest(url=f"https://example.com/{i}", engines=["yt-dlp"], selection=DownloadSelection(),
                           output_dir=str(tmp_path), filename_template=DEFAULT_TEMPLATE, title=f"Job {i}")


def wait_for(predicate, timeout: float = 10.0) -> None:
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if predicate():
            return
        time.sleep(0.02)
    raise AssertionError("condição não atingida a tempo")


@pytest.fixture
def make_manager(settings_store, db):
    managers = []

    def factory(behavior, **settings):
        if settings:
            settings_store.update(**settings)
        executor = FakeExecutor(behavior)
        manager = DownloadManager(executor, settings_store, DownloadRepository(db))
        manager.start()
        managers.append(manager)
        return manager, executor

    yield factory
    for manager in managers:
        manager.shutdown(timeout=5)


def test_concurrency_limit_and_fifo_order(make_manager, tmp_path) -> None:
    manager, executor = make_manager(slow(0.25), max_concurrent_downloads=2)
    jobs = [manager.enqueue(request(tmp_path, i)) for i in range(5)]
    assert manager.wait_all(timeout=15)
    assert executor.max_running == 2
    assert executor.started == [f"https://example.com/{i}" for i in range(5)]
    assert all(manager.get(j.id).status == JobStatus.COMPLETED for j in jobs)


def test_cancel_running_job_discards_partials(make_manager, tmp_path) -> None:
    manager, executor = make_manager(slow(5))
    job = manager.enqueue(request(tmp_path, 1))
    wait_for(lambda: manager.get(job.id).status in (JobStatus.STARTING, JobStatus.DOWNLOADING) and executor.running)
    manager.cancel(job.id)
    wait_for(lambda: manager.get(job.id).status == JobStatus.CANCELLED)
    assert job.id in executor.discarded


def test_cancel_queued_job(make_manager, tmp_path) -> None:
    manager, executor = make_manager(slow(1), max_concurrent_downloads=1)
    first = manager.enqueue(request(tmp_path, 1))
    second = manager.enqueue(request(tmp_path, 2))
    manager.cancel(second.id)
    assert manager.get(second.id).status == JobStatus.CANCELLED
    manager.cancel(first.id)
    assert manager.wait_all(timeout=10)
    assert "https://example.com/2" not in executor.started


def test_pause_keeps_partials_and_resume_finishes(make_manager, tmp_path) -> None:
    manager, executor = make_manager(slow(0.6))
    job = manager.enqueue(request(tmp_path, 1))
    wait_for(lambda: executor.running == 1)
    manager.pause(job.id)
    wait_for(lambda: manager.get(job.id).status == JobStatus.PAUSED)
    assert job.id not in executor.discarded
    manager.resume(job.id)
    wait_for(lambda: manager.get(job.id).status == JobStatus.COMPLETED)
    assert executor.started.count("https://example.com/1") == 2


def test_pause_queued_job(make_manager, tmp_path) -> None:
    manager, _ = make_manager(slow(1), max_concurrent_downloads=1)
    manager.enqueue(request(tmp_path, 1))
    queued = manager.enqueue(request(tmp_path, 2))
    manager.pause(queued.id)
    assert manager.get(queued.id).status == JobStatus.PAUSED


def test_retry_on_temporary_failure(make_manager, tmp_path) -> None:
    attempts = {"n": 0}

    def flaky(job, report):
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise UMDError(ErrorCode.NETWORK, details="connection reset")

    manager, _ = make_manager(flaky, retries=3, retry_delay_seconds=0)
    job = manager.enqueue(request(tmp_path, 1))
    wait_for(lambda: manager.get(job.id).status == JobStatus.COMPLETED)
    assert manager.get(job.id).attempts == 3


def test_no_retry_for_permanent_errors(make_manager, tmp_path) -> None:
    def private(job, report):
        raise UMDError(ErrorCode.PRIVATE, details="Private video")

    manager, _ = make_manager(private, retries=5)
    job = manager.enqueue(request(tmp_path, 1))
    wait_for(lambda: manager.get(job.id).status == JobStatus.FAILED)
    snap = manager.get(job.id)
    assert snap.attempts == 1 and snap.error.code == ErrorCode.PRIVATE


def test_retries_exhausted(make_manager, tmp_path) -> None:
    def always_down(job, report):
        raise UMDError(ErrorCode.TIMEOUT)

    manager, executor = make_manager(always_down, retries=2, retry_delay_seconds=0)
    job = manager.enqueue(request(tmp_path, 1))
    wait_for(lambda: manager.get(job.id).status == JobStatus.FAILED)
    assert len(executor.started) == 3


def test_unexpected_exception_becomes_friendly_failure(make_manager, tmp_path) -> None:
    def boom(job, report):
        raise RuntimeError("algo inesperado")

    manager, _ = make_manager(boom, retries=0)
    job = manager.enqueue(request(tmp_path, 1))
    wait_for(lambda: manager.get(job.id).status == JobStatus.FAILED)
    error = manager.get(job.id).error
    assert isinstance(error, UMDError) and "RuntimeError" in error.details


def test_interval_between_downloads(make_manager, tmp_path) -> None:
    manager, executor = make_manager(lambda job, report: None, download_interval_seconds=0.4)
    manager.enqueue(request(tmp_path, 1))
    manager.enqueue(request(tmp_path, 2))
    assert manager.wait_all(timeout=10)
    assert executor.start_times[1] - executor.start_times[0] >= 0.35


def test_settings_change_raises_concurrency_live(make_manager, settings_store, tmp_path) -> None:
    manager, executor = make_manager(slow(0.8), max_concurrent_downloads=1)
    for i in range(3):
        manager.enqueue(request(tmp_path, i))
    wait_for(lambda: executor.running == 1)
    settings_store.update(max_concurrent_downloads=3)
    wait_for(lambda: executor.max_running == 3)


def test_shutdown_marks_interrupted_and_persists(settings_store, db, tmp_path) -> None:
    repo = DownloadRepository(db)
    executor = FakeExecutor(slow(10))
    manager = DownloadManager(executor, settings_store, repo)
    manager.start()
    settings_store.update(max_concurrent_downloads=1)
    running = manager.enqueue(request(tmp_path, 1))
    pending = manager.enqueue(request(tmp_path, 2))
    wait_for(lambda: executor.running == 1)
    manager.shutdown(timeout=5)
    assert repo.get(running.id).status == "interrupted"
    assert repo.get(pending.id).status == "interrupted"
    assert executor.discarded == []  # parciais preservados para retomar


def test_restore_and_resume_previous_session(settings_store, db, tmp_path) -> None:
    repo = DownloadRepository(db)
    first = DownloadManager(FakeExecutor(slow(10)), settings_store, repo)
    first.start()
    job = first.enqueue(request(tmp_path, 1))
    wait_for(lambda: first.get(job.id).status in (JobStatus.STARTING, JobStatus.DOWNLOADING))
    first.shutdown(timeout=5)

    executor = FakeExecutor(lambda j, r: None)
    second = DownloadManager(executor, settings_store, repo)
    second.start()
    try:
        for record in repo.list_resumable():
            second.restore(record)
        assert second.get(job.id).status == JobStatus.INTERRUPTED
        second.resume(job.id)
        wait_for(lambda: second.get(job.id).status == JobStatus.COMPLETED)
        assert repo.get(job.id).status == "completed"
    finally:
        second.shutdown(timeout=5)


def test_listeners_and_remove(make_manager, tmp_path) -> None:
    events = []
    manager, _ = make_manager(lambda job, report: report())
    manager.add_listener(lambda event, snap: events.append((event, snap.status)))
    job = manager.enqueue(request(tmp_path, 1))
    wait_for(lambda: manager.get(job.id).status == JobStatus.COMPLETED)
    manager.remove(job.id)
    kinds = [e for e, _ in events]
    assert kinds[0] == "added" and "updated" in kinds and kinds[-1] == "removed"
    assert manager.get(job.id) is None


def test_record_from_job_serializes_request(tmp_path) -> None:
    from umd.downloader.job import DownloadJob

    job = DownloadJob(request(tmp_path, 1))
    record = record_from_job(job)
    restored = DownloadRequest.model_validate_json(record.request_json)
    assert restored.url == "https://example.com/1" and record.status == "queued"
