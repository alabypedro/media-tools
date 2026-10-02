"""Editor de GIF, animacoes e video (tela Editor / `umd edit`).

Reune as ferramentas no estilo do ezgif.com: criar GIF a partir de imagens,
video -> GIF, redimensionar, cortar, girar, recortar o tempo, velocidade,
inverter, efeitos, texto, marca-d'agua, censurar, otimizar, dividir em
quadros, sprite sheet, GIF -> MP4 e juntar videos.

Independente do conversor (umd.convert): so reaproveita dele os erros
amigaveis, os caminhos seguros e a execucao cancelavel do FFmpeg.

* clip.py   animacao em memoria e operacoes quadro a quadro (Pillow)
* video.py  tudo que passa pelo FFmpeg (video -> video, video <-> quadros)
* tools.py  as ferramentas em si (o que a tela e a CLI chamam)
"""

from __future__ import annotations
