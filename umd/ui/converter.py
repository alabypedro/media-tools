"""Tela Converter: converte arquivos locais (video, audio, imagem, PDF,
texto/Markdown, documentos do Office e compactados).

Usa o motor de umd.convert (fila, conversores, historico). A conversao
roda numa thread separada; o progresso volta para a interface por sinal
Qt (entregue na thread da interface), entao a janela nunca trava.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PySide6.QtCore import QObject, QSize, Qt, Signal
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent, QImage, QImageReader, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QProgressBar,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..convert.core import registry
from ..convert.core.config import Settings, load_settings, save_settings
from ..convert.core.errors import ConversionError
from ..convert.core.history import History
from ..convert.core.paths import ConflictPolicy
from ..convert.core.queue import ConversionQueue, Job, JobStatus, merge_images_to_pdf, run_queue
from ..core.i18n import format_day, tr
from ..core.logger import get_logger
from ..downloader.progress import human_size
from ..services.context import AppContext
from . import theme
from .widgets import button, card, confirm, info_box, muted, page_header, reveal_in_folder

log = get_logger("converter")

PREVIEW_SIZE = QSize(220, 220)

STATUS_LABELS = {
    JobStatus.WAITING: "Aguardando",
    JobStatus.RUNNING: "Convertendo…",
    JobStatus.DONE: "Concluído",
    JobStatus.ERROR: "Erro",
    JobStatus.CANCELLED: "Cancelado",
    JobStatus.SKIPPED: "Ignorado (já existe)",
}
FINISHED = (JobStatus.DONE, JobStatus.ERROR, JobStatus.CANCELLED, JobStatus.SKIPPED)
# "ask" (perguntar) nao existe numa fila em segundo plano: equivale a "criar copia"
CONFLICT_LABELS = {
    ConflictPolicy.RENAME.value: "Criar cópia: nome (1).ext",
    ConflictPolicy.OVERWRITE.value: "Sobrescrever",
    ConflictPolicy.SKIP.value: "Pular o arquivo",
}
CATEGORY_LABELS = {
    "video": "Vídeo", "audio": "Áudio", "image": "Imagem", "document": "Documento", "spreadsheet": "Planilha",
    "presentation": "Apresentação", "text": "Texto", "markdown": "Markdown", "pdf": "PDF", "archive": "Compactado",
}
FONT_LABELS = {"helvetica": "Helvetica", "times": "Times", "courier": "Courier"}
PAGE_SIZE_LABELS = {"a4": "A4", "letter": "Carta", "a3": "A3", "original": "Tamanho da imagem"}
ORIENTATION_LABELS = {"portrait": "Retrato", "landscape": "Paisagem"}
FIT_LABELS = {"contain": "Caber inteira", "cover": "Preencher a página", "stretch": "Esticar"}


# formato sugerido ao montar a fila (o primeiro desta lista que servir para todos os arquivos)
PREFERRED_TARGETS = ("mp4", "mp3", "jpg", "png", "pdf", "docx", "xlsx", "pptx", "zip")


def target_label(ext: str) -> str:
    """Rotulo do formato de saida (ex.: 'JPG'; o destino especial 'flatten' vira 'PDF achatado')."""
    return tr("PDF achatado") if ext == registry.FLATTEN_TARGET else ext.upper()


def is_convertible(path: Path) -> bool:
    """Tem pelo menos um formato de saida na fila (ex.: .tar.gz nao tem: a fila usa so a ultima extensao)."""
    return bool(registry.compatible_targets(path.suffix))


def _combo(labels: dict[str, str], current: str) -> QComboBox:
    combo = QComboBox()
    for key, label in labels.items():
        combo.addItem(tr(label), key)
    combo.setCurrentIndex(max(0, combo.findData(current)))
    return combo


def _warn(parent: QWidget | None, title: str, text: str) -> None:
    QMessageBox.warning(parent, title, text)


@dataclass
class ConvertEntry:
    path: Path
    root: Path | None = None  # pasta adicionada (para preservar subpastas no destino)
    status: JobStatus = JobStatus.WAITING
    outputs: list[Path] = field(default_factory=list)
    error: str | None = None


class _Signals(QObject):
    progress = Signal(object)  # (job_id, status, outputs, error, nome, destino)
    finished = Signal()


class ConverterPage(QWidget):
    COLUMNS = ["Arquivo", "Tipo", "Tamanho", "Destino", "Status"]

    def __init__(self, ctx: AppContext, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.settings: Settings = load_settings()
        self._history: History | None = None
        self.entries: list[ConvertEntry] = []
        self.options: dict[str, Any] = {}  # ajustes de "Opcoes avancadas" (valem para a proxima conversao)
        self._last_output_dir: Path | None = None
        self._cancel = threading.Event()
        self._pause = threading.Event()
        self._worker: threading.Thread | None = None
        self._entry_by_job: dict[int, ConvertEntry] = {}
        self._total = 0
        self._done = 0
        self._signals = _Signals(self)
        self._signals.progress.connect(self._on_progress)
        self._signals.finished.connect(self._on_finished)
        self.setAcceptDrops(True)
        self._build()
        self._refresh_formats()
        self._update_buttons()

    # ------------------------------------------------------------ layout

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 12)
        layout.setSpacing(10)

        top = QHBoxLayout()
        top.addWidget(page_header(tr("Converter"), tr(
            "Converta vídeos, áudios, imagens, PDFs, documentos e arquivos compactados. "
            "Os originais nunca são alterados.")), 1)
        for label, slot in ((tr("Formatos suportados"), self._show_formats),
                            (tr("Histórico de conversões"), self._show_history),
                            (tr("Configurações de conversão"), self._open_settings)):
            link = button(label, "link")
            link.clicked.connect(slot)
            top.addWidget(link, 0, Qt.AlignmentFlag.AlignTop)
        layout.addLayout(top)

        drop, drop_layout = card()
        row = QHBoxLayout()
        row.addWidget(QLabel("📂"))
        row.addWidget(muted(tr("Arraste arquivos ou pastas para esta tela, ou use os botões ao lado."), wrap=False), 1)
        self.add_files_btn = button(tr("Adicionar arquivos…"))
        self.add_files_btn.clicked.connect(self.pick_files)
        self.add_folder_btn = button(tr("Adicionar pasta…"))
        self.add_folder_btn.clicked.connect(self.pick_folder)
        merge = button(tr("Juntar imagens em um PDF…"),
                       tooltip=tr("Várias imagens viram um único PDF, uma por página, na ordem que você escolher"))
        merge.clicked.connect(lambda: self.open_merge_dialog())
        for btn in (self.add_files_btn, self.add_folder_btn, merge):
            row.addWidget(btn)
        drop_layout.addLayout(row)
        layout.addWidget(drop)

        body = QHBoxLayout()
        left = QVBoxLayout()
        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels([tr(c) for c in self.COLUMNS])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(True)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        for col in range(1, len(self.COLUMNS)):
            header.setSectionResizeMode(col, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemSelectionChanged.connect(self._show_preview)
        left.addWidget(self.table, 1)
        queue_row = QHBoxLayout()
        self.remove_btn = button(tr("Remover selecionados"))
        self.remove_btn.clicked.connect(self.remove_selected)
        self.clear_btn = button(tr("Limpar fila"))
        self.clear_btn.clicked.connect(self.clear_queue)
        queue_row.addWidget(self.remove_btn)
        queue_row.addWidget(self.clear_btn)
        queue_row.addStretch(1)
        self.count_label = muted(wrap=False)
        queue_row.addWidget(self.count_label)
        left.addLayout(queue_row)
        body.addLayout(left, 1)

        preview, preview_layout = card()
        preview.setFixedWidth(270)
        title = QLabel(tr("Prévia"))
        title.setObjectName("SectionTitle")
        preview_layout.addWidget(title)
        self.preview_image = QLabel()
        self.preview_image.setObjectName("Thumb")
        self.preview_image.setFixedSize(PREVIEW_SIZE)
        self.preview_image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview_layout.addWidget(self.preview_image, 0, Qt.AlignmentFlag.AlignHCenter)
        self.preview_text = muted()
        self.preview_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        preview_layout.addWidget(self.preview_text)
        preview_layout.addStretch(1)
        body.addWidget(preview)
        layout.addLayout(body, 1)

        options = QHBoxLayout()
        options.addWidget(QLabel(tr("Converter para:")))
        self.format_combo = QComboBox()
        self.format_combo.setMinimumWidth(110)
        self.format_combo.currentIndexChanged.connect(self._update_destination_column)
        options.addWidget(self.format_combo)
        options.addSpacing(16)
        options.addWidget(QLabel(tr("Salvar em:")))
        self.output_dir = QLineEdit(self.settings.default_output_dir)
        self.output_dir.setPlaceholderText(tr("Mesma pasta do arquivo original"))
        self.output_dir.setClearButtonEnabled(True)
        options.addWidget(self.output_dir, 1)
        choose = button(tr("Escolher…"))
        choose.clicked.connect(self._pick_output_dir)
        options.addWidget(choose)
        advanced = button(tr("Opções avançadas…"))
        advanced.clicked.connect(self.open_options_dialog)
        options.addWidget(advanced)
        layout.addLayout(options)

        actions = QHBoxLayout()
        self.convert_btn = button(tr("Converter"), "primary")
        self.convert_btn.clicked.connect(self.start_conversion)
        self.pause_btn = button(tr("Pausar"), tooltip=tr("Pausa entre um arquivo e outro"))
        self.pause_btn.clicked.connect(self.toggle_pause)
        self.cancel_btn = button(tr("Cancelar"), "danger")
        self.cancel_btn.clicked.connect(self.cancel_conversion)
        self.open_folder_btn = button(tr("Abrir pasta dos convertidos"))
        self.open_folder_btn.clicked.connect(self.open_output_folder)
        for btn in (self.convert_btn, self.pause_btn, self.cancel_btn, self.open_folder_btn):
            actions.addWidget(btn)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.overall_label = muted(wrap=False)
        layout.addWidget(self.overall_label)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.current_label = muted(wrap=False)
        layout.addWidget(self.current_label)

    @property
    def history(self) -> History:
        if self._history is None:
            self._history = History()
        return self._history

    @property
    def running(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    def _update_buttons(self) -> None:
        running = self.running
        waiting = any(e.status == JobStatus.WAITING for e in self.entries)
        self.convert_btn.setEnabled(not running and waiting)
        self.pause_btn.setEnabled(running)
        self.cancel_btn.setEnabled(running and not self._cancel.is_set())
        self.remove_btn.setEnabled(not running and bool(self.entries))
        self.clear_btn.setEnabled(not running and bool(self.entries))
        self.open_folder_btn.setEnabled(self._last_output_dir is not None)
        n = len(self.entries)
        self.count_label.setText(tr("{n} arquivo(s) na fila", n=n) if n else "")

    # ------------------------------------------------------------ arquivos

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802 - API Qt
        if any(u.isLocalFile() for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802 - API Qt
        paths = [Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            self.add_paths(paths)
            event.acceptProposedAction()

    def pick_files(self) -> None:
        files, _ = QFileDialog.getOpenFileNames(self, tr("Adicionar arquivos"))
        if files:
            self.add_paths([Path(f) for f in files])

    def pick_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("Adicionar pasta"))
        if folder:
            self.add_paths([Path(folder)])

    def add_paths(self, paths: list[Path]) -> int:
        """Adiciona arquivos (e o conteudo convertivel de pastas) a fila, sem duplicar."""
        collected: list[tuple[Path, Path | None]] = []
        ignored = 0
        for path in paths:
            if path.is_dir():
                collected.extend((f, path) for f in sorted(path.rglob("*")) if f.is_file() and is_convertible(f))
            elif path.is_file():
                if is_convertible(path):
                    collected.append((path, None))
                else:
                    ignored += 1
        known = {str(e.path.resolve()).lower() for e in self.entries}
        added = 0
        for path, root in collected:
            key = str(path.resolve()).lower()
            if key in known:
                continue
            known.add(key)
            entry = ConvertEntry(path, root)
            self.entries.append(entry)
            self._append_row(entry)
            added += 1
        if added:
            self._refresh_formats()
        if ignored:
            self.current_label.setText(tr("{n} arquivo(s) ignorado(s): formato não suportado.", n=ignored))
        self._update_buttons()
        return added

    def _append_row(self, entry: ConvertEntry) -> None:
        row = self.table.rowCount()
        self.table.insertRow(row)
        try:
            size = human_size(entry.path.stat().st_size)
        except OSError:
            size = "?"
        values = [entry.path.name, entry.path.suffix.lstrip(".").upper(), size, "", ""]
        for col, value in enumerate(values):
            item = QTableWidgetItem(value)
            if col == 0:
                item.setToolTip(str(entry.path))
            self.table.setItem(row, col, item)
        self._paint_row(row)

    def _paint_row(self, row: int) -> None:
        entry = self.entries[row]
        status_item = self.table.item(row, 4)
        status_item.setText(tr(STATUS_LABELS[entry.status]))
        colors = theme.current()
        color = {JobStatus.DONE: colors.success, JobStatus.ERROR: colors.danger,
                 JobStatus.CANCELLED: colors.warning, JobStatus.SKIPPED: colors.muted}.get(entry.status)
        status_item.setForeground(QColor(color) if color else self.table.palette().text())
        status_item.setToolTip(entry.error or "\n".join(str(p) for p in entry.outputs))
        if entry.status == JobStatus.WAITING:
            fmt = self.format_combo.currentData()
            self.table.item(row, 3).setText(target_label(fmt) if fmt else "—")

    def _selected_rows(self) -> list[int]:
        return sorted({i.row() for i in self.table.selectedIndexes()})

    def remove_selected(self) -> None:
        if self.running:
            return
        for row in reversed(self._selected_rows()):
            del self.entries[row]
            self.table.removeRow(row)
        self._refresh_formats()
        self._update_buttons()

    def clear_queue(self) -> None:
        if self.running:
            return
        self.entries.clear()
        self.table.setRowCount(0)
        self.progress.setValue(0)
        self.overall_label.setText("")
        self.current_label.setText("")
        self._refresh_formats()
        self._update_buttons()

    # ------------------------------------------------------------ formato de saida

    def _refresh_formats(self) -> None:
        waiting = [e for e in self.entries if e.status == JobStatus.WAITING] or self.entries
        exts = {e.path.suffix.lower().lstrip(".") for e in waiting}
        common: set[str] | None = None
        for ext in exts:
            targets = set(registry.compatible_targets(ext))
            common = targets if common is None else common & targets
        choices = sorted(common or [])
        current = self.format_combo.currentData()
        if current not in choices:
            preferred = ((self.settings.pdf_default_image_format,) if exts == {"pdf"} else ()) + PREFERRED_TARGETS
            current = next((ext for ext in preferred if ext in choices), None)
        self.format_combo.blockSignals(True)
        self.format_combo.clear()
        for ext in choices:
            self.format_combo.addItem(target_label(ext), ext)
        self.format_combo.setCurrentIndex(max(0, self.format_combo.findData(current)))
        self.format_combo.blockSignals(False)
        self.format_combo.setEnabled(bool(choices))
        if self.entries and not choices:
            self.current_label.setText(tr("Os arquivos da fila não têm um formato de saída em comum. "
                                          "Converta tipos diferentes separadamente."))
        self._update_destination_column()

    def _update_destination_column(self) -> None:
        for row, entry in enumerate(self.entries):
            if entry.status == JobStatus.WAITING:
                self._paint_row(row)

    # ------------------------------------------------------------ previa

    def _show_preview(self) -> None:
        rows = self._selected_rows()
        self.preview_image.setPixmap(QPixmap())
        self.preview_image.setText("")
        if len(rows) != 1:
            self.preview_text.setText(tr("{n} arquivo(s) selecionado(s).", n=len(rows)) if rows
                                      else tr("Selecione um arquivo para ver a prévia."))
            return
        entry = self.entries[rows[0]]
        path = entry.path
        lines = [path.name, tr("Formato: {ext}", ext=path.suffix.lstrip(".").upper())]
        try:
            lines.append(tr("Tamanho: {size}", size=human_size(path.stat().st_size)))
        except OSError:
            lines.append(tr("(arquivo não encontrado)"))
        ext = path.suffix.lower().lstrip(".")
        image: QImage | None = None
        try:
            if registry.category_of(ext) is registry.IMAGE:
                reader = QImageReader(str(path))
                reader.setAutoTransform(True)
                size = reader.size()
                if size.isValid():
                    lines.append(tr("Dimensões: {w}×{h} px", w=size.width(), h=size.height()))
                image = reader.read()
            elif ext == "pdf":
                image = self._pdf_first_page(path, lines)
        except Exception:  # noqa: BLE001 - previa e so informativa
            log.debug("preview failed for %s", path, exc_info=True)
        if image is not None and not image.isNull():
            self.preview_image.setPixmap(QPixmap.fromImage(image).scaled(
                PREVIEW_SIZE, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        else:
            self.preview_image.setText(tr("Sem prévia"))
        if entry.outputs:
            lines.append("")
            lines.append(tr("Gerado: {names}", names=", ".join(p.name for p in entry.outputs[:5])))
        if entry.error:
            lines.append("")
            lines.append(tr("Erro: {message}", message=entry.error))
        self.preview_text.setText("\n".join(lines))

    @staticmethod
    def _pdf_first_page(path: Path, lines: list[str]) -> QImage | None:
        import pymupdf

        doc = pymupdf.open(path)
        try:
            lines.append(tr("Páginas: {n}", n=doc.page_count))
            if doc.page_count == 0:
                return None
            pix = doc.load_page(0).get_pixmap(matrix=pymupdf.Matrix(0.4, 0.4), alpha=False)
            return QImage(pix.samples, pix.width, pix.height, pix.stride, QImage.Format.Format_RGB888).copy()
        finally:
            doc.close()

    # ------------------------------------------------------------ opcoes

    def _pick_output_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("Salvar convertidos em"), self.output_dir.text())
        if folder:
            self.output_dir.setText(folder)

    def open_options_dialog(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle(tr("Opções avançadas"))
        form = QFormLayout(dialog)
        opts = self.options

        def section(text: str) -> None:
            label = QLabel(text)
            label.setObjectName("SectionTitle")
            form.addRow(label)

        section(tr("PDF → imagem"))
        pages = QLineEdit(opts.get("pages", ""))
        pages.setPlaceholderText(tr("todas (ex.: 1-5,10)"))
        form.addRow(tr("Páginas:"), pages)
        dpi = QSpinBox()
        dpi.setRange(36, 1200)
        dpi.setValue(int(opts.get("dpi", self.settings.pdf_default_dpi)))
        form.addRow(tr("Resolução (DPI):"), dpi)
        section(tr("Imagens e PDF"))
        quality = QSpinBox()
        quality.setRange(1, 95)
        quality.setValue(int(opts.get("quality", self.settings.image_default_quality)))
        quality.setToolTip(tr("Vale para JPG/WEBP e para PDF → imagem"))
        form.addRow(tr("Qualidade (1-95):"), quality)
        section(tr("Texto/Markdown → PDF"))
        font = _combo(FONT_LABELS, opts.get("font", "helvetica"))
        form.addRow(tr("Fonte:"), font)
        font_size = QSpinBox()
        font_size.setRange(6, 36)
        font_size.setValue(int(opts.get("font_size", 11)))
        form.addRow(tr("Tamanho da fonte:"), font_size)
        margin = QDoubleSpinBox()
        margin.setRange(0, 60)
        margin.setSuffix(" mm")
        margin.setValue(float(opts.get("margin_mm", 20)))
        form.addRow(tr("Margem:"), margin)
        section(tr("Arquivo de saída já existe"))
        conflict = _combo(CONFLICT_LABELS, opts.get("conflict_policy", self.settings.conflict_policy))
        form.addRow(tr("Quando existir:"), conflict)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        form.addRow(buttons)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        self.options = {
            "dpi": dpi.value(), "quality": quality.value(), "font": font.currentData(),
            "font_size": font_size.value(), "margin_mm": margin.value(), "conflict_policy": conflict.currentData(),
        }
        if pages.text().strip():
            self.options["pages"] = pages.text().strip()

    def job_options(self, entry: ConvertEntry) -> dict[str, Any]:
        """Padroes das Configuracoes conforme o tipo do arquivo + ajustes de 'Opcoes avancadas'."""
        s = self.settings
        options: dict[str, Any] = {
            "conflict_policy": s.conflict_policy,
            "ffmpeg_path": self.ctx.settings_store.settings.ffmpeg_path,
        }
        ext = entry.path.suffix.lower().lstrip(".")
        if ext == "pdf":
            options.update(dpi=s.pdf_default_dpi, quality=s.pdf_default_quality)
        elif registry.category_of(ext) is registry.IMAGE:
            options.update(quality=s.image_default_quality, keep_metadata=s.image_keep_metadata)
        overrides = dict(self.options)
        if ext != "pdf":
            overrides.pop("dpi", None)  # DPI e so para PDF -> imagem (nao mexe na DPI gravada em fotos)
        options.update(overrides)
        return options

    # ------------------------------------------------------------ conversao

    def start_conversion(self) -> None:
        if self.running:
            return
        waiting = [e for e in self.entries if e.status == JobStatus.WAITING]
        if not waiting:
            info_box(self, tr("Nada para converter"), tr("Adicione arquivos à fila."))
            return
        target = self.format_combo.currentData()
        if not target:
            info_box(self, tr("Escolha o formato"), tr("Escolha para qual formato converter."))
            return
        out_text = self.output_dir.text().strip()
        if out_text != self.settings.default_output_dir:
            self.settings.default_output_dir = out_text
            save_settings(self.settings)
        output_dir = Path(out_text) if out_text else None

        queue = ConversionQueue()
        self._entry_by_job = {}
        for entry in waiting:
            job = queue.add(entry.path, target, output_dir=output_dir, relative_root=entry.root,
                            options=self.job_options(entry))
            if job is not None:
                self._entry_by_job[job.id] = entry
        self._total = len(queue)
        self._done = 0
        self._cancel = threading.Event()
        self._pause = threading.Event()
        self.pause_btn.setText(tr("Pausar"))
        self.progress.setValue(0)
        self.overall_label.setText(tr("Progresso geral: 0 de {total}", total=self._total))
        log.info("conversion started: %d file(s) -> %s", self._total, target)

        settings, history = self.settings, self.history
        cancel, pause, signals = self._cancel, self._pause, self._signals

        def on_progress(job: Job) -> None:
            signals.progress.emit((job.id, job.status, list(job.output_paths), job.error, job.source.name, job.target_ext))

        def work() -> None:
            try:
                run_queue(queue, settings, history=history, cancel_event=cancel, pause_event=pause,
                          on_progress=on_progress)
            except Exception:  # noqa: BLE001 - run_queue ja trata erro por arquivo; isto e so defesa
                log.exception("conversion queue crashed")
            finally:
                signals.finished.emit()

        self._worker = threading.Thread(target=work, name="umd-convert", daemon=True)
        self._worker.start()
        self._update_buttons()

    def _on_progress(self, payload: tuple) -> None:
        job_id, status, outputs, error, name, target = payload
        entry = self._entry_by_job.get(job_id)
        if entry is None:
            return
        entry.status, entry.outputs, entry.error = status, outputs, error
        if entry in self.entries:
            row = self.entries.index(entry)
            if outputs:
                self.table.item(row, 3).setText(target_label(target))
            self._paint_row(row)
        if status == JobStatus.RUNNING:
            self.current_label.setText(tr("Convertendo: {name} → {fmt}", name=name, fmt=target_label(target)))
        elif status in FINISHED:
            self._done += 1
            self.progress.setValue(int(self._done / self._total * 100) if self._total else 100)
            self.overall_label.setText(tr("Progresso geral: {done} de {total}", done=self._done, total=self._total))
            if outputs:
                self._last_output_dir = outputs[0].parent
            if status == JobStatus.ERROR:
                log.warning("conversion failed: %s: %s", name, error)
            self._update_buttons()

    def _on_finished(self) -> None:
        if self._worker is not None:
            self._worker.join(timeout=1)
        self._worker = None
        counts = {s: 0 for s in FINISHED}
        for entry in self._entry_by_job.values():
            if entry.status in counts:
                counts[entry.status] += 1
        parts = [tr("{n} concluído(s)", n=counts[JobStatus.DONE])]
        if counts[JobStatus.ERROR]:
            parts.append(tr("{n} com erro", n=counts[JobStatus.ERROR]))
        if counts[JobStatus.SKIPPED]:
            parts.append(tr("{n} ignorado(s)", n=counts[JobStatus.SKIPPED]))
        if counts[JobStatus.CANCELLED]:
            parts.append(tr("{n} cancelado(s)", n=counts[JobStatus.CANCELLED]))
        self.current_label.setText(tr("Conversão finalizada: {summary}.", summary=", ".join(parts)))
        self.progress.setValue(100)
        self._refresh_formats()
        self._update_buttons()
        if self.settings.open_folder_after_conversion and self._last_output_dir is not None:
            reveal_in_folder(self, self._last_output_dir)

    def toggle_pause(self) -> None:
        if self._pause.is_set():
            self._pause.clear()
            self.pause_btn.setText(tr("Pausar"))
        else:
            self._pause.set()
            self.pause_btn.setText(tr("Continuar"))
            self.current_label.setText(tr("Pausado: o arquivo atual termina e a fila espera."))

    def cancel_conversion(self) -> None:
        self._cancel.set()
        self._pause.clear()
        self.current_label.setText(tr("Cancelando…"))
        self._update_buttons()

    def open_output_folder(self) -> None:
        if self._last_output_dir is not None:
            reveal_in_folder(self, self._last_output_dir)

    def shutdown(self, timeout: float = 5) -> None:
        """Cancela a conversao em andamento (sem deixar ffmpeg/LibreOffice orfaos)."""
        if self.running:
            self._cancel.set()
            self._pause.clear()
            assert self._worker is not None
            self._worker.join(timeout)

    # ------------------------------------------------------------ imagens -> PDF

    def open_merge_dialog(self, files: list[Path] | None = None) -> None:
        if not files:
            chosen, _ = QFileDialog.getOpenFileNames(
                self, tr("Escolha as imagens"), "",
                tr("Imagens") + " (" + " ".join(f"*.{e}" for e in sorted(registry.IMAGE.extensions)) + ")")
            files = [Path(f) for f in chosen]
        if not files:
            return
        MergeImagesDialog(files, self).exec()

    # ------------------------------------------------------------ dialogos

    def _show_formats(self) -> None:
        lines = [f"{tr(CATEGORY_LABELS.get(cat.key, cat.label))}: {', '.join(sorted(cat.extensions))}"
                 for cat in registry.ALL_CATEGORIES]
        lines += ["", tr("PDF achatado: campos de formulário e anotações viram conteúdo fixo "
                         "(o PDF deixa de ser editável). Gera \"nome (achatado).pdf\"."),
                  "", tr("Documentos, planilhas e apresentações exigem o LibreOffice instalado.")]
        info_box(self, tr("Formatos suportados"), "\n".join(lines))

    def _show_history(self) -> None:
        HistoryDialog(self.history, self).exec()

    def _open_settings(self) -> None:
        dialog = ConverterSettingsDialog(self.settings, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            save_settings(self.settings)
            self.output_dir.setText(self.settings.default_output_dir)


class MergeImagesDialog(QDialog):
    """Varias imagens -> um PDF (uma imagem por pagina), na ordem escolhida."""

    def __init__(self, files: list[Path], page: ConverterPage):
        super().__init__(page)
        self.page = page
        self.files = list(files)
        self.setWindowTitle(tr("Juntar imagens em um PDF"))
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.addWidget(muted(tr("A ordem da lista é a ordem das páginas.")))
        body = QHBoxLayout()
        self.list = QListWidget()
        body.addWidget(self.list, 1)
        side = QVBoxLayout()
        for label, delta in ((tr("Subir"), -1), (tr("Descer"), 1)):
            btn = button(label)
            btn.clicked.connect(lambda _=False, d=delta: self.move(d))
            side.addWidget(btn)
        side.addStretch(1)
        body.addLayout(side)
        layout.addLayout(body)
        form = QFormLayout()
        self.page_size = _combo(PAGE_SIZE_LABELS, "a4")
        self.orientation = _combo(ORIENTATION_LABELS, "portrait")
        self.fit = _combo(FIT_LABELS, "contain")
        form.addRow(tr("Tamanho da página:"), self.page_size)
        form.addRow(tr("Orientação:"), self.orientation)
        form.addRow(tr("Ajuste da imagem:"), self.fit)
        layout.addLayout(form)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        cancel = button(tr("Cancelar"))
        cancel.clicked.connect(self.reject)
        generate = button(tr("Gerar PDF…"), "primary")
        generate.clicked.connect(lambda: self.generate())
        buttons.addWidget(cancel)
        buttons.addWidget(generate)
        layout.addLayout(buttons)
        self._fill()

    def _fill(self, select: int = 0) -> None:
        self.list.clear()
        for path in self.files:
            self.list.addItem(path.name)
        self.list.setCurrentRow(select)

    def move(self, delta: int) -> None:
        i = self.list.currentRow()
        j = i + delta
        if 0 <= i < len(self.files) and 0 <= j < len(self.files):
            self.files[i], self.files[j] = self.files[j], self.files[i]
            self._fill(j)

    def generate(self, output: Path | None = None) -> Path | None:
        if output is None:
            suggested = str(self.files[0].with_name("imagens.pdf"))
            chosen, _ = QFileDialog.getSaveFileName(self, tr("Salvar PDF"), suggested, "PDF (*.pdf)")
            if not chosen:
                return None
            output = Path(chosen)
        if output.suffix.lower() != ".pdf":
            output = output.with_suffix(".pdf")
        options = {"page_size": self.page_size.currentData(), "orientation": self.orientation.currentData(),
                   "fit": self.fit.currentData(), "quality": self.page.settings.image_default_quality}
        try:
            result = merge_images_to_pdf(self.files, output, options, settings=self.page.settings,
                                         history=self.page.history)
        except ConversionError as exc:
            _warn(self, tr("Não foi possível gerar o PDF"), exc.user_message())
            return None
        self.page._last_output_dir = result.parent
        self.page._update_buttons()
        if confirm(self, tr("PDF gerado"), tr("PDF salvo em:\n{path}\n\nAbrir a pasta?", path=str(result))):
            reveal_in_folder(self, result)
        self.accept()
        return result


class HistoryDialog(QDialog):
    def __init__(self, history: History, parent: QWidget | None = None):
        super().__init__(parent)
        self.history = history
        self.setWindowTitle(tr("Histórico de conversões"))
        self.resize(760, 440)
        layout = QVBoxLayout(self)
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([tr("Data"), tr("Origem"), tr("Resultado"), tr("Status")])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.verticalHeader().setVisible(False)
        self.table.setAlternatingRowColors(True)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)
        buttons = QHBoxLayout()
        clear = button(tr("Limpar histórico"), "danger")
        clear.clicked.connect(self.clear)
        close = button(tr("Fechar"), "primary")
        close.clicked.connect(self.accept)
        buttons.addWidget(clear)
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.reload()

    def reload(self) -> None:
        from datetime import datetime

        entries = self.history.recent()
        self.table.setRowCount(len(entries))
        for row, entry in enumerate(entries):
            try:
                when = format_day(datetime.fromisoformat(entry.timestamp), with_time=True)
            except ValueError:
                when = entry.timestamp
            status = tr("Concluído") if entry.status == "Concluido" else tr(entry.status)
            values = [when, Path(entry.source_path).name if "," not in entry.source_path else entry.source_path,
                      Path(entry.target_path).name if entry.target_path else "—", status]
            for col, value in enumerate(values):
                item = QTableWidgetItem(value)
                tip = {1: entry.source_path, 2: entry.target_path or "", 3: entry.error_message or ""}.get(col)
                if tip:
                    item.setToolTip(tip)
                self.table.setItem(row, col, item)

    def clear(self) -> None:
        if confirm(self, tr("Limpar histórico"), tr("Apagar todo o histórico de conversões?\nOs arquivos não são apagados.")):
            self.history.clear()
            self.reload()


class ConverterSettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent: QWidget | None = None):
        super().__init__(parent)
        self.settings = settings
        self.setWindowTitle(tr("Configurações de conversão"))
        self.setMinimumWidth(460)
        form = QFormLayout(self)

        def section(text: str) -> None:
            label = QLabel(text)
            label.setObjectName("SectionTitle")
            form.addRow(label)

        section(tr("Conversão"))
        self.output_dir = QLineEdit(settings.default_output_dir)
        self.output_dir.setPlaceholderText(tr("Mesma pasta do arquivo original"))
        form.addRow(tr("Pasta padrão:"), self.output_dir)
        self.conflict = _combo(CONFLICT_LABELS, settings.conflict_policy)
        form.addRow(tr("Se o arquivo já existir:"), self.conflict)
        self.preserve = QCheckBox(tr("Manter as subpastas ao converter uma pasta"))
        self.preserve.setChecked(settings.preserve_folder_structure)
        form.addRow(self.preserve)
        self.open_after = QCheckBox(tr("Abrir a pasta ao terminar"))
        self.open_after.setChecked(settings.open_folder_after_conversion)
        form.addRow(self.open_after)

        section(tr("PDF → imagem"))
        self.pdf_dpi = QSpinBox()
        self.pdf_dpi.setRange(36, 1200)
        self.pdf_dpi.setValue(settings.pdf_default_dpi)
        form.addRow(tr("Resolução padrão (DPI):"), self.pdf_dpi)
        self.pdf_quality = QSpinBox()
        self.pdf_quality.setRange(1, 95)
        self.pdf_quality.setValue(settings.pdf_default_quality)
        form.addRow(tr("Qualidade padrão:"), self.pdf_quality)
        self.pdf_format = QComboBox()
        for ext in ("png", "jpg", "webp"):
            self.pdf_format.addItem(ext.upper(), ext)
        self.pdf_format.setCurrentIndex(max(0, self.pdf_format.findData(settings.pdf_default_image_format)))
        form.addRow(tr("Formato padrão:"), self.pdf_format)

        section(tr("Imagens"))
        self.image_quality = QSpinBox()
        self.image_quality.setRange(1, 95)
        self.image_quality.setValue(settings.image_default_quality)
        form.addRow(tr("Qualidade padrão (JPG/WEBP):"), self.image_quality)
        self.keep_metadata = QCheckBox(tr("Manter metadados (EXIF) quando possível"))
        self.keep_metadata.setChecked(settings.image_keep_metadata)
        form.addRow(self.keep_metadata)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.save)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def save(self) -> None:
        s = self.settings
        s.default_output_dir = self.output_dir.text().strip()
        s.conflict_policy = self.conflict.currentData()
        s.preserve_folder_structure = self.preserve.isChecked()
        s.open_folder_after_conversion = self.open_after.isChecked()
        s.pdf_default_dpi = self.pdf_dpi.value()
        s.pdf_default_quality = self.pdf_quality.value()
        s.pdf_default_image_format = self.pdf_format.currentData()
        s.image_default_quality = self.image_quality.value()
        s.image_keep_metadata = self.keep_metadata.isChecked()
        self.accept()
