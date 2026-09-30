"""Ponto de entrada da interface grafica."""

from __future__ import annotations

import sys
import threading

from . import APP_ID, APP_NAME, __version__
from .core.logger import get_logger

log = get_logger("app")


def _set_windows_app_id() -> None:
    """Faz o Windows agrupar a janela com o icone do app na barra de tarefas."""
    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_ID)
        except (OSError, AttributeError):
            pass


def main(argv: list[str] | None = None) -> int:
    from PySide6.QtGui import QIcon
    from PySide6.QtWidgets import QApplication, QMessageBox

    from .core.exceptions import to_user_error
    from .core.i18n import tr
    from .core.paths import resource_path
    from .services.context import create_context

    _set_windows_app_id()
    app = QApplication([sys.argv[0], *(argv or [])])
    app.setApplicationName(APP_NAME)
    app.setApplicationVersion(__version__)
    app.setOrganizationName(APP_ID)
    icon = resource_path("assets", "icon.png")
    if icon.exists():
        app.setWindowIcon(QIcon(str(icon)))

    try:
        ctx = create_context()
    except Exception as exc:  # noqa: BLE001 - mostra algo compreensivel em vez de fechar em silencio
        log.exception("startup failed")
        QMessageBox.critical(None, APP_NAME, tr("Não foi possível iniciar o aplicativo.") + f"\n\n{to_user_error(exc).message}")
        return 1

    from .ui.main_window import MainWindow
    from .ui.theme import apply_theme
    from .ui.widgets import show_error

    apply_theme(app, ctx.settings_store.settings.theme)

    def excepthook(exc_type, exc, tb) -> None:  # nunca mostra traceback cru ao usuario
        log.error("unhandled exception", exc_info=(exc_type, exc, tb))
        try:
            show_error(QApplication.activeWindow(), exc)
        except Exception:  # noqa: BLE001
            pass

    sys.excepthook = excepthook
    threading.excepthook = lambda args: log.error("unhandled thread exception", exc_info=(args.exc_type, args.exc_value, args.exc_traceback))

    window = MainWindow(ctx)
    args = list(argv or [])
    if "--page" in args[:-1]:  # ex.: --page converter (abre direto numa tela)
        from .ui.main_window import PAGES

        page = args[args.index("--page") + 1]
        if page in PAGES:
            window.navigate(page)
    window.show()
    code = app.exec()
    ctx.close()
    return code


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
