"""Download HTTP direto de um arquivo de midia (ex.: https://site/video.mp4).

So aceita conteudo de midia (video/audio/imagem). Paginas HTML,
executaveis e afins sao recusados -- o app nao e um baixador generico
de arquivos e nunca deve trazer um .exe para o disco do usuario.
"""

from __future__ import annotations

import re
import time
from email.message import Message
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin, urlsplit

from ...core.exceptions import ErrorCode, UMDError
from ...core.naming import sanitize_component, sanitize_extension
from ...core.security import is_dangerous_file, validate_url
from ...media.metadata import AUDIO_EXTS, IMAGE_EXTS, VIDEO_EXTS, kind_from_ext
from ..protocol import Emitter

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/140.0 Safari/537.36"
)
_MEDIA_PREFIXES = ("video/", "audio/", "image/")
# playlists HLS/DASH (m3u8/mpd) NAO entram aqui: sao manifestos, nao a midia -- vao para o yt-dlp
_MEDIA_EXTRA = {"application/ogg", "application/mp4"}
_MIME_EXT = {
    "video/mp4": "mp4", "video/webm": "webm", "video/quicktime": "mov", "video/x-matroska": "mkv",
    "video/x-msvideo": "avi", "video/mp2t": "ts", "audio/mpeg": "mp3", "audio/mp4": "m4a", "audio/x-m4a": "m4a",
    "audio/aac": "aac", "audio/ogg": "ogg", "audio/opus": "opus", "audio/wav": "wav", "audio/x-wav": "wav",
    "audio/flac": "flac", "audio/x-flac": "flac", "audio/webm": "weba", "image/jpeg": "jpg", "image/png": "png",
    "image/gif": "gif", "image/webp": "webp", "image/avif": "avif", "image/bmp": "bmp", "image/tiff": "tif",
    "image/heic": "heic", "application/ogg": "ogg",
}
CHUNK = 256 * 1024


def _session():
    import requests

    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT, "Accept": "*/*"})
    session.max_redirects = 10
    return session


_REDIRECT_CODES = {301, 302, 303, 307, 308}


def _get(session: Any, url: str, *, timeout: float, allow_local: bool, headers: dict | None = None) -> Any:
    """GET seguindo redirecionamentos manualmente: CADA salto e validado
    (um redirecionamento nao pode levar o app a um endereco da rede local)."""
    import requests

    current = url
    for _ in range(10):
        validate_url(current, allow_local=allow_local)
        try:
            response = session.get(current, stream=True, timeout=(15, timeout), allow_redirects=False, headers=headers)
        except requests.RequestException as exc:
            raise _map_request_error(exc) from None
        location = response.headers.get("Location")
        if response.status_code in _REDIRECT_CODES and location:
            response.close()
            current = urljoin(current, location)
            continue
        return response
    raise UMDError(ErrorCode.NETWORK, details=f"Too many redirects starting at {url}")


def _map_request_error(exc: BaseException) -> UMDError:
    import requests

    details = f"{type(exc).__name__}: {exc}"
    if isinstance(exc, requests.Timeout):
        return UMDError(ErrorCode.TIMEOUT, details=details)
    if isinstance(exc, (requests.ConnectionError, requests.TooManyRedirects)):
        return UMDError(ErrorCode.NETWORK, details=details)
    return UMDError(ErrorCode.UNKNOWN, details=details)


def _status_error(status: int, url: str) -> UMDError:
    code = {401: ErrorCode.AUTH_REQUIRED, 403: ErrorCode.ACCESS_DENIED, 404: ErrorCode.NOT_FOUND,
            410: ErrorCode.NOT_FOUND, 429: ErrorCode.RATE_LIMITED}.get(status)
    if code is None:
        code = ErrorCode.NETWORK if status >= 500 else ErrorCode.UNKNOWN
    return UMDError(code, details=f"HTTP {status} for {url}")


def _filename_from(response: Any, url: str, mime: str) -> tuple[str, str]:
    name = ""
    disposition = response.headers.get("Content-Disposition", "")
    if disposition:
        msg = Message()
        msg["content-disposition"] = disposition
        name = msg.get_filename() or ""
    if not name:
        name = unquote(Path(urlsplit(response.url or url).path).name)
    stem, dot, ext = name.rpartition(".")
    if not dot:
        stem, ext = name, ""
    ext = ext.lower()
    if ext not in VIDEO_EXTS | AUDIO_EXTS | IMAGE_EXTS:
        ext = _MIME_EXT.get(mime, ext)
    return sanitize_component(stem or "arquivo", max_len=100), sanitize_extension(ext)


def _is_media(mime: str, ext: str) -> bool:
    if mime.startswith(_MEDIA_PREFIXES) or mime in _MEDIA_EXTRA:
        return True
    # alguns servidores mandam octet-stream para midia; decide pela extensao
    return mime in ("application/octet-stream", "binary/octet-stream", "") and ext in VIDEO_EXTS | AUDIO_EXTS | IMAGE_EXTS


def probe(url: str, *, timeout: float, allow_local: bool) -> dict[str, Any]:
    session = _session()
    response = _get(session, url, timeout=timeout, allow_local=allow_local)
    try:
        if response.status_code >= 400:
            raise _status_error(response.status_code, response.url)
        mime = response.headers.get("Content-Type", "").split(";")[0].strip().lower()
        stem, ext = _filename_from(response, url, mime)
        size = response.headers.get("Content-Length")
        return {
            "final_url": response.url,
            "mime": mime,
            "size": int(size) if size and size.isdigit() else None,
            "stem": stem,
            "ext": ext,
            "accept_ranges": response.headers.get("Accept-Ranges", "").lower() == "bytes",
            "is_media": _is_media(mime, ext) and not is_dangerous_file(f"x.{ext}"),
            "kind": kind_from_ext(ext).value,
        }
    finally:
        response.close()


def analyze(req: dict[str, Any], emitter: Emitter) -> None:
    info = probe(req["url"], timeout=req.get("timeout", 30), allow_local=bool(req.get("allow_local")))
    if not info["is_media"]:
        raise UMDError(ErrorCode.NO_MEDIA, details=f"Content-Type {info['mime']!r} is not a media file")
    emitter.result(info)


def download(req: dict[str, Any], emitter: Emitter) -> None:
    import requests

    url = req["url"]
    staging = Path(req["staging_dir"])
    parts = staging / "_parts"
    parts.mkdir(parents=True, exist_ok=True)
    timeout = float(req.get("timeout", 30))
    max_size = int(req.get("max_size") or 0)
    rate = int(req.get("rate_limit") or 0)  # bytes/s
    allow_local = bool(req.get("allow_local"))

    session = _session()
    info = probe(url, timeout=timeout, allow_local=allow_local)
    if not info["is_media"]:
        raise UMDError(ErrorCode.NO_MEDIA, details=f"Content-Type {info['mime']!r} is not a media file")
    if max_size and info["size"] and info["size"] > max_size:
        raise UMDError(ErrorCode.FILE_TOO_LARGE, details=f"{info['size']} > {max_size}")

    safe_stem = re.sub(r"[^\w.-]+", "_", info["stem"])[:80] or "arquivo"
    part_path = parts / f"{safe_stem}.{info['ext']}.part"
    final_path = staging / f"{safe_stem}.{info['ext']}"
    emitter.item({"index": 1, "count": 1, "title": info["stem"], "expected_size": info["size"]})

    start = part_path.stat().st_size if (req.get("resume", True) and part_path.exists()) else 0
    headers = {"Range": f"bytes={start}-"} if start and info["accept_ranges"] else {}
    response = _get(session, info["final_url"], timeout=timeout, allow_local=allow_local, headers=headers)

    with response:
        if response.status_code >= 400:
            raise _status_error(response.status_code, response.url)
        if response.status_code != 206:
            start = 0  # servidor ignorou o Range: recomeca do zero
        length = response.headers.get("Content-Length")
        total = start + int(length) if length and length.isdigit() else info["size"]
        mode = "ab" if start else "wb"
        downloaded = start
        began = time.monotonic()
        received_now = 0
        try:
            with open(part_path, mode) as fh:
                for chunk in response.iter_content(CHUNK):
                    if not chunk:
                        continue
                    fh.write(chunk)
                    downloaded += len(chunk)
                    received_now += len(chunk)
                    if max_size and downloaded > max_size:
                        raise UMDError(ErrorCode.FILE_TOO_LARGE, details=f"{downloaded} > {max_size}")
                    elapsed = max(time.monotonic() - began, 1e-6)
                    if rate:
                        expected = received_now / rate
                        if expected > elapsed:
                            time.sleep(expected - elapsed)
                            elapsed = expected
                    speed = received_now / elapsed
                    eta = (total - downloaded) / speed if total and speed else None
                    emitter.progress(status="downloading", downloaded=downloaded, total=total, speed=speed, eta=eta,
                                     item=1, items=1)
        except requests.RequestException as exc:
            raise _map_request_error(exc) from None

    if total and downloaded < total:
        raise UMDError(ErrorCode.NETWORK, details=f"Incomplete download: {downloaded} of {total} bytes")
    part_path.replace(final_path)
    emitter.file(str(final_path), {"title": info["stem"], "ext": info["ext"], "filesize": downloaded,
                                    "webpage_url": url, "mime": info["mime"]})
