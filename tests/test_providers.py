"""Providers com runner falso: fallback entre engines, gallery-dl e parametros."""

from __future__ import annotations

import pytest

from umd.core.config import Settings
from umd.core.exceptions import ErrorCode, UMDError
from umd.media.formats import DownloadSelection
from umd.media.metadata import ContentType, MediaKind
from umd.providers.base import GALLERYDL, YTDLP, EngineContext
from umd.providers.gallery_dl_provider import (
    GalleryDlAdapter,
    extract_author,
    extract_date,
    extract_text,
    parse_result,
    title_from,
)
from umd.providers.registry import detect_provider
from umd.providers.yt_dlp_provider import YtDlpAdapter

YT_VIDEO = {"_type": "video", "id": "v", "title": "Vídeo", "extractor_key": "Instagram",
            "formats": [{"format_id": "1", "ext": "mp4", "height": 720, "width": 720, "vcodec": "avc1", "acodec": "aac"}]}
GDL_CAROUSEL = {
    "category": "instagram", "subcategory": "post", "extractor": "InstagramPostExtractor",
    "posts": [{"description": "Praia linda 🌊\nsegunda linha", "username": "fulano", "date": "2026-01-02 10:00:00",
               "shortcode": "C1"}],
    "files": [
        {"url": "https://cdn/1.jpg", "meta": {"extension": "jpg", "width": 1080, "height": 1350, "num": 1, "filename": "1"}},
        {"url": "https://cdn/2.mp4", "meta": {"extension": "mp4", "width": 720, "height": 1280, "num": 2, "filename": "2"}},
        {"url": "https://cdn/3.jpg", "meta": {"extension": "jpg", "num": 3, "filename": "3"}},
    ],
}


def _ctx(handler, fake_runner_factory, settings: Settings | None = None) -> tuple[EngineContext, object]:
    runner = fake_runner_factory(handler)
    return EngineContext(runner, settings or Settings()), runner


def test_instagram_post_uses_gallery_dl_first(fake_runner_factory) -> None:
    def handler(request, _on_event):
        assert request["engine"] == GALLERYDL
        return GDL_CAROUSEL

    ctx, runner = _ctx(handler, fake_runner_factory)
    url = "https://www.instagram.com/p/C1/"
    info = detect_provider(url).analyze(url, ctx)
    assert info.engine == GALLERYDL and info.platform_name == "Instagram"
    assert info.content_type == ContentType.POST
    assert [e.kind for e in info.entries] == [MediaKind.IMAGE, MediaKind.VIDEO, MediaKind.IMAGE]
    assert info.author == "fulano" and info.upload_date == "20260102"
    assert info.title == "Praia linda 🌊" and "segunda linha" in (info.description or "")
    assert len(runner.requests) == 1


def test_fallthrough_to_next_engine_when_unsupported(fake_runner_factory) -> None:
    calls = []

    def handler(request, _on_event):
        calls.append(request["engine"])
        if request["engine"] == YTDLP:
            raise UMDError(ErrorCode.NO_MEDIA, details="There is no video in this post")
        return GDL_CAROUSEL

    ctx, _ = _ctx(handler, fake_runner_factory)
    url = "https://x.com/user/status/1"  # X: yt-dlp primeiro, gallery-dl para imagens
    info = detect_provider(url).analyze(url, ctx)
    assert calls == [YTDLP, GALLERYDL] and info.engine == GALLERYDL


def test_real_errors_do_not_fall_through(fake_runner_factory) -> None:
    calls = []

    def handler(request, _on_event):
        calls.append(request["engine"])
        raise UMDError(ErrorCode.PRIVATE, details="Private video")

    ctx, _ = _ctx(handler, fake_runner_factory)
    url = "https://www.tiktok.com/@u/video/1"
    with pytest.raises(UMDError) as info:
        detect_provider(url).analyze(url, ctx)
    assert info.value.code == ErrorCode.PRIVATE and calls == [YTDLP]


def test_nothing_recognizes_the_url(fake_runner_factory) -> None:
    def handler(request, _on_event):
        raise UMDError(ErrorCode.UNSUPPORTED, details=f"{request['engine']}: unsupported")

    ctx, _ = _ctx(handler, fake_runner_factory)
    url = "https://www.threads.net/@a/post/C1"
    with pytest.raises(UMDError) as info:
        detect_provider(url).analyze(url, ctx)
    assert info.value.code == ErrorCode.UNSUPPORTED
    assert info.value.message == "Esta plataforma/conteúdo não é suportado atualmente."


def test_generic_extractor_result_is_flagged(fake_runner_factory) -> None:
    def handler(request, _on_event):
        return {**YT_VIDEO, "extractor_key": "Generic"}

    ctx, _ = _ctx(handler, fake_runner_factory)
    url = "https://www.kwai.com/@u/video/1"
    info = detect_provider(url).analyze(url, ctx)
    assert info.platform_name == "Kwai"
    assert info.support_note and "genérico" in info.support_note


def test_dedicated_gallery_extractor_beats_generic(fake_runner_factory) -> None:
    def handler(request, _on_event):
        if request["engine"] == YTDLP:
            return {**YT_VIDEO, "extractor_key": "Generic"}
        return {**GDL_CAROUSEL, "category": "imgbox", "extractor": "ImgboxGalleryExtractor"}

    ctx, _ = _ctx(handler, fake_runner_factory)
    url = "https://example-gallery.com/g/123"
    info = detect_provider(url).analyze(url, ctx)
    assert info.engine == GALLERYDL and info.platform_name == "Imgbox"


def test_generic_site_with_dedicated_ytdlp_extractor_is_named(fake_runner_factory) -> None:
    ctx, _ = _ctx(lambda r, e: {**YT_VIDEO, "extractor_key": "BiliBili"}, fake_runner_factory)
    url = "https://www.bilibili.com/video/BV1"
    info = detect_provider(url).analyze(url, ctx)
    assert info.platform_name == "BiliBili" and info.support_note is None


def test_url_hint_refines_content_type(fake_runner_factory) -> None:
    ctx, _ = _ctx(lambda r, e: YT_VIDEO, fake_runner_factory)
    url = "https://www.instagram.com/reel/C1/"
    assert detect_provider(url).analyze(url, ctx).content_type == ContentType.REEL


def test_gallery_profile_queue() -> None:
    raw = {"category": "instagram", "subcategory": "user", "files": [], "posts": [],
           "queue": [{"url": f"https://www.instagram.com/p/C{i}/", "meta": {"description": f"Post {i}"}} for i in range(3)]}
    info = parse_result(raw, "https://www.instagram.com/fulano/")
    assert info.content_type == ContentType.PROFILE and info.entries_are_urls
    assert [e.url for e in info.entries][0] == "https://www.instagram.com/p/C0/"


def test_gallery_single_image_and_title_cleanup() -> None:
    raw = {"category": "wikimediacommons", "files": [{"url": "https://up/Example.jpg",
           "meta": {"extension": "jpg", "title": "File:Example.jpg", "filename": "Example"}}], "posts": []}
    info = parse_result(raw, "https://commons.wikimedia.org/wiki/File:Example.jpg")
    assert info.content_type == ContentType.IMAGE and info.title == "Example"
    assert info.platform_name == "Wikimedia Commons"


@pytest.mark.parametrize("data, author", [
    ({"author": {"name": "Nome", "nick": "nick"}}, "nick"),
    ({"user": {"username": "u1"}}, "u1"),
    ({"owner": "dono"}, "dono"),
    ({"blog_name": "meublog"}, "meublog"),
    ({}, None),
])
def test_extract_author(data, author) -> None:
    assert extract_author(data) == author


def test_extract_text_strips_html_and_date_formats() -> None:
    assert extract_text({"body": "<p>Olá<br>mundo</p>"}) == "Olá\nmundo"
    assert extract_date({"date": "2025-03-04 10:00:00"}) == "20250304"
    assert extract_date({"timestamp": 1700000000}) == "20231114"
    assert title_from({}, "Uma frase bem longa " * 10, "fallback").endswith("…")


def test_ytdlp_download_request(fake_runner_factory) -> None:
    settings = Settings(rate_limit_kbps=500, max_file_size_mb=100, concurrent_fragments=4, cookies_browser="firefox",
                        download_interval_seconds=2)
    ctx = EngineContext(fake_runner_factory(lambda r, e: None), settings, ffmpeg="C:/ffmpeg.exe")
    request = YtDlpAdapter().build_download("https://youtube.com/playlist?list=PL1", DownloadSelection(items=[1, 3]),
                                            ctx, __import__("pathlib").Path("C:/tmp/stage"), expected_items=2,
                                            is_collection=True)
    params = request["params"]
    assert params["ratelimit"] == 500 * 1024 and params["max_filesize"] == 100 * 1024 * 1024
    assert params["playlist_items"] == "1,3" and params["ignoreerrors"] == "only_download"
    assert params["cookiesfrombrowser"] == ["firefox", None]
    assert params["concurrent_fragment_downloads"] == 4 and params["sleep_interval"] == 2
    assert request["ffmpeg"] == "C:/ffmpeg.exe" and request["expected_items"] == 2


def test_single_video_url_with_list_param_downloads_only_the_video(fake_runner_factory) -> None:
    ctx = EngineContext(fake_runner_factory(lambda r, e: None), Settings())
    request = YtDlpAdapter().build_download("https://www.youtube.com/watch?v=a&list=PL1", DownloadSelection(), ctx,
                                            __import__("pathlib").Path("C:/tmp/s"))
    assert request["params"]["noplaylist"] is True


def test_gallery_download_request_selection(fake_runner_factory) -> None:
    from pathlib import Path

    ctx = EngineContext(fake_runner_factory(lambda r, e: None), Settings(rate_limit_kbps=200, cookies_file="C:/c.txt"))
    request = GalleryDlAdapter().build_download("https://www.instagram.com/p/C1/", DownloadSelection(items=[1, 3]), ctx,
                                                Path("C:/tmp/s"))
    config = {(tuple(p), k): v for p, k, v in request["config"]}
    assert config[(("extractor",), "image-range")] == "1,3"
    assert config[(("extractor",), "base-directory")] == str(Path("C:/tmp/s"))
    assert config[(("downloader",), "rate")] == "200k"
    assert config[(("extractor",), "cookies")] == str(Path("C:/c.txt"))  # caminho normalizado
    urls = GalleryDlAdapter().build_download("https://www.instagram.com/fulano/",
                                             DownloadSelection(item_urls=["https://a", "https://b"]), ctx, Path("C:/tmp/s"))
    assert urls["urls"] == ["https://a", "https://b"]
