@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if %errorlevel%==0 (
    python disponibilidad_hostnames_ad.py --tui
    goto fin
)

where py >nul 2>nul
if %errorlevel%==0 (
    py disponibilidad_hostnames_ad.py --tui
    goto fin
)

echo No se encontro Python instalado, ni "python" ni "py" en el PATH.
echo Instalalo desde https://www.python.org/downloads/ y volve a intentar.
echo Al instalar, asegurate de tildar "Add python.exe to PATH".

:fin
echo.
pause
