"""Nomes de arquivo e organizacao automatica em pastas.

* `sanitize_component` deixa qualquer texto (titulo, autor...) seguro como
  nome de arquivo/pasta no Windows, macOS e Linux.
* `render_template` monta o caminho relativo a partir de um template como
  "{platform}/{author}/{title}.{ext}". Os valores dos campos nunca criam
  pastas por conta propria (barras viram hifens); so as barras literais
  do template criam subpastas.
* `safe_join` garante que o caminho final fique dentro da pasta de destino
  (protecao contra path traversal).
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_TEMPLATE = "{platform}/{author}/{title}.{ext}"

TEMPLATE_FIELDS: dict[str, str] = {
    "title": "Título do conteúdo",
    "author": "Autor / canal / perfil",
    "platform": "Plataforma (YouTube, TikTok...)",
    "date": "Data de publicação (AAAA-MM-DD)",
    "year": "Ano de publicação",
    "month": "Mês de publicação (01-12)",
    "id": "Identificador do conteúdo na plataforma",
    "index": "Posição na playlist/galeria (01, 02...)",
    "playlist": "Nome da playlist/coleção",
    "type": "Tipo (video, audio, imagem)",
    "quality": "Qualidade/formato escolhido",
    "ext": "Extensão do arquivo",
}

_FIELD_RE = re.compile(r"\{([a-z_]+)\}")
_ANY_BRACE_RE = re.compile(r"[{}]")

_REPLACEMENTS = {
    ":": " -",
    "/": "-",
    "\\": "-",
    "|": "-",
    "?": "",
    "*": "",
    '"': "'",
    "<": "",
    ">": "",
}
_WINDOWS_RESERVED = {
    "CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$",
    *(f"COM{i}" for i in range(10)),
    *(f"LPT{i}" for i in range(10)),
}

MAX_COMPONENT = 120
MAX_PATH = 250  # abaixo do MAX_PATH classico do Windows (260)


class TemplateError(ValueError):
    pass


def sanitize_component(value: object, max_len: int = MAX_COMPONENT, fallback: str = "sem_titulo") -> str:
    text = unicodedata.normalize("NFC", str(value or ""))
    chars = []
    for ch in text:
        if ch in _REPLACEMENTS:
            chars.append(_REPLACEMENTS[ch])
        elif unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Co", "Cn"):
            continue
        else:
            chars.append(ch)
    text = " ".join("".join(chars).split())
    text = text.strip(" .")
    if len(text) > max_len:
        text = text[:max_len].rstrip(" .")
    if not text:
        text = fallback
    if text.split(".")[0].upper() in _WINDOWS_RESERVED:
        text = f"_{text}"
    return text


def sanitize_extension(ext: object) -> str:
    clean = re.sub(r"[^a-z0-9]", "", str(ext or "").lower())[:10]
    return clean or "bin"


def validate_template(template: str) -> str:
    template = (template or "").strip()
    if not template:
        raise TemplateError("O template de nome não pode ficar vazio.")
    if len(template) > 300:
        raise TemplateError("O template de nome é longo demais.")
    if template.startswith(("/", "\\")) or re.match(r"^[A-Za-z]:", template):
        raise TemplateError("O template deve ser um caminho relativo (sem letra de unidade ou barra inicial).")
    segments = re.split(r"[\\/]", template)
    if any(seg.strip() in ("", ".", "..") for seg in segments):
        raise TemplateError("O template não pode ter pastas vazias, '.' ou '..'.")
    for name in _FIELD_RE.findall(template):
        if name not in TEMPLATE_FIELDS:
            raise TemplateError(f"Campo desconhecido no template: {{{name}}}")
    if _ANY_BRACE_RE.search(_FIELD_RE.sub("", template)):
        raise TemplateError("Chaves { } desbalanceadas no template.")
    last = segments[-1]
    stem = last[: -len(".{ext}")] if last.endswith(".{ext}") else last
    if not stem.replace("{ext}", "").strip():
        raise TemplateError("O template precisa gerar um nome de arquivo (ex.: {title}.{ext}).")
    return template


def format_date(upload_date: object = None, timestamp: object = None) -> str:
    """'20240131' / timestamp -> '2024-01-31'. Vazio se desconhecida."""
    if upload_date:
        digits = re.sub(r"\D", "", str(upload_date))
        if len(digits) >= 8:
            try:
                return datetime.strptime(digits[:8], "%Y%m%d").strftime("%Y-%m-%d")
            except ValueError:
                pass
    if timestamp:
        try:
            return datetime.fromtimestamp(float(timestamp), tz=timezone.utc).strftime("%Y-%m-%d")
        except (ValueError, OverflowError, OSError):
            pass
    return ""


def build_fields(**values: object) -> dict[str, str]:
    """Normaliza os valores dos campos; campos de data derivados de 'date'."""
    fields = {key: ("" if val is None else str(val)) for key, val in values.items()}
    date = fields.get("date", "")
    if date and len(date) >= 7:
        fields.setdefault("year", date[:4])
        fields.setdefault("month", date[5:7])
    return fields


_FALLBACKS = {
    "title": "sem_titulo",
    "author": "desconhecido",
    "platform": "Outros",
    "date": "sem_data",
    "year": "sem_data",
    "month": "sem_data",
    "id": "sem_id",
    "index": "00",
    "playlist": "sem_playlist",
    "type": "midia",
    "quality": "original",
}


def render_template(
    template: str, fields: dict[str, str], *, create_subfolders: bool = True, root_len: int = 0
) -> Path:
    """Devolve o caminho RELATIVO (pastas + nome.ext) para o arquivo.

    `root_len` e o tamanho do caminho da pasta de destino, para o total
    caber no limite de caminho do Windows."""
    template = validate_template(template)
    segments = re.split(r"[\\/]+", template)
    if not create_subfolders:
        segments = segments[-1:]

    ext = sanitize_extension(fields.get("ext"))

    def substitute(segment: str) -> str:
        def repl(match: re.Match[str]) -> str:
            name = match.group(1)
            value = fields.get(name) or _FALLBACKS.get(name, "")
            # o valor nunca pode criar pastas nem sair da pasta atual
            return sanitize_component(value, fallback=_FALLBACKS.get(name, "x"))

        return _FIELD_RE.sub(repl, segment)

    rendered: list[str] = []
    for i, segment in enumerate(segments):
        if i == len(segments) - 1:
            stem_template = segment[: -len(".{ext}")] if segment.endswith(".{ext}") else segment
            stem_template = stem_template.replace("{ext}", "")
            rendered.append(sanitize_component(substitute(stem_template)))
        else:
            rendered.append(sanitize_component(substitute(segment), fallback="pasta"))

    return fit_path(rendered, ext, root_len=root_len)


def fit_path(segments: list[str], ext: str, root_len: int = 0, limit: int = MAX_PATH) -> Path:
    """Encurta nome/pastas para o caminho total caber no limite do Windows."""
    segments = list(segments)

    def total() -> int:
        return root_len + sum(len(s) + 1 for s in segments) + len(ext) + 1

    minimum = 16
    # primeiro o nome do arquivo, depois as pastas (da mais funda para a raiz)
    for idx in [len(segments) - 1, *range(len(segments) - 2, -1, -1)]:
        excess = total() - limit
        if excess <= 0:
            break
        seg = segments[idx]
        new_len = max(minimum, len(seg) - excess)
        segments[idx] = seg[:new_len].rstrip(" .") or "x"
    *dirs, stem = segments
    return Path(*dirs, f"{stem}.{ext}")


def safe_join(root: Path, relative: Path) -> Path:
    """root/relative, garantindo que o resultado continue dentro de root."""
    if relative.is_absolute() or relative.drive:
        raise ValueError(f"Caminho relativo esperado: {relative}")
    root_resolved = root.resolve()
    candidate = (root_resolved / relative).resolve()
    if candidate != root_resolved and root_resolved not in candidate.parents:
        raise ValueError(f"Caminho fora da pasta de destino: {relative}")
    return candidate


def unique_path(path: Path) -> Path:
    """Nunca sobrescreve: 'nome.ext' -> 'nome (1).ext', 'nome (2).ext'..."""
    if not path.exists():
        return path
    n = 1
    while True:
        candidate = path.with_name(f"{path.stem} ({n}){path.suffix}")
        if not candidate.exists():
            return candidate
        n += 1
