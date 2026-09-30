"""Leva os eventos do DownloadManager (threads de download) para a thread
da interface, via sinais Qt (entrega enfileirada, thread-safe)."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal

from ..downloader.job import JobSnapshot
from ..downloader.manager import DownloadManager


class ManagerBridge(QObject):
    job_added = Signal(object)  # JobSnapshot
    job_updated = Signal(object)  # JobSnapshot
    job_removed = Signal(str)
    job_finished = Signal(object)  # JobSnapshot (concluido/falhou/cancelado)

    def __init__(self, manager: DownloadManager, parent: QObject | None = None):
        super().__init__(parent)
        self.manager = manager
        self._finished_seen: set[str] = set()
        manager.add_listener(self._on_event)

    def _on_event(self, event: str, snapshot: JobSnapshot) -> None:
        if event == "added":
            self.job_added.emit(snapshot)
        elif event == "removed":
            self.job_removed.emit(snapshot.id)
        else:
            self.job_updated.emit(snapshot)
        if snapshot.is_finished:
            key = f"{snapshot.id}:{snapshot.status.value}:{snapshot.attempts}"
            if key not in self._finished_seen:
                self._finished_seen.add(key)
                self.job_finished.emit(snapshot)
