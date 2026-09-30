"""Conversores, um modulo por familia de formato.

Cada conversor expoe uma funcao `convert(...)` com uma assinatura
proxima o suficiente para o `core.jobs` conseguir despachar de forma
generica, mas sem forcar uma interface artificial onde nao faz sentido
(ex.: PDF -> imagens produz varios arquivos; imagens -> PDF junta varios
arquivos em um so).
"""

from __future__ import annotations
