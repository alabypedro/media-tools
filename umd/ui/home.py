"""Tela Inicio: colar URL -> analisar -> preview -> escolher -> baixar.
Tambem tem a aba de download em lote (varias URLs / importar TXT-CSV)."""

from __future__ import annotations

import threading
from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSize, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication
from PySide6.QtWidgets import (
    QAbstractItemView,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPlainTextEdit,
    QProgressBar,
    QRadioButton,
    QScrollArea,
    QTableWidget,
    QTableWidgetItem,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from ..core.config import QUALITY_PRESET_LABELS, QualityPreset
from ..core.exceptions import ErrorCode, UMDError
from ..core.i18n import format_day, format_number, tr
from ..core.naming import format_date
from ..core.security import host_of
from ..downloader.progress import human_duration, human_size
from ..media.formats import (
    DownloadSelection,
    SelectionMode,
    audio_format_choices,
    bitrate_choices,
    container_choices,
    estimate_size,
    image_format_choices,
    mode_choices,
    quality_choices,
    selection_from_settings,
)
from ..media.metadata import CONTENT_TYPE_LABELS, MULTI_FILE_TYPES, ContentType, MediaInfo, MediaKind
from ..services.batch import load_url_file, parse_url_text
from ..services.context import AppContext
from .widgets import Debouncer, ThumbnailLabel, button, card, chip, info_box, muted, page_header, run_task, show_error

_KIND_ICONS = {MediaKind.VIDEO: "🎬", MediaKind.AUDIO: "🎵", MediaKind.IMAGE: "🖼", MediaKind.OTHER: "📄", MediaKind.DOCUMENT: "📄"}


def _section(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("SectionTitle")
    return label


def _format_count(value: int | None) -> str:
    return "" if value is None else format_number(value)


class HomePage(QWidget):
    download_submitted = Signal()
    show_downloads = Signal()

    def __init__(self, ctx: AppContext, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self._info: MediaInfo | None = None
        self._cancel: threading.Event | None = None
        self._analysis_id = 0
        self._custom_format: str | None = None
        self._build()

    # ================================================================ layout

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll)
        content = QWidget()
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(14)
        layout.addWidget(page_header(
            tr("Baixar mídia"),
            tr("Cole o link de um vídeo, áudio, imagem ou post. O app identifica a plataforma e mostra só as opções realmente disponíveis."),
        ))

        self.banner = QFrame()
        self.banner.setObjectName("Banner")
        banner_row = QHBoxLayout(self.banner)
        banner_row.setContentsMargins(10, 6, 10, 6)
        self.banner_label = QLabel()
        self.banner_label.setWordWrap(True)
        banner_row.addWidget(self.banner_label, 1)
        see = button(tr("Ver downloads"), "link")
        see.clicked.connect(self.show_downloads.emit)
        banner_row.addWidget(see)
        close = button("✕", "link")
        close.clicked.connect(self.banner.hide)
        banner_row.addWidget(close)
        self.banner.hide()
        layout.addWidget(self.banner)

        self.tabs = QTabWidget()
        self.tabs.addTab(self._build_single(), tr("Um link"))
        self.tabs.addTab(self._build_batch(), tr("Vários links (lote)"))
        layout.addWidget(self.tabs)
        layout.addStretch(1)

    # ---------------------------------------------------------------- aba "um link"

    def _build_single(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 12, 0, 0)
        layout.setSpacing(14)

        url_card, url_layout = card()
        url_layout.addWidget(_section(tr("Cole uma URL")))
        row = QHBoxLayout()
        self.url_input = QLineEdit()
        self.url_input.setObjectName("UrlInput")
        self.url_input.setPlaceholderText("https://…")
        self.url_input.setClearButtonEnabled(True)
        self.url_input.returnPressed.connect(self.analyze)
        self.url_input.textChanged.connect(self._on_url_changed)
        row.addWidget(self.url_input, 1)
        paste = button(tr("Colar"), tooltip=tr("Colar da área de transferência"))
        paste.clicked.connect(self._paste)
        row.addWidget(paste)
        self.analyze_btn = button(tr("ANALISAR"), "primary")
        self.analyze_btn.clicked.connect(self.analyze)
        row.addWidget(self.analyze_btn)
        url_layout.addLayout(row)
        self.detect_label = muted("")
        url_layout.addWidget(self.detect_label)

        self.busy_row = QWidget()
        busy = QHBoxLayout(self.busy_row)
        busy.setContentsMargins(0, 0, 0, 0)
        self.busy_bar = QProgressBar()
        self.busy_bar.setRange(0, 0)
        self.busy_bar.setFixedHeight(6)
        busy.addWidget(self.busy_bar, 1)
        self.busy_label = muted(tr("Analisando…"), wrap=False)
        busy.addWidget(self.busy_label)
        cancel = button(tr("Cancelar"))
        cancel.clicked.connect(self.cancel_analysis)
        busy.addWidget(cancel)
        self.busy_row.hide()
        url_layout.addWidget(self.busy_row)
        layout.addWidget(url_card)

        self.result_card = self._build_result()
        self.result_card.hide()
        layout.addWidget(self.result_card)
        return page

    def _build_result(self) -> QFrame:
        frame, layout = card()
        layout.addWidget(_section(tr("Conteúdo identificado")))

        top = QHBoxLayout()
        top.setSpacing(16)
        self.thumb = ThumbnailLabel(QSize(320, 180))
        top.addWidget(self.thumb, 0, Qt.AlignmentFlag.AlignTop)
        details = QVBoxLayout()
        details.setSpacing(6)
        self.title_label = QLabel()
        self.title_label.setObjectName("MediaTitle")
        self.title_label.setWordWrap(True)
        self.title_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        details.addWidget(self.title_label)
        self.author_label = QLabel()
        self.author_label.setWordWrap(True)
        details.addWidget(self.author_label)
        self.meta_label = muted()
        details.addWidget(self.meta_label)
        chips = QHBoxLayout()
        chips.setSpacing(6)
        self.platform_chip = chip("")
        self.type_chip = chip("")
        chips.addWidget(self.platform_chip)
        chips.addWidget(self.type_chip)
        chips.addStretch(1)
        details.addLayout(chips)
        self.note_label = QLabel()
        self.note_label.setObjectName("WarningText")
        self.note_label.setWordWrap(True)
        details.addWidget(self.note_label)
        details.addStretch(1)
        top.addLayout(details, 1)
        layout.addLayout(top)

        self.text_container = QWidget()
        text_layout = QVBoxLayout(self.text_container)
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.addWidget(muted(tr("Texto do post")))
        self.text_box = QPlainTextEdit()
        self.text_box.setReadOnly(True)
        self.text_box.setMaximumHeight(110)
        text_layout.addWidget(self.text_box)
        layout.addWidget(self.text_container)

        self.form = QFormLayout()
        self.form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.form.setHorizontalSpacing(14)
        self.mode_combo = QComboBox()
        self.mode_combo.currentIndexChanged.connect(self._refresh_options)
        self.quality_combo = QComboBox()
        self.quality_combo.currentIndexChanged.connect(self._on_quality_changed)
        self.format_combo = QComboBox()
        self.format_combo.currentIndexChanged.connect(self._on_format_changed)
        self.bitrate_combo = QComboBox()
        self.bitrate_combo.currentIndexChanged.connect(self._update_estimate)
        self.video_format_combo = QComboBox()
        self.video_format_combo.currentIndexChanged.connect(self._update_estimate)
        for combo in (self.mode_combo, self.quality_combo, self.format_combo, self.bitrate_combo, self.video_format_combo):
            combo.setMinimumWidth(280)
        self.form.addRow(tr("Tipo:"), self.mode_combo)
        self.form.addRow(tr("Qualidade:"), self.quality_combo)
        self.form.addRow(tr("Formato:"), self.format_combo)
        self.form.addRow(tr("Bitrate:"), self.bitrate_combo)
        self.form.addRow(tr("Formato dos vídeos:"), self.video_format_combo)
        layout.addLayout(self.form)
        self.custom_format_row = QWidget()
        custom = QHBoxLayout(self.custom_format_row)
        custom.setContentsMargins(0, 0, 0, 0)
        self.custom_format_label = QLabel()
        custom.addWidget(self.custom_format_label, 1)
        reset_custom = button(tr("Voltar à escolha automática"), "link")
        reset_custom.clicked.connect(self._clear_custom_format)
        custom.addWidget(reset_custom)
        self.custom_format_row.hide()
        layout.addWidget(self.custom_format_row)
        self.estimate_label = muted()
        layout.addWidget(self.estimate_label)

        # --- itens de playlist / arquivos de post
        self.entries_box = QWidget()
        entries = QVBoxLayout(self.entries_box)
        entries.setContentsMargins(0, 6, 0, 0)
        self.entries_label = QLabel()
        self.entries_label.setObjectName("SectionTitle")
        entries.addWidget(self.entries_label)
        radios = QHBoxLayout()
        self.all_radio = QRadioButton()
        self.manual_radio = QRadioButton()
        self.all_radio.setChecked(True)
        group = QButtonGroup(self)
        group.addButton(self.all_radio)
        group.addButton(self.manual_radio)
        self.all_radio.toggled.connect(self._on_entries_mode)
        radios.addWidget(self.all_radio)
        radios.addWidget(self.manual_radio)
        radios.addStretch(1)
        entries.addLayout(radios)
        self.entries_list = QListWidget()
        self.entries_list.setMaximumHeight(260)
        self.entries_list.setAlternatingRowColors(True)
        self.entries_list.itemChanged.connect(lambda _item: self._update_estimate())
        entries.addWidget(self.entries_list)
        entry_buttons = QHBoxLayout()
        self.check_all_btn = button(tr("Marcar todos"))
        self.check_all_btn.clicked.connect(lambda: self._check_all(True))
        self.uncheck_all_btn = button(tr("Desmarcar todos"))
        self.uncheck_all_btn.clicked.connect(lambda: self._check_all(False))
        entry_buttons.addWidget(self.check_all_btn)
        entry_buttons.addWidget(self.uncheck_all_btn)
        self.selected_label = muted(wrap=False)
        entry_buttons.addWidget(self.selected_label)
        entry_buttons.addStretch(1)
        entries.addLayout(entry_buttons)
        self.truncated_label = QLabel()
        self.truncated_label.setObjectName("WarningText")
        self.truncated_label.setWordWrap(True)
        entries.addWidget(self.truncated_label)
        layout.addWidget(self.entries_box)

        # --- tabela de formatos (avancado)
        self.formats_toggle = button(tr("Ver todos os formatos disponíveis (avançado)"), "link")
        self.formats_toggle.clicked.connect(self._toggle_formats)
        layout.addWidget(self.formats_toggle, 0, Qt.AlignmentFlag.AlignLeft)
        self.formats_box = QWidget()
        formats_layout = QVBoxLayout(self.formats_box)
        formats_layout.setContentsMargins(0, 0, 0, 0)
        self.formats_table = QTableWidget(0, 9)
        self.formats_table.setHorizontalHeaderLabels([
            tr("ID"), tr("Tipo"), tr("Extensão"), tr("Resolução"), tr("FPS"), tr("Codec de vídeo"),
            tr("Codec de áudio"), tr("Bitrate"), tr("Tamanho"),
        ])
        self.formats_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.formats_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.formats_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.formats_table.verticalHeader().setVisible(False)
        self.formats_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.formats_table.setMinimumHeight(220)
        self.formats_table.doubleClicked.connect(lambda _index: self._use_selected_format())
        formats_layout.addWidget(self.formats_table)
        use_format = button(tr("Usar o formato selecionado"))
        use_format.clicked.connect(self._use_selected_format)
        formats_layout.addWidget(use_format, 0, Qt.AlignmentFlag.AlignLeft)
        self.formats_box.hide()
        layout.addWidget(self.formats_box)

        self.save_text_check = QCheckBox(tr("Salvar também o texto do post (.txt)"))
        layout.addWidget(self.save_text_check)

        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel(tr("Pasta:")))
        self.folder_edit = QLineEdit()
        folder_row.addWidget(self.folder_edit, 1)
        change = button(tr("Alterar…"))
        change.clicked.connect(lambda: self._choose_folder(self.folder_edit))
        folder_row.addWidget(change)
        layout.addLayout(folder_row)

        download_row = QHBoxLayout()
        download_row.addStretch(1)
        self.download_btn = button(tr("BAIXAR"), "primary")
        self.download_btn.setMinimumHeight(40)
        self.download_btn.setMinimumWidth(180)
        self.download_btn.clicked.connect(self.start_download)
        download_row.addWidget(self.download_btn)
        layout.addLayout(download_row)
        return frame

    # ---------------------------------------------------------------- aba "lote"

    def _build_batch(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 12, 0, 0)
        frame, box = card()
        box.addWidget(_section(tr("Várias URLs (uma por linha)")))
        self.batch_edit = QPlainTextEdit()
        self.batch_edit.setPlaceholderText("https://site.com/video1\nhttps://site.com/video2\nhttps://site.com/video3")
        self.batch_edit.setMinimumHeight(180)
        self._batch_debounce = Debouncer(300, self._update_batch_count, self)
        self.batch_edit.textChanged.connect(self._batch_debounce.trigger)
        box.addWidget(self.batch_edit)

        buttons = QHBoxLayout()
        add = button(tr("Adicionar URLs da área de transferência"))
        add.clicked.connect(self._batch_paste)
        imp = button(tr("Importar lista (TXT/CSV)…"))
        imp.clicked.connect(self._batch_import)
        clear = button(tr("Limpar lista"))
        clear.clicked.connect(self.batch_edit.clear)
        buttons.addWidget(add)
        buttons.addWidget(imp)
        buttons.addWidget(clear)
        buttons.addStretch(1)
        box.addLayout(buttons)
        self.batch_count = QLabel(tr("Nenhuma URL encontrada."))
        self.batch_count.setObjectName("SectionTitle")
        box.addWidget(self.batch_count)
        self.batch_invalid = QLabel()
        self.batch_invalid.setObjectName("WarningText")
        self.batch_invalid.setWordWrap(True)
        box.addWidget(self.batch_invalid)

        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        self.batch_preset = QComboBox()
        for preset, label in QUALITY_PRESET_LABELS.items():
            self.batch_preset.addItem(tr(label), preset.value)
        self.batch_preset.currentIndexChanged.connect(self._batch_preset_changed)
        self.batch_audio = QComboBox()
        for key, label in (("mp3", "MP3"), ("m4a", "M4A (AAC)"), ("opus", "OPUS"), ("wav", "WAV"), ("original", tr("Original (sem conversão)"))):
            self.batch_audio.addItem(label, key)
        form.addRow(tr("Qualidade:"), self.batch_preset)
        form.addRow(tr("Formato do áudio:"), self.batch_audio)
        self.batch_form = form
        box.addLayout(form)
        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel(tr("Pasta:")))
        self.batch_folder = QLineEdit()
        folder_row.addWidget(self.batch_folder, 1)
        change = button(tr("Alterar…"))
        change.clicked.connect(lambda: self._choose_folder(self.batch_folder))
        folder_row.addWidget(change)
        box.addLayout(folder_row)
        box.addWidget(muted(tr(
            "Os links vão para a fila de downloads e são baixados alguns por vez (ajuste em Configurações). "
            "Playlists e perfis são baixados até o limite de itens configurado."
        )))
        row = QHBoxLayout()
        row.addStretch(1)
        self.batch_btn = button(tr("BAIXAR TODOS"), "primary")
        self.batch_btn.setMinimumHeight(40)
        self.batch_btn.setEnabled(False)
        self.batch_btn.clicked.connect(self.start_batch)
        row.addWidget(self.batch_btn)
        box.addLayout(row)
        layout.addWidget(frame)
        layout.addStretch(1)
        self.reload_defaults()
        return page

    def reload_defaults(self) -> None:
        settings = self.ctx.settings_store.settings
        self.batch_folder.setText(settings.download_dir)
        index = self.batch_preset.findData(settings.default_quality.value)
        self.batch_preset.setCurrentIndex(max(0, index))
        audio_index = self.batch_audio.findData(settings.default_audio_format)
        self.batch_audio.setCurrentIndex(max(0, audio_index))
        self._batch_preset_changed()
        if self._info is None and hasattr(self, "folder_edit"):
            self.folder_edit.setText(settings.download_dir)

    # ================================================================ URL / analise

    def _paste(self) -> None:
        text = QGuiApplication.clipboard().text().strip()
        if text:
            self.url_input.setText(text.splitlines()[0].strip())
            self.analyze()

    def _on_url_changed(self, text: str) -> None:
        text = text.strip()
        if not text:
            self.detect_label.setText("")
            return
        try:
            url, provider = self.ctx.media.detect(text)
        except UMDError as exc:
            self.detect_label.setText(tr(exc.message) if len(text) > 10 else "")
            return
        if provider.key == "generic":
            self.detect_label.setText(tr("Site não cadastrado ({host}): será feita uma tentativa com as engines disponíveis.", host=host_of(url)))
        else:
            self.detect_label.setText(tr("Plataforma detectada: {name}", name=provider.name))

    def analyze(self) -> None:
        raw = self.url_input.text().strip()
        if not raw:
            self.detect_label.setText(tr("Cole uma URL para analisar."))
            self.url_input.setFocus()
            return
        try:
            url, provider = self.ctx.media.detect(raw)
        except UMDError as exc:
            show_error(self, exc, tr("Link inválido"))
            return
        if self._cancel is not None:
            self._cancel.set()  # encerra a analise anterior que ainda estiver rodando
        self._analysis_id += 1
        analysis_id = self._analysis_id
        self._cancel = threading.Event()
        name = provider.name if provider.key != "generic" else host_of(url)
        self._set_busy(True, tr("Analisando {name}…", name=name))
        self.result_card.hide()
        self.banner.hide()
        run_task(
            self.ctx.media.analyze, url, self._cancel,
            on_done=lambda info: self._on_analyzed(analysis_id, info),
            on_error=lambda error: self._on_analyze_error(analysis_id, error),
        )

    def cancel_analysis(self) -> None:
        if self._cancel is not None:
            self._cancel.set()
        self._analysis_id += 1
        self._set_busy(False)

    def _set_busy(self, busy: bool, text: str = "") -> None:
        self.busy_row.setVisible(busy)
        self.busy_label.setText(text)
        self.analyze_btn.setEnabled(not busy)

    def _on_analyzed(self, analysis_id: int, info: MediaInfo) -> None:
        if analysis_id != self._analysis_id:
            return
        self._set_busy(False)
        self._show_info(info)

    def _on_analyze_error(self, analysis_id: int, error: UMDError) -> None:
        if analysis_id != self._analysis_id:
            return
        self._set_busy(False)
        if error.code != ErrorCode.CANCELLED:
            show_error(self, error, tr("Não foi possível analisar este link"))

    # ================================================================ resultado

    def _show_info(self, info: MediaInfo) -> None:
        self._info = info
        self._custom_format = None
        self.custom_format_row.hide()
        settings = self.ctx.settings_store.settings

        self.title_label.setText(info.title or tr("(sem título)"))
        self.author_label.setText(tr("Autor: {author}", author=info.author) if info.author else "")
        self.author_label.setVisible(bool(info.author))
        meta: list[str] = []
        if info.is_live:
            meta.append("🔴 " + tr("Ao vivo agora"))
        elif info.duration:
            meta.append(tr("Duração: {value}", value=human_duration(info.duration)))
        if info.view_count is not None:
            meta.append(tr("{value} visualizações", value=_format_count(info.view_count)))
        date = format_date(info.upload_date, info.timestamp)
        if date:
            meta.append(tr("Publicado em {value}", value=format_day(datetime.strptime(date, "%Y-%m-%d"))))
        self.meta_label.setText("  •  ".join(meta))
        self.platform_chip.setText(info.platform_name)
        self.platform_chip.setToolTip(tr("Engine: {engine} • Extrator: {extractor}", engine=info.engine, extractor=info.extractor or "-"))
        self.type_chip.setText(tr(CONTENT_TYPE_LABELS.get(info.content_type, info.content_type.value)))
        notes = [n for n in [info.support_note, *[tr(w) for w in info.warnings]] if n]
        self.note_label.setText("\n".join(f"⚠ {n}" for n in notes))
        self.note_label.setVisible(bool(notes))
        placeholder = {ContentType.AUDIO: "🎵", ContentType.IMAGE: "🖼", ContentType.GALLERY: "🖼"}.get(info.content_type, "🎞")
        thumb = info.thumbnail or next((e.thumbnail for e in info.entries if e.thumbnail), None)
        self.thumb.load(thumb, placeholder)

        post_like = info.engine == "gallery-dl" or info.content_type in MULTI_FILE_TYPES
        has_text = bool(info.description and post_like)
        self.text_container.setVisible(has_text)
        self.text_box.setPlainText((info.description or "")[:5000] if has_text else "")
        self.save_text_check.setVisible(has_text)
        self.save_text_check.setChecked(settings.save_post_text)

        initial = selection_from_settings(settings, info)
        self.mode_combo.blockSignals(True)
        self.mode_combo.clear()
        for choice in mode_choices(info):
            self.mode_combo.addItem(tr(choice.label), choice.key)
        self.mode_combo.setCurrentIndex(max(0, self.mode_combo.findData(initial.mode.value)))
        self.mode_combo.blockSignals(False)
        self.form.setRowVisible(self.mode_combo, self.mode_combo.count() > 1)

        self._fill_entries(info)
        self._fill_formats(info)
        self.folder_edit.setText(settings.download_dir)
        self._refresh_options()
        self.result_card.show()

    def _fill_entries(self, info: MediaInfo) -> None:
        entries = info.entries
        show = len(entries) > 1 or (len(entries) == 1 and info.entries_are_urls)
        self.entries_box.setVisible(show)
        self.entries_list.clear()
        if not show:
            return
        is_post = info.content_type in MULTI_FILE_TYPES
        total = info.total_entries or len(entries)
        if is_post:
            self.entries_label.setText(tr("Este post tem {n} arquivos.", n=len(entries)))
            self.all_radio.setText(tr("Baixar tudo"))
            self.manual_radio.setText(tr("Selecionar arquivos"))
        else:
            self.entries_label.setText(tr("Foram encontrados {n} itens.", n=_format_count(total)))
            self.all_radio.setText(tr("Baixar todos"))
            self.manual_radio.setText(tr("Selecionar manualmente"))
        self.entries_list.blockSignals(True)
        for entry in entries:
            parts = [f"{entry.index:>3}.", _KIND_ICONS.get(entry.kind, ""), entry.title or entry.id or ""]
            extra = []
            if entry.duration:
                extra.append(human_duration(entry.duration))
            if entry.filesize:
                extra.append(human_size(entry.filesize))
            if entry.width and entry.height:
                extra.append(f"{entry.width}×{entry.height}")
            text = " ".join(p for p in parts if p) + (f"   —   {' • '.join(extra)}" if extra else "")
            item = QListWidgetItem(text)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked)
            item.setData(Qt.ItemDataRole.UserRole, entry.url if info.entries_are_urls else entry.index)
            self.entries_list.addItem(item)
        self.entries_list.blockSignals(False)
        self.all_radio.setChecked(True)
        self._on_entries_mode()
        if info.truncated:
            self.truncated_label.setText(tr(
                "Mostrando os primeiros {n} itens (limite configurado em Configurações > Desempenho).", n=len(entries)))
        self.truncated_label.setVisible(info.truncated)

    def _fill_formats(self, info: MediaInfo) -> None:
        formats = sorted(
            info.formats,
            key=lambda f: (f.has_video, f.quality_height or 0, f.fps or 0, f.tbr or f.abr or 0),
            reverse=True,
        )
        self.formats_table.setRowCount(len(formats))
        for row, fmt in enumerate(formats):
            kind = tr("Vídeo + áudio") if fmt.has_video and fmt.has_audio else (tr("Vídeo") if fmt.has_video else tr("Áudio"))
            size = human_size(fmt.filesize) if fmt.filesize else "?"
            if fmt.filesize and fmt.filesize_estimated:
                size = "~" + size
            bitrate = f"{round(fmt.tbr or fmt.abr or 0)} kbps" if (fmt.tbr or fmt.abr) else ""
            values = [
                fmt.format_id, kind, fmt.ext, (f"{fmt.width}×{fmt.height}" if fmt.width and fmt.height else fmt.resolution_label),
                f"{fmt.fps:g}" if fmt.fps else "", fmt.short_vcodec, fmt.short_acodec, bitrate, size,
            ]
            for col, value in enumerate(values):
                item = QTableWidgetItem(str(value or ""))
                item.setData(Qt.ItemDataRole.UserRole, fmt.format_id)
                self.formats_table.setItem(row, col, item)
        has_formats = len(formats) > 1 and info.engine == "yt-dlp"
        self.formats_toggle.setVisible(has_formats)
        self.formats_box.hide()
        self.formats_toggle.setText(tr("Ver todos os formatos disponíveis (avançado)"))

    # ---------------------------------------------------------------- opcoes

    def _current_mode(self) -> SelectionMode:
        data = self.mode_combo.currentData()
        return SelectionMode(data) if data else SelectionMode.FILES

    def _refresh_options(self) -> None:
        info = self._info
        if info is None:
            return
        settings = self.ctx.settings_store.settings
        mode = self._current_mode()
        initial = selection_from_settings(settings, info)
        combos = (self.quality_combo, self.format_combo, self.bitrate_combo, self.video_format_combo)
        for combo in combos:
            combo.blockSignals(True)
            combo.clear()

        quality_label = self.form.labelForField(self.format_combo)
        if mode == SelectionMode.VIDEO:
            for choice in quality_choices(info):
                label = tr(choice.label) + (f"   (~{human_size(choice.size)})" if choice.size else "")
                self.quality_combo.addItem(label, choice.key)
            self.quality_combo.setCurrentIndex(max(0, self.quality_combo.findData(initial.quality)))
            for choice in container_choices(info):
                self.format_combo.addItem(f"{choice.label}  —  {tr(choice.detail)}", choice.key)
            self.format_combo.setCurrentIndex(max(0, self.format_combo.findData(settings.default_video_format)))
            quality_label.setText(tr("Formato:"))
        elif mode == SelectionMode.AUDIO:
            for choice in audio_format_choices(info):
                self.format_combo.addItem(tr(choice.label), choice.key)
            self.format_combo.setCurrentIndex(max(0, self.format_combo.findData(initial.audio_format)))
            for choice in bitrate_choices(info):
                label = tr(choice.label) + (f"   ({tr(choice.detail)})" if choice.detail else "")
                self.bitrate_combo.addItem(label, choice.key)
            self.bitrate_combo.setCurrentIndex(max(0, self.bitrate_combo.findData(settings.default_audio_bitrate)))
            quality_label.setText(tr("Formato do áudio:"))
        else:
            if info.has_images:
                for choice in image_format_choices():
                    self.format_combo.addItem(tr(choice.label), choice.key)
                self.format_combo.setCurrentIndex(max(0, self.format_combo.findData(settings.default_image_format)))
            quality_label.setText(tr("Formato das imagens:"))
            if any(e.kind == MediaKind.VIDEO for e in info.entries) or info.content_type == ContentType.VIDEO:
                self.video_format_combo.addItem(tr("Original (sem conversão)"), "original")
                for key in ("mp4", "mkv", "webm"):
                    self.video_format_combo.addItem(key.upper(), key)

        for combo in combos:
            combo.blockSignals(False)
        self.form.setRowVisible(self.quality_combo, mode == SelectionMode.VIDEO and self.quality_combo.count() > 1)
        self.form.setRowVisible(self.format_combo, self.format_combo.count() > 0)
        self.form.setRowVisible(self.video_format_combo, mode == SelectionMode.FILES and self.video_format_combo.count() > 0)
        self._on_format_changed()

    def _on_quality_changed(self) -> None:
        if self._custom_format is not None:
            self._clear_custom_format()
        self._update_estimate()

    def _on_format_changed(self) -> None:
        lossy = self._current_mode() == SelectionMode.AUDIO and self.format_combo.currentData() in ("mp3", "m4a", "opus")
        self.form.setRowVisible(self.bitrate_combo, lossy)
        self._update_estimate()

    def _on_entries_mode(self) -> None:
        manual = self.manual_radio.isChecked()
        self.entries_list.setEnabled(manual)
        self.check_all_btn.setEnabled(manual)
        self.uncheck_all_btn.setEnabled(manual)
        self._update_estimate()

    def _check_all(self, checked: bool) -> None:
        self.entries_list.blockSignals(True)
        state = Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked
        for i in range(self.entries_list.count()):
            self.entries_list.item(i).setCheckState(state)
        self.entries_list.blockSignals(False)
        self._update_estimate()

    def _toggle_formats(self) -> None:
        visible = not self.formats_box.isVisible()
        self.formats_box.setVisible(visible)
        self.formats_toggle.setText(tr("Ocultar formatos") if visible else tr("Ver todos os formatos disponíveis (avançado)"))

    def _use_selected_format(self) -> None:
        rows = self.formats_table.selectionModel().selectedRows()
        if not rows or self._info is None:
            return
        format_id = self.formats_table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        fmt = next((f for f in self._info.formats if f.format_id == format_id), None)
        if fmt is None:
            return
        if fmt.is_audio_only:
            self.mode_combo.setCurrentIndex(max(0, self.mode_combo.findData(SelectionMode.AUDIO.value)))
            return
        self.mode_combo.setCurrentIndex(max(0, self.mode_combo.findData(SelectionMode.VIDEO.value)))
        self._custom_format = format_id
        extra = "" if fmt.has_audio else tr(" + melhor áudio disponível")
        self.custom_format_label.setText(tr("Formato escolhido manualmente: {id}{extra}", id=format_id, extra=extra))
        self.custom_format_row.show()
        self.form.setRowVisible(self.quality_combo, False)
        self._update_estimate()

    def _clear_custom_format(self) -> None:
        self._custom_format = None
        self.custom_format_row.hide()
        self.form.setRowVisible(self.quality_combo, self._current_mode() == SelectionMode.VIDEO and self.quality_combo.count() > 1)
        self._update_estimate()

    def current_selection(self) -> DownloadSelection:
        info = self._info
        assert info is not None
        settings = self.ctx.settings_store.settings
        selection = selection_from_settings(settings, info)
        selection.mode = self._current_mode()
        if selection.mode == SelectionMode.VIDEO:
            if self._custom_format:
                selection.quality = f"format:{self._custom_format}"
            elif self.quality_combo.count():
                selection.quality = self.quality_combo.currentData() or "best"
            selection.container = self.format_combo.currentData() or settings.default_video_format
        elif selection.mode == SelectionMode.AUDIO:
            selection.audio_format = self.format_combo.currentData() or settings.default_audio_format
            selection.audio_bitrate = self.bitrate_combo.currentData() or settings.default_audio_bitrate
        else:
            selection.image_format = self.format_combo.currentData() or "original"
            selection.container = self.video_format_combo.currentData() or "original"
        if self.entries_box.isVisible() and self.manual_radio.isChecked():
            chosen = [
                self.entries_list.item(i).data(Qt.ItemDataRole.UserRole)
                for i in range(self.entries_list.count())
                if self.entries_list.item(i).checkState() == Qt.CheckState.Checked
            ]
            if info.entries_are_urls:
                selection.item_urls = [str(c) for c in chosen]
                selection.items = [] if not chosen else None
            else:
                selection.items = [int(c) for c in chosen]
        if self.save_text_check.isVisible():
            selection.save_post_text = self.save_text_check.isChecked()
        return selection

    def _update_estimate(self) -> None:
        info = self._info
        if info is None:
            return
        selection = self.current_selection()
        if self.entries_box.isVisible():
            total = self.entries_list.count()
            chosen = len(selection.item_urls or []) if info.entries_are_urls else len(selection.items or [])
            if not self.manual_radio.isChecked():
                chosen = total
            self.selected_label.setText(tr("{n} de {total} selecionados", n=chosen, total=total))
            self.download_btn.setEnabled(chosen > 0)
        else:
            self.download_btn.setEnabled(True)
        if info.is_live:
            self.estimate_label.setText(tr("Transmissão ao vivo: a gravação começa agora e continua até você parar ou a live terminar."))
            return
        size = estimate_size(info, selection)
        if size:
            self.estimate_label.setText(tr("Tamanho estimado: ~{size}", size=human_size(size)))
        else:
            self.estimate_label.setText(tr("Tamanho estimado: desconhecido (a plataforma não informa)"))

    # ================================================================ download

    def _choose_folder(self, target: QLineEdit) -> None:
        start = target.text() or self.ctx.settings_store.settings.download_dir
        folder = QFileDialog.getExistingDirectory(self, tr("Escolha a pasta de destino"), start)
        if folder:
            target.setText(str(Path(folder)))

    def _resolve_folder(self, current: str) -> str | None:
        settings = self.ctx.settings_store.settings
        folder = current.strip() or settings.download_dir
        if settings.ask_folder_each_time:
            chosen = QFileDialog.getExistingDirectory(self, tr("Salvar em…"), folder)
            if not chosen:
                return None
            folder = chosen
        return folder

    def start_download(self) -> None:
        info = self._info
        if info is None:
            return
        selection = self.current_selection()
        if (selection.items is not None and not selection.items and not selection.item_urls) or selection.items == []:
            info_box(self, tr("Nada selecionado"), tr("Selecione pelo menos um item para baixar."))
            return
        folder = self._resolve_folder(self.folder_edit.text())
        if folder is None:
            return
        try:
            request = self.ctx.download.request_from_info(info, selection, folder)
            self.ctx.download.submit(request)
        except UMDError as exc:
            show_error(self, exc, tr("Não foi possível iniciar o download"))
            return
        self._show_banner(tr("✓ “{title}” foi adicionado aos downloads.", title=info.title or info.source_url))
        self.download_submitted.emit()
        # evita adicionar o mesmo download duas vezes por clique duplo
        self.download_btn.setEnabled(False)
        QTimer.singleShot(1500, lambda: self.download_btn.setEnabled(True))

    def _show_banner(self, text: str) -> None:
        self.banner_label.setText(text)
        self.banner.show()

    # ================================================================ lote

    def _batch_preset_changed(self) -> None:
        is_audio = self.batch_preset.currentData() == QualityPreset.AUDIO.value
        self.batch_form.setRowVisible(self.batch_audio, is_audio)

    def _update_batch_count(self) -> None:
        allow_local = self.ctx.settings_store.settings.allow_local_network_urls
        result = parse_url_text(self.batch_edit.toPlainText(), allow_local=allow_local)
        count = len(result.urls)
        if count == 0:
            self.batch_count.setText(tr("Nenhuma URL encontrada."))
        elif count == 1:
            self.batch_count.setText(tr("1 URL encontrada"))
        else:
            self.batch_count.setText(tr("{n} URLs encontradas", n=count))
        notes = []
        if result.invalid:
            notes.append(tr("{n} linha(s) ignorada(s) por não conter uma URL válida.", n=len(result.invalid)))
        if result.duplicates:
            notes.append(tr("{n} URL(s) repetida(s) ignorada(s).", n=result.duplicates))
        self.batch_invalid.setText(" ".join(notes))
        self.batch_invalid.setVisible(bool(notes))
        self.batch_btn.setEnabled(count > 0)

    def _append_urls(self, urls: list[str]) -> None:
        existing = self.batch_edit.toPlainText().rstrip()
        addition = "\n".join(urls)
        self.batch_edit.setPlainText(f"{existing}\n{addition}".strip() if existing else addition)

    def _batch_paste(self) -> None:
        text = QGuiApplication.clipboard().text()
        allow_local = self.ctx.settings_store.settings.allow_local_network_urls
        result = parse_url_text(text, allow_local=allow_local)
        if not result.urls:
            info_box(self, tr("Nenhuma URL"), tr("A área de transferência não contém URLs válidas."))
            return
        self._append_urls(result.urls)

    def _batch_import(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, tr("Importar lista de URLs"), "", tr("Listas de URLs (*.txt *.csv);;Todos os arquivos (*.*)")
        )
        if not path:
            return
        try:
            result = load_url_file(Path(path), allow_local=self.ctx.settings_store.settings.allow_local_network_urls)
        except (OSError, ValueError) as exc:
            info_box(self, tr("Não foi possível importar"), str(exc))
            return
        if not result.urls:
            info_box(self, tr("Nenhuma URL"), tr("O arquivo não contém URLs válidas."))
            return
        self._append_urls(result.urls)

    def start_batch(self) -> None:
        settings = self.ctx.settings_store.settings
        result = parse_url_text(self.batch_edit.toPlainText(), allow_local=settings.allow_local_network_urls)
        if not result.urls:
            return
        folder = self._resolve_folder(self.batch_folder.text())
        if folder is None:
            return
        preset = QualityPreset(self.batch_preset.currentData())
        audio = self.batch_audio.currentData() if preset == QualityPreset.AUDIO else None
        added, failed = 0, []
        for url in result.urls:
            try:
                request = self.ctx.download.quick_request(url, preset=preset, output_dir=folder, audio_format=audio)
                self.ctx.download.submit(request)
                added += 1
            except UMDError as exc:
                failed.append(f"{url}: {tr(exc.message)}")
        if added:
            self._show_banner(tr("✓ {n} download(s) adicionado(s) à fila.", n=added))
            self.batch_edit.clear()
            self.download_submitted.emit()
        if failed:
            info_box(self, tr("Alguns links não foram adicionados"), "\n".join(failed[:20]))
