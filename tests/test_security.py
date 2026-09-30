from __future__ import annotations

import pytest

from umd.core.exceptions import ErrorCode, UMDError
from umd.core.security import extract_urls, is_dangerous_file, normalize_url, validate_url


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("https://www.youtube.com/watch?v=abc", "https://www.youtube.com/watch?v=abc"),
        ("  https://x.com/a/status/1  ", "https://x.com/a/status/1"),
        ('"https://vimeo.com/123"', "https://vimeo.com/123"),
        ("<https://vimeo.com/123>", "https://vimeo.com/123"),
        ("youtube.com/watch?v=abc", "https://youtube.com/watch?v=abc"),
        ("HTTPS://WWW.Example.COM/Path", "https://www.example.com/Path"),
        ("https://youtu.be/abc\u200b", "https://youtu.be/abc"),
    ],
)
def test_validate_accepts_and_normalizes(raw: str, expected: str) -> None:
    assert validate_url(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "ftp://example.com/file.mp4",
        "file:///C:/Windows/system32/calc.exe",
        "javascript:alert(1)",
        "data:text/html,<b>x</b>",
        "https://user:pass@example.com/video",
        "https://exa mple.com/video",
        "https://example.com/vi\ndeo",
        "https://",
        "https://semponto/video",
        "https://example.com:99999/x",
        "https://" + "a" * 5000 + ".com",
    ],
)
def test_validate_rejects(raw: str) -> None:
    with pytest.raises(UMDError) as info:
        validate_url(raw)
    assert info.value.code == ErrorCode.INVALID_URL
    assert info.value.message  # sempre uma mensagem amigavel


@pytest.mark.parametrize(
    "raw",
    ["http://localhost:8000/v.mp4", "http://127.0.0.1/v.mp4", "http://192.168.0.10/a.jpg", "http://10.0.0.5/x",
     "http://[::1]/x", "http://169.254.1.1/x", "http://nas.local/x"],
)
def test_local_network_blocked_by_default(raw: str) -> None:
    with pytest.raises(UMDError):
        validate_url(raw)
    assert validate_url(raw, allow_local=True).startswith("http://")


def test_normalize_only_adds_scheme_to_domain_like_text() -> None:
    assert normalize_url("isto não é uma url") == "isto não é uma url"
    assert normalize_url("tiktok.com/@user/video/1") == "https://tiktok.com/@user/video/1"


def test_extract_urls_from_free_text() -> None:
    text = """
    Olha esse: https://www.youtube.com/watch?v=abc, e também (https://vimeo.com/123).
    Repetido: https://www.youtube.com/watch?v=abc
    Wikipedia: https://en.wikipedia.org/wiki/Foo_(bar)
    """
    assert extract_urls(text) == [
        "https://www.youtube.com/watch?v=abc",
        "https://vimeo.com/123",
        "https://en.wikipedia.org/wiki/Foo_(bar)",
    ]


class _FakeResponse:
    def __init__(self, status: int, location: str | None = None):
        self.status_code = status
        self.headers = {"Location": location} if location else {}

    def close(self) -> None:
        pass


class _FakeSession:
    def __init__(self, responses: list[_FakeResponse]):
        self.responses = responses
        self.calls: list[str] = []

    def get(self, url: str, **_kwargs):
        self.calls.append(url)
        return self.responses[len(self.calls) - 1] if len(self.calls) <= len(self.responses) else self.responses[-1]


def test_redirect_to_local_network_is_blocked_before_request() -> None:
    from umd.engine.backends.direct import _get

    session = _FakeSession([_FakeResponse(302, "http://127.0.0.1:8080/admin"), _FakeResponse(200)])
    with pytest.raises(UMDError) as info:
        _get(session, "https://example.com/video.mp4", timeout=5, allow_local=False)
    assert info.value.code == ErrorCode.INVALID_URL
    assert session.calls == ["https://example.com/video.mp4"]  # o endereco local nunca foi acessado


def test_redirects_are_followed_with_relative_locations() -> None:
    from umd.engine.backends.direct import _get

    session = _FakeSession([_FakeResponse(301, "/cdn/video.mp4"), _FakeResponse(200)])
    response = _get(session, "https://example.com/v", timeout=5, allow_local=False)
    assert response.status_code == 200
    assert session.calls == ["https://example.com/v", "https://example.com/cdn/video.mp4"]


def test_redirect_loop_is_stopped() -> None:
    from umd.engine.backends.direct import _get

    session = _FakeSession([_FakeResponse(302, "https://example.com/loop")])
    with pytest.raises(UMDError):
        _get(session, "https://example.com/loop", timeout=5, allow_local=False)
    assert len(session.calls) == 10


@pytest.mark.parametrize("name, dangerous", [
    ("video.mp4", False), ("foto.JPG", False), ("musica.mp3", False), ("post.txt", False),
    ("setup.exe", True), ("script.PS1", True), ("atalho.lnk", True), ("x.bat", True), ("y.scr", True), ("z.js", True),
])
def test_dangerous_files(name: str, dangerous: bool) -> None:
    assert is_dangerous_file(name) is dangerous
