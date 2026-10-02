# -*- mode: python ; coding: utf-8 -*-
"""
Spec do PyInstaller. Gera, na mesma pasta (bibliotecas compartilhadas):

    dist/UniversalMediaTools/
        UniversalMediaTools.exe   interface grafica (sem console)
        umd.exe                   linha de comando + worker das engines
        _internal/                Python, Qt, yt-dlp, gallery-dl, ffmpeg...

Use o build.py (ele prepara o ffmpeg e o arquivo de versao antes):
    python build.py
"""

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_submodules

ROOT = Path(SPECPATH).parent
sys.path.insert(0, str(ROOT))

ICON = str(ROOT / "assets" / "icon.ico")
VERSION_FILE = ROOT / "packaging" / "version_info.txt"
FFMPEG_DIR = ROOT / "packaging" / "ffmpeg"

# gallery-dl carrega os extratores por nome (importlib): precisa listar todos.
# Os conversores de umd.convert sao importados sob demanda (dentro de funcoes).
hidden = collect_submodules("gallery_dl") + collect_submodules("umd.convert") + collect_submodules("umd.editor") + [
    "umd.engine.worker",
    "umd.engine.backends.ytdlp",
    "umd.engine.backends.gallerydl",
    "umd.engine.backends.direct",
]
common_excludes = [
    "imageio_ffmpeg",  # o ffmpeg vai empacotado a parte (pasta ffmpeg/)
    "tkinter", "matplotlib", "numpy", "pandas", "scipy", "IPython", "pytest", "PyInstaller",
]
qt_excludes = [
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick", "PySide6.QtQuick",
    "PySide6.QtQml", "PySide6.QtQuickWidgets", "PySide6.Qt3DCore", "PySide6.Qt3DRender", "PySide6.QtMultimedia",
    "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtPdf", "PySide6.QtPdfWidgets",
    "PySide6.QtBluetooth", "PySide6.QtPositioning", "PySide6.QtSensors", "PySide6.QtSerialPort",
    "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets", "PySide6.QtSvgWidgets", "PySide6.QtXml",
]

datas = [
    (str(ROOT / "assets" / "icon.png"), "assets"),
    (str(ROOT / "assets" / "icon.ico"), "assets"),
]
binaries = []
if (FFMPEG_DIR / "ffmpeg.exe").exists():
    binaries.append((str(FFMPEG_DIR / "ffmpeg.exe"), "ffmpeg"))
    if (FFMPEG_DIR / "ffprobe.exe").exists():
        binaries.append((str(FFMPEG_DIR / "ffprobe.exe"), "ffmpeg"))
    for note in FFMPEG_DIR.glob("*.txt"):
        datas.append((str(note), "ffmpeg"))

gui = Analysis(
    [str(ROOT / "packaging" / "gui_entry.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hidden,
    excludes=common_excludes + qt_excludes,
    noarchive=False,
)
cli = Analysis(
    [str(ROOT / "packaging" / "cli_entry.py")],
    pathex=[str(ROOT)],
    hiddenimports=hidden,
    excludes=common_excludes + ["PySide6", "shiboken6"],
    noarchive=False,
)

version = str(VERSION_FILE) if VERSION_FILE.exists() else None

gui_exe = EXE(
    PYZ(gui.pure),
    gui.scripts,
    [],
    exclude_binaries=True,
    name="UniversalMediaTools",
    icon=ICON,
    version=version,
    console=False,
    upx=False,  # UPX costuma gerar falso positivo em antivirus e quebrar DLLs do Qt
)
cli_exe = EXE(
    PYZ(cli.pure),
    cli.scripts,
    [],
    exclude_binaries=True,
    name="umd",
    icon=ICON,
    version=version,
    console=True,
    upx=False,
)

coll = COLLECT(
    gui_exe, gui.binaries, gui.datas,
    cli_exe, cli.binaries, cli.datas,
    name="UniversalMediaTools",
    upx=False,
)
