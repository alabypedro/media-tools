"""Configuracoes do conversor (dialogo "Configurações de conversão").

Guardadas em JSON simples em `<pasta de dados do app>/converter/config.json`
(a mesma pasta de dados do downloader, que respeita UMD_HOME). Nao
guardamos nada sensivel aqui, entao JSON legivel e suficiente. Tema,
idioma e inicializacao com o Windows sao do aplicativo, nao daqui.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .paths import ConflictPolicy

# None = pasta de dados do app (resolvida em tempo de chamada, para seguir
# UMD_HOME). Os testes podem apontar estes caminhos para uma pasta isolada.
APP_DIR: Path | None = None
CONFIG_PATH: Path | None = None


def app_dir() -> Path:
    if APP_DIR is not None:
        return APP_DIR
    from ...core.paths import app_data_dir

    path = app_data_dir() / "converter"
    path.mkdir(parents=True, exist_ok=True)
    return path


def config_path() -> Path:
    return CONFIG_PATH if CONFIG_PATH is not None else app_dir() / "config.json"


@dataclass
class Settings:
    # CONVERSAO
    default_output_dir: str = ""  # "" = mesma pasta do arquivo de origem
    conflict_policy: str = ConflictPolicy.RENAME.value
    preserve_folder_structure: bool = True
    open_folder_after_conversion: bool = False

    # PDF
    pdf_default_dpi: int = 200
    pdf_default_quality: int = 90
    pdf_default_image_format: str = "png"

    # IMAGENS
    image_default_quality: int = 90
    image_keep_metadata: bool = True

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Settings":
        valid_fields = {f for f in cls.__dataclass_fields__}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)


def load_settings(path: Path | None = None) -> Settings:
    # resolvido em tempo de chamada, nao como default do parametro (mesmo
    # motivo do History.__init__: permite isolar CONFIG_PATH nos testes
    # via monkeypatch mesmo quando o chamador nao passa `path`).
    path = path if path is not None else config_path()
    if not path.exists():
        return Settings()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return Settings()
    return Settings.from_dict(data)


def save_settings(settings: Settings, path: Path | None = None) -> None:
    path = path if path is not None else config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")
