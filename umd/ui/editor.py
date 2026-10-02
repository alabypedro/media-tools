"""Tela Editor: ferramentas de GIF, animacao e video no estilo do ezgif.com
(criar GIF, video -> GIF, redimensionar, cortar, girar, velocidade, efeitos,
texto, marca-d'agua, censurar, otimizar, dividir em quadros, sprite sheet,
GIF -> MP4, juntar videos).

Cada ferramenta e um formulario pequeno (ToolForm) que so sabe montar as
opcoes; o trabalho e de umd.editor.tools e roda numa thread separada. O
original nunca e alterado: o resultado sai como "nome (redimensionado).gif"
e pode virar a entrada da proxima ferramenta ("Editar o resultado").
"""

from __future__ import annotations

import shutil
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QObject, QSize, Qt, Signal
from PySide6.QtGui import QColor, QDragEnterEvent, QDropEvent, QImage, QMovie, QPixmap
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QSpinBox,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from ..convert.core.errors import ConversionError
from ..convert.core.history import History
from ..core.i18n import tr
from ..core.logger import get_logger
from ..downloader.progress import human_size
from ..editor import tools
from ..editor.clip import CANCELLED_MESSAGE, FONTS, IMAGE_EXTS
from ..editor.video import VIDEO_EXTS, MediaInfo, probe
from ..media.ffmpeg import extract_frame, find_ffmpeg
from ..services.context import AppContext
from .widgets import button, card, info_box, muted, page_header, reveal_in_folder

log = get_logger("editor")

PREVIEW_SIZE = QSize(260, 260)
MAX_MOVIE_BYTES = 40 * 1024 * 1024  # acima disso a previa mostra so o primeiro quadro

VIDEO_TARGETS = ["mp4", "webm", "mkv", "mov"]
POSITION_LABELS = {
    "top-left": "Em cima, à esquerda", "top": "Em cima", "top-right": "Em cima, à direita",
    "left": "À esquerda", "center": "No centro", "right": "À direita",
    "bottom-left": "Embaixo, à esquerda", "bottom": "Embaixo", "bottom-right": "Embaixo, à direita",
}
CENSOR_LABELS = {"blur": "Desfocar", "pixelate": "Pixelar", "black": "Tarja preta"}
FONT_LABELS = {"impact": "Impact", "arial": "Arial", "times": "Times New Roman", "courier": "Courier New",
               "comic": "Comic Sans"}
DROP_LABELS = {0: "Manter todos os quadros", 2: "Remover 1 a cada 2 quadros", 3: "Remover 1 a cada 3 quadros",
               4: "Remover 1 a cada 4 quadros"}
EFFECT_LABELS = {"grayscale": "Preto e branco", "sepia": "Sépia", "invert": "Negativo", "sharpen": "Nitidez"}
SPRITE_LABELS = {"make": "Animação → sprite sheet", "cut": "Sprite sheet → animação"}
assert set(FONT_LABELS) == set(FONTS)


def animated_targets() -> list[str]:
    from PIL import features

    return ["gif", "webp", "apng"] + (["avif"] if features.check("avif") else [])


def default_targets(info: MediaInfo | None) -> list[str]:
    """Formatos de saida que fazem sentido para o arquivo aberto (o formato dele mesmo vem primeiro)."""
    if info is None:
        return []
    ext = info.path.suffix.lower().lstrip(".")
    if info.kind == "video":
        choices = [ext, *VIDEO_TARGETS, *animated_targets()]
    elif info.kind == "animation":
        choices = [ext, *animated_targets(), "mp4", "webm"]
    else:
        choices = [ext, "png", "jpg", "webp", "gif", "bmp"]
    return [e for e in dict.fromkeys(choices) if e in IMAGE_EXTS or e in VIDEO_EXTS]


def describe(info: MediaInfo) -> str:
    parts = [f"{info.width}×{info.height} px"]
    if info.kind != "image":
        if info.kind == "animation":
            parts.append(tr("{n} quadros", n=info.frames))
        parts.append(tr("{seconds} s", seconds=f"{info.duration:.2f}"))
        if info.kind == "video" and info.fps:
            parts.append(f"{info.fps:g} fps")
        if info.kind == "video":
            parts.append(tr("com áudio") if info.has_audio else tr("sem áudio"))
    return "  •  ".join(parts)


# ---------------------------------------------------------------- campos

def _spin(minimum: int, maximum: int, value: int, suffix: str = "", special: str = "", step: int = 1) -> QSpinBox:
    spin = QSpinBox()
    spin.setRange(minimum, maximum)
    spin.setSingleStep(step)
    spin.setValue(value)
    if suffix:
        spin.setSuffix(suffix)
    if special:
        spin.setSpecialValueText(special)  # mostrado no valor minimo
    return spin


def _seconds(value: float = 0.0) -> QDoubleSpinBox:
    spin = QDoubleSpinBox()
    spin.setRange(0, 24 * 3600)
    spin.setDecimals(2)
    spin.setSingleStep(0.5)
    spin.setSuffix(" s")
    spin.setValue(value)
    return spin


def _combo(labels: dict[Any, str], current: Any) -> QComboBox:
    combo = QComboBox()
    for key, label in labels.items():
        combo.addItem(tr(label), key)
    combo.setCurrentIndex(max(0, combo.findData(current)))
    return combo


class ColorField(QWidget):
    def __init__(self, color: str, parent: QWidget | None = None):
        super().__init__(parent)
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.edit = QLineEdit(color)
        self.edit.setMaximumWidth(110)
        pick = button(tr("Escolher…"))
        pick.clicked.connect(self._pick)
        row.addWidget(self.edit)
        row.addWidget(pick)
        row.addStretch(1)

    def _pick(self) -> None:
        color = QColorDialog.getColor(QColor(self.edit.text()), self, tr("Escolha a cor"))
        if color.isValid():
            self.edit.setText(color.name())

    def value(self) -> str:
        text = self.edit.text().strip()
        if not QColor(text).isValid():
            raise ValueError(tr("Cor inválida: {color}. Use o formato #rrggbb.", color=text))
        return QColor(text).name()


class FileList(QWidget):
    """Lista de arquivos com ordem (para criar animacao e juntar videos)."""

    def __init__(self, extensions: tuple[str, ...], parent: QWidget | None = None):
        super().__init__(parent)
        self.extensions = extensions
        self.files: list[Path] = []
        row = QHBoxLayout(self)
        row.setContentsMargins(0, 0, 0, 0)
        self.list = QListWidget()
        self.list.setMinimumHeight(150)
        row.addWidget(self.list, 1)
        side = QVBoxLayout()
        for label, slot in ((tr("Adicionar…"), self.pick), (tr("Remover"), self.remove),
                            (tr("Subir"), lambda: self.move(-1)), (tr("Descer"), lambda: self.move(1))):
            btn = button(label)
            btn.clicked.connect(slot)
            side.addWidget(btn)
        side.addStretch(1)
        row.addLayout(side)

    def pick(self) -> None:
        pattern = " ".join(f"*.{e}" for e in sorted(self.extensions))
        chosen, _ = QFileDialog.getOpenFileNames(self, tr("Adicionar arquivos"), "", f"{tr('Arquivos')} ({pattern})")
        self.add([Path(f) for f in chosen])

    def add(self, paths: list[Path]) -> None:
        self.files += [p for p in paths if p.suffix.lower().lstrip(".") in self.extensions]
        self._fill(len(self.files) - 1)

    def remove(self) -> None:
        row = self.list.currentRow()
        if 0 <= row < len(self.files):
            del self.files[row]
            self._fill(min(row, len(self.files) - 1))

    def move(self, delta: int) -> None:
        i = self.list.currentRow()
        j = i + delta
        if 0 <= i < len(self.files) and 0 <= j < len(self.files):
            self.files[i], self.files[j] = self.files[j], self.files[i]
            self._fill(j)

    def _fill(self, select: int) -> None:
        self.list.clear()
        for path in self.files:
            self.list.addItem(path.name)
        self.list.setCurrentRow(select)


# ---------------------------------------------------------------- ferramentas

Job = Callable[[threading.Event], list[Path]]


class ToolForm(QWidget):
    key = ""
    title = ""
    hint = ""
    suffix = "editado"  # vai no nome do arquivo gerado: "nome (editado).ext"
    needs_input = True
    changed = Signal()  # os formatos de saida possiveis mudaram

    def __init__(self, page: "EditorPage"):
        super().__init__(page)
        self.page = page
        self.form = QFormLayout(self)
        self.form.setContentsMargins(0, 0, 0, 0)
        self.build()

    def build(self) -> None:
        pass

    def set_info(self, info: MediaInfo | None) -> None:
        """Arquivo aberto mudou: preenche os campos que dependem dele."""

    def targets(self, info: MediaInfo | None) -> list[str]:
        return default_targets(info)

    def options(self) -> dict[str, Any]:
        """Opcoes de umd.editor (ValueError com mensagem para o usuario se algo estiver invalido)."""
        return {}

    def prepare(self, source: Path | None, target: str, out_dir: Path | None) -> Job:
        """Le os campos (na thread da interface) e devolve o trabalho a rodar em segundo plano."""
        assert source is not None
        options = {**self.page.base_options(), **self.options()}
        output = tools.output_path(source, tr(self.suffix), target, out_dir)
        return lambda cancel: [tools.edit(source, output, options, cancel)]


class _RangeMixin:
    """Campos inicio/fim em segundos; so entram nas opcoes se o usuario mexeu neles."""

    def add_range(self, form: QFormLayout) -> None:
        self.start, self.end = _seconds(), _seconds()
        self._full = 0.0
        form.addRow(tr("Início:"), self.start)
        form.addRow(tr("Fim:"), self.end)

    def fill_range(self, info: MediaInfo | None) -> None:
        self._full = round(info.duration, 2) if info else 0.0
        self.start.setValue(0)
        self.end.setValue(self._full)

    def range_options(self) -> dict[str, Any]:
        start, end = self.start.value(), self.end.value()
        if end and end <= start:
            raise ValueError(tr("O fim do trecho precisa vir depois do início."))
        options: dict[str, Any] = {}
        if start:
            options["start"] = start
        if end and end < self._full:
            options["end"] = end
        return options


class VideoToGifForm(_RangeMixin, ToolForm):
    key, title, suffix = "video2gif", "Vídeo para GIF", "convertido"
    hint = "Transforma um trecho de vídeo em GIF, WebP, APNG ou AVIF animado. Trechos curtos e larguras menores geram arquivos bem mais leves."

    def build(self) -> None:
        self.add_range(self.form)
        self.fps = _spin(1, 60, 10, " fps")
        self.form.addRow(tr("Quadros por segundo:"), self.fps)
        self.width_spin = _spin(0, 8000, 480, " px", tr("Tamanho original"), step=10)
        self.form.addRow(tr("Largura:"), self.width_spin)

    def set_info(self, info: MediaInfo | None) -> None:
        self.fill_range(info)

    def targets(self, info: MediaInfo | None) -> list[str]:
        return animated_targets() if info else []

    def options(self) -> dict[str, Any]:
        options = {**self.range_options(), "fps": self.fps.value()}
        if self.width_spin.value():
            options["resize"] = {"width": self.width_spin.value()}
        return options


class ResizeForm(ToolForm):
    key, title, suffix = "resize", "Redimensionar", "redimensionado"
    hint = "Muda a largura e a altura. Com a proporção mantida, a imagem cabe dentro do tamanho pedido sem distorcer."

    def build(self) -> None:
        self.width_spin = _spin(0, 20000, 0, " px", tr("Automática"))
        self.height_spin = _spin(0, 20000, 0, " px", tr("Automática"))
        self.percent = _spin(1, 1000, 100, " %")
        self.percent.setToolTip(tr("Diferente de 100%, vale no lugar da largura e da altura"))
        self.keep = QCheckBox(tr("Manter a proporção"))
        self.keep.setChecked(True)
        self.form.addRow(tr("Largura:"), self.width_spin)
        self.form.addRow(tr("Altura:"), self.height_spin)
        self.form.addRow(tr("Ou em porcentagem:"), self.percent)
        self.form.addRow(self.keep)

    def set_info(self, info: MediaInfo | None) -> None:
        self.width_spin.setValue(info.width if info else 0)
        self.height_spin.setValue(0)
        self.percent.setValue(100)

    def options(self) -> dict[str, Any]:
        if self.percent.value() != 100:
            return {"resize": {"percent": self.percent.value()}}
        if not self.width_spin.value() and not self.height_spin.value():
            raise ValueError(tr("Informe a largura, a altura ou a porcentagem."))
        return {"resize": {"width": self.width_spin.value(), "height": self.height_spin.value(),
                           "keep_aspect": self.keep.isChecked()}}


class _BoxMixin:
    """Campos X, Y, largura e altura de uma area retangular."""

    def add_box(self, form: QFormLayout) -> None:
        self.box = {key: _spin(0, 20000, 0, " px") for key in ("x", "y", "width", "height")}
        for key, label in (("x", tr("Esquerda (X):")), ("y", tr("Topo (Y):")), ("width", tr("Largura:")),
                           ("height", tr("Altura:"))):
            form.addRow(label, self.box[key])

    def fill_box(self, info: MediaInfo | None, fraction: float = 1.0) -> None:
        width, height = (info.width, info.height) if info else (0, 0)
        w, h = round(width * fraction), round(height * fraction)
        for key, value in (("x", (width - w) // 2), ("y", (height - h) // 2), ("width", w), ("height", h)):
            self.box[key].setValue(value)

    def box_options(self) -> dict[str, int]:
        box = {key: spin.value() for key, spin in self.box.items()}
        if not box["width"] or not box["height"]:
            raise ValueError(tr("Informe a largura e a altura da área."))
        return box


class CropForm(_BoxMixin, ToolForm):
    key, title, suffix = "crop", "Cortar", "cortado"
    hint = "Mantém só a área escolhida. X e Y são a distância do canto superior esquerdo da imagem."

    def build(self) -> None:
        self.add_box(self.form)

    def set_info(self, info: MediaInfo | None) -> None:
        self.fill_box(info)

    def options(self) -> dict[str, Any]:
        return {"crop": self.box_options()}


class RotateForm(ToolForm):
    key, title, suffix = "rotate", "Girar e espelhar", "girado"
    hint = "Gira no sentido horário e/ou espelha. Ângulos que não são múltiplos de 90° deixam os cantos transparentes (pretos no vídeo)."

    def build(self) -> None:
        self.angle = QDoubleSpinBox()
        self.angle.setRange(0, 359)
        self.angle.setDecimals(1)
        self.angle.setSuffix("°")
        self.angle.setValue(90)
        row = QHBoxLayout()
        row.addWidget(self.angle)
        for degrees in (90, 180, 270):
            quick = button(f"{degrees}°")
            quick.clicked.connect(lambda _=False, d=degrees: self.angle.setValue(d))
            row.addWidget(quick)
        row.addStretch(1)
        self.form.addRow(tr("Ângulo:"), row)
        self.flip_h = QCheckBox(tr("Espelhar na horizontal"))
        self.flip_v = QCheckBox(tr("Espelhar na vertical"))
        self.form.addRow(self.flip_h)
        self.form.addRow(self.flip_v)

    def options(self) -> dict[str, Any]:
        return {"rotate": self.angle.value(), "flip_h": self.flip_h.isChecked(), "flip_v": self.flip_v.isChecked()}


class CutForm(_RangeMixin, ToolForm):
    key, title, suffix = "cut", "Recortar duração", "trecho"
    hint = "Mantém só o trecho entre o início e o fim."

    def build(self) -> None:
        self.add_range(self.form)

    def set_info(self, info: MediaInfo | None) -> None:
        self.fill_range(info)

    def options(self) -> dict[str, Any]:
        options = self.range_options()
        if not options:
            raise ValueError(tr("Mude o início ou o fim para escolher um trecho."))
        return options


class SpeedForm(ToolForm):
    key, title, suffix = "speed", "Velocidade e direção", "velocidade"
    hint = "200% toca no dobro da velocidade; 50%, na metade. No vídeo o áudio acompanha (o vai e volta fica sem som)."

    def build(self) -> None:
        self.speed = _spin(10, 1000, 100, " %", step=10)
        self.reverse = QCheckBox(tr("Inverter (de trás para frente)"))
        self.boomerang = QCheckBox(tr("Vai e volta (bumerangue)"))
        self.form.addRow(tr("Velocidade:"), self.speed)
        self.form.addRow(self.reverse)
        self.form.addRow(self.boomerang)

    def options(self) -> dict[str, Any]:
        return {"speed": self.speed.value() / 100, "reverse": self.reverse.isChecked(),
                "boomerang": self.boomerang.isChecked()}


class EffectsForm(ToolForm):
    key, title, suffix = "effects", "Efeitos", "efeitos"
    hint = "Cores, brilho, contraste, desfoque e nitidez. 100% deixa como está."

    def build(self) -> None:
        self.checks = {key: QCheckBox(tr(label)) for key, label in EFFECT_LABELS.items()}
        row = QHBoxLayout()
        for check in self.checks.values():
            row.addWidget(check)
        row.addStretch(1)
        self.form.addRow(row)
        self.levels = {key: _spin(0, 300, 100, " %", step=5) for key in ("brightness", "contrast", "saturation")}
        self.form.addRow(tr("Brilho:"), self.levels["brightness"])
        self.form.addRow(tr("Contraste:"), self.levels["contrast"])
        self.form.addRow(tr("Saturação:"), self.levels["saturation"])
        self.blur = _spin(0, 50, 0, " px", tr("Sem desfoque"))
        self.form.addRow(tr("Desfoque:"), self.blur)
        self.use_background = QCheckBox(tr("Trocar a transparência por uma cor:"))
        self.background = ColorField("#ffffff")
        self.form.addRow(self.use_background, self.background)

    def options(self) -> dict[str, Any]:
        options: dict[str, Any] = {key: True for key, check in self.checks.items() if check.isChecked()}
        options.update({key: spin.value() / 100 for key, spin in self.levels.items() if spin.value() != 100})
        if self.blur.value():
            options["blur"] = self.blur.value()
        if self.use_background.isChecked():
            options["background"] = self.background.value()
        if not options:
            raise ValueError(tr("Escolha pelo menos um efeito."))
        return options


class TextForm(ToolForm):
    key, title, suffix = "text", "Texto", "texto"
    hint = "Escreve uma legenda sobre a imagem (em todos os quadros). Use Enter para quebrar a linha."

    def build(self) -> None:
        self.text = QPlainTextEdit()
        self.text.setFixedHeight(70)
        self.text.setPlaceholderText(tr("Digite o texto"))
        self.form.addRow(self.text)
        self.font_combo = _combo(FONT_LABELS, "impact")
        self.size = _spin(0, 500, 0, " px", tr("Automático"))
        self.color = ColorField("#ffffff")
        self.outline = ColorField("#000000")
        self.position = _combo(POSITION_LABELS, "bottom")
        self.form.addRow(tr("Fonte:"), self.font_combo)
        self.form.addRow(tr("Tamanho:"), self.size)
        self.form.addRow(tr("Cor:"), self.color)
        self.form.addRow(tr("Contorno:"), self.outline)
        self.form.addRow(tr("Posição:"), self.position)

    def options(self) -> dict[str, Any]:
        caption = self.text.toPlainText().strip()
        if not caption:
            raise ValueError(tr("Digite o texto."))
        return {"text": {"text": caption, "font": self.font_combo.currentData(), "size": self.size.value() or None,
                         "color": self.color.value(), "stroke_color": self.outline.value(),
                         "position": self.position.currentData()}}


class OverlayForm(ToolForm):
    key, title, suffix = "overlay", "Marca-d'água", "marca"
    hint = "Coloca uma imagem (logo, figurinha) por cima. PNG com transparência funciona melhor."

    def build(self) -> None:
        self.path = QLineEdit()
        self.path.setPlaceholderText(tr("Imagem a sobrepor"))
        pick = button(tr("Escolher…"))
        pick.clicked.connect(self._pick)
        row = QHBoxLayout()
        row.addWidget(self.path, 1)
        row.addWidget(pick)
        self.form.addRow(tr("Imagem:"), row)
        self.position = _combo(POSITION_LABELS, "bottom-right")
        self.scale = _spin(0, 100, 25, " %", tr("Tamanho original"))
        self.scale.setToolTip(tr("Largura da imagem sobreposta, em % da largura do arquivo"))
        self.opacity = _spin(5, 100, 100, " %")
        self.form.addRow(tr("Posição:"), self.position)
        self.form.addRow(tr("Largura:"), self.scale)
        self.form.addRow(tr("Opacidade:"), self.opacity)

    def _pick(self) -> None:
        pattern = " ".join(f"*.{e}" for e in sorted(IMAGE_EXTS))
        chosen, _ = QFileDialog.getOpenFileName(self, tr("Imagem a sobrepor"), "", f"{tr('Imagens')} ({pattern})")
        if chosen:
            self.path.setText(chosen)

    def options(self) -> dict[str, Any]:
        path = self.path.text().strip().strip('"')
        if not path or not Path(path).is_file():
            raise ValueError(tr("Escolha a imagem a sobrepor."))
        return {"overlay": {"path": path, "position": self.position.currentData(),
                            "scale": self.scale.value() or None, "opacity": self.opacity.value()}}


class CensorForm(_BoxMixin, ToolForm):
    key, title, suffix = "censor", "Censurar área", "censurado"
    hint = "Esconde uma área retangular (rosto, placa, nome) em todos os quadros."

    def build(self) -> None:
        self.mode = _combo(CENSOR_LABELS, "blur")
        self.form.addRow(tr("Como esconder:"), self.mode)
        self.add_box(self.form)

    def set_info(self, info: MediaInfo | None) -> None:
        self.fill_box(info, 0.5)

    def options(self) -> dict[str, Any]:
        return {"censor": {**self.box_options(), "mode": self.mode.currentData()}}


class OptimizeForm(ToolForm):
    key, title, suffix = "optimize", "Otimizar (reduzir o arquivo)", "otimizado"
    hint = "Menos cores e menos quadros deixam o GIF menor; qualidade vale para WebP, AVIF, JPG e vídeo. Reduzir o tamanho em Redimensionar costuma ser o que mais ajuda."

    def build(self) -> None:
        self.colors = _spin(1, 256, 1, "", tr("Manter as cores"))
        self.colors.setToolTip(tr("GIF e PNG: no máximo esta quantidade de cores (2 a 256)"))
        self.quality = _spin(1, 100, 80)
        self.lossless = QCheckBox(tr("WebP sem perdas"))
        self.drop = _combo(DROP_LABELS, 0)
        self.dedupe = QCheckBox(tr("Juntar quadros repetidos"))
        self.mute = QCheckBox(tr("Remover o áudio (vídeo)"))
        self.form.addRow(tr("Cores (GIF/PNG):"), self.colors)
        self.form.addRow(tr("Qualidade (1-100):"), self.quality)
        self.form.addRow(self.lossless)
        self.form.addRow(tr("Quadros:"), self.drop)
        self.form.addRow(self.dedupe)
        self.form.addRow(self.mute)

    def options(self) -> dict[str, Any]:
        options: dict[str, Any] = {"quality": self.quality.value(), "lossless": self.lossless.isChecked(),
                                   "dedupe": self.dedupe.isChecked(), "mute": self.mute.isChecked()}
        if self.colors.value() > 1:
            options["colors"] = self.colors.value()
        if self.drop.currentData():
            options["drop_every"] = self.drop.currentData()
        return options


class ConvertForm(ToolForm):
    key, title, suffix = "convert", "Converter formato", "convertido"
    hint = "GIF → MP4/WebM, GIF ↔ WebP/APNG/AVIF, vídeo → animação. Escolha o formato em \"Salvar como\"."

    def build(self) -> None:
        self.loop = _spin(-1, 1000, -1, "", tr("Manter"))
        self.loop.setToolTip(tr("Quantas vezes a animação toca: 0 = sem parar, 1 = uma vez"))
        self.fps = _spin(1, 60, 10, " fps")
        self.fps.setToolTip(tr("Usado só quando a origem é um vídeo e a saída é uma animação"))
        self.use_background = QCheckBox(tr("Trocar a transparência por uma cor:"))
        self.background = ColorField("#ffffff")
        self.form.addRow(tr("Repetições:"), self.loop)
        self.form.addRow(tr("Quadros por segundo:"), self.fps)
        self.form.addRow(self.use_background, self.background)

    def options(self) -> dict[str, Any]:
        options: dict[str, Any] = {"fps": self.fps.value()}
        if self.loop.value() >= 0:
            options["loop"] = self.loop.value()
        if self.use_background.isChecked():
            options["background"] = self.background.value()
        return options


class MakeForm(ToolForm):
    key, title, suffix = "make", "Criar GIF com imagens", "animação"
    hint = "Cada imagem vira um quadro, na ordem da lista. Um GIF na lista entra com todos os seus quadros (serve para juntar GIFs). Todos ficam do tamanho da primeira."
    needs_input = False

    def build(self) -> None:
        self.files = FileList(IMAGE_EXTS)
        self.form.addRow(self.files)
        self.delay = _spin(10, 60000, 200, " ms", step=10)
        self.loop = _spin(0, 1000, 0, "", tr("Sem parar"))
        self.form.addRow(tr("Tempo de cada imagem:"), self.delay)
        self.form.addRow(tr("Repetições:"), self.loop)

    def targets(self, info: MediaInfo | None) -> list[str]:
        return [*animated_targets(), "mp4", "webm"]

    def prepare(self, source: Path | None, target: str, out_dir: Path | None) -> Job:
        files = list(self.files.files)
        if not files:
            raise ValueError(tr("Adicione pelo menos uma imagem."))
        options = {**self.page.base_options(), "delay": self.delay.value(), "loop": self.loop.value()}
        output = tools.output_path(files[0], tr(self.suffix), target, out_dir)
        return lambda cancel: [tools.make_animation(files, output, options, cancel)]


class SplitForm(ToolForm):
    key, title = "split", "Dividir em quadros"
    hint = "Grava cada quadro como uma imagem, numa pasta \"nome (quadros)\" ao lado do original (ou num ZIP)."

    def build(self) -> None:
        self.fps = _spin(0, 60, 0, " fps", tr("Todos os quadros"))
        self.fps.setToolTip(tr("Só para vídeo: quantos quadros por segundo extrair"))
        self.zip = QCheckBox(tr("Gravar num arquivo ZIP"))
        self.form.addRow(tr("Quadros por segundo:"), self.fps)
        self.form.addRow(self.zip)

    def targets(self, info: MediaInfo | None) -> list[str]:
        return list(tools.FRAME_FORMATS) if info else []

    def prepare(self, source: Path | None, target: str, out_dir: Path | None) -> Job:
        assert source is not None
        options = {**self.page.base_options(), "format": target, "zip": self.zip.isChecked()}
        if self.fps.value():
            options["fps"] = self.fps.value()
        return lambda cancel: tools.split_frames(source, out_dir, options, cancel)


class SpriteForm(ToolForm):
    key, title, suffix = "sprite", "Sprite sheet", "sprite"
    hint = "Coloca todos os quadros lado a lado numa imagem só, ou faz o caminho inverso: corta uma grade de quadros e monta a animação."

    def build(self) -> None:
        self.mode = _combo(SPRITE_LABELS, "make")
        self.mode.currentIndexChanged.connect(self._mode_changed)
        self.columns = _spin(0, 500, 0, "", tr("Automático"))
        self.rows = _spin(1, 500, 1)
        self.delay = _spin(10, 60000, 100, " ms", step=10)
        self.form.addRow(tr("O que fazer:"), self.mode)
        self.form.addRow(tr("Colunas:"), self.columns)
        self.form.addRow(tr("Linhas:"), self.rows)
        self.form.addRow(tr("Tempo de cada quadro:"), self.delay)
        self._mode_changed()

    def _mode_changed(self) -> None:
        cutting = self.mode.currentData() == "cut"
        self.rows.setEnabled(cutting)
        self.delay.setEnabled(cutting)
        self.changed.emit()

    def targets(self, info: MediaInfo | None) -> list[str]:
        if info is None:
            return []
        return animated_targets() if self.mode.currentData() == "cut" else ["png", "webp"]

    def prepare(self, source: Path | None, target: str, out_dir: Path | None) -> Job:
        assert source is not None
        options = {**self.page.base_options(), "columns": self.columns.value()}
        if self.mode.currentData() == "make":
            output = tools.output_path(source, tr(self.suffix), target, out_dir)
            return lambda cancel: [tools.make_sprite_sheet(source, output, options, cancel)]
        if not self.columns.value():
            raise ValueError(tr("Informe quantas colunas e linhas a grade tem."))
        options.update(rows=self.rows.value(), delay=self.delay.value())
        output = tools.output_path(source, tr(MakeForm.suffix), target, out_dir)
        return lambda cancel: [tools.cut_sprite_sheet(source, output, options, cancel)]


class MergeForm(ToolForm):
    key, title, suffix = "merge", "Juntar vídeos", "junto"
    hint = "Coloca os vídeos um depois do outro, na ordem da lista. Todos são ajustados ao tamanho do primeiro (com barras, sem distorcer)."
    needs_input = False

    def build(self) -> None:
        self.files = FileList(tuple(VIDEO_EXTS))
        self.form.addRow(self.files)
        self.mute = QCheckBox(tr("Remover o áudio"))
        self.form.addRow(self.mute)

    def targets(self, info: MediaInfo | None) -> list[str]:
        return VIDEO_TARGETS[:3]

    def prepare(self, source: Path | None, target: str, out_dir: Path | None) -> Job:
        files = list(self.files.files)
        if len(files) < 2:
            raise ValueError(tr("Adicione pelo menos dois vídeos."))
        options = {**self.page.base_options(), "mute": self.mute.isChecked()}
        output = tools.output_path(files[0], tr(self.suffix), target, out_dir)
        return lambda cancel: [tools.merge_videos(files, output, options, cancel)]


TOOL_FORMS: tuple[type[ToolForm], ...] = (
    MakeForm, VideoToGifForm, ResizeForm, CropForm, RotateForm, CutForm, SpeedForm, EffectsForm, TextForm,
    OverlayForm, CensorForm, OptimizeForm, ConvertForm, SplitForm, SpriteForm, MergeForm,
)


# ---------------------------------------------------------------- pagina

class _Signals(QObject):
    done = Signal(object)  # list[Path]
    failed = Signal(str)


class EditorPage(QWidget):
    def __init__(self, ctx: AppContext, parent: QWidget | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.source: Path | None = None
        self.info: MediaInfo | None = None
        self.result: Path | None = None
        self._history: History | None = None
        self._cancel = threading.Event()
        self._worker: threading.Thread | None = None
        self._movie: QMovie | None = None
        self._tmp: Path | None = None  # quadro de previa de video
        self._signals = _Signals(self)
        self._signals.done.connect(self._on_done)
        self._signals.failed.connect(self._on_failed)
        self.setAcceptDrops(True)
        self._build()

    # ------------------------------------------------------------ layout

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 24, 28, 12)
        layout.setSpacing(10)
        layout.addWidget(page_header(tr("Editor"), tr(
            "Ferramentas para GIFs, animações (WebP, APNG, AVIF) e vídeos: criar, redimensionar, cortar, "
            "girar, mudar a velocidade, escrever texto, otimizar e converter. Os originais nunca são alterados.")))

        top, top_layout = card()
        row = QHBoxLayout()
        row.addWidget(QLabel("🎞"))
        names = QVBoxLayout()
        self.file_label = QLabel(tr("Nenhum arquivo aberto"))
        self.file_label.setObjectName("SectionTitle")
        self.info_label = muted(tr("Arraste um GIF, imagem ou vídeo para esta tela, ou use o botão ao lado."), wrap=False)
        names.addWidget(self.file_label)
        names.addWidget(self.info_label)
        row.addLayout(names, 1)
        self.open_btn = button(tr("Abrir arquivo…"))
        self.open_btn.clicked.connect(self.pick_file)
        self.use_result_btn = button(tr("Editar o resultado"),
                                     tooltip=tr("Abre o arquivo que acabou de ser gerado, para aplicar outra ferramenta"))
        self.use_result_btn.clicked.connect(self.use_result)
        row.addWidget(self.open_btn)
        row.addWidget(self.use_result_btn)
        top_layout.addLayout(row)
        layout.addWidget(top)

        body = QHBoxLayout()
        self.tool_list = QListWidget()
        self.tool_list.setFixedWidth(215)
        self.tool_list.setStyleSheet("QListWidget::item { padding: 6px 8px; }")
        body.addWidget(self.tool_list)

        tool_card, tool_layout = card()
        self.tool_title = QLabel()
        self.tool_title.setObjectName("SectionTitle")
        self.tool_hint = muted()
        tool_layout.addWidget(self.tool_title)
        tool_layout.addWidget(self.tool_hint)
        self.stack = QStackedWidget()
        self.stack.setStyleSheet("QStackedWidget { background: transparent; }")
        self.forms: list[ToolForm] = []
        for form_class in TOOL_FORMS:
            form = form_class(self)
            form.changed.connect(self._refresh_targets)
            self.forms.append(form)
            self.stack.addWidget(form)
            self.tool_list.addItem(tr(form.title))
        tool_layout.addWidget(self.stack)
        tool_layout.addStretch(1)
        body.addWidget(tool_card, 1)
        self.tool_list.currentRowChanged.connect(self._select_tool)

        preview, preview_layout = card()
        preview.setFixedWidth(PREVIEW_SIZE.width() + 40)
        self.preview_title = QLabel(tr("Prévia"))
        self.preview_title.setObjectName("SectionTitle")
        preview_layout.addWidget(self.preview_title)
        self.preview = QLabel(tr("Sem prévia"))
        self.preview.setObjectName("Thumb")
        self.preview.setFixedSize(PREVIEW_SIZE)
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        preview_layout.addWidget(self.preview, 0, Qt.AlignmentFlag.AlignHCenter)
        self.preview_text = muted()
        self.preview_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        preview_layout.addWidget(self.preview_text)
        preview_layout.addStretch(1)
        body.addWidget(preview)
        layout.addLayout(body, 1)

        options = QHBoxLayout()
        options.addWidget(QLabel(tr("Salvar como:")))
        self.format_combo = QComboBox()
        self.format_combo.setMinimumWidth(110)
        options.addWidget(self.format_combo)
        options.addSpacing(16)
        options.addWidget(QLabel(tr("Salvar em:")))
        self.output_dir = QLineEdit()
        self.output_dir.setPlaceholderText(tr("Mesma pasta do arquivo original"))
        self.output_dir.setClearButtonEnabled(True)
        options.addWidget(self.output_dir, 1)
        choose = button(tr("Escolher…"))
        choose.clicked.connect(self._pick_output_dir)
        options.addWidget(choose)
        layout.addLayout(options)

        actions = QHBoxLayout()
        self.apply_btn = button(tr("Aplicar"), "primary")
        self.apply_btn.clicked.connect(self.apply)
        self.cancel_btn = button(tr("Cancelar"), "danger")
        self.cancel_btn.clicked.connect(self.cancel)
        self.open_folder_btn = button(tr("Abrir pasta do resultado"))
        self.open_folder_btn.clicked.connect(self.open_result_folder)
        for btn in (self.apply_btn, self.cancel_btn, self.open_folder_btn):
            actions.addWidget(btn)
        actions.addStretch(1)
        layout.addLayout(actions)

        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.status_label = muted(wrap=False)
        layout.addWidget(self.status_label)
        self.tool_list.setCurrentRow(0)

    @property
    def history(self) -> History:
        if self._history is None:
            self._history = History()
        return self._history

    @property
    def running(self) -> bool:
        return self._worker is not None and self._worker.is_alive()

    @property
    def form(self) -> ToolForm:
        return self.forms[max(0, self.stack.currentIndex())]

    def select_tool(self, key: str) -> ToolForm:
        index = next(i for i, form in enumerate(self.forms) if form.key == key)
        self.tool_list.setCurrentRow(index)
        return self.forms[index]

    def _select_tool(self, index: int) -> None:
        if index < 0:
            return
        self.stack.setCurrentIndex(index)
        self.tool_title.setText(tr(self.form.title))
        self.tool_hint.setText(tr(self.form.hint))
        self._refresh_targets()

    def _refresh_targets(self) -> None:
        # o primeiro e sempre o formato natural da ferramenta (em geral, o do proprio arquivo)
        choices = self.form.targets(self.info)
        self.format_combo.clear()
        for ext in choices:
            self.format_combo.addItem(ext.upper(), ext)
        self.format_combo.setEnabled(bool(choices))
        self._update_buttons()

    def _update_buttons(self) -> None:
        running = self.running
        ready = self.format_combo.count() > 0 and (self.info is not None or not self.form.needs_input)
        self.apply_btn.setEnabled(not running and ready)
        self.cancel_btn.setEnabled(running and not self._cancel.is_set())
        self.open_btn.setEnabled(not running)
        self.tool_list.setEnabled(not running)
        self.open_folder_btn.setEnabled(self.result is not None)
        self.use_result_btn.setEnabled(not running and self.result is not None and self.result != self.source
                                       and self._can_open(self.result))

    def base_options(self) -> dict[str, Any]:
        return {"ffmpeg_path": self.ctx.settings_store.settings.ffmpeg_path}

    # ------------------------------------------------------------ arquivo

    @staticmethod
    def _can_open(path: Path) -> bool:
        ext = path.suffix.lower().lstrip(".")
        return path.is_file() and (ext in IMAGE_EXTS or ext in VIDEO_EXTS)

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802 - API Qt
        if any(u.isLocalFile() for u in event.mimeData().urls()):
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802 - API Qt
        paths = [Path(u.toLocalFile()) for u in event.mimeData().urls() if u.isLocalFile()]
        if not paths or self.running:
            return
        event.acceptProposedAction()
        if isinstance(self.form, (MakeForm, MergeForm)):  # ferramentas com lista: soltar adiciona a lista
            self.form.files.add(paths)
        else:
            self.open_file(paths[0])

    def pick_file(self) -> None:
        pattern = " ".join(f"*.{e}" for e in sorted({*IMAGE_EXTS, *VIDEO_EXTS}))
        chosen, _ = QFileDialog.getOpenFileName(self, tr("Abrir arquivo"), "",
                                                f"{tr('Imagens e vídeos')} ({pattern});;{tr('Todos os arquivos')} (*)")
        if chosen:
            self.open_file(Path(chosen))

    def open_file(self, path: Path) -> bool:
        try:
            info = probe(path, self.base_options()["ffmpeg_path"])
        except ConversionError as exc:
            QMessageBox.warning(self, tr("Não foi possível abrir o arquivo"), exc.user_message())
            return False
        self.source, self.info = path, info
        self.file_label.setText(path.name)
        self.file_label.setToolTip(str(path))
        try:
            size = human_size(path.stat().st_size)
        except OSError:
            size = "?"
        self.info_label.setText(f"{describe(info)}  •  {size}")
        for form in self.forms:
            form.set_info(info)
        self._show_preview(path, info, tr("Prévia (original)"))
        self._refresh_targets()
        return True

    def use_result(self) -> None:
        if self.result is not None and not self.running:
            self.open_file(self.result)

    def _pick_output_dir(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, tr("Salvar em"), self.output_dir.text())
        if folder:
            self.output_dir.setText(folder)

    # ------------------------------------------------------------ previa

    def _show_preview(self, path: Path, info: MediaInfo | None, title: str) -> None:
        self.preview_title.setText(title)
        if self._movie is not None:
            self._movie.stop()
            self._movie.deleteLater()
            self._movie = None
        self.preview.setPixmap(QPixmap())
        self.preview.setText(tr("Sem prévia"))
        lines = [path.name]
        if info is not None:
            lines.append(describe(info))
        try:
            lines.append(human_size(path.stat().st_size))
            ext = path.suffix.lower().lstrip(".")
            if (info is not None and info.kind == "animation" and ext in ("gif", "webp")
                    and path.stat().st_size <= MAX_MOVIE_BYTES):
                self._play(path, info)
            else:
                image = self._still(path)
                if image is not None and not image.isNull():
                    self.preview.setPixmap(QPixmap.fromImage(image).scaled(
                        PREVIEW_SIZE, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        except Exception:  # noqa: BLE001 - previa e so informativa
            log.debug("preview failed for %s", path, exc_info=True)
        self.preview_text.setText("\n".join(lines))

    def _play(self, path: Path, info: MediaInfo) -> None:
        # o QMovie le de uma copia em memoria: o arquivo nao fica travado no disco
        buffer = QBuffer(self)
        buffer.setData(QByteArray(path.read_bytes()))
        buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        movie = QMovie(buffer, QByteArray(), self)
        buffer.setParent(movie)
        movie.setScaledSize(QSize(info.width, info.height).scaled(PREVIEW_SIZE, Qt.AspectRatioMode.KeepAspectRatio))
        self._movie = movie
        self.preview.setMovie(movie)
        movie.start()

    def _still(self, path: Path) -> QImage | None:
        if path.suffix.lower().lstrip(".") in VIDEO_EXTS:
            ffmpeg = find_ffmpeg(self.base_options()["ffmpeg_path"])
            if self._tmp is None:
                self._tmp = Path(tempfile.mkdtemp(prefix="umd_editor_"))
            thumb = self._tmp / "preview.jpg"
            thumb.unlink(missing_ok=True)
            if not ffmpeg or not extract_frame(ffmpeg, path, thumb, 0.5, PREVIEW_SIZE.width() * 2):
                return None
            return QImage(str(thumb))
        from PIL import Image

        with Image.open(path) as img:
            frame = img.convert("RGBA")
        frame.thumbnail((PREVIEW_SIZE.width() * 2, PREVIEW_SIZE.height() * 2))
        return QImage(frame.tobytes(), frame.width, frame.height, frame.width * 4,
                      QImage.Format.Format_RGBA8888).copy()

    # ------------------------------------------------------------ execucao

    def apply(self) -> None:
        if self.running:
            return
        form = self.form
        if form.needs_input and self.source is None:
            info_box(self, tr("Abra um arquivo"), tr("Abra um GIF, imagem ou vídeo para usar esta ferramenta."))
            return
        target = self.format_combo.currentData()
        out_text = self.output_dir.text().strip()
        try:
            job = form.prepare(self.source, target, Path(out_text) if out_text else None)
        except ValueError as exc:
            info_box(self, tr(form.title), str(exc))
            return
        source = self.source if form.needs_input else None
        self._cancel = threading.Event()
        cancel, signals, history = self._cancel, self._signals, self.history
        log.info("editor: %s -> %s", form.key, target)

        def work() -> None:
            try:
                outputs = job(cancel)
            except ConversionError as exc:
                message = exc.user_message()
                if exc.message != CANCELLED_MESSAGE and source is not None:
                    history.record(str(source), None, "Erro", message)
                signals.failed.emit("" if exc.message == CANCELLED_MESSAGE else message)
            except Exception as exc:  # noqa: BLE001 - nunca derruba a tela
                log.exception("editor tool crashed")
                signals.failed.emit(tr("Erro inesperado: {message}", message=str(exc)))
            else:
                if source is not None and outputs:
                    history.record(str(source), str(outputs[0]), "Concluido")
                signals.done.emit(outputs)

        self.progress.setRange(0, 0)  # sem porcentagem: so indica que esta trabalhando
        self.status_label.setText(tr("Processando: {tool}…", tool=tr(form.title)))
        self._worker = threading.Thread(target=work, name="umd-editor", daemon=True)
        self._worker.start()
        self._update_buttons()

    def _finish(self, value: int) -> None:
        if self._worker is not None:
            self._worker.join(timeout=1)
        self._worker = None
        self.progress.setRange(0, 100)
        self.progress.setValue(value)

    def _on_done(self, outputs: list) -> None:
        self._finish(100)
        if not outputs:
            self._update_buttons()
            return
        first: Path = outputs[0]
        self.result = first
        if len(outputs) > 1:
            self.status_label.setText(tr("{n} quadros gravados em: {path}", n=len(outputs), path=str(first.parent)))
        else:
            text = tr("Salvo: {path}", path=str(first))
            try:
                size = first.stat().st_size
                text += f"  •  {human_size(size)}"
                if self.source is not None and self.form.needs_input:
                    before = self.source.stat().st_size
                    if before:
                        text += "  " + tr("({change} em relação ao original)", change=f"{(size - before) / before:+.0%}")
            except OSError:
                pass
            self.status_label.setText(text)
        if self._can_open(first):
            try:
                info = probe(first, self.base_options()["ffmpeg_path"])
            except ConversionError:
                info = None
            self._show_preview(first, info, tr("Prévia (resultado)"))
        self._update_buttons()

    def _on_failed(self, message: str) -> None:
        self._finish(0)
        self.status_label.setText(tr("Cancelado.") if not message else tr("Não foi possível concluir."))
        self._update_buttons()
        if message:
            QMessageBox.warning(self, tr("Não foi possível concluir"), message)

    def cancel(self) -> None:
        self._cancel.set()
        self.status_label.setText(tr("Cancelando…"))
        self._update_buttons()

    def open_result_folder(self) -> None:
        if self.result is not None:
            reveal_in_folder(self, self.result)

    def shutdown(self, timeout: float = 5) -> None:
        """Cancela o que estiver rodando (sem deixar ffmpeg orfao) e apaga os temporarios da previa."""
        if self.running:
            self._cancel.set()
            assert self._worker is not None
            self._worker.join(timeout)
        if self._tmp is not None:
            shutil.rmtree(self._tmp, ignore_errors=True)
            self._tmp = None
