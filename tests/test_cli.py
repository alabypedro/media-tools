from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from umd import cli


def test_help_and_version(capsys) -> None:
    with pytest.raises(SystemExit) as info:
        cli.main(["--help"])
    assert info.value.code == 0
    assert "umd" in capsys.readouterr().out
    with pytest.raises(SystemExit):
        cli.main(["--version"])


def test_no_arguments_prints_usage(capsys) -> None:
    assert cli.main([]) == cli.EXIT_USAGE
    assert "usage" in capsys.readouterr().out.lower()


def test_parse_items() -> None:
    assert cli.parse_items("1-3,7, 5") == [1, 2, 3, 5, 7]
    with pytest.raises(ValueError):
        cli.parse_items("0-2")
    with pytest.raises(ValueError):
        cli.parse_items("abc")


def test_invalid_items_is_usage_error() -> None:
    with pytest.raises(SystemExit) as info:
        cli.main(["https://a.com/x", "--items", "x"])
    assert info.value.code == 2


def test_only_invalid_urls(capsys) -> None:
    assert cli.main(["isto-nao-e-url"]) == cli.EXIT_USAGE
    out = capsys.readouterr().out
    assert "[ignorado]" in out and "Nenhuma URL válida" in out


def test_overrides_are_not_persisted(umd_home: Path) -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["https://a.com/x", "--rate-limit", "300", "--cookies-from-browser", "firefox:perfil",
                              "--no-subfolders", "-j", "5"])
    changes = cli._overrides(args)
    assert changes == {"rate_limit_kbps": 300, "cookies_browser": "firefox", "cookies_browser_profile": "perfil",
                       "create_subfolders": False, "max_concurrent_downloads": 5}


def _allow_local(umd_home: Path, download_dir: Path) -> None:
    umd_home.mkdir(parents=True, exist_ok=True)
    (umd_home / "settings.json").write_text(json.dumps({"allow_local_network_urls": True, "download_dir": str(download_dir)}),
                                            encoding="utf-8")


def test_cli_downloads_from_list_file(umd_home, media_server, tmp_path, sample_png, capsys) -> None:
    """Fluxo completo da CLI (mesmos servicos da GUI) com servidor local."""
    base, root = media_server
    shutil.copy(sample_png, root / "a.png")
    shutil.copy(sample_png, root / "b.png")
    out = tmp_path / "saida"
    _allow_local(umd_home, tmp_path / "padrao")
    url_list = tmp_path / "urls.txt"
    url_list.write_text(f"{base}/a.png\n{base}/b.png\n", encoding="utf-8")
    code = cli.main(["--file", str(url_list), "--output", str(out), "--template", "{title}.{ext}"])
    stdout = capsys.readouterr().out
    assert code == cli.EXIT_OK, stdout
    assert sorted(p.name for p in out.iterdir() if p.is_file()) == ["a.png", "b.png"]
    assert "Concluídos: 2" in stdout
    # a CLI nao alterou a configuracao gravada
    saved = json.loads((umd_home / "settings.json").read_text(encoding="utf-8"))
    assert saved["download_dir"] == str(tmp_path / "padrao")


def test_cli_info_json(umd_home, media_server, tmp_path, sample_png, capsys) -> None:
    base, root = media_server
    shutil.copy(sample_png, root / "c.png")
    _allow_local(umd_home, tmp_path)
    assert cli.main([f"{base}/c.png", "--info", "--json"]) == cli.EXIT_OK
    data = json.loads(capsys.readouterr().out)
    assert data["content_type"] == "image" and data["engine"] in ("direct", "gallery-dl")


def test_cli_reports_failures_with_exit_code(umd_home, media_server, tmp_path, capsys) -> None:
    base, _root = media_server
    _allow_local(umd_home, tmp_path)
    code = cli.main([f"{base}/nao-existe.mp4", "--output", str(tmp_path / "o"), "--retries", "0"])
    stdout = capsys.readouterr().out
    assert code == cli.EXIT_FAILED and "[erro]" in stdout
    assert "Traceback" not in stdout
