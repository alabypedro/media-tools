"""Conversor de arquivos (antigo Universal Converter, incorporado ao app).

Converte video, audio, imagens, PDF, texto/Markdown, documentos do
Office (via LibreOffice) e arquivos compactados. Independente das
camadas de download: so usa o FFmpeg e a pasta de dados do aplicativo.

    core/        registro de formatos, fila, configuracoes, historico
    converters/  um modulo por familia de formato
    cli.py       `umd convert ...`
"""

from __future__ import annotations
