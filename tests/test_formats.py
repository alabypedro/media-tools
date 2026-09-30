"""Selecao de formato e interpretacao da saida do yt-dlp (sem rede)."""

from __future__ import annotations

import pytest

from umd.core.config import QualityPreset, Settings
from umd.core.exceptions import ErrorCode, UMDError
from umd.media.formats import (
    DownloadSelection,
    SelectionMode,
    audio_format_choices,
    estimate_size,
    mode_choices,
    nearest_height,
    quality_choices,
    selection_from_settings,
)
from umd.media.metadata import ContentType
from umd.providers.yt_dlp_provider import build_format_params, items_spec, parse_info


def _fmt(fid, ext, height=None, vcodec="none", acodec="none", tbr=None, abr=None, size=None, width=None, **kw):
    return {"format_id": fid, "ext": ext, "height": height, "width": width, "vcodec": vcodec, "acodec": acodec,
            "tbr": tbr, "abr": abr, "filesize": size, **kw}


VIDEO_INFO = {
    "_type": "video", "id": "abc", "title": "Vídeo de teste", "uploader": "Canal", "duration": 100,
    "view_count": 1234, "upload_date": "20240131", "extractor_key": "Youtube", "thumbnail": "https://i.ytimg.com/x.jpg",
    "formats": [
        _fmt("sb0", "mhtml", format_note="storyboard", protocol="mhtml"),
        _fmt("140", "m4a", acodec="mp4a.40.2", abr=129.5, size=1_600_000),
        _fmt("251", "webm", acodec="opus", abr=140.0, size=1_700_000),
        _fmt("134", "mp4", 360, "avc1.4d401e", width=640, tbr=400, size=5_000_000),
        _fmt("136", "mp4", 720, "avc1.4d401f", width=1280, tbr=1500, size=18_000_000),
        _fmt("137", "mp4", 1080, "avc1.640028", width=1920, tbr=3000, size=37_000_000),
        _fmt("313", "webm", 2160, "vp9", width=3840, tbr=12000, size=150_000_000),
        _fmt("999", "mp4", 1440, "avc1", width=2560, has_drm=True),
    ],
}


def test_parse_video_info() -> None:
    info = parse_info(VIDEO_INFO, "https://youtu.be/abc")
    assert info.title == "Vídeo de teste" and info.author == "Canal"
    assert info.content_type == ContentType.VIDEO
    assert info.video_heights == [2160, 1080, 720, 360]  # DRM e storyboard ficam de fora
    assert info.audio_bitrates == [140, 130]
    assert any("DRM" in w for w in info.warnings)


def test_vertical_video_uses_smaller_dimension() -> None:
    raw = {"_type": "video", "id": "s", "title": "Short", "media_type": "short",
           "formats": [_fmt("1", "mp4", 1920, "avc1", width=1080, acodec="mp4a", tbr=2000)]}
    info = parse_info(raw, "https://youtube.com/shorts/s")
    assert info.video_heights == [1080]
    assert info.content_type == ContentType.SHORT


def test_audio_only_source() -> None:
    raw = {"_type": "video", "id": "t", "title": "Música", "duration": 200, "extractor_key": "Soundcloud",
           "formats": [_fmt("mp3", "mp3", acodec="mp3", abr=128), _fmt("flac", "flac", acodec="flac", abr=900)]}
    info = parse_info(raw, "https://soundcloud.com/a/t")
    assert info.content_type == ContentType.AUDIO
    assert [c.key for c in mode_choices(info)] == ["audio"]
    assert "flac" in [c.key for c in audio_format_choices(info)]  # fonte sem perdas: FLAC liberado


def test_flac_hidden_for_lossy_source() -> None:
    info = parse_info(VIDEO_INFO, "https://youtu.be/abc")
    assert "flac" not in [c.key for c in audio_format_choices(info)]


def test_playlist_entries() -> None:
    raw = {"_type": "playlist", "id": "PL1", "title": "Minha playlist", "uploader": "Canal", "playlist_count": 3,
           "entries": [{"id": f"v{i}", "title": f"Vídeo {i}", "url": f"https://youtu.be/v{i}", "duration": 60}
                       for i in range(1, 4)]}
    info = parse_info(raw, "https://youtube.com/playlist?list=PL1")
    assert info.content_type == ContentType.PLAYLIST and info.is_collection
    assert [e.index for e in info.entries] == [1, 2, 3]
    assert info.total_entries == 3 and not info.truncated
    assert [c.key for c in quality_choices(info)] == ["best", "1080", "720", "480", "original"]


def test_channel_tabs_are_selected_by_url() -> None:
    raw = {"_type": "playlist", "id": "UC1", "title": "Canal", "entries": [
        {"_type": "playlist", "title": "Canal - Videos", "webpage_url": "https://www.youtube.com/@canal/videos"},
        {"_type": "playlist", "title": "Canal - Shorts", "webpage_url": "https://www.youtube.com/@canal/shorts"},
    ]}
    info = parse_info(raw, "https://www.youtube.com/@canal")
    assert info.entries_are_urls
    assert [e.url for e in info.entries] == ["https://www.youtube.com/@canal/videos", "https://www.youtube.com/@canal/shorts"]
    assert [c.key for c in mode_choices(info)] == ["video", "audio"]


def test_ytdlp_request_with_selected_urls(fake_runner_factory) -> None:
    from pathlib import Path

    from umd.providers.base import EngineContext
    from umd.providers.yt_dlp_provider import YtDlpAdapter

    ctx = EngineContext(fake_runner_factory(lambda r, e: None), Settings())
    selection = DownloadSelection(item_urls=["https://www.youtube.com/@canal/videos"])
    request = YtDlpAdapter().build_download("https://www.youtube.com/@canal", selection, ctx, Path("C:/tmp/s"),
                                            is_collection=True)
    assert request["urls"] == ["https://www.youtube.com/@canal/videos"]
    assert "playlist_items" not in request["params"]


@pytest.mark.parametrize("raw, code", [
    ({"_type": "video", "id": "x", "live_status": "is_upcoming", "formats": [_fmt("1", "mp4", 720, "avc1", acodec="aac")]},
     ErrorCode.LIVE_NOT_STARTED),
    ({"_type": "video", "id": "x", "formats": [_fmt("1", "mp4", 720, "avc1", has_drm=True)]}, ErrorCode.DRM),
    ({"_type": "video", "id": "x", "formats": [_fmt("img", "jpg", 172, None, acodec=None)]}, ErrorCode.NO_MEDIA),
])
def test_parse_errors(raw: dict, code: ErrorCode) -> None:
    with pytest.raises(UMDError) as info:
        parse_info(raw, "https://example.com/x")
    assert info.value.code == code


def test_quality_choices_only_real_resolutions_with_sizes() -> None:
    info = parse_info(VIDEO_INFO, "https://youtu.be/abc")
    choices = quality_choices(info)
    assert [c.key for c in choices] == ["best", "2160", "1080", "720", "360", "original"]
    by_key = {c.key: c for c in choices}
    assert by_key["720"].size == 18_000_000 + 1_700_000  # video + melhor audio
    assert by_key["best"].size == 150_000_000 + 1_700_000


def test_estimate_audio_conversion() -> None:
    info = parse_info(VIDEO_INFO, "https://youtu.be/abc")
    selection = DownloadSelection(mode=SelectionMode.AUDIO, audio_format="mp3", audio_bitrate="320")
    assert estimate_size(info, selection) == 320 * 1000 // 8 * 100
    selection.quality = "format:137+140"
    selection.mode = SelectionMode.VIDEO
    assert estimate_size(info, selection) == 37_000_000 + 1_600_000


def test_nearest_height() -> None:
    assert nearest_height(1080, [2160, 1440, 720, 360]) == 720
    assert nearest_height(480, [1080, 720]) == 720  # nada abaixo: menor disponivel
    assert nearest_height(720, []) is None


@pytest.mark.parametrize("preset, mode, quality, container", [
    (QualityPreset.BEST, SelectionMode.VIDEO, "best", "mp4"),
    (QualityPreset.P1080, SelectionMode.VIDEO, "1080", "mp4"),
    (QualityPreset.P720, SelectionMode.VIDEO, "720", "mp4"),
    (QualityPreset.P480, SelectionMode.VIDEO, "360", "mp4"),
    (QualityPreset.AUDIO, SelectionMode.AUDIO, "best", "mp4"),
    (QualityPreset.ORIGINAL, SelectionMode.VIDEO, "original", "original"),
])
def test_presets(preset, mode, quality, container) -> None:
    info = parse_info(VIDEO_INFO, "https://youtu.be/abc")
    selection = selection_from_settings(Settings(), info, preset)
    assert (selection.mode, selection.quality, selection.container) == (mode, quality, container)


def test_format_params_best_mp4_avoids_recompression() -> None:
    params = build_format_params(DownloadSelection())
    assert params["format"] == "bv*+ba/b"
    assert params["merge_output_format"] == "mp4"
    assert "ext:mp4:m4a" in params["format_sort"]
    keys = [pp["key"] for pp in params["postprocessors"]]
    assert "FFmpegVideoRemuxer" in keys and "FFmpegVideoConvertor" not in keys


def test_format_params_resolution_and_codec() -> None:
    params = build_format_params(DownloadSelection(quality="720", video_codec="h264", container="mkv"))
    assert params["format_sort"][:1] == ["res:720"]
    assert "vcodec:h264" in params["format_sort"]
    assert params["merge_output_format"] == "mkv"


def test_format_params_original_has_no_conversion() -> None:
    params = build_format_params(DownloadSelection(quality="original", container="original", embed_metadata=False))
    assert "merge_output_format" not in params
    assert params["postprocessors"] == []


def test_format_params_audio() -> None:
    params = build_format_params(DownloadSelection(mode=SelectionMode.AUDIO, audio_format="mp3", audio_bitrate="256"))
    assert params["format"] == "ba/b"
    extract = params["postprocessors"][0]
    assert extract == {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "256"}
    original = build_format_params(DownloadSelection(mode=SelectionMode.AUDIO, audio_format="original"))
    assert original["postprocessors"][0]["preferredcodec"] == "best"


def test_format_params_specific_format() -> None:
    assert build_format_params(DownloadSelection(quality="format:137"))["format"] == "137+ba/137"
    assert build_format_params(DownloadSelection(quality="format:137+140"))["format"] == "137+140"


def test_live_skips_metadata_postprocessor() -> None:
    params = build_format_params(DownloadSelection(), is_live=True)
    assert "FFmpegMetadata" not in [pp["key"] for pp in params["postprocessors"]]


@pytest.mark.parametrize("items, spec", [
    ([1, 2, 3, 7, 9, 10], "1-3,7,9-10"), ([5], "5"), ([3, 1, 2, 2], "1-3"), (None, None), ([], None),
])
def test_items_spec(items, spec) -> None:
    assert items_spec(items) == spec
