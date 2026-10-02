"""Registro central de formatos e regras de conversao.

Objetivo (item 23 do pedido de evolucao): adicionar um formato novo deve
ser uma alteracao pequena e localizada. Para adicionar HEIC, por exemplo,
basta acrescentar "heic" ao conjunto IMAGE.extensions abaixo -- nenhum
outro arquivo precisa mudar.

Cada `ConversionRule` diz: "arquivos com uma destas extensoes de origem
podem virar uma destas extensoes de destino, usando este conversor".
`resolve_handler()` percorre as regras em ordem e devolve a primeira que
bate -- por isso regras mais especificas (ex.: txt -> pdf via reportlab)
vem antes de regras mais genericas (ex.: office, que tambem aceita
txt -> docx/odt via LibreOffice).
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Category:
    key: str
    label: str
    extensions: frozenset[str]


VIDEO = Category("video", "Video", frozenset({
    "mp4", "mkv", "avi", "mov", "webm", "flv", "wmv", "m4v",
    "mpg", "mpeg", "3gp", "ts",
}))
AUDIO = Category("audio", "Audio", frozenset({
    "mp3", "wav", "flac", "aac", "ogg", "m4a", "wma", "opus", "aiff",
}))
IMAGE = Category("image", "Imagem", frozenset({
    "jpg", "jpeg", "png", "bmp", "gif", "webp", "tiff", "tif", "ico",
    "avif", "heic", "heif",
}))
DOCUMENT = Category("document", "Documento", frozenset({"doc", "docx", "odt", "rtf"}))
SPREADSHEET = Category("spreadsheet", "Planilha", frozenset({"xls", "xlsx", "ods", "csv"}))
PRESENTATION = Category("presentation", "Apresentacao", frozenset({"ppt", "pptx", "odp"}))
TEXT = Category("text", "Texto", frozenset({"txt"}))
MARKDOWN = Category("markdown", "Markdown", frozenset({"md", "markdown"}))
PDF = Category("pdf", "PDF", frozenset({"pdf"}))
ARCHIVE = Category("archive", "Compactado", frozenset({
    "zip", "7z", "tar", "tar.gz", "tgz", "tar.bz2", "tbz2", "tar.xz", "txz",
}))

ALL_CATEGORIES: tuple[Category, ...] = (
    VIDEO, AUDIO, IMAGE, DOCUMENT, SPREADSHEET, PRESENTATION, TEXT, MARKDOWN, PDF, ARCHIVE,
)

# Mapa extensao (simples, sem ponto, minuscula) -> categoria. Extensoes de
# arquivo compactado com ponto composto (tar.gz...) nao entram aqui porque
# nao dao pra detectar so pelo Path.suffix; ver converters/archive.py.
EXTENSION_TO_CATEGORY: dict[str, Category] = {
    ext: cat
    for cat in ALL_CATEGORIES
    for ext in cat.extensions
    if "." not in ext
}


# "Formato" especial: PDF -> o mesmo PDF achatado (formularios e anotacoes
# viram conteudo fixo). O arquivo gerado e "<nome> (achatado).pdf".
FLATTEN_TARGET = "flatten"
FLATTEN_SUFFIX = " (achatado)"


@dataclass(frozen=True)
class ConversionRule:
    source_exts: frozenset[str]
    target_exts: frozenset[str]
    handler: str
    label: str


# Ordem importa: a primeira regra cujo (origem, destino) bate e usada.
RULES: tuple[ConversionRule, ...] = (
    ConversionRule(PDF.extensions, frozenset({"png", "jpg", "jpeg", "webp"}), "pdf_to_image",
                   "PDF -> imagem (por pagina)"),
    ConversionRule(PDF.extensions, frozenset({FLATTEN_TARGET}), "pdf_flatten",
                   "PDF -> PDF achatado (formularios e anotacoes viram conteudo fixo)"),
    # imagem -> pdf de UM arquivo usa o mesmo conversor Pillow de imagem
    # (uma imagem = uma pagina). Juntar VARIAS imagens em um so PDF e uma
    # operacao separada (converters/image_to_pdf.py), com sua propria tela
    # na GUI/subcomando na CLI -- nao se encaixa no modelo 1 arquivo -> 1
    # arquivo da fila padrao.
    ConversionRule(IMAGE.extensions, frozenset({"pdf"}), "image",
                   "Imagem -> PDF"),
    ConversionRule(TEXT.extensions, frozenset({"pdf"}), "text_to_pdf",
                   "Texto -> PDF"),
    ConversionRule(MARKDOWN.extensions, frozenset({"pdf"}), "markdown_to_pdf",
                   "Markdown -> PDF"),
    # video pode virar audio (extrai a trilha sonora, ffmpeg -vn) ou outro
    # video; audio so pode virar outro audio (nao existe "video" a extrair
    # de um mp3 para gerar um mp4 com sentido).
    ConversionRule(VIDEO.extensions, VIDEO.extensions | AUDIO.extensions, "media",
                   "Video -> Video/Audio (ffmpeg)"),
    ConversionRule(AUDIO.extensions, AUDIO.extensions, "media",
                   "Audio -> Audio (ffmpeg)"),
    ConversionRule(IMAGE.extensions, IMAGE.extensions, "image",
                   "Imagem -> Imagem"),
    ConversionRule(set(ARCHIVE.extensions), set(ARCHIVE.extensions), "archive",
                   "Compactados"),
    ConversionRule(
        DOCUMENT.extensions | SPREADSHEET.extensions | PRESENTATION.extensions | TEXT.extensions | PDF.extensions,
        DOCUMENT.extensions | SPREADSHEET.extensions | PRESENTATION.extensions | TEXT.extensions | PDF.extensions,
        "office",
        "Documentos/Planilhas/Apresentacoes (LibreOffice)",
    ),
)


def normalize_ext(ext_or_name: str) -> str:
    ext = ext_or_name.lower().lstrip(".")
    return ext


def resolve_handler(source_ext: str, target_ext: str) -> str | None:
    source_ext = normalize_ext(source_ext)
    target_ext = normalize_ext(target_ext)
    for rule in RULES:
        if source_ext in rule.source_exts and target_ext in rule.target_exts:
            return rule.handler
    return None


def compatible_targets(source_ext: str) -> list[str]:
    """Formatos de destino com sentido para essa extensao de origem,
    usados para preencher o combo de "Formato de saida" na GUI (item 22:
    formato de destino inteligente)."""
    source_ext = normalize_ext(source_ext)
    targets: set[str] = set()
    for rule in RULES:
        if source_ext in rule.source_exts:
            targets.update(rule.target_exts)
    targets.discard(source_ext)
    return sorted(targets)


def category_of(ext_or_name: str) -> Category | None:
    ext = normalize_ext(ext_or_name)
    return EXTENSION_TO_CATEGORY.get(ext)


def describe_supported_formats() -> str:
    lines = []
    for cat in ALL_CATEGORIES:
        exts = ", ".join(sorted(cat.extensions))
        lines.append(f"{cat.label}: {exts}")
    lines.append(f"PDF achatado: destino '{FLATTEN_TARGET}' (formularios e anotacoes viram conteudo fixo)")
    return "\n".join(lines)
