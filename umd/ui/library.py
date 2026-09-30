"""Tela Biblioteca: arquivos baixados pelo app, em grade ou lista."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from PySide6.QtCore import QAbstractListModel, QModelIndex, QPoint, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QFont, QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QComboBox,
    QHBoxLayout,
    QLineEdit,
    QListView,
    QMenu,
    QVBoxLayout,
    QWidget,
)

from ..core.config import SettingsStore
from ..core.i18n import format_day, tr
from ..database.models import FileRecord
from ..downloader.progress import human_duration, human_size
from ..services.context import AppContext
from . import theme
from .converter import is_convertible
from .widgets import Debouncer, button, confirm, copy_text, info_box, load_scaled_image, muted, open_file, page_header, reveal_in_folder, run_task

GRID_ICON = QSize(208, 117)
LIST_ICON = QSize(96, 54)
_PLACEHOLDERS = {"video": "🎬", "audio": "🎵", "image": "🖼", "document": "📄", "other": "📦"}


def _placeholder(kind: str, size: QSize) -> QPixmap:
    colors = theme.current()
    pixmap = QPixmap(size)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(colors.surface2))
    painter.setPen(Qt.PenStyle.NoPen)
    painter.drawRoundedRect(QRect(0, 0, size.width(), size.height()), 8, 8)
    font = QFont()
    font.setPointSize(max(12, size.height() // 3))
    painter.setFont(font)
    painter.setPen(QColor(colors.muted))
    painter.drawText(QRect(0, 0, size.width(), size.height()), Qt.AlignmentFlag.AlignCenter, _PLACEHOLDERS.get(kind, "📦"))
    painter.end()
    return pixmap


def _fit(image: QImage, size: QSize) -> QPixmap:
    pixmap = QPixmap.fromImage(image).scaled(size, Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                                             Qt.TransformationMode.SmoothTransformation)
    x = max(0, (pixmap.width() - size.width()) // 2)
    y = max(0, (pixmap.height() - size.height()) // 2)
    return pixmap.copy(x, y, size.width(), size.height())


class LibraryModel(QAbstractListModel):
    def __init__(self) -> None:
        super().__init__()
        self.records: list[FileRecord] = []
        self.grid = True
        self._exists: dict[str, bool] = {}
        self._pixmaps: dict[tuple[str, bool], QPixmap] = {}
        self._loading: set[tuple[str, bool]] = set()
        self._generation = 0

    def set_records(self, records: list[FileRecord]) -> None:
        self.beginResetModel()
        self.records = records
        self._exists = {r.path: Path(r.path).exists() for r in records}
        self._generation += 1
        self.endResetModel()

    def set_grid(self, grid: bool) -> None:
        self.beginResetModel()
        self.grid = grid
        self.endResetModel()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self.records)

    def _thumb_source(self, record: FileRecord) -> str | None:
        if record.media_kind == "image":
            return record.path
        if record.thumbnail_path and Path(record.thumbnail_path).exists():
            return record.thumbnail_path
        return None

    def _decoration(self, row: int, record: FileRecord) -> QPixmap:
        size = GRID_ICON if self.grid else LIST_ICON
        source = self._thumb_source(record) if self._exists.get(record.path) else None
        if not source:
            return _placeholder(record.media_kind, size)
        key = (source, self.grid)
        if key in self._pixmaps:
            return self._pixmaps[key]
        if key not in self._loading:
            self._loading.add(key)
            generation = self._generation
            run_task(
                load_scaled_image, Path(source), size,
                on_done=lambda image, k=key, r=row, g=generation: self._loaded(k, r, g, image),
                on_error=lambda _err, k=key: self._loading.discard(k),
            )
        return _placeholder(record.media_kind, size)

    def _loaded(self, key: tuple[str, bool], row: int, generation: int, image: QImage | None) -> None:
        self._loading.discard(key)
        size = GRID_ICON if key[1] else LIST_ICON
        self._pixmaps[key] = _fit(image, size) if image is not None and not image.isNull() else _placeholder("image", size)
        if generation == self._generation and 0 <= row < len(self.records):
            index = self.index(row)
            self.dataChanged.emit(index, index, [Qt.ItemDataRole.DecorationRole])

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid():
            return None
        record = self.records[index.row()]
        exists = self._exists.get(record.path, False)
        if role == Qt.ItemDataRole.DisplayRole:
            title = record.title or record.filename
            if self.grid:
                return title if exists else f"{title}\n{tr('(arquivo não encontrado)')}"
            details = [record.author or "", record.platform or "", human_size(record.size) if record.size else "",
                       human_duration(record.duration) if record.duration else "", _date(record.created_at)]
            second = "  •  ".join(d for d in details if d)
            if not exists:
                second = tr("(arquivo não encontrado)") + "  •  " + second
            return f"{title}\n{second}"
        if role == Qt.ItemDataRole.DecorationRole:
            return self._decoration(index.row(), record)
        if role == Qt.ItemDataRole.ToolTipRole:
            return "\n".join(x for x in [record.title or "", record.filename, record.author or "", record.platform or "",
                                          record.path, record.source_url or ""] if x)
        if role == Qt.ItemDataRole.ForegroundRole and not exists:
            return QColor(theme.current().muted)
        if role == Qt.ItemDataRole.UserRole:
            return record
        return None


def _date(iso: str | None) -> str:
    try:
        return format_day(datetime.fromisoformat(iso or ""))
    except ValueError:
        return ""


class LibraryPage(QWidget):
    convert_requested = Signal(list)  # caminhos dos arquivos a converter

    def __init__(self, ctx: AppContext, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.store: SettingsStore = ctx.settings_store
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 12)
        layout.setSpacing(10)
        layout.addWidget(page_header(tr("Biblioteca"), tr("Arquivos baixados pelo aplicativo.")))

        filters = QHBoxLayout()
        self.search = QLineEdit()
        self.search.setPlaceholderText(tr("Buscar por título, autor ou nome do arquivo…"))
        self.search.setClearButtonEnabled(True)
        self._debounce = Debouncer(250, self.reload, self)
        self.search.textChanged.connect(self._debounce.trigger)
        filters.addWidget(self.search, 2)
        self.kind_filter = QComboBox()
        for key, label in (("", "Todos os tipos"), ("video", "Vídeos"), ("audio", "Áudios"), ("image", "Imagens"),
                           ("document", "Documentos relacionados")):
            self.kind_filter.addItem(tr(label), key)
        self.kind_filter.currentIndexChanged.connect(self.reload)
        filters.addWidget(self.kind_filter)
        self.platform_filter = QComboBox()
        self.platform_filter.currentIndexChanged.connect(self.reload)
        filters.addWidget(self.platform_filter)
        self.period_filter = QComboBox()
        for key, label in (("all", "Qualquer data"), ("today", "Hoje"), ("7d", "Últimos 7 dias"),
                           ("30d", "Últimos 30 dias"), ("365d", "Últimos 12 meses")):
            self.period_filter.addItem(tr(label), key)
        self.period_filter.currentIndexChanged.connect(self.reload)
        filters.addWidget(self.period_filter)
        self.grid_btn = button(tr("Grade"))
        self.list_btn = button(tr("Lista"))
        group = QButtonGroup(self)
        for btn in (self.grid_btn, self.list_btn):
            btn.setCheckable(True)
            group.addButton(btn)
            filters.addWidget(btn)
        self.grid_btn.clicked.connect(lambda: self._set_view(True))
        self.list_btn.clicked.connect(lambda: self._set_view(False))
        layout.addLayout(filters)

        self.model = LibraryModel()
        self.view = QListView()
        self.view.setModel(self.model)
        self.view.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.view.setUniformItemSizes(True)
        self.view.setWordWrap(True)
        self.view.setResizeMode(QListView.ResizeMode.Adjust)
        self.view.setMovement(QListView.Movement.Static)
        self.view.doubleClicked.connect(self._open)
        self.view.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.view.customContextMenuRequested.connect(self._context_menu)
        layout.addWidget(self.view, 1)

        bottom = QHBoxLayout()
        self.count_label = muted(wrap=False)
        bottom.addWidget(self.count_label)
        bottom.addStretch(1)
        clean = button(tr("Remover arquivos ausentes"),
                       tooltip=tr("Tira da biblioteca os itens cujo arquivo foi apagado ou movido fora do app"))
        clean.clicked.connect(self._remove_missing)
        bottom.addWidget(clean)
        layout.addLayout(bottom)
        self._set_view(self.store.settings.library_view == "grid", save=False)

    def _set_view(self, grid: bool, save: bool = True) -> None:
        self.grid_btn.setChecked(grid)
        self.list_btn.setChecked(not grid)
        if grid:
            self.view.setViewMode(QListView.ViewMode.IconMode)
            self.view.setIconSize(GRID_ICON)
            self.view.setGridSize(QSize(GRID_ICON.width() + 24, GRID_ICON.height() + 62))
            self.view.setSpacing(6)
        else:
            self.view.setViewMode(QListView.ViewMode.ListMode)
            self.view.setIconSize(LIST_ICON)
            self.view.setGridSize(QSize())
            self.view.setSpacing(3)
        self.model.set_grid(grid)
        if save and self.store.settings.library_view != ("grid" if grid else "list"):
            self.store.update(library_view="grid" if grid else "list")

    def reload(self) -> None:
        current = self.platform_filter.currentData()
        self.platform_filter.blockSignals(True)
        self.platform_filter.clear()
        self.platform_filter.addItem(tr("Todas as plataformas"), "")
        for platform in self.ctx.library.platforms():
            self.platform_filter.addItem(platform, platform)
        self.platform_filter.setCurrentIndex(max(0, self.platform_filter.findData(current or "")))
        self.platform_filter.blockSignals(False)
        records = self.ctx.library.list(
            kind=self.kind_filter.currentData() or None,
            platform=self.platform_filter.currentData() or None,
            period=self.period_filter.currentData() or "all",
            search=self.search.text(),
        )
        self.model.set_records(records)
        self.count_label.setText(tr("{n} arquivo(s)", n=len(records)))

    def showEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().showEvent(event)
        self.reload()

    def _selected(self) -> list[FileRecord]:
        return [self.model.records[i.row()] for i in self.view.selectionModel().selectedIndexes()
                if i.row() < len(self.model.records)]

    def _open(self, index: QModelIndex | None = None) -> None:
        records = [self.model.records[index.row()]] if index is not None and index.isValid() else self._selected()
        if records:
            open_file(self, records[0].path)

    def _remove_missing(self) -> None:
        removed = self.ctx.library.remove_missing()
        self.reload()
        info_box(self, tr("Biblioteca"), tr("{n} item(ns) sem arquivo foram removidos da biblioteca.", n=removed))

    def _remove_selected(self) -> None:
        selected = self._selected()
        if not selected or not confirm(self, tr("Remover da biblioteca"), tr(
                "Remover {n} item(ns) da biblioteca?\nOs arquivos NÃO serão apagados do disco.", n=len(selected))):
            return
        for record in selected:
            if record.id is not None:
                self.ctx.library.remove(record.id)
        self.reload()

    def _context_menu(self, pos: QPoint) -> None:
        selected = self._selected()
        if not selected:
            return
        record = selected[0]
        menu = QMenu(self)
        entries = [
            (tr("Abrir"), lambda: open_file(self, record.path)),
            (tr("Abrir pasta"), lambda: reveal_in_folder(self, record.path)),
            (tr("Copiar caminho do arquivo"), lambda: copy_text(record.path)),
        ]
        if record.source_url:
            entries.append((tr("Copiar URL de origem"), lambda: copy_text(record.source_url or "")))
        convertible = [r.path for r in selected if Path(r.path).is_file() and is_convertible(Path(r.path))]
        if convertible:
            entries.append((tr("Converter…"), lambda: self.convert_requested.emit(convertible)))
        entries.append((tr("Remover da biblioteca"), self._remove_selected))
        for label, slot in entries:
            menu.addAction(label).triggered.connect(slot)
        menu.exec(self.view.viewport().mapToGlobal(pos))
