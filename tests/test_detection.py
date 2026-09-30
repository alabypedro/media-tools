"""Deteccao de plataforma e ordem de engines (sem rede)."""

from __future__ import annotations

import pytest

from umd.media.metadata import ContentType
from umd.providers.base import DIRECT, GALLERYDL, YTDLP
from umd.providers.registry import all_providers, detect_provider


@pytest.mark.parametrize(
    "url, key",
    [
        ("https://www.youtube.com/watch?v=jNQXAC9IVRw", "youtube"),
        ("https://youtu.be/jNQXAC9IVRw", "youtube"),
        ("https://m.youtube.com/shorts/abcdefghijk", "youtube"),
        ("https://www.instagram.com/p/C1234567890/", "instagram"),
        ("https://www.instagram.com/reel/C1234567890/", "instagram"),
        ("https://www.tiktok.com/@user/video/7234567890123456789", "tiktok"),
        ("https://vm.tiktok.com/ZMabcdef/", "tiktok"),
        ("https://www.facebook.com/watch/?v=123", "facebook"),
        ("https://fb.watch/abc/", "facebook"),
        ("https://x.com/user/status/1", "x"),
        ("https://twitter.com/user/status/1", "x"),
        ("https://www.reddit.com/r/pics/comments/abc/t/", "reddit"),
        ("https://www.twitch.tv/videos/123", "twitch"),
        ("https://clips.twitch.tv/Slug", "twitch"),
        ("https://br.pinterest.com/pin/123/", "pinterest"),
        ("https://www.pinterest.com.br/pin/123/", "pinterest"),
        ("https://vimeo.com/76979871", "vimeo"),
        ("https://soundcloud.com/artist/track", "soundcloud"),
        ("https://www.dailymotion.com/video/x7tgad0", "dailymotion"),
        ("https://www.kwai.com/@user/video/1", "kwai"),
        ("https://www.threads.net/@user/post/C1", "threads"),
        ("https://www.threads.com/@user/post/C1", "threads"),
        ("https://bsky.app/profile/a.bsky.social/post/3k", "bluesky"),
        ("https://staff.tumblr.com/post/123", "tumblr"),
        ("https://imgur.com/gallery/abc", "imgur"),
        ("https://example.com/some/page", "generic"),
        ("https://notyoutube.com/watch?v=abc", "generic"),  # dominio parecido nao engana
    ],
)
def test_detect_provider(url: str, key: str) -> None:
    assert detect_provider(url).key == key


def test_every_platform_has_domains_and_sample() -> None:
    for provider in all_providers():
        assert provider.domains, provider.key
        assert provider.sample_url.startswith("https://"), provider.key
        assert detect_provider(provider.sample_url).key == provider.key


@pytest.mark.parametrize(
    "url, first",
    [
        ("https://www.instagram.com/reel/C1/", YTDLP),
        ("https://www.instagram.com/p/C1/", GALLERYDL),
        ("https://www.tiktok.com/@u/photo/1", GALLERYDL),
        ("https://www.tiktok.com/@u/video/1", YTDLP),
        ("https://x.com/u/status/1", YTDLP),
        ("https://www.reddit.com/r/pics/", GALLERYDL),
        ("https://www.pinterest.com/pin/1/", GALLERYDL),
        ("https://example.com/video.mp4", DIRECT),
        ("https://example.com/foto.JPG", DIRECT),
        ("https://example.com/live/index.m3u8", YTDLP),
        ("https://example.com/pagina", YTDLP),
    ],
)
def test_engine_order(url: str, first: str) -> None:
    assert detect_provider(url).engine_order(url)[0] == first


@pytest.mark.parametrize(
    "url, content_type",
    [
        ("https://www.youtube.com/shorts/abc", ContentType.SHORT),
        ("https://www.youtube.com/playlist?list=PL1", ContentType.PLAYLIST),
        ("https://www.youtube.com/@canal", ContentType.CHANNEL),
        ("https://www.instagram.com/reel/C1/", ContentType.REEL),
        ("https://www.instagram.com/nasa/", ContentType.PROFILE),
        ("https://x.com/user/status/1", ContentType.POST),
        ("https://www.reddit.com/r/pics/", ContentType.COLLECTION),
        ("https://clips.twitch.tv/Slug", ContentType.CLIP),
        ("https://soundcloud.com/a/sets/b", ContentType.PLAYLIST),
        ("https://www.youtube.com/watch?v=abc", None),
    ],
)
def test_classify(url: str, content_type: ContentType | None) -> None:
    assert detect_provider(url).classify(url) == content_type
