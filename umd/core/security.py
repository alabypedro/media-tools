"""Tratamento de entrada nao confiavel: URLs coladas/importadas pelo
usuario e arquivos baixados da internet.

Nada aqui executa comandos: as URLs validadas seguem para as engines
dentro de um JSON (nunca como argumento de linha de comando), e os
arquivos baixados nunca sao abertos automaticamente.
"""

from __future__ import annotations

import ipaddress
import re
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

from .exceptions import ErrorCode, UMDError

MAX_URL_LENGTH = 4096

_URL_IN_TEXT = re.compile(r"""https?://[^\s<>"'`]+""", re.IGNORECASE)
# pontuacao que costuma grudar no fim de uma URL copiada de um texto
_TRAILING_PUNCT = ".,;:!?)]}>”’"
_DOMAIN_LIKE = re.compile(r"^(?:[\w-]+\.)+[a-z]{2,}(?::\d+)?(?:[/?#]|$)", re.IGNORECASE)

# Extensoes que o Windows (ou outro SO) executaria ao "abrir" o arquivo.
DANGEROUS_EXTENSIONS = frozenset(
    {
        "exe", "com", "bat", "cmd", "msi", "msp", "msix", "appx", "ps1", "psm1", "vbs", "vbe", "js", "jse",
        "wsf", "wsh", "scr", "pif", "lnk", "url", "reg", "hta", "cpl", "jar", "dll", "sys", "inf", "gadget",
        "application", "sh", "bash", "command", "app", "run", "bin", "deb", "rpm", "apk", "iso", "img", "vhd",
        "vhdx", "chm", "scf", "xll", "docm", "xlsm", "pptm",
    }
)


def _invalid(message: str) -> UMDError:
    return UMDError(ErrorCode.INVALID_URL, message, details=message, reasons=[])


def normalize_url(raw: str) -> str:
    """Limpa uma URL colada: espacos, aspas, <...> e esquema faltando."""
    url = (raw or "").strip().strip("\"'<>").strip()
    url = "".join(ch for ch in url if unicodedata.category(ch) != "Cf")  # zero-width etc.
    if url and "://" not in url and _DOMAIN_LIKE.match(url):
        url = "https://" + url
    return url


def _is_local_host(host: str) -> bool:
    host = host.strip("[]").lower()
    if host in {"localhost", "localhost.localdomain"} or host.endswith(".localhost") or host.endswith(".local"):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_reserved or ip.is_unspecified or ip.is_multicast


def validate_url(raw: str, *, allow_local: bool = False) -> str:
    """Valida e devolve a URL normalizada, ou levanta UMDError(INVALID_URL).

    Regras: so http/https, host obrigatorio, sem usuario/senha embutidos,
    sem caracteres de controle, tamanho limitado e (por padrao) sem
    enderecos da rede local.
    """
    url = normalize_url(raw)
    if not url:
        raise _invalid("Cole uma URL para analisar.")
    if len(url) > MAX_URL_LENGTH:
        raise _invalid("A URL é longa demais.")
    if any(ch.isspace() or unicodedata.category(ch) == "Cc" for ch in url):
        raise _invalid("A URL contém espaços ou caracteres inválidos.")

    try:
        parts = urlsplit(url)
        port = parts.port  # levanta ValueError se a porta for invalida
    except ValueError:
        raise _invalid("A URL informada não é válida.") from None

    if parts.scheme.lower() not in ("http", "https"):
        raise _invalid("Apenas links http:// e https:// são aceitos.")
    if parts.username or parts.password:
        raise _invalid("Links com usuário e senha embutidos não são aceitos. Use os cookies do navegador para conteúdo que exige login.")
    host = parts.hostname
    if not host:
        raise _invalid("A URL informada não tem um endereço de site válido.")
    try:
        host.encode("idna")
    except UnicodeError:
        raise _invalid("O endereço do site contém caracteres inválidos.") from None
    if "." not in host and not _looks_like_ip(host) and host != "localhost":
        raise _invalid("A URL informada não tem um endereço de site válido.")
    if not allow_local and _is_local_host(host):
        raise _invalid("Endereços da rede local não são aceitos (pode ser liberado em Configurações > Avançado).")
    if port is not None and not (0 < port < 65536):
        raise _invalid("A porta da URL é inválida.")

    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path, parts.query, parts.fragment))


def _looks_like_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host.strip("[]"))
        return True
    except ValueError:
        return False


def extract_urls(text: str) -> list[str]:
    """Todas as URLs http(s) de um texto livre, sem duplicatas, na ordem."""
    seen: set[str] = set()
    found: list[str] = []
    for match in _URL_IN_TEXT.finditer(text or ""):
        url = match.group(0).rstrip(_TRAILING_PUNCT)
        # parentese de fechamento faz parte da URL se houver um de abertura (Wikipedia)
        if match.group(0).endswith(")") and url.count("(") > url.count(")"):
            url += ")"
        if url not in seen:
            seen.add(url)
            found.append(url)
    return found


def is_dangerous_file(path: str | Path) -> bool:
    return Path(path).suffix.lower().lstrip(".") in DANGEROUS_EXTENSIONS


def host_of(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""
