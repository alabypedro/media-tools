"""Tela Configuracoes (inclui status e atualizacao das engines)."""

from __future__ import annotations

from pathlib import Path

from pydantic import ValidationError
from PySide6.QtCore import QObject, Qt, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from .. import __version__
from ..core import paths
from ..core.config import (
    AUDIO_BITRATES,
    AUDIO_FORMATS,
    COOKIE_BROWSERS,
    IMAGE_FORMATS,
    QUALITY_PRESET_LABELS,
    VIDEO_CODECS,
    VIDEO_CONTAINERS,
    QualityPreset,
    Settings,
)
from ..core.exceptions import UMDError
from ..core.i18n import LANGUAGES, current_language, tr
from ..core.naming import TEMPLATE_FIELDS, TemplateError, build_fields, render_template
from ..services.context import AppContext
from ..services.engine_service import UPDATABLE, EngineStatus, UpdateInfo
from .theme import apply_theme
from .widgets import button, confirm, info_box, muted, page_header, reveal_in_folder, run_task, show_error

_BROWSER_LABELS = {
    "": "Nenhum (não usar cookies)", "firefox": "Firefox", "chrome": "Google Chrome", "edge": "Microsoft Edge",
    "brave": "Brave", "opera": "Opera", "vivaldi": "Vivaldi", "chromium": "Chromium", "safari": "Safari (macOS)",
}
_CONTAINER_LABELS = {"mp4": "MP4", "mkv": "MKV", "webm": "WEBM", "original": "Original (sem conversão)"}
_CODEC_LABELS = {"auto": "Automático (melhor qualidade)", "h264": "H.264 (máxima compatibilidade)", "vp9": "VP9", "av1": "AV1"}
_AUDIO_LABELS = {"mp3": "MP3", "m4a": "M4A (AAC)", "opus": "OPUS", "wav": "WAV", "flac": "FLAC (só com fonte sem perdas)",
                 "original": "Original (sem conversão)"}
_BITRATE_LABELS = {"320": "320 kbps", "256": "256 kbps", "192": "192 kbps", "128": "128 kbps", "original": "Melhor possível (VBR)"}
_IMAGE_LABELS = {"original": "Original", "jpg": "JPG", "png": "PNG", "webp": "WEBP"}
_THEME_LABELS = {"system": "Igual ao sistema", "light": "Claro", "dark": "Escuro"}
_ENGINE_LABELS = {"yt-dlp": "yt-dlp", "gallery-dl": "gallery-dl", "yt-dlp-ejs": "yt-dlp-ejs (YouTube)"}


def _combo(options: dict[str, str] | list[tuple[str, str]]) -> QComboBox:
    combo = QComboBox()
    items = options.items() if isinstance(options, dict) else options
    for key, label in items:
        combo.addItem(tr(label), key)
    combo.setMinimumWidth(260)
    return combo


def _select(combo: QComboBox, value: str) -> None:
    combo.setCurrentIndex(max(0, combo.findData(value)))


def _spin(minimum: int, maximum: int, suffix: str = "", special: str | None = None) -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(minimum, maximum)
    if suffix:
        spin.setSuffix(f" {suffix}")
    if special:
        spin.setSpecialValueText(special)
    spin.setMinimumWidth(160)
    return spin


class _Progress(QObject):
    message = Signal(str)


class PlatformsDialog(QDialog):
    """Suporte REAL: pergunta aos extratores das engines instaladas."""

    def __init__(self, ctx: AppContext, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(tr("Plataformas suportadas"))
        self.resize(760, 540)
        layout = QVBoxLayout(self)
        layout.addWidget(muted(tr(
            "Esta lista é gerada consultando os extratores das engines instaladas agora. "
            "Plataformas sem extrator dedicado ainda são tentadas com o extrator genérico, mas podem não funcionar. "
            "Além destas, qualquer site suportado pelo yt-dlp (1800+) ou gallery-dl funciona pelo modo genérico."
        )))
        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels([tr("Plataforma"), "yt-dlp", "gallery-dl", tr("Situação")])
        self.table.verticalHeader().setVisible(False)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeMode.Stretch)
        layout.addWidget(self.table, 1)
        self.status = muted(tr("Consultando as engines…"))
        layout.addWidget(self.status)
        close = button(tr("Fechar"), "primary")
        close.clicked.connect(self.accept)
        layout.addWidget(close, 0, Qt.AlignmentFlag.AlignRight)
        run_task(ctx.engines.platform_support, on_done=self._fill, on_error=self._failed)

    def _fill(self, rows: list[dict]) -> None:
        self.table.setRowCount(len(rows))
        for index, row in enumerate(rows):
            yt = ", ".join(row["yt-dlp"]) or ("⚠ " + ", ".join(row["yt-dlp-broken"]) if row["yt-dlp-broken"] else "—")
            gallery = row["gallery-dl"] or "—"
            if row["yt-dlp"] or row["gallery-dl"]:
                situation = tr("✓ Suportado (extrator dedicado)")
            elif row["yt-dlp-broken"]:
                situation = tr("⚠ Extrator marcado como quebrado pela própria engine")
            else:
                situation = tr("✕ Sem extrator dedicado: só tentativa genérica")
            for column, value in enumerate([row["name"], yt, gallery, situation]):
                self.table.setItem(index, column, QTableWidgetItem(value))
        self.status.setText(tr("{n} plataformas cadastradas.", n=len(rows)))

    def _failed(self, error: UMDError) -> None:
        self.status.setText(tr(error.message))


class SettingsPage(QWidget):
    settings_saved = Signal()
    check_app_update = Signal()  # "Verificar agora" (a janela principal faz a verificacao)

    def __init__(self, ctx: AppContext, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self._updates: dict[str, UpdateInfo] = {}
        self._versions: dict[str, EngineStatus] = {}
        self._engines_loaded = False
        self._progress = _Progress()
        self._progress.message.connect(lambda msg: self.update_status.setText(tr(msg)))
        self._build()
        self.load(ctx.settings_store.settings)

    # ================================================================ layout

    def _build(self) -> None:
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.Shape.NoFrame)
        outer.addWidget(scroll, 1)
        content = QWidget()
        scroll.setWidget(content)
        layout = QVBoxLayout(content)
        layout.setContentsMargins(28, 24, 28, 24)
        layout.setSpacing(8)
        layout.addWidget(page_header(tr("Configurações")))

        # --- Downloads
        box, form = self._group(tr("Downloads"))
        folder_row = QHBoxLayout()
        self.download_dir = QLineEdit()
        folder_row.addWidget(self.download_dir, 1)
        browse = button(tr("Escolher…"))
        browse.clicked.connect(self._choose_download_dir)
        folder_row.addWidget(browse)
        form.addRow(tr("Pasta padrão:"), folder_row)
        self.ask_folder = QCheckBox(tr("Perguntar a pasta antes de cada download"))
        form.addRow("", self.ask_folder)
        self.create_subfolders = QCheckBox(tr("Organizar em subpastas conforme o template"))
        self.create_subfolders.toggled.connect(self._update_template_preview)
        form.addRow("", self.create_subfolders)
        self.template = QLineEdit()
        self.template.textChanged.connect(self._update_template_preview)
        form.addRow(tr("Template de nome:"), self.template)
        self.template_preview = muted(wrap=False)
        form.addRow("", self.template_preview)
        fields_help = "  ".join(f"{{{name}}}" for name in TEMPLATE_FIELDS)
        help_label = muted(tr("Campos disponíveis: {fields}", fields=fields_help), wrap=False)
        help_label.setToolTip("\n".join(f"{{{k}}} — {tr(v)}" for k, v in TEMPLATE_FIELDS.items()))
        form.addRow("", help_label)
        self.concurrent = _spin(1, 10)
        form.addRow(tr("Downloads simultâneos:"), self.concurrent)
        self.save_post_text = QCheckBox(tr("Salvar o texto de posts em um arquivo .txt junto da mídia"))
        form.addRow("", self.save_post_text)
        layout.addWidget(box)

        # --- Video / audio / imagem
        box, form = self._group(tr("Vídeo"))
        self.quality = _combo({p.value: label for p, label in QUALITY_PRESET_LABELS.items()})
        form.addRow(tr("Qualidade padrão:"), self.quality)
        self.video_format = _combo({k: _CONTAINER_LABELS[k] for k in VIDEO_CONTAINERS})
        form.addRow(tr("Formato padrão:"), self.video_format)
        self.video_codec = _combo({k: _CODEC_LABELS[k] for k in VIDEO_CODECS})
        form.addRow(tr("Codec preferido:"), self.video_codec)
        form.addRow(muted(tr("A melhor qualidade evita recompressão: quando possível, só o contêiner é trocado (sem perda).")))
        layout.addWidget(box)

        box, form = self._group(tr("Áudio e imagens"))
        self.audio_format = _combo({k: _AUDIO_LABELS[k] for k in AUDIO_FORMATS})
        form.addRow(tr("Formato de áudio padrão:"), self.audio_format)
        self.audio_bitrate = _combo({k: _BITRATE_LABELS[k] for k in AUDIO_BITRATES})
        form.addRow(tr("Bitrate padrão:"), self.audio_bitrate)
        self.embed_metadata = QCheckBox(tr("Gravar título/artista/capítulos nos arquivos (sem recompressão)"))
        form.addRow("", self.embed_metadata)
        self.image_format = _combo({k: _IMAGE_LABELS[k] for k in IMAGE_FORMATS})
        form.addRow(tr("Formato de imagem padrão:"), self.image_format)
        layout.addWidget(box)

        # --- Desempenho
        box, form = self._group(tr("Desempenho"))
        self.rate_limit = _spin(0, 10_000_000, "KB/s", tr("Sem limite"))
        form.addRow(tr("Limite de velocidade (por download):"), self.rate_limit)
        self.fragments = _spin(1, 16)
        form.addRow(tr("Conexões simultâneas (quando suportado):"), self.fragments)
        self.retries = _spin(0, 20)
        form.addRow(tr("Tentativas automáticas:"), self.retries)
        self.retry_delay = _spin(0, 600, "s")
        form.addRow(tr("Espera entre tentativas:"), self.retry_delay)
        self.interval = QDoubleSpinBox()
        self.interval.setRange(0, 3600)
        self.interval.setDecimals(1)
        self.interval.setSuffix(" s")
        self.interval.setMinimumWidth(160)
        form.addRow(tr("Intervalo entre downloads:"), self.interval)
        self.resume = QCheckBox(tr("Continuar downloads interrompidos de onde pararam (quando suportado)"))
        form.addRow("", self.resume)
        self.max_size = _spin(0, 1_000_000, "MB", tr("Sem limite"))
        form.addRow(tr("Tamanho máximo por arquivo:"), self.max_size)
        self.analysis_timeout = _spin(10, 900, "s")
        form.addRow(tr("Tempo limite da análise:"), self.analysis_timeout)
        self.socket_timeout = _spin(5, 300, "s")
        form.addRow(tr("Tempo limite de conexão:"), self.socket_timeout)
        self.max_items = _spin(1, 5000)
        form.addRow(tr("Máximo de itens por playlist/perfil:"), self.max_items)
        layout.addWidget(box)

        # --- Contas e cookies
        box, form = self._group(tr("Contas e cookies"))
        form.addRow(muted(tr(
            "O aplicativo nunca pede nem guarda sua senha. Para conteúdo ao qual você JÁ tem acesso (ex.: conta "
            "própria), ele pode usar a sessão que você já tem aberta no navegador. Os cookies são lidos só na hora "
            "do download, nunca são gravados pelo app nem aparecem nos logs. Não use para acessar contas de terceiros."
        )))
        self.cookie_browser = _combo({k: _BROWSER_LABELS[k] for k in COOKIE_BROWSERS})
        form.addRow(tr("Usar sessão do navegador:"), self.cookie_browser)
        self.cookie_profile = QLineEdit()
        self.cookie_profile.setPlaceholderText(tr("Perfil padrão"))
        form.addRow(tr("Perfil do navegador (opcional):"), self.cookie_profile)
        cookie_row = QHBoxLayout()
        self.cookie_file = QLineEdit()
        self.cookie_file.setPlaceholderText(tr("Opcional: arquivo cookies.txt exportado por você"))
        cookie_row.addWidget(self.cookie_file, 1)
        pick = button(tr("Escolher…"))
        pick.clicked.connect(self._choose_cookie_file)
        cookie_row.addWidget(pick)
        form.addRow(tr("Arquivo de cookies:"), cookie_row)
        form.addRow(muted(tr("Chrome e Edge bloqueiam a leitura dos cookies enquanto estão abertos; o Firefox costuma funcionar melhor.")))
        layout.addWidget(box)

        # --- Interface
        box, form = self._group(tr("Interface"))
        self.theme = _combo(_THEME_LABELS)
        form.addRow(tr("Tema:"), self.theme)
        self.language = _combo(LANGUAGES)
        form.addRow(tr("Idioma:"), self.language)
        form.addRow("", muted(tr("A troca de idioma vale a partir da próxima abertura do aplicativo."), wrap=False))
        layout.addWidget(box)

        # --- Engines
        engines_box = QGroupBox(tr("Engines"))
        engines = QVBoxLayout(engines_box)
        self.engine_grid = QGridLayout()
        self.engine_grid.setHorizontalSpacing(18)
        self.engine_grid.setVerticalSpacing(8)
        engines.addLayout(self.engine_grid)
        self._engine_rows: dict[str, tuple[QLabel, QLabel, QLabel, object]] = {}
        names = ["yt-dlp", "gallery-dl", "yt-dlp-ejs", "ffmpeg", "js"]
        titles = {"ffmpeg": "FFmpeg", "js": tr("Runtime JavaScript")}
        for row, name in enumerate(names):
            label = QLabel(_ENGINE_LABELS.get(name, titles.get(name, name)))
            label.setStyleSheet("font-weight: 600;")
            status = QLabel("…")
            detail = muted()
            self.engine_grid.addWidget(label, row, 0)
            self.engine_grid.addWidget(status, row, 1)
            self.engine_grid.addWidget(detail, row, 2)
            action = None
            if name in UPDATABLE:
                action = button(tr("Atualizar"))
                action.setVisible(False)
                action.clicked.connect(lambda _=False, n=name: self._apply_update(n))
                self.engine_grid.addWidget(action, row, 3)
            self._engine_rows[name] = (label, status, detail, action)
        self.engine_grid.setColumnStretch(2, 1)

        ffmpeg_row = QHBoxLayout()
        self.ffmpeg_path = QLineEdit()
        self.ffmpeg_path.setPlaceholderText(tr("Automático (embutido ou do sistema)"))
        ffmpeg_row.addWidget(QLabel(tr("Caminho do FFmpeg:")))
        ffmpeg_row.addWidget(self.ffmpeg_path, 1)
        pick_ffmpeg = button(tr("Escolher…"))
        pick_ffmpeg.clicked.connect(self._choose_ffmpeg)
        ffmpeg_row.addWidget(pick_ffmpeg)
        engines.addLayout(ffmpeg_row)

        self.update_status = muted()
        engines.addWidget(self.update_status)
        engine_buttons = QHBoxLayout()
        refresh = button(tr("Atualizar status"))
        refresh.clicked.connect(self.refresh_engines)
        self.check_btn = button(tr("Verificar atualizações"))
        self.check_btn.clicked.connect(self.check_updates)
        reset = button(tr("Restaurar versões embutidas"))
        reset.clicked.connect(self._reset_engines)
        platforms = button(tr("Plataformas suportadas…"))
        platforms.clicked.connect(lambda: PlatformsDialog(self.ctx, self).exec())
        logs = button(tr("Abrir pasta de logs"))
        logs.clicked.connect(lambda: reveal_in_folder(self, paths.logs_dir()))
        for btn in (refresh, self.check_btn, reset, platforms, logs):
            engine_buttons.addWidget(btn)
        engine_buttons.addStretch(1)
        engines.addLayout(engine_buttons)
        self.check_on_start = QCheckBox(tr("Verificar atualizações das engines ao abrir o aplicativo"))
        engines.addWidget(self.check_on_start)
        layout.addWidget(engines_box)

        # --- Atualizacoes do programa (GitHub Releases)
        box, form = self._group(tr("Atualizações do programa"))
        version_row = QHBoxLayout()
        version_row.addWidget(muted(tr("Versão instalada: {version}", version=__version__), wrap=False))
        check_now = button(tr("Verificar agora"))
        check_now.clicked.connect(self.check_app_update.emit)
        version_row.addWidget(check_now)
        version_row.addStretch(1)
        form.addRow(version_row)
        self.check_app_on_start = QCheckBox(tr("Avisar quando houver versão nova do programa (verifica ao abrir)"))
        form.addRow("", self.check_app_on_start)
        form.addRow("", muted(tr("Nada é baixado nem instalado sem a sua confirmação.")))
        layout.addWidget(box)

        # --- Avancado
        box, form = self._group(tr("Avançado"))
        self.allow_local = QCheckBox(tr("Permitir URLs da rede local (localhost, 192.168.x.x…)"))
        form.addRow("", self.allow_local)
        form.addRow(tr("Pasta de dados do app:"), muted(str(paths.app_data_dir())))
        layout.addWidget(box)
        layout.addStretch(1)

        # --- barra fixa
        bar = QFrame()
        bar.setObjectName("Card")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(16, 10, 16, 10)
        self.save_status = muted(wrap=False)
        bar_layout.addWidget(self.save_status, 1)
        defaults = button(tr("Restaurar padrões"))
        defaults.clicked.connect(self._restore_defaults)
        save = button(tr("Salvar"), "primary")
        save.clicked.connect(self.save)
        bar_layout.addWidget(defaults)
        bar_layout.addWidget(save)
        outer.addWidget(bar)

    def _group(self, title: str) -> tuple[QGroupBox, QFormLayout]:
        box = QGroupBox(title)
        form = QFormLayout(box)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(8)
        return box, form

    # ================================================================ carregar / salvar

    def load(self, s: Settings) -> None:
        self.download_dir.setText(s.download_dir)
        self.ask_folder.setChecked(s.ask_folder_each_time)
        self.create_subfolders.setChecked(s.create_subfolders)
        self.template.setText(s.filename_template)
        self.concurrent.setValue(s.max_concurrent_downloads)
        self.save_post_text.setChecked(s.save_post_text)
        _select(self.quality, s.default_quality.value)
        _select(self.video_format, s.default_video_format)
        _select(self.video_codec, s.preferred_video_codec)
        _select(self.audio_format, s.default_audio_format)
        _select(self.audio_bitrate, s.default_audio_bitrate)
        self.embed_metadata.setChecked(s.embed_metadata)
        _select(self.image_format, s.default_image_format)
        self.rate_limit.setValue(s.rate_limit_kbps)
        self.fragments.setValue(s.concurrent_fragments)
        self.retries.setValue(s.retries)
        self.retry_delay.setValue(s.retry_delay_seconds)
        self.interval.setValue(s.download_interval_seconds)
        self.resume.setChecked(s.resume_partial)
        self.max_size.setValue(s.max_file_size_mb)
        self.analysis_timeout.setValue(s.analysis_timeout_seconds)
        self.socket_timeout.setValue(s.socket_timeout_seconds)
        self.max_items.setValue(s.max_playlist_items)
        _select(self.cookie_browser, s.cookies_browser)
        self.cookie_profile.setText(s.cookies_browser_profile)
        self.cookie_file.setText(s.cookies_file)
        _select(self.theme, s.theme)
        _select(self.language, s.language)
        self.ffmpeg_path.setText(s.ffmpeg_path)
        self.check_on_start.setChecked(s.check_updates_on_start)
        self.check_app_on_start.setChecked(s.check_app_updates_on_start)
        self.allow_local.setChecked(s.allow_local_network_urls)
        self._update_template_preview()

    def collect(self) -> dict:
        return {
            "download_dir": self.download_dir.text().strip(),
            "ask_folder_each_time": self.ask_folder.isChecked(),
            "create_subfolders": self.create_subfolders.isChecked(),
            "filename_template": self.template.text().strip(),
            "max_concurrent_downloads": self.concurrent.value(),
            "save_post_text": self.save_post_text.isChecked(),
            "default_quality": QualityPreset(self.quality.currentData()),
            "default_video_format": self.video_format.currentData(),
            "preferred_video_codec": self.video_codec.currentData(),
            "default_audio_format": self.audio_format.currentData(),
            "default_audio_bitrate": self.audio_bitrate.currentData(),
            "embed_metadata": self.embed_metadata.isChecked(),
            "default_image_format": self.image_format.currentData(),
            "rate_limit_kbps": self.rate_limit.value(),
            "concurrent_fragments": self.fragments.value(),
            "retries": self.retries.value(),
            "retry_delay_seconds": self.retry_delay.value(),
            "download_interval_seconds": self.interval.value(),
            "resume_partial": self.resume.isChecked(),
            "max_file_size_mb": self.max_size.value(),
            "analysis_timeout_seconds": self.analysis_timeout.value(),
            "socket_timeout_seconds": self.socket_timeout.value(),
            "max_playlist_items": self.max_items.value(),
            "cookies_browser": self.cookie_browser.currentData(),
            "cookies_browser_profile": self.cookie_profile.text().strip(),
            "cookies_file": self.cookie_file.text().strip(),
            "theme": self.theme.currentData(),
            "language": self.language.currentData(),
            "ffmpeg_path": self.ffmpeg_path.text().strip(),
            "check_updates_on_start": self.check_on_start.isChecked(),
            "check_app_updates_on_start": self.check_app_on_start.isChecked(),
            "allow_local_network_urls": self.allow_local.isChecked(),
        }

    def save(self) -> None:
        store = self.ctx.settings_store
        previous = store.settings
        values = self.collect()
        if not values["download_dir"]:
            info_box(self, tr("Pasta obrigatória"), tr("Escolha uma pasta padrão para os downloads."))
            return
        if values["cookies_file"] and not Path(values["cookies_file"]).is_file():
            info_box(self, tr("Arquivo não encontrado"), tr("O arquivo de cookies informado não existe."))
            return
        if values["ffmpeg_path"] and not Path(values["ffmpeg_path"]).exists():
            info_box(self, tr("Arquivo não encontrado"), tr("O caminho do FFmpeg informado não existe."))
            return
        try:
            Path(values["download_dir"]).mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            info_box(self, tr("Pasta inválida"), tr("Não foi possível usar a pasta escolhida:\n{error}", error=str(exc)))
            return
        try:
            settings = Settings(**{**previous.model_dump(), **values})
        except ValidationError as exc:
            problems = "\n".join(f"• {err['loc'][0]}: {err['msg']}" for err in exc.errors())
            info_box(self, tr("Configuração inválida"), tr("Corrija os campos abaixo:\n{problems}", problems=problems))
            return
        store.save(settings)
        if settings.theme != previous.theme:
            app = QApplication.instance()
            if isinstance(app, QApplication):
                apply_theme(app, settings.theme)
        message = tr("Configurações salvas.")
        if settings.language != current_language():
            message += " " + tr("Reinicie o aplicativo para aplicar o novo idioma.")
        self.save_status.setText(message)
        if settings.ffmpeg_path != previous.ffmpeg_path:
            self.refresh_engines()
        self.settings_saved.emit()

    def _restore_defaults(self) -> None:
        if confirm(self, tr("Restaurar padrões"), tr("Voltar todas as configurações para os valores padrão? (É preciso clicar em Salvar depois.)")):
            self.load(Settings())
            self.save_status.setText(tr("Valores padrão carregados — clique em Salvar para aplicar."))

    def _update_template_preview(self) -> None:
        fields = build_fields(title="Meu vídeo", author="Canal", platform="YouTube", date="2026-09-20", id="abc123",
                              index="01", playlist="Minha playlist", type="video", quality="1080p", ext="mp4")
        try:
            relative = render_template(self.template.text(), fields, create_subfolders=self.create_subfolders.isChecked())
            self.template_preview.setObjectName("Muted")
            self.template_preview.setText(tr("Exemplo: {path}", path=str(Path("Downloads") / relative)))
        except TemplateError as exc:
            self.template_preview.setObjectName("ErrorText")
            self.template_preview.setText(tr(str(exc)))
        self.template_preview.style().unpolish(self.template_preview)
        self.template_preview.style().polish(self.template_preview)

    # ---------------------------------------------------------------- escolha de arquivos

    def _choose_download_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("Pasta padrão de downloads"), self.download_dir.text())
        if folder:
            self.download_dir.setText(str(Path(folder)))

    def _choose_cookie_file(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("Arquivo de cookies (formato Netscape)"), "",
                                              tr("Cookies (*.txt);;Todos os arquivos (*.*)"))
        if path:
            self.cookie_file.setText(str(Path(path)))

    def _choose_ffmpeg(self) -> None:
        path, _ = QFileDialog.getOpenFileName(self, tr("Localizar o FFmpeg"), "", "ffmpeg (ffmpeg*);;*.*")
        if path:
            self.ffmpeg_path.setText(str(Path(path)))

    # ================================================================ engines

    def showEvent(self, event) -> None:  # noqa: N802 - API Qt
        super().showEvent(event)
        if not self._engines_loaded:
            self._engines_loaded = True
            self.refresh_engines()

    def refresh_engines(self) -> None:
        for name in ("yt-dlp", "gallery-dl", "yt-dlp-ejs", "ffmpeg", "js"):
            self._engine_rows[name][1].setText("…")
        run_task(self._collect_engine_status, on_done=self._show_engine_status,
                 on_error=lambda err: self.update_status.setText(tr(err.message)))

    def _collect_engine_status(self) -> dict:
        return {
            "versions": self.ctx.engines.versions(),
            "ffmpeg": self.ctx.engines.ffmpeg(),
            "js": self.ctx.engines.js_runtimes(),
        }

    def _show_engine_status(self, data: dict) -> None:
        self._versions = data["versions"]
        for name in ("yt-dlp", "gallery-dl", "yt-dlp-ejs"):
            _label, status, detail, _action = self._engine_rows[name]
            info = self._versions.get(name)
            if info and info.ok:
                status.setText(f"✓ {info.version}")
                status.setObjectName("SuccessText")
                detail.setText(tr("versão atualizada pelo app") if info.override else tr("versão embutida"))
            else:
                status.setText("✕ " + tr("não encontrado"))
                status.setObjectName("ErrorText")
                detail.setText(info.error if info and info.error else "")
            status.style().unpolish(status)
            status.style().polish(status)
        ffmpeg = data["ffmpeg"]
        _l, status, detail, _a = self._engine_rows["ffmpeg"]
        sources = {"custom": tr("configurado"), "bundled": tr("embutido"), "system": tr("do sistema"), "imageio": tr("embutido (imageio-ffmpeg)")}
        if ffmpeg.ok:
            status.setText(f"✓ {ffmpeg.version}")
            status.setObjectName("SuccessText")
            detail.setText(f"{sources.get(ffmpeg.source, ffmpeg.source)} — {ffmpeg.path}")
        else:
            status.setText("✕ " + tr("não encontrado"))
            status.setObjectName("ErrorText")
            detail.setText(tr("Necessário para juntar áudio e vídeo e converter formatos."))
        _l, js_status, js_detail, _a = self._engine_rows["js"]
        runtimes = data["js"]
        if runtimes:
            js_status.setText("✓ " + ", ".join(runtimes))
            js_status.setObjectName("SuccessText")
            js_detail.setText(tr("Usado pelo yt-dlp para liberar todos os formatos do YouTube."))
        else:
            js_status.setText("⚠ " + tr("não encontrado"))
            js_status.setObjectName("WarningText")
            js_detail.setText(tr("Sem Deno ou Node.js instalado, alguns formatos do YouTube podem não aparecer. Instale o Deno (recomendado)."))
        for widget in (status, js_status):
            widget.style().unpolish(widget)
            widget.style().polish(widget)

    def check_updates(self) -> None:
        self.check_btn.setEnabled(False)
        self.update_status.setText(tr("Consultando versões mais recentes…"))
        current = {name: (self._versions.get(name).version if self._versions.get(name) else None) for name in UPDATABLE}

        def work() -> dict[str, UpdateInfo]:
            return {name: self.ctx.engines.check_update(name, current.get(name)) for name in UPDATABLE}

        run_task(work, on_done=self._show_updates, on_error=self._update_failed)

    def _show_updates(self, updates: dict[str, UpdateInfo]) -> None:
        self.check_btn.setEnabled(True)
        self._updates = updates
        available = []
        for name, update in updates.items():
            _label, status, detail, action = self._engine_rows[name]
            if update.available:
                available.append(name)
                detail.setText(tr("Versão atual: {current} • Nova versão: {latest}", current=update.current or "?", latest=update.latest))
                action.setVisible(True)
                action.setEnabled(True)
            else:
                detail.setText(tr("Versão atual: {current} • Você já tem a versão mais recente.", current=update.current or "?"))
                action.setVisible(False)
        self.update_status.setText(
            tr("Atualizações disponíveis: {names}", names=", ".join(available)) if available else tr("Todas as engines estão atualizadas.")
        )

    def _update_failed(self, error: UMDError) -> None:
        self.check_btn.setEnabled(True)
        self.update_status.setText(tr("Não foi possível verificar atualizações: {msg}", msg=tr(error.message)))

    def _apply_update(self, name: str) -> None:
        update = self._updates.get(name)
        if update is None:
            return
        action = self._engine_rows[name][3]
        action.setEnabled(False)
        run_task(
            self.ctx.engines.apply_update, update, self._progress.message.emit,
            on_done=lambda version: self._update_done(name, version),
            on_error=lambda err: self._update_error(name, err),
        )

    def _update_done(self, name: str, version: str) -> None:
        self.update_status.setText(tr("{name} atualizado para a versão {version}. Os próximos downloads já usam a nova versão.",
                                      name=name, version=version))
        self._engine_rows[name][3].setVisible(False)
        self.refresh_engines()

    def _update_error(self, name: str, error: UMDError) -> None:
        self._engine_rows[name][3].setEnabled(True)
        self.update_status.setText("")
        show_error(self, error, tr("Falha ao atualizar {name}", name=name))

    def _reset_engines(self) -> None:
        if not confirm(self, tr("Restaurar versões embutidas"), tr("Descartar as versões atualizadas e voltar às que vieram com o aplicativo?")):
            return
        for name in UPDATABLE:
            self.ctx.engines.reset(name)
        self.update_status.setText(tr("Versões embutidas restauradas."))
        self.refresh_engines()
