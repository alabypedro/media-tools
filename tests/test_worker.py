"""Worker REAL (subprocesso) contra um servidor HTTP local -- sem internet.

Cobre o protocolo, cancelamento/timeout (matando a arvore de processos),
e os tres backends de verdade: download direto, yt-dlp e gallery-dl.
"""

from __future__ import annotations

import shutil
import time
from pathlib import Path

import pytest

from umd.core.exceptions import ErrorCode, UMDError
from umd.engine.runner import EngineRunner


@pytest.fixture
def runner(umd_home) -> EngineRunner:
    return EngineRunner(umd_home / "engines")


def test_versions(runner) -> None:
    result = runner.run({"action": "versions"}, timeout=60).result
    engines = result["engines"]
    assert engines["yt-dlp"]["version"] and engines["gallery-dl"]["version"]
    assert engines["yt-dlp"]["override"] is False


def test_unknown_action_is_an_error(runner) -> None:
    with pytest.raises(UMDError):
        runner.run({"action": "rm -rf /"}, timeout=30)


def test_cancel_kills_worker_quickly(runner) -> None:
    import threading

    cancel = threading.Event()
    seen = []

    def on_event(event):
        seen.append(event)
        if len(seen) == 3:
            cancel.set()

    started = time.monotonic()
    with pytest.raises(UMDError) as info:
        runner.run({"action": "test_sleep", "seconds": 30}, on_event=on_event, cancel_event=cancel)
    assert info.value.code == ErrorCode.CANCELLED
    assert time.monotonic() - started < 15


def test_timeout(runner) -> None:
    with pytest.raises(UMDError) as info:
        runner.run({"action": "test_sleep", "seconds": 30}, timeout=2)
    assert info.value.code == ErrorCode.TIMEOUT


def _direct(url: str, staging: Path, **extra) -> dict:
    return {"action": "download", "engine": "direct", "url": url, "staging_dir": str(staging), "timeout": 10,
            "allow_local": True, **extra}


def test_direct_download(runner, media_server, tmp_path, sample_png) -> None:
    base, root = media_server
    shutil.copy(sample_png, root / "foto.png")
    events = []
    result = runner.run(_direct(f"{base}/foto.png", tmp_path / "stage"), on_event=events.append, timeout=60)
    files = [e for e in result.events if e["type"] == "file"]
    assert len(files) == 1
    assert Path(files[0]["path"]).read_bytes() == sample_png.read_bytes()
    assert any(e["type"] == "progress" for e in events)


def test_direct_download_resumes_partial_file(runner, media_server, tmp_path) -> None:
    base, root = media_server
    payload = bytes(range(256)) * 4000  # ~1 MB
    (root / "video.mp4").write_bytes(payload)
    parts = tmp_path / "stage" / "_parts"
    parts.mkdir(parents=True)
    (parts / "video.mp4.part").write_bytes(payload[:300_000])
    events = []
    result = runner.run(_direct(f"{base}/video.mp4", tmp_path / "stage"), on_event=events.append, timeout=60)
    final = Path(next(e for e in result.events if e["type"] == "file")["path"])
    assert final.read_bytes() == payload
    first = next(e for e in events if e["type"] == "progress")
    assert first["downloaded"] > 300_000  # continuou de onde parou


@pytest.mark.parametrize("name, body", [("pagina.html", b"<html>oi</html>"), ("setup.exe", b"MZ\x90\x00")])
def test_direct_refuses_non_media(runner, media_server, tmp_path, name, body) -> None:
    base, root = media_server
    (root / name).write_bytes(body)
    with pytest.raises(UMDError) as info:
        runner.run(_direct(f"{base}/{name}", tmp_path / "stage"), timeout=60)
    assert info.value.code == ErrorCode.NO_MEDIA


def test_direct_size_limit(runner, media_server, tmp_path) -> None:
    base, root = media_server
    (root / "grande.mp4").write_bytes(b"0" * 200_000)
    with pytest.raises(UMDError) as info:
        runner.run(_direct(f"{base}/grande.mp4", tmp_path / "stage", max_size=50_000), timeout=60)
    assert info.value.code == ErrorCode.FILE_TOO_LARGE


def test_direct_blocks_local_network_by_default(runner, media_server, tmp_path) -> None:
    base, root = media_server
    (root / "a.mp4").write_bytes(b"0" * 10)
    with pytest.raises(UMDError) as info:
        runner.run({**_direct(f"{base}/a.mp4", tmp_path / "stage"), "allow_local": False}, timeout=60)
    assert info.value.code == ErrorCode.INVALID_URL


def test_direct_404(runner, media_server, tmp_path) -> None:
    base, _root = media_server
    with pytest.raises(UMDError) as info:
        runner.run(_direct(f"{base}/nao-existe.mp4", tmp_path / "stage"), timeout=60)
    assert info.value.code == ErrorCode.NOT_FOUND


def test_real_ytdlp_backend_offline(runner, media_server, tmp_path, sample_video, ffmpeg_path) -> None:
    """yt-dlp de verdade (extrator generico) baixando um MP4 do servidor local."""
    base, root = media_server
    shutil.copy(sample_video, root / "clipe.mp4")
    url = f"{base}/clipe.mp4"
    analysis = runner.run({"action": "analyze", "engine": "yt-dlp", "url": url, "params": {}}, timeout=120).result
    assert analysis["formats"]
    staging = tmp_path / "stage"
    params = {"format": "bv*+ba/b", "postprocessors": [{"key": "FFmpegMetadata", "add_metadata": True}]}
    result = runner.run({"action": "download", "engine": "yt-dlp", "url": url, "params": params,
                         "staging_dir": str(staging), "ffmpeg": ffmpeg_path}, timeout=120)
    files = [e for e in result.events if e["type"] == "file"]
    assert len(files) == 1 and Path(files[0]["path"]).stat().st_size > 1000


def test_real_gallery_dl_backend_offline(runner, media_server, tmp_path, sample_png) -> None:
    """gallery-dl de verdade (extrator 'directlink') baixando uma imagem do servidor local."""
    base, root = media_server
    shutil.copy(sample_png, root / "imagem.png")
    url = f"{base}/imagem.png"
    analysis = runner.run({"action": "analyze", "engine": "gallery-dl", "url": url, "config": []}, timeout=120).result
    assert len(analysis["files"]) == 1
    staging = tmp_path / "stage"
    config = [[["extractor"], "base-directory", str(staging)]]
    result = runner.run({"action": "download", "engine": "gallery-dl", "url": url, "config": config,
                         "staging_dir": str(staging)}, timeout=120)
    files = [e for e in result.events if e["type"] == "file"]
    assert len(files) == 1 and Path(files[0]["path"]).read_bytes() == sample_png.read_bytes()


def test_gallery_dl_unsupported_url(runner) -> None:
    with pytest.raises(UMDError) as info:
        runner.run({"action": "analyze", "engine": "gallery-dl", "url": "https://example.invalid/pagina", "config": []},
                   timeout=60)
    assert info.value.code == ErrorCode.UNSUPPORTED


def test_check_support_uses_real_extractors(runner) -> None:
    urls = ["https://www.youtube.com/watch?v=jNQXAC9IVRw", "https://www.threads.net/@a/post/C1"]
    result = runner.run({"action": "check_support", "urls": urls}, timeout=120).result
    assert "Youtube" in result[urls[0]]["yt-dlp"]
    assert result[urls[1]]["yt-dlp"] == [] and result[urls[1]]["gallery-dl"] is None
