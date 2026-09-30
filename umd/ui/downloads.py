"""Tela Downloads: fila e progresso em tempo real."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QProgressBar,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from ..core.i18n import tr
from ..downloader.job import STAGE_LABELS, STATUS_LABELS, JobSnapshot, JobStatus
from ..downloader.manager import DownloadManager
from ..downloader.progress import human_duration, human_size, human_speed
from .bridge import ManagerBridge
from .widgets import ErrorDialog, ThumbnailLabel, button, chip, muted, open_file, page_header, reveal_in_folder, set_state


class JobWidget(QFrame):
    def __init__(self, snapshot: JobSnapshot, manager: DownloadManager, parent: QWidget | None = None):
        super().__init__(parent)
        self.manager = manager
        self.snapshot = snapshot
        self.job_id = snapshot.id
        self.setObjectName("JobCard")
        row = QHBoxLayout(self)
        row.setContentsMargins(12, 10, 12, 10)
        row.setSpacing(12)

        self.thumb = ThumbnailLabel(QSize(112, 63))
        row.addWidget(self.thumb, 0, Qt.AlignmentFlag.AlignTop)

        body = QVBoxLayout()
        body.setSpacing(4)
        head = QHBoxLayout()
        self.title = QLabel()
        self.title.setStyleSheet("font-weight: 600;")
        self.title.setMinimumWidth(100)
        head.addWidget(self.title, 1)
        self.platform = chip("")
        self.quality = chip("")
        head.addWidget(self.platform)
        head.addWidget(self.quality)
        body.addLayout(head)
        self.item_line = muted(wrap=False)
        body.addWidget(self.item_line)
        self.bar = QProgressBar()
        self.bar.setFixedHeight(8)
        self.bar.setRange(0, 1000)
        body.addWidget(self.bar)
        self.status_line = muted(wrap=False)
        body.addWidget(self.status_line)
        self.message = QLabel()
        self.message.setWordWrap(True)
        body.addWidget(self.message)
        row.addLayout(body, 1)

        actions = QVBoxLayout()
        actions.setSpacing(6)
        self.primary = button("")
        self.secondary = button("")
        self.tertiary = button("", "link")
        for btn in (self.primary, self.secondary, self.tertiary):
            btn.setMinimumWidth(130)
            actions.addWidget(btn)
        actions.addStretch(1)
        row.addLayout(actions)
        self.primary.clicked.connect(lambda: self._act("primary"))
        self.secondary.clicked.connect(lambda: self._act("secondary"))
        self.tertiary.clicked.connect(lambda: self._act("tertiary"))
        self._actions: dict[str, str] = {}
        self.update_snapshot(snapshot)

    # ------------------------------------------------------------ estado -> tela

    def update_snapshot(self, snap: JobSnapshot) -> None:
        self.snapshot = snap
        title = snap.title or snap.url
        self.title.setText(self.title.fontMetrics().elidedText(title, Qt.TextElideMode.ElideRight, max(200, self.title.width())))
        self.title.setToolTip(f"{title}\n{snap.url}")
        self.platform.setText(snap.platform)
        self.quality.setText(tr(snap.quality_label))
        if snap.thumbnail:
            self.thumb.load(snap.thumbnail)

        progress = snap.progress
        items = progress.items or 0
        if items > 1:
            label = tr("Item {i} de {n}", i=min(progress.item, items), n=items)
            if snap.item_title and snap.item_title != snap.title:
                label += f": {snap.item_title}"
            self.item_line.setText(self.item_line.fontMetrics().elidedText(label, Qt.TextElideMode.ElideRight, 520))
            self.item_line.show()
        else:
            self.item_line.hide()

        status = snap.status
        fraction = progress.fraction
        if status == JobStatus.COMPLETED:
            self.bar.setRange(0, 1000)
            self.bar.setValue(1000)
            set_state(self.bar, "state", "done")
        elif status in (JobStatus.FAILED, JobStatus.CANCELLED):
            set_state(self.bar, "state", "error")
            if self.bar.maximum() == 0:
                self.bar.setRange(0, 1000)
        elif status in (JobStatus.PAUSED, JobStatus.INTERRUPTED, JobStatus.QUEUED, JobStatus.WAITING_RETRY):
            set_state(self.bar, "state", "paused")
            self.bar.setRange(0, 1000)
            self.bar.setValue(int((fraction or 0) * 1000))
        else:
            set_state(self.bar, "state", "")
            if fraction is None or (status == JobStatus.PROCESSING and progress.stage_detail not in ("convert",)):
                self.bar.setRange(0, 0)  # indeterminado
            else:
                self.bar.setRange(0, 1000)
                self.bar.setValue(int(fraction * 1000))

        self.status_line.setText(self._status_text(snap))
        self._update_message(snap)
        self._update_actions(snap)

    def _status_text(self, snap: JobSnapshot) -> str:
        p = snap.progress
        status = snap.status
        if status == JobStatus.DOWNLOADING:
            parts = []
            if p.total:
                parts.append(f"{human_size(p.downloaded)} / {human_size(p.total)}")
            elif p.downloaded:
                parts.append(human_size(p.downloaded))
            if p.speed:
                parts.append(human_speed(p.speed))
            if p.eta is not None and p.speed:
                parts.append(tr("{eta} restantes", eta=human_duration(p.eta)))
            if snap.is_live and not p.total:
                parts.insert(0, "🔴 " + tr("Gravando ao vivo"))
            if (p.fraction or 0) > 0 and p.total:
                parts.insert(0, f"{int((p.fraction or 0) * 100)}%")
            return "  •  ".join(parts) or tr("Baixando…")
        if status == JobStatus.PROCESSING:
            return tr(STAGE_LABELS.get(p.stage_detail, "Processando…"))
        if status == JobStatus.WAITING_RETRY:
            seconds = int(snap.next_retry_in or 0)
            return tr("Nova tentativa em {s}s (tentativa {n})", s=seconds, n=snap.attempts + 1)
        if status == JobStatus.COMPLETED:
            count = len([f for f in snap.files if not f.endswith(".txt")])
            size = 0
            for path in snap.files:
                try:
                    size += Path(path).stat().st_size
                except OSError:
                    pass
            text = tr("Concluído")
            if count > 1:
                text += " • " + tr("{n} arquivos", n=count)
            if size:
                text += f" • {human_size(size)}"
            return text
        return tr(STATUS_LABELS.get(status, status.value))

    def _update_message(self, snap: JobSnapshot) -> None:
        if snap.status == JobStatus.FAILED and snap.error is not None:
            self.message.setObjectName("ErrorText")
            self.message.setText(tr(snap.error.message))
        elif snap.status == JobStatus.WAITING_RETRY and snap.error is not None:
            self.message.setObjectName("WarningText")
            self.message.setText(tr(snap.error.message))
        elif snap.warning:
            self.message.setObjectName("WarningText")
            self.message.setText("⚠ " + snap.warning)
        elif snap.status == JobStatus.INTERRUPTED:
            self.message.setObjectName("WarningText")
            self.message.setText(tr("O aplicativo foi fechado durante este download. Clique em Retomar para continuar."))
        else:
            self.message.setText("")
        self.message.style().unpolish(self.message)
        self.message.style().polish(self.message)
        self.message.setVisible(bool(self.message.text()))

    def _update_actions(self, snap: JobSnapshot) -> None:
        status = snap.status
        if status in (JobStatus.STARTING, JobStatus.DOWNLOADING, JobStatus.PROCESSING):
            if snap.is_live:
                actions = {"primary": "stop_live", "secondary": "cancel"}
            else:
                actions = {"primary": "pause", "secondary": "cancel"}
        elif status in (JobStatus.QUEUED, JobStatus.WAITING_RETRY):
            actions = {"primary": "pause", "secondary": "cancel"}
        elif status in (JobStatus.PAUSED, JobStatus.INTERRUPTED):
            actions = {"primary": "resume", "secondary": "cancel"}
        elif status == JobStatus.FAILED:
            actions = {"primary": "retry", "secondary": "details", "tertiary": "remove"}
        elif status == JobStatus.CANCELLED:
            actions = {"primary": "restart", "tertiary": "remove"}
        else:  # concluido
            actions = {"primary": "open", "secondary": "folder", "tertiary": "remove"}
        labels = {
            "pause": tr("Pausar"), "resume": tr("Retomar"), "cancel": tr("Cancelar"), "retry": tr("Tentar novamente"),
            "restart": tr("Reiniciar"), "details": tr("Ver detalhes"), "remove": tr("Remover da lista"),
            "open": tr("Abrir arquivo"), "folder": tr("Abrir pasta"), "stop_live": tr("Parar e salvar"),
        }
        self._actions = actions
        for slot, btn in (("primary", self.primary), ("secondary", self.secondary), ("tertiary", self.tertiary)):
            action = actions.get(slot)
            btn.setVisible(action is not None)
            if action:
                btn.setText(labels[action])
                btn.setProperty("variant", "danger" if action == "cancel" else ("link" if slot == "tertiary" else ""))
                btn.style().unpolish(btn)
                btn.style().polish(btn)

    # ------------------------------------------------------------ acoes

    def _act(self, slot: str) -> None:
        action = self._actions.get(slot)
        snap = self.snapshot
        if action == "pause":
            self.manager.pause(snap.id)
        elif action in ("resume", "retry", "restart"):
            self.manager.resume(snap.id)
        elif action == "cancel":
            self.manager.cancel(snap.id)
        elif action == "stop_live":
            self.manager.stop_live(snap.id)
        elif action == "remove":
            self.manager.remove(snap.id)
        elif action == "details" and snap.error is not None:
            ErrorDialog(snap.error, tr("Detalhes do erro"), self).exec()
        elif action == "open":
            media = [f for f in snap.files if not f.endswith(".txt")] or list(snap.files)
            if len(media) == 1:
                open_file(self, media[0])
            elif media:
                reveal_in_folder(self, media[0])
        elif action == "folder":
            target = snap.files[0] if snap.files else snap.output_dir
            reveal_in_folder(self, target)


class DownloadsPage(QWidget):
    counts_changed = Signal(dict)

    def __init__(self, manager: DownloadManager, bridge: ManagerBridge, open_downloads_folder, parent: QWidget | None = None):
        super().__init__(parent)
        self.manager = manager
        self._widgets: dict[str, JobWidget] = {}
        self._pending: dict[str, JobSnapshot] = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(28, 24, 28, 12)
        outer.setSpacing(10)
        header = QHBoxLayout()
        header.addWidget(page_header(tr("Downloads"), tr("Acompanhe a fila e o progresso em tempo real.")), 1)
        pause_all = button(tr("Pausar todos"))
        pause_all.clicked.connect(manager.pause_all)
        resume_all = button(tr("Retomar todos"))
        resume_all.clicked.connect(manager.resume_all)
        clear = button(tr("Limpar concluídos"))
        clear.clicked.connect(manager.clear_finished)
        folder = button(tr("Abrir pasta de downloads"))
        folder.clicked.connect(open_downloads_folder)
        for btn in (pause_all, resume_all, clear, folder):
            header.addWidget(btn, 0, Qt.AlignmentFlag.AlignBottom)
        outer.addLayout(header)
        self.summary = muted(wrap=False)
        outer.addWidget(self.summary)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        container = QWidget()
        self.list_layout = QVBoxLayout(container)
        self.list_layout.setContentsMargins(0, 0, 6, 0)
        self.list_layout.setSpacing(10)
        self.empty = QLabel(tr("Nenhum download por aqui ainda.\nCole uma URL na tela Início para começar."))
        self.empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty.setObjectName("Muted")
        self.empty.setMinimumHeight(200)
        self.list_layout.addWidget(self.empty)
        self.list_layout.addStretch(1)
        scroll.setWidget(container)
        outer.addWidget(scroll, 1)

        bridge.job_added.connect(self._on_added)
        bridge.job_updated.connect(self._on_updated)
        bridge.job_removed.connect(self._on_removed)
        # agrupa atualizacoes de progresso (ate ~6 por segundo por job)
        self._timer = QTimer(self)
        self._timer.setInterval(160)
        self._timer.timeout.connect(self._flush)
        self._timer.start()
        for snap in manager.jobs():
            self._on_added(snap)

    def _on_added(self, snap: JobSnapshot) -> None:
        if snap.id in self._widgets:
            return
        widget = JobWidget(snap, self.manager)
        self._widgets[snap.id] = widget
        self.list_layout.insertWidget(0, widget)  # mais recente em cima
        self.empty.hide()
        self._refresh_summary()

    def _on_updated(self, snap: JobSnapshot) -> None:
        self._pending[snap.id] = snap

    def _on_removed(self, job_id: str) -> None:
        self._pending.pop(job_id, None)
        widget = self._widgets.pop(job_id, None)
        if widget is not None:
            widget.setParent(None)
            widget.deleteLater()
        self.empty.setVisible(not self._widgets)
        self._refresh_summary()

    def _flush(self) -> None:
        if not self._pending:
            # contagem regressiva do retry
            for widget in self._widgets.values():
                if widget.snapshot.status == JobStatus.WAITING_RETRY:
                    snap = self.manager.get(widget.job_id)
                    if snap:
                        widget.update_snapshot(snap)
            return
        pending, self._pending = self._pending, {}
        for job_id, snap in pending.items():
            widget = self._widgets.get(job_id)
            if widget is None:
                self._on_added(snap)
            else:
                widget.update_snapshot(snap)
        self._refresh_summary()

    def _refresh_summary(self) -> None:
        counts = self.manager.counts()
        parts = []
        if counts["active"]:
            parts.append(tr("{n} baixando", n=counts["active"]))
        if counts["pending"]:
            parts.append(tr("{n} na fila", n=counts["pending"]))
        if counts["paused"]:
            parts.append(tr("{n} pausado(s)", n=counts["paused"]))
        if counts["completed"]:
            parts.append(tr("{n} concluído(s)", n=counts["completed"]))
        if counts["failed"]:
            parts.append(tr("{n} com falha", n=counts["failed"]))
        self.summary.setText("  •  ".join(parts) if parts else "")
        self.counts_changed.emit(counts)
