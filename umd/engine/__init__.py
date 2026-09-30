"""Download Engine: roda yt-dlp / gallery-dl / download HTTP direto num
subprocesso isolado (worker), falando com o app por JSON lines.

Lado do app:     runner.py      (dispara o worker, le eventos, cancela)
Lado do worker:  worker.py + backends/
"""
