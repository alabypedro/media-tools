"""Fixtures compartilhadas.

Nenhum teste toca nos dados reais do usuario: UMD_HOME aponta para uma
pasta temporaria, e os downloads "de verdade" usam um servidor HTTP local
(sem internet). Testes que acessam a internet sao marcados com
@pytest.mark.network e so rodam com `pytest --run-network`.
"""

from __future__ import annotations

import http.server
import os
import re
import sys
import threading
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption("--run-network", action="store_true", default=False, help="roda testes que acessam a internet")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--run-network"):
        return
    skip = pytest.mark.skip(reason="acessa a internet (use --run-network)")
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip)


@pytest.fixture(autouse=True)
def no_app_update_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """A verificacao de atualizacao do programa (ex.: ao abrir a janela) nunca vai a internet nos testes."""
    import urllib.error

    from umd.core import app_update

    def offline(*_args, **_kwargs):
        raise urllib.error.URLError("rede desligada nos testes")

    monkeypatch.setattr(app_update.urllib.request, "urlopen", offline)


@pytest.fixture(autouse=True)
def umd_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    home = tmp_path / "umd_home"
    monkeypatch.setenv("UMD_HOME", str(home))
    monkeypatch.setenv("UMD_ENABLE_TEST_ACTIONS", "1")
    return home


@pytest.fixture
def settings_store(tmp_path: Path):
    from umd.core.config import SettingsStore

    store = SettingsStore(tmp_path / "settings.json")
    store.update(download_dir=str(tmp_path / "downloads"), retry_delay_seconds=0)
    return store


@pytest.fixture
def db(tmp_path: Path):
    from umd.core.database import Database

    database = Database(tmp_path / "test.db")
    yield database
    database.close()


class FakeRunner:
    """Substitui o worker: responde com resultados/eventos programados."""

    def __init__(self, handler: Callable[[dict, Callable[[dict], None] | None], Any]):
        self.handler = handler
        self.requests: list[dict] = []

    def run(self, request, *, on_event=None, cancel_event=None, timeout=None):
        from umd.engine.runner import WorkerResult

        self.requests.append(request)
        result = self.handler(request, on_event)
        return WorkerResult(result=result)


@pytest.fixture
def fake_runner_factory():
    return FakeRunner


# ---------------------------------------------------------------- servidor HTTP local

class _RangeHandler(http.server.SimpleHTTPRequestHandler):
    extensions_map = {
        **http.server.SimpleHTTPRequestHandler.extensions_map,
        ".mp4": "video/mp4", ".png": "image/png", ".jpg": "image/jpeg", ".mp3": "audio/mpeg",
        ".exe": "application/octet-stream", ".html": "text/html",
    }

    def log_message(self, *args: Any) -> None:  # silencioso nos testes
        pass

    def send_head(self):  # type: ignore[override]
        path = Path(self.translate_path(self.path))
        if not path.is_file():
            self.send_error(404)
            return None
        size = path.stat().st_size
        ctype = self.guess_type(str(path))
        header = self.headers.get("Range")
        match = re.match(r"bytes=(\d+)-", header or "")
        handle = open(path, "rb")  # noqa: SIM115 - fechado pelo copyfile do servidor
        if match and int(match.group(1)) < size:
            start = int(match.group(1))
            handle.seek(start)
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{size - 1}/{size}")
            self.send_header("Content-Length", str(size - start))
        else:
            self.send_response(200)
            self.send_header("Content-Length", str(size))
        self.send_header("Content-Type", ctype)
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        return handle


@pytest.fixture
def media_server(tmp_path: Path) -> Iterator[tuple[str, Path]]:
    root = tmp_path / "www"
    root.mkdir()
    handler = lambda *a, **kw: _RangeHandler(*a, directory=str(root), **kw)  # noqa: E731
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", root
    finally:
        server.shutdown()
        server.server_close()


# ---------------------------------------------------------------- midia de teste

@pytest.fixture(scope="session")
def ffmpeg_path() -> str:
    from umd.media.ffmpeg import find_ffmpeg

    path = find_ffmpeg()
    if not path:
        pytest.skip("FFmpeg não disponível")
    return path


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory: pytest.TempPathFactory, ffmpeg_path: str) -> Path:
    """Video MP4 de 2 s (H.264 + AAC) gerado pelo proprio ffmpeg."""
    from umd.core.process import run_quiet

    out = tmp_path_factory.mktemp("media") / "sample.mp4"
    code, output = run_quiet([
        ffmpeg_path, "-y", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=15:duration=2",
        "-f", "lavfi", "-i", "sine=frequency=440:duration=2", "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(out),
    ], timeout=60)
    if code != 0 or not out.exists():
        pytest.skip(f"não foi possível gerar vídeo de teste: {output[-300:]}")
    return out


@pytest.fixture
def sample_png(tmp_path: Path) -> Path:
    from PIL import Image

    path = tmp_path / "foto.png"
    Image.new("RGBA", (64, 48), (10, 120, 200, 128)).save(path)
    return path
