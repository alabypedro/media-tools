@echo off
rem Abre o Universal Media Tools com um duplo-clique, usando o Python
rem ja instalado (nao precisa gerar o .exe).
setlocal
cd /d "%~dp0"

where pythonw >nul 2>nul
if errorlevel 1 goto no_python

rem roda sem janela de console; se falhar logo de cara (ex.: dependencia
rem faltando), reabre visivel para mostrar o motivo.
pythonw -m umd --gui
if errorlevel 1 (
    echo O Universal Media Tools fechou com um erro. Detalhes:
    echo.
    python -m umd --gui
    echo.
    pause
)
goto :eof

:no_python
echo Python nao foi encontrado no PATH desta maquina.
echo Instale o Python 3.10+ (https://www.python.org/downloads/) marcando
echo "Add python.exe to PATH" e rode, nesta pasta:
echo     pip install -r requirements.txt
pause
