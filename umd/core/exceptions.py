"""Erros com mensagem compreensivel para o usuario comum.

Regra do projeto: nenhum traceback ou mensagem crua do yt-dlp/gallery-dl
chega direto na tela. Toda falha vira um `UMDError` com:

* `code`     categoria (ErrorCode), usada para decidir retry e dicas;
* `message`  texto curto e amigavel;
* `reasons`  possiveis motivos (lista mostrada em topicos);
* `details`  o log tecnico real, mostrado so em "Ver detalhes tecnicos".

`classify_error_text` traduz as mensagens tecnicas das engines numa
categoria. A ordem das regras importa: as mais especificas vem antes
(ex.: "HTTP Error 404" tem que virar NOT_FOUND, e nao erro de rede so
porque a mesma linha diz "Unable to download webpage").
"""

from __future__ import annotations

import re
from enum import Enum

from .i18n import tr


class ErrorCode(str, Enum):
    INVALID_URL = "invalid_url"
    UNSUPPORTED = "unsupported"
    NO_MEDIA = "no_media"
    NOT_FOUND = "not_found"
    PRIVATE = "private"
    AUTH_REQUIRED = "auth_required"
    ACCESS_DENIED = "access_denied"
    PAYWALL = "paywall"
    DRM = "drm"
    GEO_BLOCKED = "geo_blocked"
    AGE_RESTRICTED = "age_restricted"
    BOT_CHECK = "bot_check"
    COOKIES_ERROR = "cookies_error"
    LIVE_NOT_STARTED = "live_not_started"
    RATE_LIMITED = "rate_limited"
    NETWORK = "network"
    TIMEOUT = "timeout"
    FORMAT_UNAVAILABLE = "format_unavailable"
    FFMPEG_MISSING = "ffmpeg_missing"
    FFMPEG_FAILED = "ffmpeg_failed"
    DISK_FULL = "disk_full"
    PERMISSION = "permission"
    FILE_TOO_LARGE = "file_too_large"
    ENGINE_MISSING = "engine_missing"
    ENGINE_CRASH = "engine_crash"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"


RETRYABLE = {
    ErrorCode.NETWORK,
    ErrorCode.TIMEOUT,
    ErrorCode.RATE_LIMITED,
    ErrorCode.ENGINE_CRASH,
    ErrorCode.UNKNOWN,
}

# Erros que significam "esta engine nao sabe lidar com esta URL" -- o
# provider pode tentar a proxima engine da lista em vez de desistir.
FALLTHROUGH = {ErrorCode.UNSUPPORTED, ErrorCode.NO_MEDIA}


_ACCESS_REASONS = [
    "O conteúdo foi removido.",
    "A plataforma exige autenticação.",
    "O conteúdo está privado.",
    "A plataforma não disponibiliza este conteúdo para download.",
    "A URL está incorreta.",
]

# code -> (mensagem, motivos possiveis, dica)
_CATALOG: dict[ErrorCode, tuple[str, list[str], str | None]] = {
    ErrorCode.INVALID_URL: (
        "A URL informada não é válida.",
        ["Confira se o endereço começa com http:// ou https://.", "Verifique se a URL foi colada inteira."],
        None,
    ),
    ErrorCode.UNSUPPORTED: (
        "Esta plataforma/conteúdo não é suportado atualmente.",
        [
            "Nenhuma das engines instaladas (yt-dlp, gallery-dl) reconhece este site.",
            "A página não contém vídeo, áudio ou imagem detectável.",
            "A URL aponta para uma página que não é de mídia (ex.: página inicial).",
        ],
        "Atualizar as engines em Configurações > Engines pode adicionar suporte a novos sites.",
    ),
    ErrorCode.NO_MEDIA: (
        "Nenhuma mídia para download foi encontrada neste link.",
        ["O post pode conter apenas texto.", "A mídia pode ter sido removida.", "A plataforma pode exigir login para exibir a mídia."],
        None,
    ),
    ErrorCode.NOT_FOUND: (
        "Não foi possível acessar este conteúdo.",
        _ACCESS_REASONS,
        None,
    ),
    ErrorCode.PRIVATE: (
        "Este conteúdo é privado.",
        ["Apenas pessoas autorizadas pelo autor podem vê-lo.", "Se você tem acesso a ele, pode usar os cookies do seu navegador (Configurações > Contas e cookies)."],
        None,
    ),
    ErrorCode.AUTH_REQUIRED: (
        "A plataforma exige que você esteja logado para acessar este conteúdo.",
        [
            "Algumas plataformas (ex.: Instagram) só liberam o conteúdo para usuários logados.",
            "O limite de acessos anônimos pode ter sido atingido.",
        ],
        "Se você tem acesso a este conteúdo, ative o uso dos cookies do seu navegador em Configurações > Contas e cookies.",
    ),
    ErrorCode.ACCESS_DENIED: (
        "A plataforma recusou o acesso a este conteúdo.",
        [
            "O conteúdo pode exigir autenticação.",
            "O conteúdo pode estar bloqueado na sua região.",
            "A plataforma pode estar bloqueando downloads automatizados.",
        ],
        "Tente novamente mais tarde ou atualize as engines em Configurações > Engines.",
    ),
    ErrorCode.PAYWALL: (
        "Este conteúdo é pago ou exclusivo para assinantes.",
        ["O download só é possível para quem tem acesso legítimo ao conteúdo."],
        "Se você é assinante, pode usar os cookies do seu navegador (Configurações > Contas e cookies).",
    ),
    ErrorCode.DRM: (
        "Este conteúdo é protegido por DRM e não pode ser baixado.",
        ["A plataforma protege este conteúdo contra cópia. O aplicativo não contorna esse tipo de proteção."],
        None,
    ),
    ErrorCode.GEO_BLOCKED: (
        "Este conteúdo não está disponível na sua região.",
        ["A plataforma restringe este conteúdo por localização."],
        None,
    ),
    ErrorCode.AGE_RESTRICTED: (
        "Este conteúdo tem restrição de idade.",
        ["A plataforma exige uma conta com idade verificada para exibi-lo."],
        "Se sua conta tem acesso, use os cookies do seu navegador (Configurações > Contas e cookies).",
    ),
    ErrorCode.BOT_CHECK: (
        "A plataforma pediu uma verificação de que você não é um robô.",
        ["Muitos acessos seguidos a partir da sua rede.", "A plataforma pode estar limitando acessos anônimos."],
        "Aguarde alguns minutos, ou use os cookies do seu navegador logado (Configurações > Contas e cookies).",
    ),
    ErrorCode.COOKIES_ERROR: (
        "Não foi possível ler os cookies do navegador configurado.",
        [
            "O navegador pode estar aberto e bloqueando o banco de cookies (comum no Chrome/Edge).",
            "O perfil do navegador informado pode não existir.",
            "O arquivo de cookies pode não estar no formato Netscape (cookies.txt).",
        ],
        "Feche o navegador e tente de novo, use o Firefox, ou exporte um arquivo cookies.txt.",
    ),
    ErrorCode.LIVE_NOT_STARTED: (
        "Esta transmissão ao vivo ainda não começou.",
        ["Tente novamente quando a transmissão estiver no ar."],
        None,
    ),
    ErrorCode.RATE_LIMITED: (
        "A plataforma limitou temporariamente o número de acessos.",
        ["Muitas requisições em pouco tempo."],
        "Aguarde alguns minutos. Aumentar o intervalo entre downloads (Configurações > Desempenho) ajuda.",
    ),
    ErrorCode.NETWORK: (
        "Houve um problema de conexão com a internet ou com o site.",
        ["Sua conexão pode ter caído.", "O site pode estar fora do ar.", "Um firewall ou proxy pode estar bloqueando o acesso."],
        None,
    ),
    ErrorCode.TIMEOUT: (
        "O site demorou demais para responder.",
        ["Conexão lenta ou instável.", "O site pode estar sobrecarregado."],
        "Tente novamente. Se persistir, aumente o tempo limite em Configurações > Desempenho.",
    ),
    ErrorCode.FORMAT_UNAVAILABLE: (
        "A qualidade/formato escolhido não está disponível para este conteúdo.",
        ["A plataforma pode ter mudado os formatos oferecidos desde a análise."],
        "Analise a URL de novo e escolha outra qualidade.",
    ),
    ErrorCode.FFMPEG_MISSING: (
        "O FFmpeg não foi encontrado.",
        ["Ele é necessário para juntar áudio e vídeo e para converter formatos."],
        "Configure o caminho do FFmpeg em Configurações > Engines.",
    ),
    ErrorCode.FFMPEG_FAILED: (
        "Falha ao processar o arquivo com o FFmpeg.",
        ["O formato de destino pode não ser compatível com os codecs da mídia.", "O arquivo baixado pode estar incompleto."],
        "Tente outro formato de saída, ou escolha 'Original' para evitar conversão.",
    ),
    ErrorCode.DISK_FULL: (
        "Não há espaço livre suficiente no disco.",
        ["Libere espaço ou escolha outra pasta de destino."],
        None,
    ),
    ErrorCode.PERMISSION: (
        "Sem permissão para gravar na pasta de destino.",
        ["A pasta pode ser protegida pelo sistema.", "O arquivo pode estar aberto em outro programa."],
        "Escolha outra pasta de destino.",
    ),
    ErrorCode.FILE_TOO_LARGE: (
        "O arquivo é maior que o limite de tamanho configurado.",
        [],
        "Ajuste o limite em Configurações > Desempenho.",
    ),
    ErrorCode.ENGINE_MISSING: (
        "Uma engine de download necessária não está instalada.",
        [],
        "Reinstale o aplicativo ou verifique Configurações > Engines.",
    ),
    ErrorCode.ENGINE_CRASH: (
        "A engine de download parou inesperadamente.",
        ["Pode ser uma falha temporária.", "A plataforma pode ter mudado e a engine precisa de atualização."],
        "Tente novamente. Se persistir, verifique atualizações em Configurações > Engines.",
    ),
    ErrorCode.CANCELLED: (
        "Operação cancelada.",
        [],
        None,
    ),
    ErrorCode.UNKNOWN: (
        "Não foi possível concluir a operação.",
        _ACCESS_REASONS,
        None,
    ),
}


class UMDError(Exception):
    def __init__(
        self,
        code: ErrorCode | str = ErrorCode.UNKNOWN,
        message: str | None = None,
        *,
        details: str = "",
        hint: str | None = None,
        reasons: list[str] | None = None,
    ):
        self.code = ErrorCode(code)
        base_message, base_reasons, base_hint = _CATALOG[self.code]
        self.message = message or base_message
        self.reasons = reasons if reasons is not None else base_reasons
        self.hint = hint if hint is not None else base_hint
        self.details = details
        super().__init__(self.message)

    @property
    def retryable(self) -> bool:
        return self.code in RETRYABLE

    @property
    def fallthrough(self) -> bool:
        return self.code in FALLTHROUGH

    def user_message(self) -> str:
        """Texto completo para o usuario (mensagem + motivos + dica), traduzido."""
        parts = [tr(self.message)]
        if self.reasons:
            parts.append("")
            parts.append(tr("Possíveis motivos:"))
            parts.extend(f"• {tr(r)}" for r in self.reasons)
        if self.hint:
            parts.append("")
            parts.append(tr(self.hint))
        return "\n".join(parts)

    def to_dict(self) -> dict:
        return {"code": self.code.value, "message": self.message, "details": self.details, "hint": self.hint}

    @classmethod
    def from_dict(cls, data: dict) -> "UMDError":
        try:
            code = ErrorCode(data.get("code", "unknown"))
        except ValueError:
            code = ErrorCode.UNKNOWN
        # a mensagem que vem do worker e tecnica; usamos a do catalogo,
        # a nao ser que o worker mande uma mensagem ja amigavel (hint).
        return cls(code, details=str(data.get("details") or data.get("message") or ""), hint=data.get("hint"))

    def __repr__(self) -> str:
        return f"UMDError({self.code.value!r}, {self.message!r})"


_RULES: list[tuple[ErrorCode, re.Pattern[str]]] = [
    (ErrorCode.DRM, r"\bDRM\b|widevine|playready|fairplay"),
    (ErrorCode.BOT_CHECK, r"confirm you.?re not a bot|not a robot|captcha"),
    (ErrorCode.COOKIES_ERROR, r"cookie(s)? database|decrypt with dpapi|could not (find|copy|load|decrypt).{0,40}cookie|"
                              r"netscape format cookies|unsupported browser|failed to (load|read) cookies|cookies? file"),
    (ErrorCode.FFMPEG_MISSING, r"ffmpeg (is )?not (found|installed)|ffprobe and ffmpeg not found|ffmpeg could not be found|"
                               r"ffmpeg.{0,20}(is )?required"),
    (ErrorCode.AGE_RESTRICTED, r"age.?restrict|confirm your age|inappropriate for some users|age verification|age-gated"),
    (ErrorCode.PAYWALL, r"members.only|join this channel|only available (for|to) (subscribers|members|premium)|"
                        r"requires (a )?(payment|purchase|subscription)|paid (content|video)|rental|premium (content|members)"),
    (ErrorCode.PRIVATE, r"private video|video is private|is a private|account is private|private account|"
                        r"protected tweets|this (post|account) is private"),
    (ErrorCode.GEO_BLOCKED, r"not available in your (country|region)|geo.?restrict|not available from your location|"
                            r"blocked in your (country|region)|uploader has not made this video available in your"),
    (ErrorCode.LIVE_NOT_STARTED, r"live event will begin|premieres in|stream has not started|is an upcoming|"
                                 r"this live (event|stream) (will|has not)"),
    (ErrorCode.AUTH_REQUIRED, r"login required|log ?in (is )?required|sign in to (view|see|watch)|requires? (authentication|login|an account|sign.?in)|"
                              r"authorizationerror|authrequired|\b401\b|unauthorized|--cookies|cookies-from-browser|"
                              r"use cookies|you need to log in|account (is )?needed|not logged in"),
    (ErrorCode.NOT_FOUND, r"video unavailable|has been removed|no longer available|does not exist|\b404\b|not found|"
                          r"notfounderror|content isn.?t available|post (is )?unavailable|account (has been )?suspended|"
                          r"deleted|this (video|page|tweet|post) (is )?(unavailable|not available)"),
    (ErrorCode.ACCESS_DENIED, r"\b403\b|forbidden"),
    (ErrorCode.RATE_LIMITED, r"\b429\b|too many requests|rate.?limit"),
    (ErrorCode.FORMAT_UNAVAILABLE, r"requested format (is )?not available|format is not available"),
    (ErrorCode.NO_MEDIA, r"no video could be found|no media (found|in)|there.?s no video|no video formats found|"
                         r"does not contain (a |any )?(video|media)|no videos? (in|found)|no results for|nothing to download|"
                         r"no (image|file)s? found"),
    (ErrorCode.UNSUPPORTED, r"unsupported url|noextractorerror|no suitable extractor|not a valid url|unsupported (site|platform)"),
    (ErrorCode.DISK_FULL, r"no space left|errno 28|disk (is )?full|enospc|not enough space|winerror 112"),
    (ErrorCode.PERMISSION, r"permission denied|errno 13|access is denied|winerror 5\b|acesso negado"),
    (ErrorCode.FILE_TOO_LARGE, r"larger than max-filesize|file is larger than|filesize-max|exceeds the (maximum|size limit)"),
    (ErrorCode.FFMPEG_FAILED, r"postprocessing|conversion failed|ffmpeg exited|error opening output|invalid data found when processing"),
    (ErrorCode.TIMEOUT, r"timed? ?out|timeout"),
    (ErrorCode.NETWORK, r"connection (reset|refused|aborted|error)|getaddrinfo failed|name or service not known|"
                        r"temporary failure in name resolution|network is unreachable|failed to resolve|ssl|certificate|"
                        r"remote end closed|urlopen error|unable to download|http error 5\d\d|\b50[234]\b|incomplete ?read|"
                        r"connectionerror|max retries exceeded|nameresolution|winerror 10054|winerror 10060"),
]
_COMPILED = [(code, re.compile(pattern, re.IGNORECASE)) for code, pattern in _RULES]


def classify_error_text(text: str) -> ErrorCode:
    if not text:
        return ErrorCode.UNKNOWN
    for code, pattern in _COMPILED:
        if pattern.search(text):
            return code
    return ErrorCode.UNKNOWN


def error_from_text(text: str, details: str | None = None) -> UMDError:
    return UMDError(classify_error_text(text), details=details if details is not None else text)


def to_user_error(exc: BaseException) -> UMDError:
    """Converte qualquer excecao numa UMDError (sem vazar traceback para a tela)."""
    if isinstance(exc, UMDError):
        return exc
    if isinstance(exc, PermissionError):
        return UMDError(ErrorCode.PERMISSION, details=repr(exc))
    if isinstance(exc, OSError) and getattr(exc, "errno", None) == 28:
        return UMDError(ErrorCode.DISK_FULL, details=repr(exc))
    text = f"{type(exc).__name__}: {exc}"
    return UMDError(classify_error_text(text), details=text)
