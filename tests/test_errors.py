from __future__ import annotations

import pytest

from umd.core.exceptions import ErrorCode, UMDError, classify_error_text, to_user_error


@pytest.mark.parametrize(
    "text, code",
    [
        ("ERROR: [youtube] abc: Video unavailable", ErrorCode.NOT_FOUND),
        ("ERROR: [youtube] abc: This video is unavailable", ErrorCode.NOT_FOUND),
        ("ERROR: [youtube] abc: Private video. Sign in if you've been granted access", ErrorCode.PRIVATE),
        ("ERROR: [youtube] abc: Sign in to confirm you're not a bot", ErrorCode.BOT_CHECK),
        ("ERROR: [youtube] abc: Sign in to confirm your age", ErrorCode.AGE_RESTRICTED),
        ("ERROR: [youtube] abc: Join this channel to get access to members-only content", ErrorCode.PAYWALL),
        ("ERROR: This video is DRM protected", ErrorCode.DRM),
        ("ERROR: [instagram] xyz: Requested content is not available, rate-limit reached or login required",
         ErrorCode.AUTH_REQUIRED),
        ("[instagram][error] AuthRequired: 'cookies' needed", ErrorCode.AUTH_REQUIRED),
        ("ERROR: The uploader has not made this video available in your country", ErrorCode.GEO_BLOCKED),
        ("ERROR: [youtube] abc: This live event will begin in 3 hours", ErrorCode.LIVE_NOT_STARTED),
        ("ERROR: Unable to download webpage: HTTP Error 404: Not Found", ErrorCode.NOT_FOUND),
        ("ERROR: Unable to download webpage: HTTP Error 403: Forbidden", ErrorCode.ACCESS_DENIED),
        ("ERROR: HTTP Error 429: Too Many Requests", ErrorCode.RATE_LIMITED),
        ("ERROR: Unsupported URL: https://example.com/", ErrorCode.UNSUPPORTED),
        ("ERROR: [twitter] 1: No video could be found in this tweet", ErrorCode.NO_MEDIA),
        ("ERROR: Requested format is not available", ErrorCode.FORMAT_UNAVAILABLE),
        ("ERROR: ffprobe and ffmpeg not found. Please install", ErrorCode.FFMPEG_MISSING),
        ("ERROR: Could not copy Chrome cookie database", ErrorCode.COOKIES_ERROR),
        ("ERROR: Failed to decrypt with DPAPI", ErrorCode.COOKIES_ERROR),
        ("OSError: [Errno 28] No space left on device", ErrorCode.DISK_FULL),
        ("PermissionError: [WinError 5] Access is denied", ErrorCode.PERMISSION),
        ("ERROR: Unable to download webpage: <urlopen error [Errno 11001] getaddrinfo failed>", ErrorCode.NETWORK),
        ("ERROR: Read timed out.", ErrorCode.TIMEOUT),
        ("something completely different", ErrorCode.UNKNOWN),
        ("", ErrorCode.UNKNOWN),
    ],
)
def test_classify(text: str, code: ErrorCode) -> None:
    assert classify_error_text(text) == code


def test_user_message_is_friendly_and_has_reasons() -> None:
    error = UMDError(ErrorCode.NOT_FOUND, details="Traceback (most recent call last): ...")
    message = error.user_message()
    assert message.startswith("Não foi possível acessar este conteúdo.")
    assert "Possíveis motivos:" in message and "• O conteúdo foi removido." in message
    assert "Traceback" not in message  # detalhe tecnico so em "Ver detalhes tecnicos"


def test_retry_and_fallthrough_flags() -> None:
    assert UMDError(ErrorCode.NETWORK).retryable
    assert UMDError(ErrorCode.RATE_LIMITED).retryable
    assert not UMDError(ErrorCode.PRIVATE).retryable
    assert not UMDError(ErrorCode.DRM).retryable
    assert UMDError(ErrorCode.UNSUPPORTED).fallthrough
    assert UMDError(ErrorCode.NO_MEDIA).fallthrough
    assert not UMDError(ErrorCode.AUTH_REQUIRED).fallthrough


def test_roundtrip_dict() -> None:
    original = UMDError(ErrorCode.GEO_BLOCKED, details="geo")
    restored = UMDError.from_dict(original.to_dict())
    assert restored.code == ErrorCode.GEO_BLOCKED and restored.details == "geo"
    assert UMDError.from_dict({"code": "nao-existe"}).code == ErrorCode.UNKNOWN


def test_to_user_error_maps_python_exceptions() -> None:
    assert to_user_error(PermissionError("x")).code == ErrorCode.PERMISSION
    disk = OSError(28, "No space left on device")
    assert to_user_error(disk).code == ErrorCode.DISK_FULL
    assert to_user_error(RuntimeError("HTTP Error 429")).code == ErrorCode.RATE_LIMITED
    same = UMDError(ErrorCode.DRM)
    assert to_user_error(same) is same
