"""Markdown -> PDF (item 11).

Fluxo: `markdown` (biblioteca leve, so Python) converte o texto para
HTML; um parser proprio, em cima do `html.parser` da stdlib, transforma
esse HTML em flowables do reportlab (paragrafos, listas, tabelas, bloco
de codigo, citacao, linha horizontal). Isso evita puxar uma dependencia
pesada so para "HTML -> PDF" (avaliamos xhtml2pdf e ele traz ~15
pacotes transitivos, incluindo cryptography/aiohttp, que nao usamos
para nada aqui).
"""

from __future__ import annotations

from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape as xml_escape

from ..core.errors import ConversionError, missing_dependency

_INLINE_OPEN = {
    "strong": "<b>", "b": "<b>",
    "em": "<i>", "i": "<i>",
    "code": '<font face="Courier" size="9">',
    "u": "<u>",
    "del": "<strike>", "s": "<strike>",
}
_INLINE_CLOSE = {
    "strong": "</b>", "b": "</b>",
    "em": "</i>", "i": "</i>",
    "code": "</font>",
    "u": "</u>",
    "del": "</strike>", "s": "</strike>",
}


class _Block:
    __slots__ = ("kind", "text", "level", "ordered", "items", "rows", "cur_row")

    def __init__(self, kind: str, level: int = 0, ordered: bool = False):
        self.kind = kind
        self.text = ""
        self.level = level
        self.ordered = ordered
        self.items: list[str] = []
        self.rows: list[list[str]] = []
        self.cur_row: list[str] | None = None


class _MarkdownHTMLParser(HTMLParser):
    """Converte o HTML gerado pelo `markdown` numa lista de flowables
    reportlab. So entende o subconjunto de tags que o `markdown` com as
    extensoes usadas aqui realmente produz -- nao e um parser de HTML
    generico."""

    _HEADING_TAGS = {f"h{i}" for i in range(1, 7)}

    def __init__(self, styles):
        super().__init__(convert_charrefs=True)
        self.styles = styles
        self.flowables: list = []
        self.stack: list[_Block] = []
        self.in_pre = False
        self.pre_text = ""

    # -- ajuda -----------------------------------------------------------
    def _current(self) -> _Block | None:
        return self.stack[-1] if self.stack else None

    def _append_inline(self, chunk: str) -> None:
        block = self._current()
        if block is None:
            return
        if block.kind == "table":
            if block.cur_row:  # dentro de um <td>/<th> ja aberto
                block.cur_row[-1] += chunk
            # texto solto entre <table>/<tr>/<td> (espacos/quebras de linha
            # de indentacao do HTML) e ignorado de proposito
        else:
            block.text += chunk

    # -- eventos do parser -------------------------------------------------
    def handle_starttag(self, tag: str, attrs) -> None:
        attrs_dict = dict(attrs)
        if self.in_pre:
            return

        if tag == "pre":
            self.in_pre = True
            self.pre_text = ""
            return
        if tag == "hr":
            self._flush_paragraph_flowable()
            from reportlab.platypus import HRFlowable
            self.flowables.append(HRFlowable(width="100%", thickness=1, color="#bbbbbb", spaceBefore=6, spaceAfter=6))
            return
        if tag == "br":
            self._append_inline("<br/>")
            return
        if tag in self._HEADING_TAGS:
            self.stack.append(_Block("heading", level=int(tag[1])))
            return
        if tag == "p":
            self.stack.append(_Block("paragraph"))
            return
        if tag == "blockquote":
            self.stack.append(_Block("blockquote"))
            return
        if tag in ("ul", "ol"):
            self.stack.append(_Block("list", ordered=(tag == "ol")))
            return
        if tag == "li":
            self.stack.append(_Block("listitem"))
            return
        if tag == "table":
            self.stack.append(_Block("table"))
            return
        if tag == "tr":
            block = self._current()
            if block is not None and block.kind == "table":
                block.cur_row = []
            return
        if tag in ("td", "th"):
            block = self._current()
            if block is not None and block.kind == "table" and block.cur_row is not None:
                block.cur_row.append("")
            return
        if tag == "a":
            href = attrs_dict.get("href", "")
            self._append_inline(f'<a href="{xml_escape(href)}" color="blue">')
            return
        if tag in _INLINE_OPEN:
            self._append_inline(_INLINE_OPEN[tag])

    def handle_startendtag(self, tag: str, attrs) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        if tag == "pre":
            self.in_pre = False
            from reportlab.platypus import Preformatted
            code_style = self.styles["Code"]
            self.flowables.append(Preformatted(self.pre_text.rstrip("\n"), code_style))
            return
        if self.in_pre:
            return

        if tag == "a":
            self._append_inline("</a>")
            return
        if tag in _INLINE_CLOSE:
            self._append_inline(_INLINE_CLOSE[tag])
            return

        if tag in self._HEADING_TAGS and self._current() and self._current().kind == "heading":
            block = self.stack.pop()
            from reportlab.platypus import Paragraph
            level = min(block.level, 4)
            self.flowables.append(Paragraph(block.text, self.styles[f"Heading{level}"]))
            return

        if tag == "p" and self._current() and self._current().kind == "paragraph":
            block = self.stack.pop()
            self._emit_paragraph(block.text, self.styles["BodyText"])
            return

        if tag == "blockquote" and self._current() and self._current().kind == "blockquote":
            block = self.stack.pop()
            self._emit_paragraph(block.text, self.styles["Blockquote"])
            return

        if tag == "li" and self._current() and self._current().kind == "listitem":
            item = self.stack.pop()
            parent = self._current()
            if parent is not None and parent.kind == "list":
                parent.items.append(item.text)
            return

        if tag in ("ul", "ol") and self._current() and self._current().kind == "list":
            block = self.stack.pop()
            self._emit_list(block)
            return

        if tag == "tr" and self._current() and self._current().kind == "table":
            block = self._current()
            if block.cur_row is not None:
                block.rows.append(block.cur_row)
                block.cur_row = None
            return

        if tag == "table" and self._current() and self._current().kind == "table":
            block = self.stack.pop()
            self._emit_table(block)
            return

    def handle_data(self, data: str) -> None:
        if self.in_pre:
            self.pre_text += data
            return
        self._append_inline(xml_escape(data))

    # -- emissao de flowables ----------------------------------------------
    def _flush_paragraph_flowable(self) -> None:
        return  # reservado (hoje nao ha buffer pendente fora de blocos)

    def _emit_paragraph(self, text: str, style) -> None:
        from reportlab.platypus import Paragraph, Spacer
        text = text.strip()
        if not text:
            return
        self.flowables.append(Paragraph(text, style))
        self.flowables.append(Spacer(1, 4))

    def _emit_list(self, block: _Block) -> None:
        from reportlab.platypus import ListFlowable, ListItem, Paragraph, Spacer
        if not block.items:
            return
        items = [ListItem(Paragraph(text.strip() or "&nbsp;", self.styles["BodyText"])) for text in block.items]
        bullet_type = "1" if block.ordered else "bullet"
        self.flowables.append(ListFlowable(items, bulletType=bullet_type, leftIndent=18))
        self.flowables.append(Spacer(1, 4))

    def _emit_table(self, block: _Block) -> None:
        from reportlab.lib import colors
        from reportlab.platypus import Paragraph, Spacer, Table, TableStyle
        if not block.rows:
            return
        width = max(len(r) for r in block.rows)
        rows = [r + [""] * (width - len(r)) for r in block.rows]
        table_data = [[Paragraph(cell.strip(), self.styles["BodyText"]) for cell in row] for row in rows]
        table = Table(table_data, hAlign="LEFT")
        table.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#bbbbbb")),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eeeeee")),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
            ("LEFTPADDING", (0, 0), (-1, -1), 6),
            ("RIGHTPADDING", (0, 0), (-1, -1), 6),
        ]))
        self.flowables.append(table)
        self.flowables.append(Spacer(1, 8))


def _build_styles():
    from reportlab.lib import colors
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

    # getSampleStyleSheet() ja vem com Normal/BodyText/Heading1-6/Code
    # prontos; so ajustamos a aparencia deles em vez de tentar
    # re-adiciona-los (styles.add() com um nome ja existente da erro).
    styles = getSampleStyleSheet()
    styles["Heading1"].textColor = colors.HexColor("#1a1a2e")
    styles["Heading1"].spaceAfter = 12
    styles["Heading2"].textColor = colors.HexColor("#1a1a2e")
    styles["Heading2"].spaceBefore = 10
    styles["Heading3"].spaceBefore = 8
    styles["Heading4"].spaceBefore = 6

    code_style = styles["Code"]
    code_style.backColor = colors.HexColor("#f4f4f4")
    code_style.borderPadding = 6
    code_style.leading = 11

    styles.add(ParagraphStyle(
        "Blockquote", parent=styles["BodyText"], leftIndent=18, textColor=colors.HexColor("#555555"),
        backColor=colors.HexColor("#f7f7f7"), spaceBefore=4, spaceAfter=4,
    ))
    return styles


def convert(
    input_path: Path,
    output_path: Path,
    options: dict[str, Any] | None = None,
) -> Path:
    """options:
        page_size: "a4" | "letter"
        margin_mm: float
    """
    options = options or {}
    try:
        import markdown as markdown_lib
    except ModuleNotFoundError as exc:
        raise missing_dependency("markdown", "interpretar Markdown", exc) from exc
    try:
        from reportlab.lib.pagesizes import A4, LETTER
        from reportlab.lib.units import mm
        from reportlab.platypus import SimpleDocTemplate
    except ModuleNotFoundError as exc:
        raise missing_dependency("reportlab", "gerar PDF", exc) from exc

    try:
        source = input_path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        source = input_path.read_text(encoding="latin-1")
    except OSError as exc:
        raise ConversionError(f"Nao foi possivel ler '{input_path.name}'.", detail=exc) from exc

    html = markdown_lib.markdown(
        source, extensions=["extra", "sane_lists", "tables", "fenced_code"]
    )

    styles = _build_styles()
    parser = _MarkdownHTMLParser(styles)
    parser.feed(html)
    parser.close()

    page_size = A4 if options.get("page_size", "a4").lower() == "a4" else LETTER
    margin = float(options.get("margin_mm", 20)) * mm

    output_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(output_path), pagesize=page_size,
        leftMargin=margin, rightMargin=margin, topMargin=margin, bottomMargin=margin,
        title=input_path.stem,
    )
    if not parser.flowables:
        from reportlab.platypus import Paragraph
        parser.flowables = [Paragraph("(documento vazio)", styles["BodyText"])]
    doc.build(parser.flowables)
    return output_path
