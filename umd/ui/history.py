"""Tela Historico: todos os downloads registrados no SQLite."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from PySide6.QtCore import QAbstractTableModel, QModelIndex, QPoint, Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLineEdit,
    QMenu,
    QTableView,
    QVBoxLayout,
    QWidget,
)

from ..core.exceptions import UMDError
from ..core.i18n import format_day, tr
from ..database.models import DownloadRecord
from ..downloader.job import STATUS_LABELS, JobStatus
from ..downloader.progress import human_size
from ..media.metadata import CONTENT_TYPE_LABELS, ContentType
from ..services.context import AppContext
from . import theme
from .widgets import Debouncer, ErrorDialog, button, confirm, copy_text, info_box, muted, open_file, page_header, reveal_in_folder, show_error

_STATUS_ICON = {
    "completed": "✓", "failed": "✕", "cancelled": "⦸", "interrupted": "⏸", "paused": "⏸",
    "queued": "…", "starting": "…", "downloading": "⬇", "processing": "⚙", "waiting_retry": "↻",
}
_KIND_LABELS = {"video": "Vídeo", "audio": "Áudio", "image": "Imagem", "mixed": "Vários", "document": "Documento"}


def _format_date(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        return format_day(datetime.fromisoformat(iso), with_time=True)
    except ValueError:
        return iso


class HistoryModel(QAbstractTableModel):
    HEADERS = ["Título", "Plataforma", "Tipo", "Data", "Qualidade", "Formato", "Tamanho", "Status"]

    def __init__(self) -> None:
        super().__init__()
        self.records: list[DownloadRecord] = []

    def set_records(self, records: list[DownloadRecord]) -> None:
        self.beginResetModel()
        self.records = records
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self.records)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return len(self.HEADERS)

    def headerData(self, section: int, orientation: Qt.Orientation, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if orientation == Qt.Orientation.Horizontal and role == Qt.ItemDataRole.DisplayRole:
            return tr(self.HEADERS[section])
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        record = self.records[index.row()]
        column = index.column()
        if role == Qt.ItemDataRole.DisplayRole:
            if column == 0:
                return record.title or record.url
            if column == 1:
                return record.platform or ""
            if column == 2:
                if record.media_kind:
                    return tr(_KIND_LABELS.get(record.media_kind, record.media_kind))
                try:
                    return tr(CONTENT_TYPE_LABELS[ContentType(record.content_type)])
                except (ValueError, KeyError):
                    return ""
            if column == 3:
                return _format_date(record.completed_at or record.created_at)
            if column == 4:
                return tr(record.quality or "")
            if column == 5:
                return (record.format or "").upper()
            if column == 6:
                return human_size(record.total_size) if record.total_size else ""
            if column == 7:
                try:
                    label = tr(STATUS_LABELS[JobStatus(record.status)])
                except (ValueError, KeyError):
                    label = record.status
                return f"{_STATUS_ICON.get(record.status, '')} {label}"
        if role == Qt.ItemDataRole.ForegroundRole and column == 7:
            colors = theme.current()
            if record.status == "completed":
                return QColor(colors.success)
            if record.status == "failed":
                return QColor(colors.danger)
            if record.status in ("cancelled", "interrupted", "paused"):
                return QColor(colors.warning)
        if role == Qt.ItemDataRole.ToolTipRole:
            lines = [record.title or "", record.url]
            if record.file_path:
                lines.append(record.file_path)
            if record.error_message:
                lines.append(tr(record.error_message))
            return "\n".join(line for line in lines if line)
        return None


class HistoryPage(QWidget):
    redownloaded = Signal()

    def __init__(self, ctx: AppContext, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 12)
        layout.setSpacing(10)
        layout.addWidget(page_header(tr("Histórico"), tr("Todos os downloads feitos pelo aplicativo.")))

        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Buscar por título, autor ou URL…"))
        self.search.setClearButtonEnabled(True)
        self._debounce = Debouncer(250, self.reload, self)
        self.search.textChanged.connect(self._debounce.trigger)
        filters.addWidget(self.search, 2)
        self.status_filter = QComboBox()
        for key, label in (("", "Todos os status"), ("completed", "Concluídos"), ("failed", "Com falha"),
                           ("cancelled", "Cancelados"), ("interrupted", "Interrompidos"), ("paused", "Pausados")):
            self.status_filter.addItem(tr(label), key)
        self.status_filter.currentIndexChanged.connect(self.reload)
        filters.addWidget(self.status_filter)
        self.platform_filter = QComboBox()
        self.platform_filter.currentIndexChanged.connect(self.reload)
        filters.addWidget(self.platform_filter)
        layout.addLayout(filters)

        self.model = HistoryModel()
        self.table = QTableView()
        self.table.setModel(self.model)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        self.table.setShowGrid(False)
        self.table.setWordWrap(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for column in range(1, self.model.columnCount()):
            header.setSectionResizeMode(column, QHeaderView.ResizeMode.ResizeToContents)
        self.table.doubleClicked.connect(lambda _index: self._open_file())
        self.table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self._context_menu)
        self.table.selectionModel().selectionChanged.connect(lambda *_: self._update_buttons())
        layout.addWidget(self.table, 1)

        actions = QHBoxLayout()
        self.open_btn = button(tr("Abrir arquivo"))
        self.open_btn.clicked.connect(self._open_file)
        self.folder_btn = button(tr("Abrir pasta"))
        self.folder_btn.clicked.connect(self._open_folder)
        self.copy_btn = button(tr("Copiar URL"))
        self.copy_btn.clicked.connect(self._copy_url)
        self.again_btn = button(tr("Baixar novamente"))
        self.again_btn.clicked.connect(self._download_again)
        self.error_btn = button(tr("Ver erro"))
        self.error_btn.clicked.connect(self._show_error)
        self.remove_btn = button(tr("Remover registro"), "danger")
        self.remove_btn.clicked.connect(self._remove)
        for btn in (self.open_btn, self.folder_btn, self.copy_btn, self.again_btn, self.error_btn):
            actions.addWidget(btn)
        actions.addStretch(1)
        self.count_label = muted(wrap=False)
        actions.addWidget(self.count_label)
        actions.addWidget(self.remove_btn)
        layout.addLayout(actions)
        self._update_buttons()

    # ------------------------------------------------------------ dados

    def reload(self) -> None:
        current_platform = self.platform_filter.currentData()
        self.platform_filter.blockSignals(True)
        self.platform_filter.clear()
        self.platform_filter.addItem(tr("Todas as plataformas"), "")
        for platform in self.ctx.downloads.platforms():
            self.platform_filter.addItem(platform, platform)
        self.platform_filter.setCurrentIndex(max(0, self.platform_filter.findData(current_platform or "")))
        self.platform_filter.blockSignals(False)
        records = self.ctx.downloads.list(
            search=self.search.text(),
            status=self.status_filter.currentData() or None,
            platform=self.platform_filter.currentData() or None,
        )
        # downloads ainda na fila/andamento aparecem na tela Downloads; aqui so o que terminou
        records = [r for r in records if r.status in ("completed", "failed", "cancelled", "interrupted", "paused")]
        self.model.set_records(records)
        self.count_label.setText(tr("{n} registro(s)", n=len(records)))
        self._update_buttons()

    def showEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().showEvent(event)
        self.reload()

    def _selected(self) -> list[DownloadRecord]:
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()})
        return [self.model.records[r] for r in rows if r < len(self.model.records)]

    def _update_buttons(self) -> None:
        selected = self._selected()
        one = selected[0] if len(selected) == 1 else None
        has_file = bool(one and one.file_path)
        self.open_btn.setEnabled(has_file)
        self.folder_btn.setEnabled(bool(one and (one.file_path or one.output_dir)))
        self.copy_btn.setEnabled(bool(selected))
        self.again_btn.setEnabled(bool(selected))
        self.error_btn.setEnabled(bool(one and one.error_code))
        self.remove_btn.setEnabled(bool(selected))

    # ------------------------------------------------------------ acoes

    def _open_file(self) -> None:
        selected = self._selected()
        if not selected:
            return
        record = selected[0]
        if record.file_path:
            open_file(self, record.file_path)
        elif record.error_code:
            self._show_error()

    def _open_folder(self) -> None:
        selected = self._selected()
        if selected:
            record = selected[0]
            reveal_in_folder(self, record.file_path or record.output_dir or "")

    def _copy_url(self) -> None:
        copy_text("\n".join(r.url for r in self._selected()))

    def _download_again(self) -> None:
        count = 0
        for record in self._selected():
            try:
                self.ctx.download.redownload(record)
                count += 1
            except UMDError as exc:
                show_error(self, exc, tr("Não foi possível baixar novamente"))
                break
        if count:
            info_box(self, tr("Adicionado"), tr("{n} download(s) adicionado(s) à fila.", n=count))
            self.redownloaded.emit()

    def _show_error(self) -> None:
        selected = self._selected()
        if not selected or not selected[0].error_code:
            return
        record = selected[0]
        error = UMDError(record.error_code, details=record.error_details or "")
        ErrorDialog(error, tr("Detalhes do erro"), self).exec()

    def _remove(self) -> None:
        selected = self._selected()
        if not selected:
            return
        if not confirm(self, tr("Remover registros"), tr(
                "Remover {n} registro(s) do histórico?\nOs arquivos baixados NÃO serão apagados.", n=len(selected))):
            return
        for record in selected:
            if record.status in ("paused", "interrupted"):
                self.ctx.manager.remove(record.id)  # descarta parciais
            self.ctx.downloads.delete(record.id)
        self.reload()

    def _context_menu(self, pos: QPoint) -> None:
        if not self._selected():
            return
        menu = QMenu(self)
        for label, slot, enabled in (
            (tr("Abrir arquivo"), self._open_file, self.open_btn.isEnabled()),
            (tr("Abrir pasta"), self._open_folder, self.folder_btn.isEnabled()),
            (tr("Copiar URL"), self._copy_url, True),
            (tr("Baixar novamente"), self._download_again, True),
            (tr("Ver erro"), self._show_error, self.error_btn.isEnabled()),
            (tr("Remover registro"), self._remove, True),
        ):
            action = menu.addAction(label)
            action.setEnabled(enabled)
            action.triggered.connect(slot)
        menu.exec(self.table.viewport().mapToGlobal(pos))
