from __future__ import annotations

from pathlib import Path

import pytest

COLORS = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0)]


def make_gif(path: Path, size=(40, 30), colors=COLORS, duration=100, transparent=False) -> Path:
    from PIL import Image

    frames = []
    for color in colors:
        frame = Image.new("RGBA", size, color + (255,))
        if transparent:
            frame.paste((0, 0, 0, 0), (0, 0, 10, 10))
        frames.append(frame)
    path.parent.mkdir(parents=True, exist_ok=True)
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=duration, loop=0, disposal=2)
    return path


def make_png(path: Path, color=(10, 120, 200), size=(40, 30)) -> Path:
    from PIL import Image

    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGB", size, color).save(path)
    return path


def read(path: Path):
    """(quadros RGBA, duracoes em ms) do arquivo gravado."""
    from PIL import Image, ImageSequence

    with Image.open(path) as img:
        frames, durations = [], []
        for frame in ImageSequence.Iterator(img):
            durations.append(frame.info.get("duration"))
            frames.append(frame.convert("RGBA"))
        return frames, durations


@pytest.fixture
def gif(tmp_path: Path) -> Path:
    return make_gif(tmp_path / "anim.gif")
