"""Execucao de subprocessos (ffmpeg, LibreOffice) que pode ser cancelada
e que nunca deixa processo orfao para tras (item 18: cancelamento nao
pode deixar ffmpeg/soffice abandonado em segundo plano).

stdout/stderr sao redirecionados para arquivos temporarios em vez de
PIPE: assim conseguimos esperar o processo com `poll()` (sem ficar preso
em `communicate()`) sem risco do processo travar por o buffer do pipe
encher (ffmpeg em particular e bem verboso no stderr).
"""

from __future__ import annotations

import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass
class ProcessResult:
    returncode: int | None
    stdout: str
    stderr: str
    cancelled: bool
    timed_out: bool


def run_cancellable(
    args: list[str],
    *,
    cancel_event: threading.Event | None = None,
    timeout: float | None = None,
    poll_interval: float = 0.2,
) -> ProcessResult:
    with tempfile.TemporaryDirectory(prefix="uc_proc_") as tmp:
        out_path = Path(tmp) / "stdout.log"
        err_path = Path(tmp) / "stderr.log"
        with open(out_path, "wb") as out_f, open(err_path, "wb") as err_f:
            proc = subprocess.Popen(
                args,
                stdout=out_f,
                stderr=err_f,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            start = time.monotonic()
            cancelled = False
            timed_out = False

            while proc.poll() is None:
                if cancel_event is not None and cancel_event.is_set():
                    cancelled = True
                    break
                if timeout is not None and (time.monotonic() - start) > timeout:
                    timed_out = True
                    break
                time.sleep(poll_interval)

            if cancelled or timed_out:
                _terminate(proc)

        stdout = out_path.read_text(encoding="utf-8", errors="replace")
        stderr = err_path.read_text(encoding="utf-8", errors="replace")

    return ProcessResult(
        returncode=proc.returncode,
        stdout=stdout,
        stderr=stderr,
        cancelled=cancelled,
        timed_out=timed_out,
    )


def _terminate(proc: subprocess.Popen) -> None:
    try:
        proc.terminate()
        proc.wait(timeout=3)
    except Exception:
        try:
            proc.kill()
            proc.wait(timeout=3)
        except Exception:
            pass
