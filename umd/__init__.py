"""Universal Media Tools - baixa videos, audios, imagens e posts de redes
sociais e sites de midia a partir de uma URL, e converte arquivos
(umd.convert). O pacote continua se chamando `umd` (nome historico, do
tempo em que era o Universal Media Downloader).

Camadas (cada uma so conhece a de baixo):

    ui / cli  ->  services  ->  providers  ->  engine (worker)  ->  storage
"""

import os

APP_NAME = "Universal Media Tools"
APP_ID = "UniversalMediaTools"
# Nome anterior: instalacoes antigas continuam usando a pasta de dados e as
# preferencias da janela gravadas com ele (ver core/paths.py e ui/main_window.py).
LEGACY_APP_ID = "UniversalMediaDownloader"
__version__ = "1.2.1"  # unica fonte da versao: build.py, instalador e publish_release.py leem daqui

# Repositorio do GitHub ("dono/nome") cujos Releases trazem as atualizacoes do programa
# (core/app_update.py). A variavel UMD_UPDATE_REPO troca a origem (testes).
UPDATE_REPO = os.environ.get("UMD_UPDATE_REPO", "alabypedro/media-tools")
