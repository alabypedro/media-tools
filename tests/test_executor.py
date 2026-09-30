"""JobExecutor com runner falso: organizacao de arquivos, texto de post,
conversao, fallback de engine e protecoes."""

from __future__ import annotations

from pathlib import Path

import pytest

from umd.core.exceptions import ErrorCode, UMDError
from umd.core.naming import DEFAULT_TEMPLATE
from umd.database.repository import FileRepository
from umd.downloader.executor import JobExecutor
from umd.downloader.job import DownloadJob, DownloadRequest
from umd.media.formats import DownloadSelection, SelectionMode


def make_request(tmp_path: Path, **kw) -> DownloadRequest:
    data = dict(url="https://www.youtube.com/watch?v=abc", provider_key="youtube", platform_name="YouTube",
                engines=["yt-dlp"], selection=DownloadSelection(), output_dir=str(tmp_path / "out"),
                filename_template=DEFAULT_TEMPLATE, title="Título original")
    data.update(kw)
    return DownloadRequest(**data)


def run_job(executor: JobExecutor, request: DownloadRequest) -> DownloadJob:
    job = DownloadJob(request)
    executor.execute(job, lambda: None)
    return job


def emit_file(on_event, staging: Path, name: str, meta: dict, content: bytes = b"data") -> Path:
    path = staging / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    on_event({"type": "file", "path": str(path), "meta": meta})
    return path


@pytest.fixture
def files_repo(db):
    return FileRepository(db)


def test_ytdlp_file_is_organized_by_template(tmp_path, settings_store, files_repo, fake_runner_factory) -> None:
    def handler(request, on_event):
        staging = Path(request["staging_dir"])
        on_event({"type": "item", "data": {"index": 1, "count": 1, "title": "Meu vídeo", "expected_size": 4}})
        on_event({"type": "progress", "downloaded": 4, "total": 4, "speed": 10.0, "eta": 0})
        emit_file(on_event, staging, "abc.mp4", {"title": "Meu vídeo: parte 1/2", "uploader": "Canal", "id": "abc",
                                                 "upload_date": "20240131", "width": 1280, "height": 720, "duration": 12})

    runner = fake_runner_factory(handler)
    executor = JobExecutor(runner, settings_store, files_repo, ffmpeg_resolver=lambda: "ffmpeg")
    job = run_job(executor, make_request(tmp_path))
    expected = tmp_path / "out" / "YouTube" / "Canal" / "Meu vídeo - parte 1-2.mp4"
    assert job.files == [expected] and expected.read_bytes() == b"data"
    assert not job.staging_dir.exists()  # area temporaria limpa
    record = files_repo.list()[0]
    assert (record.media_kind, record.author, record.width, record.duration) == ("video", "Canal", 1280, 12)


def test_existing_files_are_never_overwritten(tmp_path, settings_store, fake_runner_factory) -> None:
    target = tmp_path / "out" / "YouTube" / "Canal" / "Vídeo.mp4"
    target.parent.mkdir(parents=True)
    target.write_bytes(b"antigo")

    def handler(request, on_event):
        emit_file(on_event, Path(request["staging_dir"]), "x.mp4", {"title": "Vídeo", "uploader": "Canal"})

    executor = JobExecutor(fake_runner_factory(handler), settings_store, ffmpeg_resolver=lambda: "ffmpeg")
    job = run_job(executor, make_request(tmp_path))
    assert target.read_bytes() == b"antigo"
    assert job.files == [target.with_name("Vídeo (1).mp4")]


def test_gallery_post_files_text_sidecar_and_image_conversion(tmp_path, settings_store, files_repo,
                                                              fake_runner_factory) -> None:
    from PIL import Image

    def handler(request, on_event):
        staging = Path(request["staging_dir"])
        for i in (1, 2):
            path = staging / "instagram" / "fulano" / f"{i}.jpg"
            path.parent.mkdir(parents=True, exist_ok=True)
            Image.new("RGB", (8, 8), (i * 50, 0, 0)).save(path)
            on_event({"type": "file", "path": str(path), "meta": {
                "description": "Pôr do sol 🌅\n#praia", "username": "fulano", "date": "2026-01-02 10:00:00",
                "shortcode": "C1", "num": i}})

    request = make_request(tmp_path, url="https://www.instagram.com/p/C1/", provider_key="instagram",
                           platform_name="Instagram", engines=["gallery-dl"], content_type="post", title=None,
                           selection=DownloadSelection(mode=SelectionMode.FILES, image_format="png"))
    job = run_job(JobExecutor(fake_runner_factory(handler), settings_store, files_repo), request)
    folder = tmp_path / "out" / "Instagram" / "fulano"
    names = sorted(p.name for p in job.files)
    assert names == ["Pôr do sol 🌅 - 01.png", "Pôr do sol 🌅 - 01.txt", "Pôr do sol 🌅 - 02.png"]
    text = (folder / "Pôr do sol 🌅 - 01.txt").read_text(encoding="utf-8")
    assert "Autor: fulano" in text and "#praia" in text and "https://www.instagram.com/p/C1/" in text
    kinds = sorted(r.media_kind for r in files_repo.list())
    assert kinds == ["document", "image", "image"]


def test_post_text_can_be_disabled(tmp_path, settings_store, fake_runner_factory) -> None:
    def handler(request, on_event):
        emit_file(on_event, Path(request["staging_dir"]), "1.jpg", {"description": "texto", "username": "u"})

    request = make_request(tmp_path, engines=["gallery-dl"], platform_name="X (Twitter)",
                           selection=DownloadSelection(mode=SelectionMode.FILES, save_post_text=False))
    job = run_job(JobExecutor(fake_runner_factory(handler), settings_store), request)
    assert [p.suffix for p in job.files] == [".jpg"]


def test_fallthrough_to_second_engine(tmp_path, settings_store, fake_runner_factory) -> None:
    def handler(request, on_event):
        if request["engine"] == "yt-dlp":
            raise UMDError(ErrorCode.NO_MEDIA, details="No video could be found in this tweet")
        emit_file(on_event, Path(request["staging_dir"]), "img.jpg", {"content": "oi", "author": {"nick": "a"}})

    runner = fake_runner_factory(handler)
    request = make_request(tmp_path, engines=["yt-dlp", "gallery-dl"], platform_name="X (Twitter)", provider_key="x")
    job = run_job(JobExecutor(runner, settings_store, ffmpeg_resolver=lambda: "ffmpeg"), request)
    assert [r["engine"] for r in runner.requests] == ["yt-dlp", "gallery-dl"]
    assert job.files[0].suffix == ".jpg"


def test_permanent_error_is_not_masked_by_fallback(tmp_path, settings_store, fake_runner_factory) -> None:
    runner = fake_runner_factory(lambda r, e: (_ for _ in ()).throw(UMDError(ErrorCode.AUTH_REQUIRED)))
    request = make_request(tmp_path, engines=["gallery-dl", "yt-dlp"])
    with pytest.raises(UMDError) as info:
        run_job(JobExecutor(runner, settings_store), request)
    assert info.value.code == ErrorCode.AUTH_REQUIRED and len(runner.requests) == 1


def test_executables_are_refused(tmp_path, settings_store, fake_runner_factory) -> None:
    def handler(request, on_event):
        emit_file(on_event, Path(request["staging_dir"]), "virus.exe", {"title": "nada suspeito"}, b"MZ")

    with pytest.raises(UMDError) as info:
        run_job(JobExecutor(fake_runner_factory(handler), settings_store, ffmpeg_resolver=lambda: "ffmpeg"),
                make_request(tmp_path))
    assert info.value.code == ErrorCode.NO_MEDIA
    assert not list((tmp_path / "out").rglob("*.exe"))


def test_files_outside_staging_are_ignored(tmp_path, settings_store, fake_runner_factory) -> None:
    outside = tmp_path / "segredo.txt"
    outside.write_text("não mover")

    def handler(request, on_event):
        on_event({"type": "file", "path": str(outside), "meta": {"title": "x"}})
        emit_file(on_event, Path(request["staging_dir"]), "ok.mp4", {"title": "ok"})

    job = run_job(JobExecutor(fake_runner_factory(handler), settings_store, ffmpeg_resolver=lambda: "ffmpeg"),
                  make_request(tmp_path))
    assert outside.exists() and [p.name for p in job.files] == ["ok.mp4"]


def test_ffmpeg_required_for_ytdlp(tmp_path, settings_store, fake_runner_factory) -> None:
    executor = JobExecutor(fake_runner_factory(lambda r, e: None), settings_store, ffmpeg_resolver=lambda: None)
    with pytest.raises(UMDError) as info:
        run_job(executor, make_request(tmp_path))
    assert info.value.code == ErrorCode.FFMPEG_MISSING


def test_discard_removes_staging(tmp_path, settings_store, fake_runner_factory) -> None:
    executor = JobExecutor(fake_runner_factory(lambda r, e: None), settings_store)
    job = DownloadJob(make_request(tmp_path))
    job.staging_dir.mkdir(parents=True)
    (job.staging_dir / "parcial.part").write_bytes(b"x")
    executor.discard(job)
    assert not job.staging_dir.exists() and not job.staging_dir.parent.exists()


def test_live_stop_salvages_recording(tmp_path, settings_store, ffmpeg_path, fake_runner_factory) -> None:
    from umd.core.process import run_quiet

    def handler(request, on_event):
        parts = Path(request["staging_dir"]) / "_parts"
        parts.mkdir(parents=True, exist_ok=True)
        run_quiet([ffmpeg_path, "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=160x120:rate=10",
                   "-c:v", "libx264", "-f", "mpegts", str(parts / "live123.mp4.part")], timeout=60)
        job.stop_reason = "stop"  # usuario clicou "Parar e salvar"
        raise UMDError(ErrorCode.CANCELLED)

    request = make_request(tmp_path, is_live=True, title="Live de teste", author="Canal")
    job = DownloadJob(request)
    JobExecutor(fake_runner_factory(handler), settings_store, ffmpeg_resolver=lambda: ffmpeg_path).execute(job, lambda: None)
    assert len(job.files) == 1 and job.files[0].suffix == ".mp4" and job.files[0].stat().st_size > 0
    assert job.files[0].parent.name == "Canal"
