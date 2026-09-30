"""Widgets e utilitarios compartilhados pela interface."""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QByteArray, QObject, QRunnable, QSize, Qt, QThreadPool, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication, QImage, QImageReader, QPixmap
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest
from PySide6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from ..core.exceptions import UMDError, to_user_error
from ..core.i18n import tr
from ..core.logger import get_logger, redact
from ..core.process import run_quiet
from ..core.security import is_dangerous_file, validate_url

log = get_logger("ui")

MAX_THUMBNAIL_BYTES = 10 * 1024 * 1024


# ---------------------------------------------------------------- tarefas em segundo plano

class _TaskSignals(QObject):
    done = Signal(object)
    failed = Signal(object)


_live: set[_TaskSignals] = set()


class _Task(QRunnable):
    def __init__(self, fn: Callable[..., Any], args: tuple, signals: _TaskSignals):
        super().__init__()
        self.fn = fn
        self.args = args
        self.signals = signals
        self.setAutoDelete(True)

    def run(self) -> None:
        try:
            result = self.fn(*self.args)
        except BaseException as exc:  # noqa: BLE001 - vira UMDError amigavel na tela
            log.debug("background task failed", exc_info=True)
            self.signals.failed.emit(to_user_error(exc))
        else:
            self.signals.done.emit(result)


def run_task(
    fn: Callable[..., Any],
    *args: Any,
    on_done: Callable[[Any], None] | None = None,
    on_error: Callable[[UMDError], None] | None = None,
) -> None:
    """Roda fn(*args) fora da thread da interface; callbacks voltam na thread da interface."""
    signals = _TaskSignals()
    _live.add(signals)

    def finish(callback: Callable[[Any], None] | None, value: Any) -> None:
        _live.discard(signals)
        if callback is not None:
            callback(value)

    signals.done.connect(lambda value: finish(on_done, value))
    signals.failed.connect(lambda error: finish(on_error, error))
    QThreadPool.globalInstance().start(_Task(fn, args, signals))


# ---------------------------------------------------------------- blocos visuais

def page_header(title: str, subtitle: str = "") -> QWidget:
    box = QWidget()
    layout = QVBoxLayout(box)
    layout.setContentsMargins(0, 0, 0, 6)
    layout.setSpacing(2)
    label = QLabel(title)
    label.setObjectName("PageTitle")
    layout.addWidget(label)
    if subtitle:
        sub = QLabel(subtitle)
        sub.setObjectName("PageSubtitle")
        sub.setWordWrap(True)
        layout.addWidget(sub)
    return box


def card() -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("Card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(18, 16, 18, 16)
    layout.setSpacing(10)
    return frame, layout


def chip(text: str) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Chip")
    label.setSizePolicy(QSizePolicy.Policy.Maximum, QSizePolicy.Policy.Fixed)
    return label


def muted(text: str = "", wrap: bool = True) -> QLabel:
    label = QLabel(text)
    label.setObjectName("Muted")
    label.setWordWrap(wrap)
    return label


def button(text: str, variant: str | None = None, tooltip: str | None = None) -> QPushButton:
    btn = QPushButton(text)
    if variant:
        btn.setProperty("variant", variant)
    if tooltip:
        btn.setToolTip(tooltip)
    btn.setCursor(Qt.CursorShape.PointingHandCursor)
    return btn


def set_state(widget: QWidget, prop: str, value: str) -> None:
    """Muda uma propriedade usada no QSS e reaplica o estilo."""
    if widget.property(prop) != value:
        widget.setProperty(prop, value)
        widget.style().unpolish(widget)
        widget.style().polish(widget)


# ---------------------------------------------------------------- miniaturas

class _Network:
    manager: QNetworkAccessManager | None = None

    @classmethod
    def get(cls) -> QNetworkAccessManager:
        if cls.manager is None:
            cls.manager = QNetworkAccessManager()
        return cls.manager


class ThumbnailLabel(QLabel):
    """Mostra a miniatura de uma URL http(s) ou de um arquivo local, com limite de tamanho."""

    def __init__(self, size: QSize, placeholder: str = "🎞", parent: QWidget | None = None):
        super().__init__(parent)
        self._size = size
        self._placeholder = placeholder
        self._reply: QNetworkReply | None = None
        self._source: str | None = None
        self.setObjectName("Thumb")
        self.setFixedSize(size)
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.clear_thumbnail()

    def clear_thumbnail(self, placeholder: str | None = None) -> None:
        self._abort()
        self._source = None
        self.setPixmap(QPixmap())
        self.setText(placeholder or self._placeholder)
        font = self.font()
        font.setPointSize(max(12, self._size.height() // 5))
        self.setFont(font)

    def _abort(self) -> None:
        if self._reply is not None:
            try:
                self._reply.abort()
                self._reply.deleteLater()
            except RuntimeError:
                pass
            self._reply = None

    def load(self, source: str | None, placeholder: str | None = None) -> None:
        if source == self._source and source:
            return
        self.clear_thumbnail(placeholder)
        if not source:
            return
        self._source = source
        if source.startswith(("http://", "https://")):
            try:
                # a URL da miniatura vem dos metadados do site: entrada nao confiavel
                validate_url(source)
            except UMDError:
                return
            request = QNetworkRequest(QUrl(source))
            request.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader, "Mozilla/5.0")
            request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                                 QNetworkRequest.RedirectPolicy.NoLessSafeRedirectPolicy)
            request.setTransferTimeout(15000)
            reply = _Network.get().get(request)
            reply.downloadProgress.connect(lambda received, _total, r=reply: received > MAX_THUMBNAIL_BYTES and r.abort())
            reply.finished.connect(lambda r=reply, s=source: self._on_reply(r, s))
            self._reply = reply
        else:
            path = Path(source)
            if path.is_file():
                self._set_image(load_scaled_image(path, self._size))

    def _on_reply(self, reply: QNetworkReply, source: str) -> None:
        if reply is self._reply:
            self._reply = None
        try:
            if reply.error() == QNetworkReply.NetworkError.NoError and source == self._source:
                data: QByteArray = reply.readAll()
                if 0 < data.size() <= MAX_THUMBNAIL_BYTES:
                    image = QImage()
                    if image.loadFromData(data):
                        self._set_image(image)
        finally:
            reply.deleteLater()

    def _set_image(self, image: QImage | None) -> None:
        if image is None or image.isNull():
            return
        pixmap = QPixmap.fromImage(image).scaled(
            self._size, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation
        )
        # recorta no centro para preencher o quadro
        x = max(0, (pixmap.width() - self._size.width()) // 2)
        y = max(0, (pixmap.height() - self._size.height()) // 2)
        self.setText("")
        self.setPixmap(pixmap.copy(x, y, self._size.width(), self._size.height()))


def load_scaled_image(path: Path, size: QSize) -> QImage | None:
    """Le so o necessario de uma imagem local (rapido mesmo para fotos grandes)."""
    reader = QImageReader(str(path))
    reader.setAutoTransform(True)
    original = reader.size()
    if original.isValid() and original.width() > 0:
        scaled = original.scaled(size * 2, Qt.AspectRatioMode.KeepAspectRatioByExpanding)
        if scaled.width() < original.width():
            reader.setScaledSize(scaled)
    image = reader.read()
    return None if image.isNull() else image


# ---------------------------------------------------------------- erros

class ErrorDialog(QDialog):
    """Mensagem amigavel; o log tecnico real so aparece em 'Ver detalhes tecnicos'."""

    def __init__(self, error: UMDError, title: str | None = None, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWindowTitle(title or tr("Não foi possível concluir"))
        self.setMinimumWidth(520)
        layout = QVBoxLayout(self)
        layout.setSpacing(12)

        header = QHBoxLayout()
        icon = QLabel("⚠")
        icon.setStyleSheet("font-size: 26pt;")
        icon.setAlignment(Qt.AlignmentFlag.AlignTop)
        header.addWidget(icon)
        message = QLabel(error.user_message())
        message.setWordWrap(True)
        message.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        header.addWidget(message, 1)
        layout.addLayout(header)

        details_text = redact(error.details or "").strip() or tr("Sem detalhes técnicos disponíveis.")
        details_text = f"[{error.code.value}]\n{details_text}"
        self.details = QPlainTextEdit(details_text)
        self.details.setReadOnly(True)
        self.details.setMinimumHeight(200)
        self.details.setVisible(False)
        self.details.setStyleSheet("font-family: Consolas, 'Courier New', monospace; font-size: 9pt;")
        layout.addWidget(self.details)

        buttons = QHBoxLayout()
        self.toggle = button(tr("Ver detalhes técnicos"))
        self.toggle.clicked.connect(self._toggle)
        copy = button(tr("Copiar detalhes"))
        copy.clicked.connect(lambda: QGuiApplication.clipboard().setText(details_text))
        close = button(tr("Fechar"), "primary")
        close.clicked.connect(self.accept)
        buttons.addWidget(self.toggle)
        buttons.addWidget(copy)
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout.addLayout(buttons)

    def _toggle(self) -> None:
        visible = not self.details.isVisible()
        self.details.setVisible(visible)
        self.toggle.setText(tr("Ocultar detalhes técnicos") if visible else tr("Ver detalhes técnicos"))
        self.adjustSize()


def show_error(parent: QWidget | None, error: BaseException, title: str | None = None) -> None:
    ErrorDialog(to_user_error(error), title, parent).exec()


def info_box(parent: QWidget | None, title: str, text: str) -> None:
    QMessageBox.information(parent, title, text)


def confirm(parent: QWidget | None, title: str, text: str) -> bool:
    answer = QMessageBox.question(parent, title, text, QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
    return answer == QMessageBox.StandardButton.Yes


# ---------------------------------------------------------------- arquivos

def open_file(parent: QWidget | None, path: str | Path) -> None:
    """Abre um arquivo baixado -- so por acao do usuario, e nunca executaveis."""
    target = Path(path)
    if not target.exists():
        info_box(parent, tr("Arquivo não encontrado"), tr("O arquivo não existe mais neste local:\n{path}", path=str(target)))
        return
    if is_dangerous_file(target):
        # nunca executa: so mostra na pasta
        reveal_in_folder(parent, target)
        return
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(target)))


def reveal_in_folder(parent: QWidget | None, path: str | Path) -> None:
    target = Path(path)
    if target.is_file() and sys.platform == "win32":
        # argumentos separados (sem montar string de comando): explorer /select, "<caminho>"
        run_quiet(["explorer.exe", "/select,", str(target)], timeout=5)
        return
    folder = target if target.is_dir() else target.parent
    if not folder.exists():
        info_box(parent, tr("Pasta não encontrada"), tr("A pasta não existe mais:\n{path}", path=str(folder)))
        return
    QDesktopServices.openUrl(QUrl.fromLocalFile(str(folder)))


def copy_text(text: str) -> None:
    QGuiApplication.clipboard().setText(text)


class Debouncer(QObject):
    """Agrupa varias chamadas seguidas numa so (ex.: recarregar lista)."""

    def __init__(self, interval_ms: int, callback: Callable[[], None], parent: QObject | None = None):
        super().__init__(parent)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(interval_ms)
        self._timer.timeout.connect(callback)

    def trigger(self) -> None:
        self._timer.start()
