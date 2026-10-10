"""Remover fundo com IA, no proprio PC (como o remove.bg, mas offline e sem limite).

Usa o modelo BiRefNet (versao "lite", ~220 MB, licenca MIT) rodando no
onnxruntime. O modelo nao vai no instalador: e baixado uma vez, na primeira
vez que a ferramenta e usada, para a pasta de dados do programa (models/), e
so e aceito se o SHA-256 conferir.

    ensure_model(progress, cancel)  baixa o modelo se ainda nao estiver la
    predict_mask(frame)             mascara L (255 = manter) do tamanho do quadro
"""

from __future__ import annotations

import hashlib
import threading
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

from ..convert.core.errors import ConversionError
from ..core.paths import app_data_dir

MODEL_NAME = "birefnet-general-lite.onnx"
MODEL_URL = ("https://github.com/danielgatis/rembg/releases/download/v0.0.0/"
             "BiRefNet-general-bb_swin_v1_tiny-epoch_232.onnx")
MODEL_SHA256 = "5600024376f572a557870a5eb0afb1e5961636bef4e1e22132025467d0f03333"
MODEL_SIZE = 224_005_088
INPUT_SIZE = (1024, 1024)
_MEAN, _STD = (0.485, 0.456, 0.406), (0.229, 0.224, 0.225)
_ALLOWED_HOSTS = {"github.com", "objects.githubusercontent.com", "release-assets.githubusercontent.com"}

ProgressFn = Callable[[int, int], None]  # (recebidos, total)

_session = None
_session_lock = threading.Lock()


def model_path() -> Path:
    return app_data_dir() / "models" / MODEL_NAME


def is_available() -> bool:
    """O onnxruntime esta instalado (no programa empacotado, sempre)."""
    try:
        import numpy  # noqa: F401
        import onnxruntime  # noqa: F401
    except ImportError:
        return False
    return True


def model_ready() -> bool:
    path = model_path()
    return path.is_file() and path.stat().st_size == MODEL_SIZE


def _check_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in _ALLOWED_HOSTS:
        raise ConversionError(f"Endereco de download do modelo nao permitido: {parsed.hostname or url}")


def ensure_model(progress: ProgressFn | None = None, cancel_event: threading.Event | None = None) -> Path:
    """Baixa o modelo (so na primeira vez) e confere o SHA-256 antes de aceitar."""
    target = model_path()
    if model_ready():
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_suffix(".part")
    digest = hashlib.sha256()
    received = 0
    _check_url(MODEL_URL)
    request = urllib.request.Request(MODEL_URL, headers={"User-Agent": "UniversalMediaTools (+remove-bg)"})
    try:
        with urllib.request.urlopen(request, timeout=30) as response, open(partial, "wb") as handle:  # noqa: S310
            _check_url(response.geturl())  # redirecionamentos tambem precisam ficar no GitHub
            while chunk := response.read(1024 * 1024):
                if cancel_event is not None and cancel_event.is_set():
                    from .clip import CANCELLED_MESSAGE

                    raise ConversionError(CANCELLED_MESSAGE)
                received += len(chunk)
                if received > MODEL_SIZE:
                    raise ConversionError("O download do modelo de IA passou do tamanho esperado e foi descartado.")
                digest.update(chunk)
                handle.write(chunk)
                if progress:
                    progress(received, MODEL_SIZE)
        if digest.hexdigest() != MODEL_SHA256:
            raise ConversionError("O modelo de IA baixado nao confere com o codigo de verificacao (SHA-256).",
                                  hint="Ele foi descartado. Tente de novo mais tarde.")
        partial.replace(target)
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ConversionError("Nao foi possivel baixar o modelo de IA para remover o fundo.",
                              hint="Verifique a conexao com a internet (so precisa na primeira vez).",
                              detail=exc) from exc
    finally:
        partial.unlink(missing_ok=True)
    return target


def _get_session():
    global _session
    with _session_lock:
        if _session is None:
            if not is_available():
                raise ConversionError("Esta instalacao nao tem o onnxruntime, usado pela IA de remover fundo.",
                                      hint="Instale com: pip install onnxruntime")
            import onnxruntime as ort

            if not model_ready():
                raise ConversionError("O modelo de IA para remover o fundo ainda nao foi baixado.")
            options = ort.SessionOptions()
            options.log_severity_level = 3
            try:
                _session = ort.InferenceSession(str(model_path()), options, providers=["CPUExecutionProvider"])
            except Exception as exc:  # noqa: BLE001 - arquivo corrompido, falta de memoria...
                raise ConversionError("Nao foi possivel carregar o modelo de IA.",
                                      hint="Apague a pasta 'models' nos dados do programa para baixar de novo.",
                                      detail=exc) from exc
        return _session


def predict_mask(frame):
    """Mascara em tons de cinza (255 = primeiro plano), do tamanho do quadro."""
    import numpy as np
    from PIL import Image

    session = _get_session()
    image = np.asarray(frame.convert("RGB").resize(INPUT_SIZE, Image.Resampling.LANCZOS), dtype=np.float32)
    image = (image / max(float(image.max()), 1e-6) - _MEAN) / _STD
    tensor = image.transpose(2, 0, 1)[np.newaxis].astype(np.float32)
    output = session.run(None, {session.get_inputs()[0].name: tensor})[0][0, 0]
    prediction = 1 / (1 + np.exp(-output))
    low, high = float(prediction.min()), float(prediction.max())
    prediction = (prediction - low) / max(high - low, 1e-6)
    mask = Image.fromarray((prediction * 255).astype(np.uint8), mode="L")
    return mask.resize(frame.size, Image.Resampling.LANCZOS)
