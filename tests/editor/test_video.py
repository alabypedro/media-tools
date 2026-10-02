"""Editor: tudo que passa pelo FFmpeg (video de teste gerado pelo proprio ffmpeg)."""

from __future__ import annotations

import threading
from pathlib import Path

import pytest

from umd.convert.core.errors import ConversionError
from umd.editor.tools import edit, make_sprite_sheet, split_frames
from umd.editor.video import codec_args, extract_clip, merge_videos, probe, video_filters

from .conftest import read


def test_probe_video(sample_video: Path) -> None:
    info = probe(sample_video)
    assert info.kind == "video" and info.size == (320, 240)
    assert info.duration == pytest.approx(2.0, abs=0.2) and info.fps == pytest.approx(15)
    assert info.has_audio


def test_probe_images(gif: Path, sample_png: Path, tmp_path: Path) -> None:
    info = probe(gif)
    assert (info.kind, info.frames, info.size) == ("animation", 4, (40, 30))
    assert info.duration == pytest.approx(0.4)
    assert probe(sample_png).kind == "image"
    with pytest.raises(ConversionError):
        probe(tmp_path / "nao_existe.gif")
    fake = tmp_path / "fake.mp4"
    fake.write_bytes(b"isto nao e um video")
    with pytest.raises(ConversionError):
        probe(fake)


def test_video_to_gif_with_range_fps_and_size(sample_video: Path, tmp_path: Path) -> None:
    out = edit(sample_video, tmp_path / "out.gif", {"start": 0.5, "end": 1.5, "fps": 5, "resize": {"width": 160}})
    frames, durations = read(out)
    assert len(frames) == 5 and frames[0].size == (160, 120)
    assert durations == [200] * 5


@pytest.mark.parametrize("ext", ["webp", "apng"])
def test_video_to_other_animations(sample_video: Path, tmp_path: Path, ext: str) -> None:
    frames, _ = read(edit(sample_video, tmp_path / f"out.{ext}", {"end": 1, "fps": 4, "resize": {"percent": 25}}))
    assert len(frames) == 4 and frames[0].size == (80, 60)


def test_extract_clip_then_pillow_ops_see_remaining_options(sample_video: Path, tmp_path: Path) -> None:
    clip = extract_clip(sample_video, {"end": 1, "crop": {"x": 0, "y": 0, "width": 100, "height": 50}}, fps=2)
    assert len(clip.frames) == 2 and clip.size == (100, 50) and clip.durations == [500, 500]
    # crop ja foi feito pelo ffmpeg: nao pode ser aplicado de novo (a caixa nao caberia mais)
    out = edit(sample_video, tmp_path / "o.gif", {"end": 1, "fps": 2, "rotate": 90,
                                                  "crop": {"x": 200, "y": 100, "width": 100, "height": 50}})
    frames, _ = read(out)
    assert frames[0].size == (50, 100)


def test_gif_to_mp4_and_back(gif: Path, tmp_path: Path) -> None:
    mp4 = edit(gif, tmp_path / "out.mp4", {"resize": {"width": 81}})  # largura impar: precisa virar par
    info = probe(mp4)
    assert info.kind == "video" and info.width % 2 == 0 and info.height % 2 == 0
    assert info.duration == pytest.approx(0.4, abs=0.15) and not info.has_audio
    webm = edit(gif, tmp_path / "out.webm", {})
    assert probe(webm).size == (40, 30)


def test_video_edit_keeps_audio_and_applies_geometry(sample_video: Path, tmp_path: Path) -> None:
    out = edit(sample_video, tmp_path / "out.mp4", {
        "start": 0.5, "end": 1.5, "crop": {"x": 0, "y": 0, "width": 200, "height": 200},
        "resize": {"width": 100}, "rotate": 90, "grayscale": True, "brightness": 1.2, "contrast": 1.1,
    })
    info = probe(out)
    assert info.size == (100, 100) and info.has_audio
    assert info.duration == pytest.approx(1.0, abs=0.25)


def test_video_speed_reverse_and_mute(sample_video: Path, tmp_path: Path) -> None:
    fast = probe(edit(sample_video, tmp_path / "fast.mp4", {"speed": 2}))
    assert fast.duration == pytest.approx(1.0, abs=0.25) and fast.has_audio
    slow = probe(edit(sample_video, tmp_path / "slow.mp4", {"speed": 0.25, "end": 0.5}))
    assert slow.duration == pytest.approx(2.0, abs=0.4)
    reverse = probe(edit(sample_video, tmp_path / "rev.mp4", {"reverse": True}))
    assert reverse.duration == pytest.approx(2.0, abs=0.25) and reverse.has_audio
    assert not probe(edit(sample_video, tmp_path / "mute.mp4", {"mute": True})).has_audio
    boomerang = probe(edit(sample_video, tmp_path / "boom.mp4", {"boomerang": True}))
    assert boomerang.duration == pytest.approx(4.0, abs=0.4) and not boomerang.has_audio


def test_video_text_overlay_and_censor(sample_video: Path, sample_png: Path, tmp_path: Path) -> None:
    out = edit(sample_video, tmp_path / "out.mp4", {
        "end": 1, "rotate": 30,
        "text": {"text": "legenda\nem duas linhas", "position": "bottom"},
        "overlay": {"path": str(sample_png), "position": "top-right", "scale": 20},
        "censor": {"x": 10, "y": 10, "width": 80, "height": 60, "mode": "pixelate"},
    })
    assert probe(out).duration == pytest.approx(1.0, abs=0.25)
    # o texto branco aparece de fato no quadro
    plain = split_frames(edit(sample_video, tmp_path / "plain.mp4", {"end": 0.2, "grayscale": True,
                                                                    "brightness": 0.1}), tmp_path / "a")
    captioned = split_frames(edit(sample_video, tmp_path / "cap.mp4", {
        "end": 0.2, "grayscale": True, "brightness": 0.1,
        "text": {"text": "OLA", "size": 80, "position": "center", "stroke_width": 0}}), tmp_path / "b")
    brightest = [read(files[0])[0][0].convert("L").getextrema()[1] for files in (plain, captioned)]
    assert brightest[0] < 80 and brightest[1] > 200


@pytest.mark.parametrize("mode", ["blur", "black"])
def test_video_censor_modes(sample_video: Path, tmp_path: Path, mode: str) -> None:
    out = edit(sample_video, tmp_path / f"{mode}.webm", {
        "end": 0.4, "censor": {"x": 0, "y": 0, "width": 100, "height": 100, "mode": mode}})
    assert probe(out).size == (320, 240)


def test_filter_graph_follows_the_same_order_as_pillow(sample_video: Path) -> None:
    info = probe(sample_video)
    graph, audio = video_filters(info, {"crop": {"x": 0, "y": 0, "width": 100, "height": 80},
                                        "resize": {"percent": 50}, "rotate": 90, "speed": 4, "reverse": True}, False)
    assert graph.index("crop=100:80:0:0") < graph.index("scale=50:40") < graph.index("transpose=1")
    assert graph.startswith("[0:v]") and graph.endswith("[vout]")
    assert audio == ["atempo=2.0", "atempo=2.0000", "areverse"]
    assert codec_args("mp4", 100)[0][5] == "18" and codec_args("avi", 80) == ([], [])


def test_merge_videos_of_different_sizes(sample_video: Path, tmp_path: Path) -> None:
    small = edit(sample_video, tmp_path / "small.mp4", {"end": 1, "resize": {"width": 160}})
    out = merge_videos([sample_video, small], tmp_path / "merged.mp4")
    info = probe(out)
    assert info.size == (320, 240) and info.has_audio
    assert info.duration == pytest.approx(3.0, abs=0.4)
    with pytest.raises(ConversionError):
        merge_videos([sample_video], tmp_path / "one.mp4")


def test_split_and_sprite_from_video(sample_video: Path, tmp_path: Path) -> None:
    files = split_frames(sample_video, tmp_path / "frames", {"fps": 2, "format": "jpg"})
    assert len(files) == 4 and files[0].suffix == ".jpg"
    assert len(split_frames(sample_video, tmp_path / "all", {"end": 1})) == 15  # sem fps: todos os quadros
    sheet = make_sprite_sheet(sample_video, tmp_path / "sheet.png", {"fps": 2, "columns": 4,
                                                                    "resize": {"width": 80}})
    assert read(sheet)[0][0].size == (320, 60)


def test_invalid_range_and_cancel(sample_video: Path, tmp_path: Path) -> None:
    with pytest.raises(ConversionError):
        edit(sample_video, tmp_path / "bad.mp4", {"start": 2, "end": 1})
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(ConversionError):
        edit(sample_video, tmp_path / "c.mp4", {}, cancel)
    assert not (tmp_path / "c.mp4").exists()
