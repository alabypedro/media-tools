"""Renomeador em lote (tela Renomear / `umd rename`).

Monta os nomes novos a partir de um modelo ("{date:%Y-%m-%d} {name} {n:3}"),
de localizar/substituir (texto ou regex) e da data da foto (EXIF), mostra a
previa e so entao renomeia. A ultima operacao pode ser desfeita.

Independente do conversor (umd.convert): so reaproveita dele os erros
amigaveis e a limpeza de nomes de arquivo.

* engine.py  o plano (nome atual -> nome novo), a aplicacao e o desfazer
* cli.py     `umd rename`
"""

from __future__ import annotations
