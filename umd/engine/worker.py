"""Processo worker das engines.

Uso interno -- iniciado pelo app como:
    python -m umd.engine.worker            (desenvolvimento)
    umd.exe --engine-worker                (versao empacotada)

Le UMA requisicao JSON do stdin e responde com eventos JSON no stdout
(ver protocol.py). O stdout original vira canal exclusivo do protocolo:
qualquer print perdido das engines e desviado para o stderr, que o app
guarda como "detalhes tecnicos".
"""

from __future__ import annotations

import importlib
import json
import os
import platform
import sys
import time
import traceback
from pathlib import Path
from typing import Any, Callable

from ..core.exceptions import ErrorCode, UMDError, classify_error_text
from . import overrides
from .protocol import MAX_REQUEST_BYTES, Emitter

_ENGINE_MODULES = {"yt-dlp": "yt_dlp", "gallery-dl": "gallery_dl", "yt-dlp-ejs": "yt_dlp_ejs"}


def _backend(engine: str):
    if engine == "yt-dlp":
        from .backends import ytdlp as module
    elif engine == "gallery-dl":
        from .backends import gallerydl as module
    elif engine == "direct":
        from .backends import direct as module
    else:
        raise UMDError(ErrorCode.UNSUPPORTED, details=f"Unknown engine: {engine!r}")
    return module


def _module_version(module_name: str) -> str | None:
    module = importlib.import_module(module_name)
    for candidate in (f"{module_name}.version", f"{module_name}._version"):
        try:
            version = getattr(importlib.import_module(candidate), "__version__", None)
        except ImportError:
            continue
        if version:
            return str(version)
    version = getattr(module, "__version__", None) or getattr(module, "version", None)
    return str(version) if isinstance(version, str) else None


def action_versions(req: dict[str, Any], emitter: Emitter) -> None:
    engines_dir = Path(req["engines_dir"]).resolve() if req.get("engines_dir") else None
    engines: dict[str, Any] = {}
    for name, module_name in _ENGINE_MODULES.items():
        try:
            version = _module_version(module_name)
            location = Path(importlib.import_module(module_name).__file__ or "").resolve().parent
            is_override = bool(engines_dir) and engines_dir in location.parents
            engines[name] = {"version": version, "location": str(location), "override": is_override}
        except Exception as exc:  # noqa: BLE001 - engine ausente/quebrada
            engines[name] = {"version": None, "error": f"{type(exc).__name__}: {exc}"}
    emitter.result({"python": platform.python_version(), "frozen": bool(getattr(sys, "frozen", False)), "engines": engines})


def action_check_support(req: dict[str, Any], emitter: Emitter) -> None:
    """Quais extratores (de verdade, das engines instaladas) reconhecem cada URL."""
    from gallery_dl import extractor as gallery_extractors
    from yt_dlp.extractor import gen_extractor_classes

    classes = [cls for cls in gen_extractor_classes() if cls.ie_key() != "Generic"]
    result: dict[str, Any] = {}
    for url in req.get("urls") or []:
        matched = [cls for cls in classes if cls.suitable(url)]
        gallery = gallery_extractors.find(url)
        result[url] = {
            "yt-dlp": [cls.ie_key() for cls in matched if cls.working()][:3],
            "yt-dlp-broken": [cls.ie_key() for cls in matched if not cls.working()][:3],
            "gallery-dl": type(gallery).__name__ if gallery else None,
        }
    emitter.result(result)


def action_analyze(req: dict[str, Any], emitter: Emitter) -> None:
    _backend(req["engine"]).analyze(req, emitter)


def action_download(req: dict[str, Any], emitter: Emitter) -> None:
    _backend(req["engine"]).download(req, emitter)


def action_test_sleep(req: dict[str, Any], emitter: Emitter) -> None:
    """So para a suite de testes (cancelamento/timeout do runner)."""
    total = float(req.get("seconds", 5))
    began = time.monotonic()
    while (elapsed := time.monotonic() - began) < total:
        emitter.progress(force=True, status="downloading", downloaded=int(elapsed * 1000), total=int(total * 1000))
        time.sleep(0.05)
    emitter.result({"slept": total})


ACTIONS: dict[str, Callable[[dict[str, Any], Emitter], None]] = {
    "versions": action_versions,
    "check_support": action_check_support,
    "analyze": action_analyze,
    "download": action_download,
}
if os.environ.get("UMD_ENABLE_TEST_ACTIONS") == "1":
    ACTIONS["test_sleep"] = action_test_sleep


def _open_protocol_channel():
    """Separa o stdout real (protocolo) do fd 1, que passa a apontar pro stderr."""
    if sys.stdout is None or sys.stderr is None:
        devnull = open(os.devnull, "w", encoding="utf-8")  # noqa: SIM115
        sys.stderr = sys.stderr or devnull
    proto_fd = os.dup(1)
    try:
        os.dup2(2, 1)
    except OSError:
        pass
    sys.stdout = sys.stderr
    return os.fdopen(proto_fd, "w", encoding="utf-8", newline="\n")


def main(argv: list[str] | None = None) -> int:
    # nunca carregar plugins de terceiros das pastas do usuario: o worker so
    # executa o codigo das engines empacotadas (ou atualizadas pelo proprio app)
    os.environ["YTDLP_NO_PLUGINS"] = "1"
    channel = _open_protocol_channel()
    emitter = Emitter(channel)
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        if len(raw) > MAX_REQUEST_BYTES:
            raise UMDError(ErrorCode.UNKNOWN, details="Request too large")
        req = json.loads(raw.decode("utf-8"))
        if not isinstance(req, dict):
            raise UMDError(ErrorCode.UNKNOWN, details="Invalid request")
        overrides.activate(req.get("engines_dir"))
        action = ACTIONS.get(str(req.get("action")))
        if action is None:
            raise UMDError(ErrorCode.UNKNOWN, details=f"Unknown action: {req.get('action')!r}")
        action(req, emitter)
        emitter.done()
        return 0
    except UMDError as exc:
        emitter.error(exc.to_dict())
        return 1
    except ModuleNotFoundError as exc:
        emitter.error(UMDError(ErrorCode.ENGINE_MISSING, details=f"{exc}\n{traceback.format_exc()}").to_dict())
        return 1
    except KeyboardInterrupt:
        emitter.error(UMDError(ErrorCode.CANCELLED).to_dict())
        return 1
    except BaseException as exc:  # noqa: BLE001 - nada pode sair sem virar evento de erro
        text = f"{type(exc).__name__}: {exc}"
        emitter.error(UMDError(classify_error_text(text), details=f"{text}\n{traceback.format_exc()}").to_dict())
        return 1
    finally:
        try:
            channel.flush()
        except (OSError, ValueError):
            pass


if __name__ == "__main__":
    raise SystemExit(main())
