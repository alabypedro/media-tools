"""Fluxo de atualizacao do programa na interface (botao, aviso ao abrir, download, instalacao)."""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from PySide6.QtCore import QObject, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox, QProgressDialog

from .. import __version__
from ..core import app_update
from ..core.i18n import tr
from ..core.logger import get_logger
from ..downloader.progress import human_size
from .widgets import confirm, info_box, run_task

if TYPE_CHECKING:
    from .main_window import MainWindow

log = get_logger("ui.app_update")


def _safe_check() -> app_update.UpdateInfo | str:
    """Erros esperados (sem internet, sem versao publicada...) voltam como texto para o usuario."""
    try:
        return app_update.check_for_update()
    except app_update.UpdateError as exc:
        return str(exc)


class _DownloadSignals(QObject):
    progress = Signal(int, int)
    done = Signal(object)
    failed = Signal(str)


class AppUpdater(QObject):
    """Verifica, baixa e instala atualizacoes do programa. Nada e instalado sem confirmacao."""

    update_found = Signal(str)  # versao nova (para o destaque no menu lateral)

    def __init__(self, window: MainWindow):
        super().__init__(window)
        self.window = window
        self.latest: app_update.UpdateInfo | None = None
        self._checking = False
        self._cancel = threading.Event()

    # ------------------------------------------------------------ verificar

    def check(self, manual: bool = True) -> None:
        if self._checking:
            return
        self._checking = True
        if manual:
            self.window.statusBar().showMessage(tr("Verificando atualizações do programa…"), 5000)
        run_task(_safe_check,
                 on_done=lambda result: self._checked(result, manual),
                 on_error=lambda _error: self._check_failed(
                     tr("Não foi possível verificar atualizações agora. Tente novamente mais tarde."), manual))

    def _checked(self, info: app_update.UpdateInfo | str, manual: bool) -> None:
        if isinstance(info, str):
            self._check_failed(info, manual)
            return
        self._checking = False
        if not info.available:
            if manual:
                info_box(self.window, tr("Atualizações"),
                         tr("Você já tem a versão mais recente ({version}).", version=__version__))
            return
        self.latest = info
        self.update_found.emit(info.version)
        if manual:
            self.offer(info)
        else:
            log.info("app update available: %s (current %s)", info.version, __version__)
            self.window.statusBar().showMessage(
                tr("Nova versão {version} disponível — clique em \"Atualizar para {version}\" no menu lateral.",
                   version=info.version), 15000)

    def _check_failed(self, message: str, manual: bool) -> None:
        self._checking = False
        log.warning("app update check failed: %s", message)
        if manual:
            QMessageBox.warning(self.window, tr("Atualizações"), message)

    # ------------------------------------------------------------ oferecer / instalar

    def offer(self, info: app_update.UpdateInfo | None = None) -> None:
        info = info or self.latest
        if info is None:
            self.check(manual=True)
            return
        notes = "\n".join(line for line in info.notes.splitlines() if line.strip()) or tr("(sem notas de versão)")
        size = f"\n{tr('Download')}: {human_size(info.installer_size)}" if info.installer_size else ""
        header = tr("Versão atual: {current}  →  nova: {new}", current=__version__, new=info.version) + size
        if not app_update.can_self_update():
            if confirm(self.window, tr("Atualização disponível"), tr(
                    "{header}\n\n{notes}\n\nEsta cópia não foi instalada pelo instalador (por exemplo, está "
                    "rodando pelo Python ou pela pasta dist), então não dá para atualizar automaticamente.\n"
                    "Abrir a página de download?", header=header, notes=notes)):
                QDesktopServices.openUrl(QUrl(app_update.release_page(info)))
            return
        busy = self._busy_text()
        if not confirm(self.window, tr("Atualização disponível"), tr(
                "{header}\n\n{notes}\n\nBaixar e instalar agora? O programa vai fechar, instalar a nova versão "
                "e abrir de novo sozinho. Histórico, biblioteca e configurações são mantidos.{busy}",
                header=header, notes=notes, busy=busy)):
            return
        self._download(info)

    def _busy_text(self) -> str:
        counts = self.window.ctx.manager.counts()
        parts = []
        if counts.get("active") or counts.get("pending"):
            parts.append(tr("Os downloads em andamento serão pausados e podem ser retomados depois."))
        if self.window.converter.running:
            parts.append(tr("A conversão em andamento será interrompida."))
        return ("\n\n" + " ".join(parts)) if parts else ""

    def _download(self, info: app_update.UpdateInfo) -> None:
        self._cancel = threading.Event()
        dialog = QProgressDialog(tr("Baixando a versão {version}…", version=info.version), tr("Cancelar"),
                                 0, 100, self.window)
        dialog.setWindowTitle(tr("Atualização"))
        dialog.setWindowModality(Qt.WindowModality.WindowModal)
        dialog.setMinimumDuration(0)
        dialog.setAutoClose(False)
        dialog.setAutoReset(False)
        dialog.canceled.connect(self._cancel.set)
        signals = _DownloadSignals(self)

        def on_progress(received: int, total: int) -> None:
            if total:
                dialog.setValue(min(99, int(received * 100 / total)))
            dialog.setLabelText(tr("Baixando a versão {version}: {done} de {total}", version=info.version,
                                   done=human_size(received), total=human_size(total) if total else "?"))

        def on_done(path) -> None:
            dialog.setValue(100)
            dialog.close()
            self._install(path)

        def on_failed(message: str) -> None:
            dialog.close()
            if not self._cancel.is_set():
                QMessageBox.warning(self.window, tr("Atualização"), message)

        signals.progress.connect(on_progress)
        signals.done.connect(on_done)
        signals.failed.connect(on_failed)
        cancel = self._cancel

        def work() -> None:
            try:
                path = app_update.download_installer(info, lambda r, t: signals.progress.emit(r, t), cancel)
            except app_update.UpdateError as exc:
                signals.failed.emit(str(exc))
            except Exception as exc:  # noqa: BLE001 - erro inesperado vira mensagem simples
                log.exception("app update download failed")
                signals.failed.emit(tr("Não foi possível baixar a atualização: {error}", error=str(exc)))
            else:
                signals.done.emit(path)

        threading.Thread(target=work, name="umd-app-update", daemon=True).start()
        dialog.show()

    def _install(self, path) -> None:
        try:
            app_update.launch_installer(path)
        except (OSError, app_update.UpdateError) as exc:
            QMessageBox.warning(self.window, tr("Atualização"),
                                tr("Não foi possível abrir o instalador: {error}", error=str(exc)))
            return
        log.info("closing for app update")
        self.window.close_for_update()
