"""Providers de cada plataforma cadastrada.

Cada provider so declara: dominios, qual engine tentar primeiro para cada
tipo de link e o tipo de conteudo sugerido pela URL. O suporte real e
decidido pelos extratores das engines instaladas (ver
EngineService.check_platform_support) -- Kwai e Threads, por exemplo,
aparecem aqui para serem RECONHECIDOS e rotulados, mas hoje nenhuma das
engines tem extrator dedicado para eles, e o app diz isso ao usuario.
"""

from __future__ import annotations

import re

from ..media.metadata import ContentType
from .base import DIRECT, GALLERYDL, YTDLP, MediaProvider, url_path, url_query


class YoutubeProvider(MediaProvider):
    key = "youtube"
    name = "YouTube"
    domains = ("youtube.com", "youtu.be", "youtube-nocookie.com")
    engines = (YTDLP,)
    sample_url = "https://www.youtube.com/watch?v=jNQXAC9IVRw"

    def classify(self, url: str) -> ContentType | None:
        path = url_path(url)
        query = url_query(url)
        if path.startswith("/shorts/"):
            return ContentType.SHORT
        if path.startswith("/clip/"):
            return ContentType.CLIP
        if path.startswith("/live/"):
            return ContentType.LIVE
        if path.startswith("/playlist") or ("list" in query and "v" not in query):
            return ContentType.PLAYLIST
        if path.startswith(("/@", "/channel/", "/c/", "/user/")):
            return ContentType.CHANNEL
        return None


class InstagramProvider(MediaProvider):
    key = "instagram"
    name = "Instagram"
    domains = ("instagram.com", "instagr.am")
    engines = (GALLERYDL, YTDLP)
    sample_url = "https://www.instagram.com/p/C0000000000/"

    def engine_order(self, url: str) -> list[str]:
        path = url_path(url)
        if path.startswith(("/reel/", "/reels/", "/tv/")):
            return [YTDLP, GALLERYDL]
        # posts/carrosseis podem misturar fotos e videos: gallery-dl pega os dois
        return [GALLERYDL, YTDLP]

    def classify(self, url: str) -> ContentType | None:
        path = url_path(url)
        if path.startswith(("/reel/", "/reels/")):
            return ContentType.REEL
        if path.startswith(("/p/", "/tv/", "/stories/")):
            return ContentType.POST
        if re.match(r"^/[\w.]+/?$", path):
            return ContentType.PROFILE
        return None


class TikTokProvider(MediaProvider):
    key = "tiktok"
    name = "TikTok"
    domains = ("tiktok.com",)
    engines = (YTDLP, GALLERYDL)
    sample_url = "https://www.tiktok.com/@user/video/7000000000000000000"

    def engine_order(self, url: str) -> list[str]:
        if "/photo/" in url_path(url):
            return [GALLERYDL, YTDLP]  # posts de fotos: o yt-dlp nao suporta
        return [YTDLP, GALLERYDL]

    def classify(self, url: str) -> ContentType | None:
        path = url_path(url)
        if "/photo/" in path:
            return ContentType.GALLERY
        if re.match(r"^/@[\w.-]+/?$", path):
            return ContentType.PROFILE
        return None


class FacebookProvider(MediaProvider):
    key = "facebook"
    name = "Facebook"
    domains = ("facebook.com", "fb.watch", "fb.com")
    engines = (YTDLP, GALLERYDL)
    sample_url = "https://www.facebook.com/watch/?v=1000000000000000"

    def engine_order(self, url: str) -> list[str]:
        path = url_path(url)
        if "/photo" in path or "/photos/" in path:
            return [GALLERYDL, YTDLP]
        return [YTDLP, GALLERYDL]

    def classify(self, url: str) -> ContentType | None:
        path = url_path(url)
        if path.startswith("/reel/"):
            return ContentType.REEL
        if "/photo" in path:
            return ContentType.IMAGE
        return None


class XProvider(MediaProvider):
    key = "x"
    name = "X (Twitter)"
    domains = ("x.com", "twitter.com", "fxtwitter.com", "vxtwitter.com")
    engines = (YTDLP, GALLERYDL)
    sample_url = "https://x.com/user/status/1000000000000000000"

    def engine_order(self, url: str) -> list[str]:
        if "/status/" in url_path(url):
            return [YTDLP, GALLERYDL]  # video pelo yt-dlp; so imagens -> gallery-dl
        return [GALLERYDL, YTDLP]

    def classify(self, url: str) -> ContentType | None:
        path = url_path(url)
        if "/status/" in path:
            return ContentType.POST
        if re.match(r"^/\w+/?(media)?/?$", path):
            return ContentType.PROFILE
        return None


class RedditProvider(MediaProvider):
    key = "reddit"
    name = "Reddit"
    domains = ("reddit.com", "redd.it")
    engines = (YTDLP, GALLERYDL)
    sample_url = "https://www.reddit.com/r/pics/comments/abc123/title/"

    def engine_order(self, url: str) -> list[str]:
        path = url_path(url)
        if "/comments/" in path or "/s/" in path:
            return [YTDLP, GALLERYDL]  # video (DASH com audio separado) pelo yt-dlp; galerias pelo gallery-dl
        return [GALLERYDL, YTDLP]

    def classify(self, url: str) -> ContentType | None:
        path = url_path(url)
        if "/comments/" in path:
            return ContentType.POST
        if path.startswith(("/user/", "/u/")):
            return ContentType.PROFILE
        if path.startswith("/r/"):
            return ContentType.COLLECTION
        return None


class TwitchProvider(MediaProvider):
    key = "twitch"
    name = "Twitch"
    domains = ("twitch.tv",)
    engines = (YTDLP,)
    sample_url = "https://www.twitch.tv/videos/1000000000"

    def classify(self, url: str) -> ContentType | None:
        path = url_path(url)
        if "clips.twitch.tv" in url or "/clip/" in path:
            return ContentType.CLIP
        return None


class PinterestProvider(MediaProvider):
    key = "pinterest"
    name = "Pinterest"
    domains = ("pinterest.com", "pin.it", "pinterest.com.br", "pinterest.pt", "pinterest.co.uk", "pinterest.fr",
               "pinterest.de", "pinterest.es", "pinterest.it", "pinterest.ca", "pinterest.com.mx", "pinterest.jp")
    engines = (GALLERYDL, YTDLP)
    sample_url = "https://www.pinterest.com/pin/1000000000000000/"

    def classify(self, url: str) -> ContentType | None:
        path = url_path(url)
        if path.startswith("/pin/"):
            return ContentType.POST
        if re.match(r"^/[\w.-]+/[\w.-]+/?$", path):
            return ContentType.COLLECTION
        return None


class VimeoProvider(MediaProvider):
    key = "vimeo"
    name = "Vimeo"
    domains = ("vimeo.com",)
    engines = (YTDLP,)
    sample_url = "https://vimeo.com/76979871"


class SoundCloudProvider(MediaProvider):
    key = "soundcloud"
    name = "SoundCloud"
    domains = ("soundcloud.com", "snd.sc")
    engines = (YTDLP,)
    sample_url = "https://soundcloud.com/artist/track"

    def classify(self, url: str) -> ContentType | None:
        return ContentType.PLAYLIST if "/sets/" in url_path(url) else None


class DailymotionProvider(MediaProvider):
    key = "dailymotion"
    name = "Dailymotion"
    domains = ("dailymotion.com", "dai.ly")
    engines = (YTDLP,)
    sample_url = "https://www.dailymotion.com/video/x7tgad0"


class KwaiProvider(MediaProvider):
    key = "kwai"
    name = "Kwai"
    domains = ("kwai.com", "kwai.net", "kw.ai", "kwai-video.com")
    engines = (YTDLP, GALLERYDL)
    sample_url = "https://www.kwai.com/@user/video/5200000000000000000"


class ThreadsProvider(MediaProvider):
    key = "threads"
    name = "Threads"
    domains = ("threads.net", "threads.com")
    engines = (YTDLP, GALLERYDL)
    sample_url = "https://www.threads.net/@user/post/C0000000000"


class BlueskyProvider(MediaProvider):
    key = "bluesky"
    name = "Bluesky"
    domains = ("bsky.app",)
    engines = (GALLERYDL, YTDLP)
    sample_url = "https://bsky.app/profile/user.bsky.social/post/3k0000000000a"

    def classify(self, url: str) -> ContentType | None:
        path = url_path(url)
        if "/post/" in path:
            return ContentType.POST
        if re.match(r"^/profile/[^/]+/?$", path):
            return ContentType.PROFILE
        return None


class TumblrProvider(MediaProvider):
    key = "tumblr"
    name = "Tumblr"
    domains = ("tumblr.com",)
    engines = (GALLERYDL, YTDLP)
    sample_url = "https://www.tumblr.com/staff/100000000000/post"

    def classify(self, url: str) -> ContentType | None:
        path = url_path(url)
        if "/post/" in path or re.search(r"/\d{6,}", path):
            return ContentType.POST
        return ContentType.PROFILE if path in ("", "/") or re.match(r"^/[\w-]+/?$", path) else None


class ImgurProvider(MediaProvider):
    key = "imgur"
    name = "Imgur"
    domains = ("imgur.com",)
    engines = (GALLERYDL, YTDLP, DIRECT)
    sample_url = "https://imgur.com/gallery/abcdefg"


PLATFORM_PROVIDERS: tuple[type[MediaProvider], ...] = (
    YoutubeProvider,
    InstagramProvider,
    TikTokProvider,
    FacebookProvider,
    XProvider,
    RedditProvider,
    TwitchProvider,
    PinterestProvider,
    VimeoProvider,
    SoundCloudProvider,
    DailymotionProvider,
    KwaiProvider,
    ThreadsProvider,
    BlueskyProvider,
    TumblrProvider,
    ImgurProvider,
)
