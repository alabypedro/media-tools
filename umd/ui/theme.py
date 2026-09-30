"""Temas claro/escuro (paleta Fusion + folha de estilo)."""

from __future__ import annotations

from dataclasses import dataclass

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QGuiApplication, QPalette
from PySide6.QtWidgets import QApplication


@dataclass(frozen=True)
class Colors:
    bg: str
    surface: str
    surface2: str
    border: str
    text: str
    muted: str
    accent: str
    accent_hover: str
    accent_text: str
    success: str
    danger: str
    warning: str
    sidebar: str
    sidebar_text: str
    sidebar_active: str
    selection: str


LIGHT = Colors(
    bg="#f4f6f9", surface="#ffffff", surface2="#eef1f5", border="#dde2e9", text="#1c2129", muted="#5d6878",
    accent="#2563eb", accent_hover="#1d4ed8", accent_text="#ffffff", success="#15803d", danger="#c62828",
    warning="#b45309", sidebar="#e9edf3", sidebar_text="#2a313c", sidebar_active="#dbe5fb", selection="#cfe0ff",
)
DARK = Colors(
    bg="#14161b", surface="#1c1f26", surface2="#252932", border="#313744", text="#e5e8ee", muted="#98a2b3",
    accent="#3b82f6", accent_hover="#2f6fe0", accent_text="#ffffff", success="#4ade80", danger="#f87171",
    warning="#fbbf24", sidebar="#101217", sidebar_text="#cfd5df", sidebar_active="#1f2a44", selection="#1f3b70",
)

_current = LIGHT


def current() -> Colors:
    return _current


def resolve(theme: str) -> Colors:
    if theme == "dark":
        return DARK
    if theme == "light":
        return LIGHT
    try:
        scheme = QGuiApplication.styleHints().colorScheme()
        return DARK if scheme == Qt.ColorScheme.Dark else LIGHT
    except AttributeError:
        return LIGHT


def _icon_files(colors: Colors) -> dict[str, str]:
    """Setas e marca de selecao desenhadas na hora, na cor do tema (o QSS so aceita imagens por arquivo)."""
    from PySide6.QtCore import QPointF
    from PySide6.QtGui import QPainter, QPen, QPixmap, QPolygonF

    from ..core.paths import app_data_dir

    folder = app_data_dir() / "ui"
    folder.mkdir(parents=True, exist_ok=True)
    tag = "dark" if colors is DARK else "light"
    files: dict[str, str] = {}

    def save(name: str, painter_fn) -> None:
        pixmap = QPixmap(24, 24)
        pixmap.fill(Qt.GlobalColor.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter_fn(painter)
        painter.end()
        path = folder / f"{name}_{tag}.png"
        pixmap.save(str(path))
        files[name] = path.as_posix()

    def arrow(points: list[tuple[float, float]], color: str):
        def paint(p: QPainter) -> None:
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(color))
            p.drawPolygon(QPolygonF([QPointF(x, y) for x, y in points]))
        return paint

    def check(p: QPainter) -> None:
        pen = QPen(QColor(colors.accent_text), 3.2)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.drawPolyline(QPolygonF([QPointF(5, 12.5), QPointF(10, 17.5), QPointF(19, 7)]))

    save("down", arrow([(6, 9), (18, 9), (12, 16)], colors.muted))
    save("up", arrow([(6, 15), (18, 15), (12, 8)], colors.muted))
    save("check", check)
    return files


def apply_theme(app: QApplication, theme: str) -> Colors:
    global _current
    colors = resolve(theme)
    _current = colors
    app.setStyle("Fusion")

    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(colors.bg))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(colors.text))
    palette.setColor(QPalette.ColorRole.Base, QColor(colors.surface))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(colors.surface2))
    palette.setColor(QPalette.ColorRole.Text, QColor(colors.text))
    palette.setColor(QPalette.ColorRole.Button, QColor(colors.surface))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(colors.text))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(colors.accent))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(colors.accent_text))
    palette.setColor(QPalette.ColorRole.ToolTipBase, QColor(colors.surface))
    palette.setColor(QPalette.ColorRole.ToolTipText, QColor(colors.text))
    palette.setColor(QPalette.ColorRole.PlaceholderText, QColor(colors.muted))
    palette.setColor(QPalette.ColorRole.Link, QColor(colors.accent))
    for role in (QPalette.ColorRole.Text, QPalette.ColorRole.ButtonText, QPalette.ColorRole.WindowText):
        palette.setColor(QPalette.ColorGroup.Disabled, role, QColor(colors.muted))
    app.setPalette(palette)
    try:
        icons = _icon_files(colors)
    except OSError:
        icons = {}
    app.setStyleSheet(stylesheet(colors, icons))
    return colors


def stylesheet(c: Colors, icons: dict[str, str] | None = None) -> str:
    icons = icons or {}
    down = f"image: url({icons['down']});" if "down" in icons else ""
    up = f"image: url({icons['up']});" if "up" in icons else ""
    check = f"image: url({icons['check']});" if "check" in icons else ""
    return f"""
    QComboBox::down-arrow {{ {down} width: 14px; height: 14px; }}
    QAbstractSpinBox {{ padding-right: 24px; }}
    QAbstractSpinBox::up-button {{ subcontrol-origin: border; subcontrol-position: top right; width: 22px; border: none; background: transparent; }}
    QAbstractSpinBox::down-button {{ subcontrol-origin: border; subcontrol-position: bottom right; width: 22px; border: none; background: transparent; }}
    QAbstractSpinBox::up-arrow {{ {up} width: 12px; height: 12px; }}
    QAbstractSpinBox::down-arrow {{ {down} width: 12px; height: 12px; }}
    QCheckBox, QRadioButton {{ spacing: 8px; }}
    QCheckBox::indicator {{ width: 18px; height: 18px; border: 1px solid {c.muted}; border-radius: 5px; background: {c.surface}; }}
    QCheckBox::indicator:hover {{ border-color: {c.accent}; }}
    QCheckBox::indicator:checked {{ background: {c.accent}; border-color: {c.accent}; {check} }}
    QCheckBox::indicator:disabled {{ background: {c.surface2}; border-color: {c.border}; }}
    QListWidget::indicator {{ width: 16px; height: 16px; border: 1px solid {c.muted}; border-radius: 4px; background: {c.surface}; }}
    QListWidget::indicator:checked {{ background: {c.accent}; border-color: {c.accent}; {check} }}
    QRadioButton::indicator {{ width: 16px; height: 16px; border: 1px solid {c.muted}; border-radius: 9px; background: {c.surface}; }}
    QRadioButton::indicator:checked {{
        border: 1px solid {c.accent};
        background: qradialgradient(cx:0.5, cy:0.5, radius:0.5, fx:0.5, fy:0.5,
                                    stop:0 {c.accent}, stop:0.5 {c.accent}, stop:0.6 {c.surface}, stop:1 {c.surface});
    }}
""" + f"""
    QWidget {{ color: {c.text}; font-size: 10pt; }}
    QMainWindow, QStackedWidget, QScrollArea, QScrollArea > QWidget > QWidget {{ background: {c.bg}; }}
    QToolTip {{ background: {c.surface}; color: {c.text}; border: 1px solid {c.border}; padding: 6px; }}

    #Sidebar {{ background: {c.sidebar}; border-right: 1px solid {c.border}; }}
    #AppTitle {{ font-size: 13pt; font-weight: 700; color: {c.sidebar_text}; padding: 4px 6px; }}
    #AppSubtitle {{ color: {c.muted}; font-size: 8.5pt; padding: 0 6px 8px 6px; }}
    QPushButton#NavButton {{
        text-align: left; padding: 10px 14px; border: none; border-radius: 8px;
        background: transparent; color: {c.sidebar_text}; font-size: 10.5pt;
    }}
    QPushButton#NavButton:hover {{ background: {c.surface2}; }}
    QPushButton#NavButton:checked {{ background: {c.sidebar_active}; color: {c.accent}; font-weight: 600; }}

    #PageTitle {{ font-size: 18pt; font-weight: 700; }}
    #PageSubtitle {{ color: {c.muted}; }}
    #SectionTitle {{ font-size: 11.5pt; font-weight: 600; }}
    #Muted, QLabel[muted="true"] {{ color: {c.muted}; }}
    #MediaTitle {{ font-size: 13.5pt; font-weight: 700; }}
    #Chip {{
        background: {c.surface2}; border: 1px solid {c.border}; border-radius: 10px; padding: 2px 9px;
        color: {c.muted}; font-size: 8.5pt;
    }}
    #ErrorText {{ color: {c.danger}; }}
    #WarningText {{ color: {c.warning}; }}
    #SuccessText {{ color: {c.success}; }}
    #Banner {{ background: {c.sidebar_active}; border: 1px solid {c.border}; border-radius: 8px; padding: 8px; }}

    QFrame#Card {{ background: {c.surface}; border: 1px solid {c.border}; border-radius: 12px; }}
    QFrame#JobCard {{ background: {c.surface}; border: 1px solid {c.border}; border-radius: 10px; }}
    #Thumb {{ background: {c.surface2}; border-radius: 8px; color: {c.muted}; }}

    QPushButton {{
        background: {c.surface}; border: 1px solid {c.border}; border-radius: 7px; padding: 6px 14px;
    }}
    QPushButton:hover {{ background: {c.surface2}; }}
    QPushButton:pressed {{ background: {c.border}; }}
    QPushButton:disabled {{ color: {c.muted}; background: {c.surface2}; }}
    QPushButton[variant="primary"] {{
        background: {c.accent}; color: {c.accent_text}; border: 1px solid {c.accent}; font-weight: 600; padding: 8px 22px;
    }}
    QPushButton[variant="primary"]:hover {{ background: {c.accent_hover}; }}
    QPushButton[variant="primary"]:disabled {{ background: {c.border}; border-color: {c.border}; color: {c.muted}; }}
    QPushButton[variant="danger"] {{ color: {c.danger}; }}
    QPushButton[variant="link"] {{ border: none; background: transparent; color: {c.accent}; padding: 2px 4px; }}

    QLineEdit, QPlainTextEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {{
        background: {c.surface}; border: 1px solid {c.border}; border-radius: 7px; padding: 6px 8px;
        selection-background-color: {c.accent}; selection-color: {c.accent_text};
    }}
    QSpinBox, QDoubleSpinBox {{ padding-right: 26px; }}
    QComboBox {{ padding-right: 26px; }}
    QLineEdit:focus, QPlainTextEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus {{ border: 1px solid {c.accent}; }}
    QLineEdit#UrlInput {{ font-size: 11.5pt; padding: 9px 12px; }}
    QComboBox::drop-down {{ subcontrol-origin: padding; subcontrol-position: center right; border: none; width: 26px; }}
    QComboBox QAbstractItemView {{ background: {c.surface}; border: 1px solid {c.border}; selection-background-color: {c.selection}; selection-color: {c.text}; }}

    QProgressBar {{ background: {c.surface2}; border: none; border-radius: 5px; height: 10px; text-align: center; color: transparent; }}
    QProgressBar::chunk {{ background: {c.accent}; border-radius: 5px; }}
    QProgressBar[state="done"]::chunk {{ background: {c.success}; }}
    QProgressBar[state="error"]::chunk {{ background: {c.danger}; }}
    QProgressBar[state="paused"]::chunk {{ background: {c.muted}; }}

    QTabWidget::pane {{ border: none; }}
    QTabBar::tab {{ background: transparent; padding: 8px 16px; margin-right: 4px; border-bottom: 2px solid transparent; color: {c.muted}; }}
    QTabBar::tab:selected {{ color: {c.text}; border-bottom: 2px solid {c.accent}; font-weight: 600; }}

    QGroupBox {{ background: {c.surface}; border: 1px solid {c.border}; border-radius: 12px; margin-top: 14px; padding: 16px 12px 12px 12px; font-weight: 600; }}
    QGroupBox::title {{ subcontrol-origin: margin; left: 14px; padding: 0 4px; }}

    QTableView, QListView, QTreeView, QListWidget {{
        background: {c.surface}; border: 1px solid {c.border}; border-radius: 10px; alternate-background-color: {c.surface2};
        selection-background-color: {c.selection}; selection-color: {c.text}; gridline-color: {c.border};
    }}
    QHeaderView::section {{ background: {c.surface2}; border: none; border-bottom: 1px solid {c.border}; padding: 6px 8px; font-weight: 600; }}

    QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
    QScrollBar::handle:vertical {{ background: {c.border}; border-radius: 4px; min-height: 30px; }}
    QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
    QScrollBar::handle:horizontal {{ background: {c.border}; border-radius: 4px; min-width: 30px; }}
    QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}

    QStatusBar {{ background: {c.surface}; border-top: 1px solid {c.border}; color: {c.muted}; }}
    QMenu {{ background: {c.surface}; border: 1px solid {c.border}; padding: 4px; }}
    QMenu::item {{ padding: 6px 18px; border-radius: 5px; }}
    QMenu::item:selected {{ background: {c.selection}; }}
    """
