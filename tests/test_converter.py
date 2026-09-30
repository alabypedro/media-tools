from __future__ import annotations

import shutil
import threading
from pathlib import Path

import pytest

from umd.core.exceptions import ErrorCode, UMDError
from umd.media.converter import convert_image, convert_media
from umd.media.ffmpeg import extract_frame, ffmpeg_info


def test_ffmpeg_is_found() -> None:
    info = ffmpeg_info()
    assert info.ok and info.version


def test_png_with_transparency_to_jpg(sample_png: Path) -> None:
    from PIL import Image

    out = convert_image(sample_png, "jpg")
    assert out.suffix == ".jpg" and not sample_png.exists()
    with Image.open(out) as img:
        assert img.mode == "RGB"


def test_same_format_is_not_converted(sample_png: Path) -> None:
    assert convert_image(sample_png, "png") == sample_png


def test_broken_image_gives_friendly_error(tmp_path: Path) -> None:
    broken = tmp_path / "quebrada.webp"
    broken.write_bytes(b"isto nao e imagem")
    with pytest.raises(UMDError) as info:
        convert_image(broken, "jpg")
    assert info.value.code == ErrorCode.FFMPEG_FAILED


def test_remux_mp4_to_mkv_without_reencoding(tmp_path: Path, sample_video: Path, ffmpeg_path: str) -> None:
    source = tmp_path / "v.mp4"
    shutil.copy(sample_video, source)
    progress = []
    out = convert_media(ffmpeg_path, source, "mkv", duration=2, on_progress=progress.append)
    assert out.suffix == ".mkv" and out.stat().st_size > 0 and not source.exists()


def test_extract_audio_to_mp3(tmp_path: Path, sample_video: Path, ffmpeg_path: str) -> None:
    source = tmp_path / "v.mp4"
    shutil.copy(sample_video, source)
    out = convert_media(ffmpeg_path, source, "mp3", audio_bitrate="128")
    assert out.suffix == ".mp3" and out.stat().st_size > 1000


def test_conversion_can_be_cancelled(tmp_path: Path, sample_video: Path, ffmpeg_path: str) -> None:
    source = tmp_path / "v.mp4"
    shutil.copy(sample_video, source)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(UMDError) as info:
        convert_media(ffmpeg_path, source, "webm", cancel_event=cancel)
    assert info.value.code == ErrorCode.CANCELLED
    assert source.exists()  # original preservado


def test_thumbnail_from_video(tmp_path: Path, sample_video: Path, ffmpeg_path: str) -> None:
    thumb = tmp_path / "thumb.jpg"
    assert extract_frame(ffmpeg_path, sample_video, thumb)
    assert thumb.stat().st_size > 0
