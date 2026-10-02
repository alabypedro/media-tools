"""Janela principal: navegacao lateral + paginas."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QByteArray, QSettings, Qt, QTimer
from PySide6.QtGui import QCloseEvent, QDragEnterEvent, QDropEvent, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from .. import APP_ID, APP_NAME, LEGACY_APP_ID, __version__
from ..core.i18n import tr
from ..core.security import extract_urls
from ..downloader.job import JobSnapshot, JobStatus
from ..services.context import AppContext
from ..services.engine_service import UPDATABLE
from .app_update import AppUpdater
from .bridge import ManagerBridge
from .converter import ConverterPage
from .downloads import DownloadsPage
from .editor import EditorPage
from .history import HistoryPage
from .home import HomePage
from .library import LibraryPage
from .settings import PlatformsDialog, SettingsPage
from .widgets import Debouncer, confirm, reveal_in_folder, run_task

PAGES = ("home", "downloads", "converter", "editor", "history", "library", "settings")


class MainWindow(QMainWindow):
    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self.setWindowTitle(APP_NAME)
        self.setMinimumSize(1000, 680)
        self.resize(1240, 820)
        self.setAcceptDrops(True)
        self.bridge = ManagerBridge(ctx.manager, self)
        self._closing_for_update = False
        self.updater = AppUpdater(self)
        self.updater.update_found.connect(self._show_update_badge)

        central = QWidget()
        root = QHBoxLayout(central)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.setCentralWidget(central)

        root.addWidget(self._build_sidebar())
        self.stack = QStackedWidget()
        root.addWidget(self.stack, 1)

        self.home = HomePage(ctx)
        self.downloads = DownloadsPage(ctx.manager, self.bridge, self._open_downloads_folder)
        self.converter = ConverterPage(ctx)
        self.editor = EditorPage(ctx)
        self.history = HistoryPage(ctx)
        self.library = LibraryPage(ctx)
        self.settings_page = SettingsPage(ctx)
        for page in (self.home, self.downloads, self.converter, self.editor, self.history, self.library,
                     self.settings_page):
            self.stack.addWidget(page)

        self.status_label = QLabel()
        self.statusBar().addPermanentWidget(self.status_label)

        self.home.show_downloads.connect(lambda: self.navigate("downloads"))
        self.home.download_submitted.connect(self._on_submitted)
        self.history.redownloaded.connect(lambda: self.navigate("downloads"))
        self.library.convert_requested.connect(self.convert_files)
        self.settings_page.settings_saved.connect(self.home.reload_defaults)
        self.settings_page.check_app_update.connect(lambda: self.updater.check(manual=True))
        self.downloads.counts_changed.connect(self._on_counts)
        self.bridge.job_finished.connect(self._on_job_finished)
        self._refresh_lists = Debouncer(600, self._reload_visible_lists, self)

        QShortcut(QKeySequence("Ctrl+L"), self, activated=self._focus_url)
        self.navigate("home")
        self._restore_geometry()
        self._on_counts(ctx.manager.counts())
        QTimer.singleShot(400, self._startup_notices)

    # ------------------------------------------------------------ layout

    def _build_sidebar(self) -> QFrame:
        sidebar = QFrame()
        sidebar.setObjectName("Sidebar")
        sidebar.setFixedWidth(230)
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(12, 18, 12, 12)
        layout.setSpacing(4)
        title = QLabel(APP_NAME.replace(" Tools", "\nTools"))
        title.setObjectName("AppTitle")
        layout.addWidget(title)
        subtitle = QLabel(tr("Baixe e converta vídeos, áudios, imagens e documentos"))
        subtitle.setWordWrap(True)
        subtitle.setObjectName("AppSubtitle")
        layout.addWidget(subtitle)

        self.nav_group = QButtonGroup(self)
        self.nav_buttons: dict[str, QPushButton] = {}
        labels = {
            "home": "🏠   " + tr("Início"),
            "downloads": "⬇   " + tr("Downloads"),
            "converter": "🔄   " + tr("Converter"),
            "editor": "🎞   " + tr("Editor"),
            "history": "📜   " + tr("Histórico"),
            "library": "📁   " + tr("Biblioteca"),
            "settings": "⚙   " + tr("Configurações"),
        }
        for key in PAGES:
            btn = QPushButton(labels[key])
            btn.setObjectName("NavButton")
            btn.setCheckable(True)
            btn.setCursor(Qt.CursorShape.PointingHandCursor)
            btn.clicked.connect(lambda _=False, k=key: self.navigate(k))
            self.nav_group.addButton(btn)
            self.nav_buttons[key] = btn
            layout.addWidget(btn)
        self._nav_labels = labels
        layout.addStretch(1)

        # destaque que so aparece quando ha versao nova do programa
        self.update_badge = QPushButton()
        self.update_badge.setProperty("variant", "primary")
        self.update_badge.setCursor(Qt.CursorShape.PointingHandCursor)
        self.update_badge.clicked.connect(lambda: self.updater.offer())
        self.update_badge.hide()
        layout.addWidget(self.update_badge)
        platforms = QPushButton(tr("Plataformas suportadas"))
        platforms.setProperty("variant", "link")
        platforms.setCursor(Qt.CursorShape.PointingHandCursor)
        platforms.clicked.connect(lambda: PlatformsDialog(self.ctx, self).exec())
        layout.addWidget(platforms, 0, Qt.AlignmentFlag.AlignLeft)
        check_updates = QPushButton(tr("Verificar atualizações"))
        check_updates.setProperty("variant", "link")
        check_updates.setCursor(Qt.CursorShape.PointingHandCursor)
        check_updates.clicked.connect(lambda: self.updater.check(manual=True))
        layout.addWidget(check_updates, 0, Qt.AlignmentFlag.AlignLeft)
        version = QLabel(f"v{__version__}")
        version.setObjectName("AppSubtitle")
        layout.addWidget(version)
        return sidebar

    def navigate(self, key: str) -> None:
        index = PAGES.index(key)
        self.stack.setCurrentIndex(index)
        self.nav_buttons[key].setChecked(True)

    def _focus_url(self) -> None:
        self.navigate("home")
        self.home.tabs.setCurrentIndex(0)
        self.home.url_input.setFocus()
        self.home.url_input.selectAll()

    # ------------------------------------------------------------ eventos

    def _on_submitted(self) -> None:
        self.statusBar().showMessage(tr("Download adicionado à fila."), 4000)

    def _on_counts(self, counts: dict) -> None:
        active = counts.get("active", 0) + counts.get("pending", 0)
        label = self._nav_labels["downloads"] + (f"  ({active})" if active else "")
        self.nav_buttons["downloads"].setText(label)
        parts = []
        if counts.get("active"):
            parts.append(tr("{n} baixando", n=counts["active"]))
        if counts.get("pending"):
            parts.append(tr("{n} na fila", n=counts["pending"]))
        self.status_label.setText("  •  ".join(parts) if parts else tr("Pronto"))

    def _on_job_finished(self, snap: JobSnapshot) -> None:
        if snap.status == JobStatus.COMPLETED:
            self.statusBar().showMessage(tr("Concluído: {title}", title=snap.title), 6000)
        elif snap.status == JobStatus.FAILED:
            self.statusBar().showMessage(tr("Falhou: {title}", title=snap.title), 8000)
        self._refresh_lists.trigger()

    def _reload_visible_lists(self) -> None:
        current = self.stack.currentWidget()
        if current is self.history:
            self.history.reload()
        elif current is self.library:
            self.library.reload()

    def convert_files(self, paths: list) -> None:
        """Leva arquivos (ex.: da Biblioteca) para a fila da tela Converter."""
        self.navigate("converter")
        self.converter.add_paths([Path(p) for p in paths])

    def _open_downloads_folder(self) -> None:
        reveal_in_folder(self, self.ctx.settings_store.settings.download_dir)

    def _startup_notices(self) -> None:
        paused = self.ctx.manager.counts().get("paused", 0)
        if paused:
            self.statusBar().showMessage(
                tr("{n} download(s) da sessão anterior podem ser retomados na tela Downloads.", n=paused), 10000)
        if self.ctx.settings_store.settings.check_updates_on_start:
            run_task(self._check_updates_silently, on_done=self._notify_updates, on_error=lambda _e: None)
        if self.ctx.settings_store.settings.check_app_updates_on_start:
            QTimer.singleShot(3000, lambda: self.updater.check(manual=False))  # so avisa; nunca instala sozinho

    def _show_update_badge(self, version: str) -> None:
        self.update_badge.setText("⬆  " + tr("Atualizar para {version}", version=version))
        self.update_badge.show()

    def _check_updates_silently(self) -> list[str]:
        versions = self.ctx.engines.versions()
        available = []
        for name in UPDATABLE:
            current = versions.get(name).version if versions.get(name) else None
            if self.ctx.engines.check_update(name, current).available:
                available.append(name)
        return available

    def _notify_updates(self, names: list[str]) -> None:
        if names:
            self.statusBar().showMessage(
                tr("Atualização disponível para: {names} — veja Configurações > Engines.", names=", ".join(names)), 15000)

    # ------------------------------------------------------------ arrastar e soltar links

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802 - API Qt
        mime = event.mimeData()
        if mime.hasUrls() or mime.hasText():
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802 - API Qt
        mime = event.mimeData()
        local = [u.toLocalFile() for u in mime.urls() if u.isLocalFile()] if mime.hasUrls() else []
        if local:  # arquivos do computador: vao para o conversor
            self.convert_files(local)
            event.acceptProposedAction()
            return
        text = "\n".join(u.toString() for u in mime.urls()) if mime.hasUrls() else mime.text()
        urls = [u for u in extract_urls(text)]
        if not urls:
            return
        self.navigate("home")
        if len(urls) == 1:
            self.home.tabs.setCurrentIndex(0)
            self.home.url_input.setText(urls[0])
            self.home.analyze()
        else:
            self.home.tabs.setCurrentIndex(1)
            self.home._append_urls(urls)
        event.acceptProposedAction()

    # ------------------------------------------------------------ janela

    def _restore_geometry(self) -> None:
        stored = QSettings(APP_ID, "window").value("geometry")
        if stored is None:  # tamanho/posicao salvos quando o app tinha o nome antigo
            stored = QSettings(LEGACY_APP_ID, "window").value("geometry")
        if isinstance(stored, QByteArray):
            self.restoreGeometry(stored)

    def close_for_update(self) -> None:
        """Fecha sem as perguntas de 'fechar mesmo assim?' (ja confirmadas ao aceitar a atualizacao)."""
        self._closing_for_update = True
        self.close()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802 - API Qt
        if self._closing_for_update:
            self.converter.shutdown()
            self.editor.shutdown()
            QSettings(APP_ID, "window").setValue("geometry", self.saveGeometry())
            event.accept()
            return
        counts = self.ctx.manager.counts()
        if counts.get("active") or counts.get("pending"):
            if not confirm(self, tr("Downloads em andamento"), tr(
                    "Há downloads em andamento. Fechar agora?\n"
                    "Eles ficam salvos e podem ser retomados na próxima vez que você abrir o aplicativo.")):
                event.ignore()
                return
        if self.converter.running:
            if not confirm(self, tr("Conversão em andamento"), tr(
                    "Há uma conversão em andamento. Fechar agora?\n"
                    "O arquivo atual é interrompido; os que já foram convertidos ficam salvos.")):
                event.ignore()
                return
            self.converter.shutdown()
        if self.editor.running and not confirm(self, tr("Edição em andamento"), tr(
                "O Editor ainda está processando um arquivo. Fechar agora?\n"
                "O arquivo em andamento é descartado; o original não é alterado.")):
            event.ignore()
            return
        self.editor.shutdown()
        QSettings(APP_ID, "window").setValue("geometry", self.saveGeometry())
        event.accept()
