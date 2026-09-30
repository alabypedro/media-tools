"""Lado do app: dispara o worker, le os eventos e controla cancelamento
e timeout (matando a arvore de processos inteira, inclusive ffmpeg).

A requisicao vai pelo stdin (nunca pela linha de comando): URLs e
caminhos nao aparecem na lista de processos e nao existe risco de um
valor ser interpretado como opcao/comando.
"""

from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.exceptions import ErrorCode, UMDError
from ..core.logger import get_logger, redact
from ..core.paths import is_frozen
from ..core.process import kill_process_tree, popen_kwargs
from .protocol import decode, encode

log = get_logger("engine")

EventCallback = Callable[[dict[str, Any]], None]


@dataclass
class WorkerResult:
    result: Any = None
    events: list[dict[str, Any]] = field(default_factory=list)
    stderr_tail: str = ""


def worker_command() -> tuple[list[str], dict[str, str]]:
    """Comando para iniciar o worker + variaveis de ambiente extras."""
    env: dict[str, str] = {}
    if is_frozen():
        exe_dir = Path(sys.executable).parent
        cli = exe_dir / ("umd.exe" if sys.platform == "win32" else "umd")
        # umd.exe e um executavel de console: stdin/stdout sempre validos
        target = cli if cli.exists() else Path(sys.executable)
        return [str(target), "--engine-worker"], env

    python = Path(sys.executable)
    if python.name.lower() == "pythonw.exe" and (python.parent / "python.exe").exists():
        python = python.parent / "python.exe"
    project_root = str(Path(__file__).resolve().parent.parent.parent)
    existing = os.environ.get("PYTHONPATH")
    env["PYTHONPATH"] = project_root + (os.pathsep + existing if existing else "")
    return [str(python), "-m", "umd.engine.worker"], env


class EngineRunner:
    def __init__(self, engines_dir: Path | None = None):
        self.engines_dir = engines_dir

    def run(
        self,
        request: dict[str, Any],
        *,
        on_event: EventCallback | None = None,
        cancel_event: threading.Event | None = None,
        timeout: float | None = None,
    ) -> WorkerResult:
        request = dict(request)
        if self.engines_dir is not None:
            request.setdefault("engines_dir", str(self.engines_dir))

        cmd, extra_env = worker_command()
        env = {**os.environ, **extra_env, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1"}
        action = request.get("action")
        log.debug("worker start: action=%s engine=%s", action, request.get("engine"))

        try:
            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                env=env,
                **popen_kwargs(),
            )
        except OSError as exc:
            raise UMDError(ErrorCode.ENGINE_MISSING, details=f"Could not start worker {cmd!r}: {exc}") from None

        events: queue.Queue[dict[str, Any] | None] = queue.Queue()
        stderr_tail: deque[str] = deque(maxlen=200)

        def read_stdout() -> None:
            assert proc.stdout is not None
            for raw in iter(proc.stdout.readline, b""):
                event = decode(raw.decode("utf-8", errors="replace"))
                if event is not None:
                    events.put(event)
            events.put(None)

        def read_stderr() -> None:
            assert proc.stderr is not None
            for raw in iter(proc.stderr.readline, b""):
                stderr_tail.append(raw.decode("utf-8", errors="replace").rstrip())

        threading.Thread(target=read_stdout, daemon=True, name="umd-worker-out").start()
        threading.Thread(target=read_stderr, daemon=True, name="umd-worker-err").start()

        try:
            assert proc.stdin is not None
            proc.stdin.write(encode(request).encode("utf-8"))
            proc.stdin.close()
        except OSError:
            pass  # o worker morreu cedo; o erro aparece abaixo

        outcome = WorkerResult()
        error: dict[str, Any] | None = None
        done = False
        started = time.monotonic()
        stream_open = True
        try:
            while stream_open:
                if cancel_event is not None and cancel_event.is_set():
                    kill_process_tree(proc)
                    raise UMDError(ErrorCode.CANCELLED)
                if timeout is not None and time.monotonic() - started > timeout:
                    kill_process_tree(proc)
                    raise UMDError(ErrorCode.TIMEOUT, details=f"Worker exceeded {timeout:.0f}s (action={action})")
                try:
                    event = events.get(timeout=0.15)
                except queue.Empty:
                    continue
                if event is None:
                    stream_open = False
                    break
                kind = event.get("type")
                if kind == "result":
                    outcome.result = event.get("data")
                elif kind == "error":
                    error = event.get("error") or {}
                elif kind == "done":
                    done = True
                elif kind == "log":
                    level = event.get("level")
                    if level in ("warning", "error"):
                        log.info("engine %s: %s", level, redact(str(event.get("msg", ""))[:500]))
                if kind not in ("progress", "log"):
                    outcome.events.append(event)
                if on_event is not None:
                    on_event(event)
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                kill_process_tree(proc)
        except BaseException:
            kill_process_tree(proc)
            raise

        outcome.stderr_tail = redact("\n".join(stderr_tail))
        if error is not None:
            exc = UMDError.from_dict(error)
            exc.details = redact("\n".join(p for p in (exc.details, _tail(outcome.stderr_tail)) if p))
            raise exc
        if not done:
            raise UMDError(
                ErrorCode.ENGINE_CRASH,
                details=f"Worker exited with code {proc.returncode} (action={action})\n{outcome.stderr_tail[-4000:]}",
            )
        return outcome


def _tail(text: str, lines: int = 25) -> str:
    return "\n".join(text.splitlines()[-lines:])
