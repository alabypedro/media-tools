"""Tela Renomear: renomeador em lote com previa.

A tabela mostra, ao vivo, o nome atual e o nome novo de cada arquivo conforme
as opcoes mudam; nada e alterado no disco ate o botao Renomear. O calculo e de
umd.rename.engine (o mesmo do `umd rename`). A ultima renomeacao pode ser
desfeita, e os arquivos ja renomeados podem seguir para a tela Converter.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from ..convert.core.errors import ConversionError
from ..core.i18n import tr
from ..core.logger import get_logger
from ..rename import engine
from ..services.context import AppContext
from .widgets import Debouncer, button, card, muted, page_header

log = get_logger("rename")

CUSTOM = "custom"
PRESET_LABELS = {
    CUSTOM: "Personalizado",
    "{name} {n:3}": "Numerar em sequência (nome 001)",
    engine.EXIF_DATE_PATTERN: "Data da foto (EXIF)",
    "{date:%Y-%m-%d} {name}": "Data da foto + nome atual",
    "{parent} {n:3}": "Nome da pasta + número",
}
CASE_LABELS = {"keep": "Manter como está", "lower": "minúsculas", "upper": "MAIÚSCULAS",
               "title": "Iniciais Maiúsculas"}
EXT_CASE_LABELS = {"keep": "Manter como está", "lower": "minúsculas (.jpg)", "upper": "MAIÚSCULAS (.JPG)"}
SORT_LABELS = {"list": "Ordem da lista", "name": "Nome", "date": "Data"}
DONE = "done"  # so da tela: logo depois de renomear, a tabela mostra o que foi feito
STATUS_LABELS = {engine.RENAME: "Vai renomear", engine.SAME: "Sem mudança", engine.SKIP: "Pulado",
                 DONE: "Renomeado"}
NOTE_LABELS = {
    engine.NOTE_CONFLICT: "nome já existia: ganhou um número",
    engine.NOTE_NO_DATE: "sem data EXIF",
    engine.NOTE_MTIME: "sem EXIF: usou a data de modificação",
}
assert set(CASE_LABELS) == set(engine.CASES) and set(EXT_CASE_LABELS) == set(engine.EXT_CASES)
assert set(SORT_LABELS) == set(engine.SORTS)


def _combo(labels: dict[str, str], current: str) -> QComboBox:
    combo = QComboBox()
    for key, label in labels.items():
        combo.addItem(tr(label), key)
    combo.setCurrentIndex(max(0, combo.findData(current)))
    return combo


class RenamePage(QWidget):
    COLUMNS = ["Nome atual", "Novo nome", "Situação"]
    convert_requested = Signal(list)  # arquivos (ja renomeados) para a tela Converter

    def __init__(self, ctx: AppContext, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.files: list[Path] = []
        self.plan: list[engine.RenameItem] = []
        self._exif: dict[Path, datetime | None] = {}  # ler o EXIF e o passo lento: uma vez por arquivo
        self._refresh = Debouncer(200, self.refresh, self)
        self.setAcceptDrops(True)
        self._build()
        self.refresh()

    # ------------------------------------------------------------ layout

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 20)
        layout.setSpacing(12)
        layout.addWidget(page_header(tr("Renomear"), tr(
            "Renomeia vários arquivos de uma vez por modelo, localizar/substituir (texto ou regex) ou pela data "
            "da foto (EXIF). Veja o resultado na tabela antes de aplicar; a extensão é mantida e nada é sobrescrito.")))

        options, options_layout = card()
        form = QFormLayout()
        form.setContentsMargins(0, 0, 0, 0)

        self.preset = _combo(PRESET_LABELS, CUSTOM)
        self.pattern = QLineEdit("{name}")
        self.pattern.setToolTip(tr(
            "{name} nome atual  •  {n:3} contador (001)  •  {date:%Y-%m-%d} data da foto  •  "
            "{parent} nome da pasta  •  {ext} extensão"))
        pattern_row = QHBoxLayout()
        pattern_row.addWidget(self.pattern, 1)
        pattern_row.addWidget(self.preset)
        form.addRow(tr("Modelo do nome:"), pattern_row)
        form.addRow("", muted(tr(
            "Campos: {name} nome atual, {n:3} contador (001), {date:%Y-%m-%d} data da foto, {parent} pasta.")))

        self.find = QLineEdit()
        self.find.setPlaceholderText(tr("Texto a localizar no nome (opcional)"))
        self.replace = QLineEdit()
        self.replace.setPlaceholderText(tr("Substituir por (vazio = apagar)"))
        self.regex = QCheckBox(tr("Regex"))
        self.regex.setToolTip(tr("Localizar é uma expressão regular; use \\1, \\2 em Substituir para os grupos"))
        self.ignore_case = QCheckBox(tr("Ignorar maiúsculas"))
        find_row = QHBoxLayout()
        for widget in (self.find, self.replace):
            find_row.addWidget(widget, 1)
        find_row.addWidget(self.regex)
        find_row.addWidget(self.ignore_case)
        form.addRow(tr("Localizar:"), find_row)

        self.case = _combo(CASE_LABELS, "keep")
        self.ext_case = _combo(EXT_CASE_LABELS, "keep")
        self.start = QSpinBox()
        self.start.setRange(0, 999_999)
        self.start.setValue(1)
        self.sort = _combo(SORT_LABELS, "list")
        self.sort.setToolTip(tr("Ordem em que o contador {n} numera os arquivos"))
        for title, fields in ((tr("Maiúsculas:"), ((tr("Nome:"), self.case), (tr("Extensão:"), self.ext_case))),
                              (tr("Contador:"), ((tr("Começa em:"), self.start), (tr("Numerar por:"), self.sort)))):
            row = QHBoxLayout()
            for label, widget in fields:
                widget.setMinimumWidth(170 if isinstance(widget, QComboBox) else 90)
                row.addWidget(QLabel(label))
                row.addWidget(widget)
                row.addSpacing(14)
            row.addStretch(1)
            form.addRow(title, row)

        self.date_fallback = QCheckBox(tr("Sem data EXIF, usar a data de modificação do arquivo"))
        self.date_fallback.setChecked(True)
        form.addRow("", self.date_fallback)
        options_layout.addLayout(form)
        layout.addWidget(options)

        toolbar = QHBoxLayout()
        self.add_files_btn = button(tr("Adicionar arquivos…"))
        self.add_files_btn.clicked.connect(self.pick_files)
        self.add_folder_btn = button(tr("Adicionar pasta…"))
        self.add_folder_btn.clicked.connect(self.pick_folder)
        self.remove_btn = button(tr("Remover da lista"))
        self.remove_btn.clicked.connect(self.remove_selected)
        self.clear_btn = button(tr("Limpar lista"))
        self.clear_btn.clicked.connect(self.clear)
        for btn in (self.add_files_btn, self.add_folder_btn, self.remove_btn, self.clear_btn):
            toolbar.addWidget(btn)
        toolbar.addStretch(1)
        layout.addLayout(toolbar)

        self.table = QTableWidget(0, len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels([tr(c) for c in self.COLUMNS])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        layout.addWidget(self.table, 1)

        self.status_label = muted(tr("Arraste arquivos ou uma pasta para esta tela, ou use os botões acima."))
        layout.addWidget(self.status_label)

        actions = QHBoxLayout()
        self.apply_btn = button(tr("Renomear"), "primary")
        self.apply_btn.clicked.connect(self.apply)
        self.undo_btn = button(tr("Desfazer última renomeação"))
        self.undo_btn.clicked.connect(self.undo)
        self.convert_btn = button(tr("Enviar para o Converter"),
                                  tooltip=tr("Leva os arquivos da lista para a fila da tela Converter"))
        self.convert_btn.clicked.connect(lambda: self.convert_requested.emit(list(self.files)))
        for btn in (self.apply_btn, self.undo_btn, self.convert_btn):
            actions.addWidget(btn)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.preset.currentIndexChanged.connect(self._preset_chosen)
        self.pattern.textEdited.connect(lambda _text: self.preset.setCurrentIndex(self.preset.findData(CUSTOM)))
        for edit in (self.pattern, self.find, self.replace):
            edit.textChanged.connect(lambda _text: self._refresh.trigger())
        for check in (self.regex, self.ignore_case, self.date_fallback):
            check.toggled.connect(lambda _on: self._refresh.trigger())
        for combo in (self.case, self.ext_case, self.sort):
            combo.currentIndexChanged.connect(lambda _i: self._refresh.trigger())
        self.start.valueChanged.connect(lambda _v: self._refresh.trigger())

    def _preset_chosen(self, _index: int) -> None:
        pattern = self.preset.currentData()
        if pattern != CUSTOM:
            self.pattern.setText(pattern)

    # ------------------------------------------------------------ lista de arquivos

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802 - API Qt
        if any(u.isLocalFile() for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802 - API Qt
        paths = [Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()]
        if paths:
            event.acceptProposedAction()
            self.add_paths(paths)

    def pick_files(self) -> None:
        chosen, _ = QFileDialog.getOpenFileNames(self, tr("Adicionar arquivos"), "", f"{tr('Todos os arquivos')} (*)")
        self.add_paths([Path(f) for f in chosen])

    def pick_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("Adicionar pasta"))
        if folder:
            self.add_paths([Path(folder)])

    def add_paths(self, paths: list[Path]) -> int:
        """Acrescenta arquivos (e os arquivos de dentro das pastas) a lista. Devolve quantos entraram."""
        try:
            found = engine.collect_files([p for p in paths if p.exists()])
        except ConversionError as exc:
            QMessageBox.warning(self, tr("Renomear"), exc.user_message())
            return 0
        known = set(self.files)
        new = [p for p in found if p not in known]
        self.files += new
        self.refresh()
        return len(new)

    def remove_selected(self) -> None:
        rows = [self.plan[index.row()] for index in self.table.selectionModel().selectedRows()
                if index.row() < len(self.plan)]
        selected = {item.target if item.status == DONE else item.source for item in rows}
        if selected:
            self.files = [p for p in self.files if p not in selected]
            self.refresh()

    def clear(self) -> None:
        self.files = []
        self._exif.clear()
        self.refresh()

    # ------------------------------------------------------------ previa

    def options(self) -> engine.RenameOptions:
        return engine.RenameOptions(
            pattern=self.pattern.text(), find=self.find.text(), replace=self.replace.text(),
            regex=self.regex.isChecked(), ignore_case=self.ignore_case.isChecked(),
            case=self.case.currentData(), ext_case=self.ext_case.currentData(), start=self.start.value(),
            sort=self.sort.currentData(), date_fallback=self.date_fallback.isChecked())

    def _exif_date(self, path: Path) -> datetime | None:
        if path not in self._exif:
            self._exif[path] = engine.exif_date(path)
        return self._exif[path]

    def refresh(self) -> None:
        """Recalcula a previa (nome atual -> nome novo). Nao altera nada no disco."""
        error = ""
        try:
            self.plan = engine.build_plan(self.files, self.options(), self._exif_date)
        except ConversionError as exc:
            self.plan, error = [], exc.user_message().replace("\n", "  ")
        self._fill_table()
        changes = sum(item.status == engine.RENAME for item in self.plan)
        if error:
            self.status_label.setText("⚠  " + error)
        elif not self.files:
            self.status_label.setText(tr("Arraste arquivos ou uma pasta para esta tela, ou use os botões acima."))
        else:
            text = tr("{changes} de {total} arquivo(s) serão renomeados.", changes=changes, total=len(self.files))
            skipped = sum(item.status == engine.SKIP for item in self.plan)
            if skipped:
                text += "  " + tr("{n} pulado(s).", n=skipped)
            self.status_label.setText(text)
        self.apply_btn.setEnabled(changes > 0)
        self.remove_btn.setEnabled(bool(self.files))
        self.clear_btn.setEnabled(bool(self.files))
        self.convert_btn.setEnabled(bool(self.files))
        self.undo_btn.setEnabled(bool(engine.last_operation()))

    def _fill_table(self) -> None:
        self.table.setRowCount(len(self.plan))
        for row, item in enumerate(self.plan):
            status = tr(STATUS_LABELS[item.status])
            if item.note:
                status += f" ({tr(NOTE_LABELS[item.note])})"
            new_name = item.target.name if item.status != engine.SKIP else "—"
            for col, value in enumerate((item.source.name, new_name, status)):
                cell = QTableWidgetItem(value)
                cell.setToolTip(str(item.source) if col == 0 else value)
                if item.status in (engine.SAME, engine.SKIP):
                    cell.setForeground(self.palette().color(self.palette().ColorRole.PlaceholderText))
                elif col == 1:
                    font = cell.font()
                    font.setBold(True)
                    cell.setFont(font)
                self.table.setItem(row, col, cell)

    # ------------------------------------------------------------ aplicar e desfazer

    def apply(self) -> None:
        self.refresh()  # a pasta pode ter mudado desde a ultima previa
        if not any(item.status == engine.RENAME for item in self.plan):
            return
        try:
            done = engine.apply_plan(self.plan)
        except ConversionError as exc:
            QMessageBox.warning(self, tr("Não foi possível renomear"), exc.user_message())
            self.refresh()
            return
        log.info("rename: %d file(s)", len(done))
        self._follow(dict(done))
        # mostra o que foi feito; a previa volta quando uma opcao ou a lista mudar (senao o mesmo
        # modelo ja apareceria aplicado de novo sobre os nomes novos, pronto para um segundo clique)
        self.plan = [engine.RenameItem(old, new, DONE) for old, new in done]
        self._fill_table()
        self.apply_btn.setEnabled(False)
        self.status_label.setText(tr("{n} arquivo(s) renomeados.", n=len(done)))

    def undo(self) -> None:
        try:
            restored = engine.undo_last()
        except ConversionError as exc:
            QMessageBox.warning(self, tr("Não foi possível desfazer"), exc.user_message())
            self.refresh()
            return
        self._follow(dict(restored))
        self.status_label.setText(tr("{n} arquivo(s) voltaram ao nome anterior.", n=len(restored)))

    def _follow(self, moved: dict[Path, Path]) -> None:
        """Os arquivos mudaram de nome: a lista passa a apontar para os nomes novos."""
        self.files = [moved.get(path, path) for path in self.files]
        for old, new in moved.items():
            if old in self._exif:
                self._exif[new] = self._exif.pop(old)
        self.refresh()
