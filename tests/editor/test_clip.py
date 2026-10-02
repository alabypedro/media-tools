"""Editor: operacoes sobre animacoes (Pillow), sem FFmpeg."""

from __future__ import annotations

import threading
import zipfile
from pathlib import Path

import pytest

from umd.convert.core.errors import ConversionError
from umd.editor import clip as clip_mod
from umd.editor.clip import Clip, apply_ops, load_clip, min_delay, place, plan_size, resize_size
from umd.editor.tools import cut_sprite_sheet, edit, make_animation, make_sprite_sheet, output_path, split_frames

from .conftest import COLORS, make_gif, make_png, read


def _close(pixel, color, tolerance=40) -> bool:
    return all(abs(a - b) <= tolerance for a, b in zip(pixel[:3], color))


def test_load_reads_frames_durations_and_loop(gif: Path) -> None:
    clip = load_clip(gif)
    assert len(clip.frames) == 4 and clip.durations == [100] * 4
    assert clip.size == (40, 30) and clip.loop == 0 and clip.duration == pytest.approx(0.4)


def test_original_is_never_modified(gif: Path, tmp_path: Path) -> None:
    before = gif.read_bytes()
    edit(gif, tmp_path / "out.gif", {"resize": {"percent": 50}, "reverse": True})
    assert gif.read_bytes() == before


@pytest.mark.parametrize("resize, expected", [
    ({"width": 20}, (20, 15)),
    ({"height": 60}, (80, 60)),
    ({"width": 20, "height": 20}, (20, 15)),  # cabe dentro, sem distorcer
    ({"width": 20, "height": 20, "keep_aspect": False}, (20, 20)),
    ({"percent": 50}, (20, 15)),
    ({}, (40, 30)),
])
def test_resize(gif: Path, tmp_path: Path, resize: dict, expected: tuple) -> None:
    assert resize_size((40, 30), resize) == expected
    frames, _ = read(edit(gif, tmp_path / "out.gif", {"resize": resize}))
    assert frames[0].size == expected and len(frames) == 4


def test_crop_keeps_the_chosen_area(tmp_path: Path) -> None:
    from PIL import Image

    img = Image.new("RGB", (40, 30), (255, 0, 0))
    img.paste((0, 0, 255), (20, 0, 40, 30))
    img.save(tmp_path / "half.png")
    frames, _ = read(edit(tmp_path / "half.png", tmp_path / "out.png",
                          {"crop": {"x": 20, "y": 5, "width": 15, "height": 10}}))
    assert frames[0].size == (15, 10) and frames[0].getpixel((0, 0))[:3] == (0, 0, 255)


def test_crop_outside_image_is_a_friendly_error(gif: Path, tmp_path: Path) -> None:
    with pytest.raises(ConversionError):
        edit(gif, tmp_path / "out.gif", {"crop": {"x": 500, "y": 0, "width": 10, "height": 10}})
    assert not (tmp_path / "out.gif").exists()


def test_rotate_is_clockwise_and_flip_mirrors(tmp_path: Path) -> None:
    from PIL import Image

    img = Image.new("RGB", (40, 30), (255, 255, 255))
    img.paste((255, 0, 0), (0, 0, 10, 10))  # marca no canto superior esquerdo
    img.save(tmp_path / "mark.png")
    rotated, _ = read(edit(tmp_path / "mark.png", tmp_path / "r.png", {"rotate": 90}))
    assert rotated[0].size == (30, 40) and rotated[0].getpixel((28, 1))[:3] == (255, 0, 0)  # foi para a direita
    flipped, _ = read(edit(tmp_path / "mark.png", tmp_path / "f.png", {"flip_h": True}))
    assert flipped[0].getpixel((38, 1))[:3] == (255, 0, 0) and flipped[0].getpixel((1, 1))[:3] == (255, 255, 255)
    free, _ = read(edit(tmp_path / "mark.png", tmp_path / "a.png", {"rotate": 45}))
    assert free[0].size[0] > 40 and free[0].getpixel((0, 0))[3] == 0  # cantos transparentes


def test_plan_size_matches_what_pillow_produces(gif: Path, tmp_path: Path) -> None:
    options = {"crop": {"x": 4, "y": 2, "width": 30, "height": 20}, "resize": {"width": 60}, "rotate": 270}
    frames, _ = read(edit(gif, tmp_path / "out.gif", options))
    assert frames[0].size == plan_size((40, 30), options) == (40, 60)


def test_cut_by_time(gif: Path, tmp_path: Path) -> None:
    frames, _ = read(edit(gif, tmp_path / "out.gif", {"start": 0.1, "end": 0.3}))
    assert len(frames) == 2
    assert _close(frames[0].getpixel((5, 5)), COLORS[1]) and _close(frames[1].getpixel((5, 5)), COLORS[2])
    with pytest.raises(ConversionError):
        edit(gif, tmp_path / "none.gif", {"start": 5})


def test_speed_reverse_and_boomerang(gif: Path, tmp_path: Path) -> None:
    _, durations = read(edit(gif, tmp_path / "fast.gif", {"speed": 2}))
    assert durations == [50] * 4
    frames, _ = read(edit(gif, tmp_path / "rev.gif", {"reverse": True}))
    assert _close(frames[0].getpixel((5, 5)), COLORS[3]) and _close(frames[3].getpixel((5, 5)), COLORS[0])
    frames, _ = read(edit(gif, tmp_path / "boom.gif", {"boomerang": True}))
    assert len(frames) == 6 and _close(frames[4].getpixel((5, 5)), COLORS[2])


def test_gif_never_gets_delays_browsers_would_slow_down() -> None:
    frames, durations = min_delay(list("abcdef"), [10] * 6)
    assert frames == ["a", "c", "e"] and durations == [20, 20, 20]  # mesmo tempo total, metade dos quadros


def test_drop_frames_and_dedupe_keep_total_time(tmp_path: Path) -> None:
    clip = apply_ops(load_clip(make_gif(tmp_path / "rep.gif")), {"drop_every": 2})
    assert len(clip.frames) == 2 and clip.durations == [200, 200]
    source = Clip([f.copy() for f in load_clip(make_gif(tmp_path / "x.gif")).frames[:1]] * 3, [100, 100, 100])
    clip = apply_ops(source, {"dedupe": True})
    assert len(clip.frames) == 1 and clip.durations == [300]


def test_effects(tmp_path: Path) -> None:
    src = make_png(tmp_path / "c.png", (200, 50, 50))
    gray, _ = read(edit(src, tmp_path / "g.png", {"grayscale": True}))
    r, g, b, _a = gray[0].getpixel((5, 5))
    assert r == g == b
    inverted, _ = read(edit(src, tmp_path / "i.png", {"invert": True}))
    assert inverted[0].getpixel((5, 5))[:3] == (55, 205, 205)
    dark, _ = read(edit(src, tmp_path / "d.png", {"brightness": 0.5}))
    assert dark[0].getpixel((5, 5))[:3] == (100, 25, 25)
    sepia, _ = read(edit(src, tmp_path / "s.png", {"sepia": True}))
    r, g, b, _a = sepia[0].getpixel((5, 5))
    assert r > g > b
    same, _ = read(edit(src, tmp_path / "n.png", {"brightness": 1.0, "contrast": 1, "saturation": 1.0}))
    assert same[0].getpixel((5, 5))[:3] == (200, 50, 50)


def test_text_and_overlay_are_drawn_where_asked(tmp_path: Path) -> None:
    src = make_png(tmp_path / "bg.png", (0, 0, 0), size=(200, 120))
    out, _ = read(edit(src, tmp_path / "t.png", {"text": {
        "text": "OLA", "size": 40, "color": "#ffffff", "stroke_width": 0, "position": "top"}}))
    top = out[0].crop((0, 0, 200, 60)).convert("L").getextrema()[1]
    bottom = out[0].crop((0, 60, 200, 120)).convert("L").getextrema()[1]
    assert top == 255 and bottom == 0
    mark = make_png(tmp_path / "mark.png", (255, 0, 0), size=(20, 20))
    out, _ = read(edit(src, tmp_path / "o.png", {"overlay": {
        "path": str(mark), "position": "bottom-right", "margin": 0, "scale": 50, "opacity": 50}}))
    assert out[0].getpixel((199, 119))[:3] == (128, 0, 0)  # 50% de vermelho sobre preto
    assert out[0].getpixel((100, 119))[:3] == (128, 0, 0) and out[0].getpixel((99, 119))[:3] == (0, 0, 0)
    assert place((200, 120), (20, 20), "top-left", 5) == (5, 5)
    assert place((200, 120), (20, 20), "center") == (90, 50)


def test_censor_modes_only_touch_the_box(tmp_path: Path) -> None:
    src = make_png(tmp_path / "c.png", (200, 50, 50), size=(60, 60))
    box = {"x": 10, "y": 10, "width": 20, "height": 20}
    out, _ = read(edit(src, tmp_path / "k.png", {"censor": {**box, "mode": "black"}}))
    assert out[0].getpixel((15, 15))[:3] == (0, 0, 0) and out[0].getpixel((40, 40))[:3] == (200, 50, 50)
    for mode in ("blur", "pixelate"):
        out, _ = read(edit(src, tmp_path / f"{mode}.png", {"censor": {**box, "mode": mode}}))
        assert out[0].size == (60, 60)


def test_transparency_survives_gif_and_color_reduction(tmp_path: Path) -> None:
    gif = make_gif(tmp_path / "t.gif", transparent=True)
    for name, options in (("a.gif", {}), ("b.gif", {"colors": 8}), ("c.webp", {}), ("d.apng", {})):
        frames, _ = read(edit(gif, tmp_path / name, options))
        assert len(frames) == 4, name
        assert frames[1].getpixel((2, 2))[3] == 0 and frames[1].getpixel((20, 20))[3] == 255, name
    flat, _ = read(edit(gif, tmp_path / "bg.gif", {"background": "#00ff00"}))
    assert _close(flat[0].getpixel((2, 2)), (0, 255, 0))


def test_color_reduction_makes_the_file_smaller(tmp_path: Path) -> None:
    from PIL import Image

    noisy = [Image.effect_noise((80, 80), 60).convert("RGB") for _ in range(4)]
    src = tmp_path / "noise.gif"
    noisy[0].save(src, save_all=True, append_images=noisy[1:], duration=100)
    full = edit(src, tmp_path / "full.gif", {})
    small = edit(src, tmp_path / "small.gif", {"colors": 4})
    assert small.stat().st_size < full.stat().st_size


@pytest.mark.parametrize("ext", ["gif", "webp", "apng", "png", "avif"])
def test_animated_formats_roundtrip(gif: Path, tmp_path: Path, ext: str) -> None:
    from PIL import features

    if ext == "avif" and not features.check("avif"):
        pytest.skip("Pillow sem AVIF")
    out = edit(gif, tmp_path / f"out.{ext}", {})
    frames, _ = read(out)
    assert len(frames) == 4 and frames[0].size == (40, 30)
    back, _ = read(edit(out, tmp_path / "back.gif", {}))  # e de volta para GIF
    assert len(back) == 4


def test_static_output_keeps_first_frame_and_flattens_for_jpg(tmp_path: Path) -> None:
    gif = make_gif(tmp_path / "t.gif", transparent=True)
    frames, _ = read(edit(gif, tmp_path / "out.jpg", {"background": "#ffffff"}))
    assert len(frames) == 1 and _close(frames[0].getpixel((2, 2)), (255, 255, 255))
    assert _close(frames[0].getpixel((30, 20)), COLORS[0])


def test_unknown_output_format_is_a_friendly_error(gif: Path, tmp_path: Path) -> None:
    with pytest.raises(ConversionError):
        edit(gif, tmp_path / "out.xyz", {})


def test_loop_count_and_fixed_delay(gif: Path, tmp_path: Path) -> None:
    from PIL import Image

    out = edit(gif, tmp_path / "out.gif", {"loop": 3, "delay": 250})
    _, durations = read(out)
    assert durations == [250] * 4
    with Image.open(out) as img:
        assert img.info.get("loop") == 3
    with Image.open(edit(gif, tmp_path / "once.gif", {"loop": 1})) as img:
        assert "loop" not in img.info
    assert load_clip(tmp_path / "once.gif").loop == 1


def test_make_animation_from_images_and_appends_gifs(gif: Path, tmp_path: Path) -> None:
    images = [make_png(tmp_path / "1.png", (9, 9, 9)), make_png(tmp_path / "2.png", COLORS[1], size=(80, 80))]
    out = make_animation(images, tmp_path / "made.gif", {"delay": 300, "loop": 0})
    frames, durations = read(out)
    assert len(frames) == 2 and durations == [300, 300]
    assert frames[1].size == (40, 30)  # todos do tamanho do primeiro
    frames, durations = read(make_animation([images[0], gif], tmp_path / "joined.gif", {"delay": 300}))
    assert len(frames) == 5 and durations == [300, 100, 100, 100, 100]
    with pytest.raises(ConversionError):
        make_animation([], tmp_path / "empty.gif")


def test_split_frames_to_folder_and_zip(gif: Path, tmp_path: Path) -> None:
    files = split_frames(gif, tmp_path / "out", {"format": "png"})
    assert [f.name for f in files] == [f"frame_00{i}.png" for i in range(1, 5)]
    assert files[0].parent.name == "anim (quadros)"
    again = split_frames(gif, tmp_path / "out", {"format": "jpg"})
    assert again[0].parent.name == "anim (quadros) (1)"  # nunca mistura com a pasta anterior
    (archive,) = split_frames(gif, tmp_path / "out", {"zip": True})
    assert archive.name == "anim (quadros).zip"
    with zipfile.ZipFile(archive) as zf:
        assert sorted(zf.namelist()) == [f"frame_00{i}.png" for i in range(1, 5)]
    with pytest.raises(ConversionError):
        split_frames(gif, tmp_path / "out", {"format": "exe"})


def test_sprite_sheet_roundtrip(tmp_path: Path) -> None:
    gif = make_gif(tmp_path / "s.gif", colors=COLORS[:3])
    sheet = make_sprite_sheet(gif, tmp_path / "sheet.png", {"columns": 2})
    frames, _ = read(sheet)
    assert frames[0].size == (80, 60) and frames[0].getpixel((60, 45))[3] == 0  # 4a celula vazia
    back, durations = read(cut_sprite_sheet(sheet, tmp_path / "back.gif", {"columns": 2, "rows": 2, "delay": 80}))
    assert len(back) == 3 and durations == [80] * 3
    assert _close(back[2].getpixel((5, 5)), COLORS[2])


def test_output_path_never_overwrites(gif: Path) -> None:
    first = output_path(gif, "editado", "GIF")
    assert first.name == "anim (editado).gif"
    first.write_bytes(b"x")
    assert output_path(gif, "editado", "gif").name == "anim (editado) (1).gif"


def test_cancel_stops_and_leaves_no_file(gif: Path, tmp_path: Path) -> None:
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(ConversionError) as info:
        edit(gif, tmp_path / "out.gif", {"resize": {"percent": 50}}, cancel)
    assert info.value.message == clip_mod.CANCELLED_MESSAGE
    assert not (tmp_path / "out.gif").exists()


def test_memory_guard(monkeypatch: pytest.MonkeyPatch, gif: Path) -> None:
    monkeypatch.setattr(clip_mod, "MAX_CLIP_PIXELS", 100)
    with pytest.raises(ConversionError):
        load_clip(gif)


def test_corrupt_file_is_a_friendly_error(tmp_path: Path) -> None:
    bad = tmp_path / "bad.gif"
    bad.write_bytes(b"nao e um gif")
    with pytest.raises(ConversionError):
        load_clip(bad)
