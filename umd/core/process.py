"""Subprocessos (worker das engines, ffmpeg) de forma segura.

* Sempre lista de argumentos, nunca shell=True nem string concatenada.
* Sem janela de console piscando no Windows.
* `kill_process_tree` encerra o processo E os filhos (o worker do yt-dlp
  dispara ffmpeg; matar so o worker deixaria o ffmpeg orfao).
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path
from typing import Any

IS_WINDOWS = sys.platform == "win32"


def popen_kwargs() -> dict[str, Any]:
    if IS_WINDOWS:
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200)
        return {"creationflags": flags}
    return {"start_new_session": True}  # grupo proprio -> killpg mata a arvore


def _taskkill() -> str:
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    return str(Path(system_root) / "System32" / "taskkill.exe")


def kill_process_tree(proc: subprocess.Popen, timeout: float = 5.0) -> None:
    if proc.poll() is not None:
        return
    try:
        if IS_WINDOWS:
            subprocess.run(
                [_taskkill(), "/PID", str(proc.pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=timeout,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000),
                check=False,
            )
        else:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                proc.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass
    if proc.poll() is None:
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        pass


def run_quiet(args: list[str], timeout: float = 15.0) -> tuple[int | None, str]:
    """Roda um comando curto (ex.: 'ffmpeg -version') e devolve (codigo, saida)."""
    try:
        completed = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
            **popen_kwargs(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return None, str(exc)
    return completed.returncode, completed.stdout.decode("utf-8", errors="replace")
