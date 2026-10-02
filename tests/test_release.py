"""release.py: versao, lista de builds e limpeza de dist/ (sem rede e sem build de verdade)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import release


@pytest.mark.parametrize("current, part, expected", [
    ("1.2.0", "patch", "1.2.1"), ("1.2.9", "minor", "1.3.0"), ("1.2.3", "major", "2.0.0"), ("1.2", "patch", "1.2.1"),
])
def test_next_version(current: str, part: str, expected: str) -> None:
    assert release.next_version(current, part) == expected


def test_write_version_changes_only_the_version_and_keeps_line_endings(tmp_path: Path, monkeypatch) -> None:
    source = b'APP = "x"\r\n__version__ = "1.2.0"  # unica fonte\r\nOTHER = "1.2.0"\r\n'
    target = tmp_path / "__init__.py"
    target.write_bytes(source)
    monkeypatch.setattr(release, "VERSION_FILE", target)
    assert release.current_version() == "1.2.0"
    release.write_version("1.3.0")
    assert target.read_bytes() == source.replace(b'__version__ = "1.2.0"', b'__version__ = "1.3.0"')
    assert release.current_version() == "1.3.0"


def test_real_version_file_is_readable() -> None:
    from umd import __version__

    assert release.current_version() == __version__


def test_clean_dist_removes_only_old_published_versions(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(release, "DIST", tmp_path)
    names = ["UniversalMediaTools-1.0.0-win64.zip", "UniversalMediaTools-Setup-1.0.0.exe",
             "UniversalMediaTools-Setup-1.0.0.exe.sha256", "UniversalMediaTools-Setup-1.1.0.exe",
             "UniversalMediaTools-Setup-1.2.0.exe", "UniversalMediaTools-Setup-1.2.0.exe.sha256", "anotacoes.txt"]
    for name in names:
        (tmp_path / name).write_bytes(b"x")
    (tmp_path / "UniversalMediaTools").mkdir()
    release.clean_dist("1.2.0", published={"1.0.0", "1.2.0"})
    left = sorted(p.name for p in tmp_path.iterdir())
    assert left == ["UniversalMediaTools", "UniversalMediaTools-Setup-1.1.0.exe",  # 1.1.0 nao publicada: fica
                    "UniversalMediaTools-Setup-1.2.0.exe", "UniversalMediaTools-Setup-1.2.0.exe.sha256",
                    "anotacoes.txt"]
    assert "nao esta publicada" in capsys.readouterr().out


def test_builds_list_roundtrip(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setattr(release, "BUILDS_FILE", tmp_path / "builds.json")
    release.print_builds(release.load_builds())
    assert "--sync" in capsys.readouterr().out
    api = [{"tag_name": f"v{v}", "published_at": "2026-10-02T13:56:26Z", "body": f"notas {v}", "draft": False,
            "html_url": f"https://github.com/x/y/releases/tag/v{v}",
            "assets": [{"name": f"UniversalMediaTools-Setup-{v}.exe", "size": 121 * 1024 * 1024,
                        "digest": "sha256:" + "a" * 64, "browser_download_url": f"https://github.com/x/y/{v}.exe"},
                       {"name": f"UniversalMediaTools-Setup-{v}.exe.sha256", "size": 103}]}
           for v in ("1.2.0", "1.10.0", "1.9.0")]
    api.append({"tag_name": "v0.1", "assets": [], "html_url": ""})  # sem instalador: fora da lista

    class Result:
        returncode, stdout, stderr = 0, json.dumps(api), ""

    monkeypatch.setattr(release.subprocess, "run", lambda *a, **k: Result)
    builds = release.sync_builds("gh")
    assert [b["version"] for b in builds] == ["1.10.0", "1.9.0", "1.2.0"]  # ordem numerica, nao alfabetica
    assert builds[0]["sha256"] == "a" * 64 and builds[0]["size_mb"] == 121 and builds[0]["date"] == "2026-10-02"
    assert release.load_builds() == builds
    release.print_builds(builds)
    out = capsys.readouterr().out
    assert "1.10.0" in out and "(atual)" in out and "notas 1.9.0" in out
