"""Gera o executavel para Windows (usuario final nao precisa de Python).

Uso:
    pip install -r requirements-dev.txt
    python build.py            # gera dist/UniversalMediaTools/
    python build.py --zip      # e tambem um .zip pronto para distribuir
    python build.py --installer  # e tambem o instalador (UniversalMediaTools-Setup-<versao>.exe)

Passos:
1. gera o icone (se faltar);
2. prepara o FFmpeg em packaging/ffmpeg/ (do imageio-ffmpeg, do PATH ou de
   UMD_FFMPEG) junto com o aviso de licenca;
3. escreve o arquivo de versao do Windows;
4. roda o PyInstaller com packaging/umd.spec;
5. faz um teste rapido do resultado (umd.exe --version / --check-engines).
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

from umd import APP_NAME, __version__  # noqa: E402

DIST = ROOT / "dist" / "UniversalMediaTools"
FFMPEG_DIR = ROOT / "packaging" / "ffmpeg"

FFMPEG_NOTICE = """FFmpeg incluido neste pacote
============================

Este aplicativo distribui o FFmpeg ({version}) como um programa SEPARADO
(ffmpeg.exe), executado como processo externo para juntar audio/video e
converter formatos.

Origem do binario: {origin}
O FFmpeg e software livre, licenciado sob a GNU GPL versao 3 (esta build
inclui componentes GPL como libx264). O codigo-fonte correspondente esta
disponivel em https://ffmpeg.org/download.html e, para as builds do
gyan.dev, em https://www.gyan.dev/ffmpeg/builds/ .

Licenca completa: https://www.gnu.org/licenses/gpl-3.0.html
"""


def step(message: str) -> None:
    print(f"\n==> {message}", flush=True)


def ensure_icon() -> None:
    if not (ROOT / "assets" / "icon.ico").exists():
        step("Gerando ícone")
        subprocess.run([sys.executable, str(ROOT / "tools" / "make_icon.py")], check=True)


def locate_ffmpeg() -> tuple[Path, str]:
    env = os.environ.get("UMD_FFMPEG")
    if env and Path(env).is_file():
        return Path(env), f"caminho informado em UMD_FFMPEG ({env})"
    try:
        import imageio_ffmpeg

        return Path(imageio_ffmpeg.get_ffmpeg_exe()), "pacote imageio-ffmpeg (PyPI), build do gyan.dev"
    except Exception:  # noqa: BLE001
        pass
    system = shutil.which("ffmpeg")
    if system:
        return Path(system), f"FFmpeg instalado no sistema ({system})"
    raise SystemExit("FFmpeg não encontrado: instale o imageio-ffmpeg (pip) ou defina UMD_FFMPEG.")


def prepare_ffmpeg() -> None:
    step("Preparando o FFmpeg")
    source, origin = locate_ffmpeg()
    FFMPEG_DIR.mkdir(parents=True, exist_ok=True)
    target = FFMPEG_DIR / "ffmpeg.exe"
    shutil.copy2(source, target)
    probe = source.with_name(source.name.replace("ffmpeg", "ffprobe", 1))
    if probe.is_file() and probe != source:
        shutil.copy2(probe, FFMPEG_DIR / "ffprobe.exe")
    result = subprocess.run([str(target), "-hide_banner", "-version"], capture_output=True, text=True, check=True)
    version = result.stdout.split("\n", 1)[0].replace("ffmpeg version ", "").split(" Copyright")[0]
    (FFMPEG_DIR / "FFMPEG-LICENSE.txt").write_text(FFMPEG_NOTICE.format(version=version, origin=origin), encoding="utf-8")
    print(f"    {target} ({version})")


def write_version_file() -> None:
    parts = [int(p) for p in __version__.split(".")[:3]] + [0]
    numbers = ", ".join(str(p) for p in parts[:4])
    content = f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({numbers}), prodvers=({numbers}), mask=0x3f, flags=0x0, OS=0x40004,
                    fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[
    StringFileInfo([StringTable('041604B0', [
      StringStruct('CompanyName', '{APP_NAME}'),
      StringStruct('FileDescription', '{APP_NAME}'),
      StringStruct('FileVersion', '{__version__}'),
      StringStruct('InternalName', 'UniversalMediaTools'),
      StringStruct('OriginalFilename', 'UniversalMediaTools.exe'),
      StringStruct('ProductName', '{APP_NAME}'),
      StringStruct('ProductVersion', '{__version__}')])]),
    VarFileInfo([VarStruct('Translation', [0x0416, 1200])])
  ]
)
"""
    (ROOT / "packaging" / "version_info.txt").write_text(content, encoding="utf-8")


def run_pyinstaller(clean: bool) -> None:
    step("Rodando o PyInstaller (pode levar alguns minutos)")
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", str(ROOT / "packaging" / "umd.spec"),
            "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build")]
    if clean:
        args.append("--clean")
    subprocess.run(args, check=True, cwd=ROOT)


def smoke_test() -> None:
    step("Testando o executável gerado")
    cli = DIST / "umd.exe"
    gui = DIST / "UniversalMediaTools.exe"
    for exe in (cli, gui):
        if not exe.exists():
            raise SystemExit(f"Arquivo esperado não foi gerado: {exe}")
    env = {**os.environ, "UMD_HOME": str(ROOT / "build" / "smoke-home")}
    for args in (["--version"], ["--check-engines"]):
        result = subprocess.run([str(cli), *args], capture_output=True, text=True, env=env, timeout=180)
        print("    $ umd " + " ".join(args))
        print("      " + result.stdout.strip().replace("\n", "\n      "))
        if result.returncode != 0:
            raise SystemExit(f"Falhou: umd {' '.join(args)} (código {result.returncode})\n{result.stderr}")


def make_zip() -> Path:
    step("Compactando")
    target = ROOT / "dist" / f"UniversalMediaTools-{__version__}-win64.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for path in DIST.rglob("*"):
            zf.write(path, Path("UniversalMediaTools") / path.relative_to(DIST))
    print(f"    {target} ({target.stat().st_size / 1024 / 1024:.0f} MB)")
    return target


def find_iscc() -> Path:
    candidates = [Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe"]
    for var in ("ProgramFiles(x86)", "ProgramFiles"):
        if os.environ.get(var):
            candidates.append(Path(os.environ[var]) / "Inno Setup 6" / "ISCC.exe")
    for path in candidates:
        if path.is_file():
            return path
    found = shutil.which("iscc")
    if found:
        return Path(found)
    raise SystemExit("Inno Setup 6 não encontrado. Instale com: winget install JRSoftware.InnoSetup")


def make_installer() -> Path:
    step("Gerando o instalador (Inno Setup)")
    iscc = find_iscc()
    subprocess.run([str(iscc), "/Q", f"/DAppVersion={__version__}", str(ROOT / "packaging" / "installer.iss")],
                   check=True, cwd=ROOT)
    target = ROOT / "dist" / f"UniversalMediaTools-Setup-{__version__}.exe"
    print(f"    {target} ({target.stat().st_size / 1024 / 1024:.0f} MB)")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description="Gera o executável do Universal Media Tools para Windows")
    parser.add_argument("--zip", action="store_true", help="gera também um .zip para distribuição")
    parser.add_argument("--installer", action="store_true",
                        help="gera também o instalador UniversalMediaTools-Setup-<versão>.exe (requer Inno Setup 6)")
    parser.add_argument("--clean", action="store_true", help="limpa o cache do PyInstaller antes")
    parser.add_argument("--skip-test", action="store_true", help="não roda o teste rápido no final")
    args = parser.parse_args()
    if sys.platform != "win32":
        print("Aviso: este script foi feito para gerar a versão Windows.")
    ensure_icon()
    prepare_ffmpeg()
    write_version_file()
    run_pyinstaller(args.clean)
    if not args.skip_test:
        smoke_test()
    if args.zip:
        make_zip()
    if args.installer:
        make_installer()
    size = sum(p.stat().st_size for p in DIST.rglob("*") if p.is_file()) / 1024 / 1024
    step(f"Pronto: {DIST} ({size:.0f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
