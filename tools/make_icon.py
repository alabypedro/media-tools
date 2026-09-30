"""Gera assets/icon.png e assets/icon.ico: chave inglesa e chave de fenda
cruzadas ("tools") sobre um quadrado arredondado em degrade azul -> violeta.

Uso: python tools/make_icon.py
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
SIZE = 512
WHITE = (255, 255, 255, 255)
CLEAR = (0, 0, 0, 0)


def _background(big: int) -> Image.Image:
    top, bottom = (37, 99, 235), (124, 58, 237)
    gradient = Image.new("RGBA", (big, big))
    gd = ImageDraw.Draw(gradient)
    for y in range(big):
        t = y / (big - 1)
        gd.line([(0, y), (big, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)) + (255,))
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, big - 1, big - 1], radius=int(big * 0.22), fill=255)
    img = Image.new("RGBA", (big, big), CLEAR)
    img.paste(gradient, (0, 0), mask)
    return img


def _wrench(big: int) -> Image.Image:
    """Chave inglesa na vertical (boca para cima), centrada."""
    layer = Image.new("RGBA", (big, big), CLEAR)
    d = ImageDraw.Draw(layer)
    cx = big // 2
    w = int(big * 0.115)
    d.rounded_rectangle([cx - w // 2, int(big * 0.36), cx + w // 2, int(big * 0.89)], radius=w // 2, fill=WHITE)
    head_r = int(big * 0.155)
    head_cy = int(big * 0.265)
    d.ellipse([cx - head_r, head_cy - head_r, cx + head_r, head_cy + head_r], fill=WHITE)
    # boca da chave: fenda aberta para cima, com fundo arredondado
    jaw = int(big * 0.052)
    jaw_bottom = int(big * 0.255)
    d.rectangle([cx - jaw, 0, cx + jaw, jaw_bottom], fill=CLEAR)
    d.ellipse([cx - jaw, jaw_bottom - jaw, cx + jaw, jaw_bottom + jaw], fill=CLEAR)
    return layer


def _screwdriver(big: int) -> Image.Image:
    """Chave de fenda na vertical (ponta para cima), centrada."""
    layer = Image.new("RGBA", (big, big), CLEAR)
    d = ImageDraw.Draw(layer)
    cx = big // 2
    shaft = int(big * 0.034)
    d.rectangle([cx - shaft, int(big * 0.17), cx + shaft, int(big * 0.56)], fill=WHITE)
    tip = int(big * 0.046)
    d.polygon([(cx - shaft, int(big * 0.17)), (cx + shaft, int(big * 0.17)),
               (cx + tip, int(big * 0.13)), (cx - tip, int(big * 0.13))], fill=WHITE)
    d.rounded_rectangle([cx - tip, int(big * 0.10), cx + tip, int(big * 0.135)], radius=int(big * 0.008), fill=WHITE)
    handle = int(big * 0.078)
    d.rounded_rectangle([cx - handle, int(big * 0.52), cx + handle, int(big * 0.90)], radius=int(big * 0.06), fill=WHITE)
    # sulcos do cabo (vazados: deixam aparecer o fundo)
    groove = int(big * 0.014)
    for x in (cx - int(big * 0.035), cx + int(big * 0.035)):
        d.rounded_rectangle([x - groove, int(big * 0.60), x + groove, int(big * 0.82)], radius=groove, fill=CLEAR)
    return layer


def draw(size: int = SIZE) -> Image.Image:
    scale = 4  # desenha grande e reduz: bordas suaves
    big = size * scale
    img = _background(big)

    screwdriver = _screwdriver(big).rotate(45, resample=Image.BICUBIC)  # ponta para cima-esquerda
    wrench = _wrench(big).rotate(-45, resample=Image.BICUBIC)           # boca para cima-direita

    # a chave inglesa fica por cima; um contorno vazado em volta dela separa as duas ferramentas
    gap = wrench.getchannel("A").filter(ImageFilter.MaxFilter(int(big * 0.03) | 1))
    under = screwdriver.getchannel("A")
    screwdriver.putalpha(ImageChops.subtract(under, gap))
    img.alpha_composite(screwdriver)
    img.alpha_composite(wrench)
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    assets = ROOT / "assets"
    assets.mkdir(exist_ok=True)
    icon = draw()
    icon.save(assets / "icon.png")
    icon.save(assets / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print(f"icons written to {assets}")


if __name__ == "__main__":
    main()
