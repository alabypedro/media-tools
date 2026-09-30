"""Erros com mensagem amigavel para o usuario final.

A regra do projeto: nunca deixar um traceback tecnico (ModuleNotFoundError,
subprocess falhando, etc.) estourar direto pra CLI ou pra GUI. Todo erro
"esperado" (dependencia faltando, LibreOffice ausente, arquivo bloqueado...)
deve virar um ConversionError com uma mensagem clara e, quando fizer
sentido, uma dica de como resolver.
"""

from __future__ import annotations


class ConversionError(Exception):
    """Erro de conversao com mensagem pronta para mostrar ao usuario.

    `detail` guarda a excecao tecnica original (para o modo --debug / logs),
    mas nunca e mostrada por padrao na CLI ou na GUI.
    """

    def __init__(self, message: str, *, hint: str | None = None, detail: BaseException | None = None):
        self.message = message
        self.hint = hint
        self.detail = detail
        super().__init__(message)

    def user_message(self) -> str:
        if self.hint:
            return f"{self.message}\n{self.hint}"
        return self.message


def missing_dependency(pip_name: str, purpose: str, detail: BaseException | None = None) -> ConversionError:
    return ConversionError(
        f"Nao foi possivel converter este arquivo porque a biblioteca "
        f"necessaria para {purpose} nao esta instalada.",
        hint=f"Instale com: pip install {pip_name}",
        detail=detail,
    )


def libreoffice_missing() -> ConversionError:
    return ConversionError(
        "LibreOffice nao foi encontrado. Ele e necessario para converter "
        "documentos, planilhas e apresentacoes do/para PDF e outros formatos de escritorio.",
        hint=(
            "Instale com:  winget install TheDocumentFoundation.LibreOffice\n"
            "(ou baixe em https://www.libreoffice.org/download/)\n"
            "Para verificar se ja esta instalado, rode: soffice --version"
        ),
    )


def libreoffice_timeout(seconds: float) -> ConversionError:
    return ConversionError(
        f"O LibreOffice nao respondeu em {seconds:.0f} segundos e a conversao foi cancelada.",
        hint="Isso pode acontecer com arquivos muito grandes ou complexos. Tente novamente ou aumente o timeout.",
    )


def file_locked(path_name: str) -> ConversionError:
    return ConversionError(
        f"O arquivo '{path_name}' esta aberto em outro programa ou sem permissao de acesso.",
        hint="Feche o arquivo (ex.: no Word/Excel) e tente converter novamente.",
    )


def unsupported_conversion(source_ext: str, target_ext: str) -> ConversionError:
    return ConversionError(
        f"Nao sei converter de '.{source_ext}' para '.{target_ext}'.",
        hint="Use 'umd convert --formatos' ou veja os formatos sugeridos na tela Converter.",
    )


def path_not_found(path_name: str) -> ConversionError:
    return ConversionError(f"Caminho nao encontrado: {path_name}")
