@echo off
rem Entorno de pruebas de PST (sin release). Ver manual_docs\ENTORNO_PRUEBAS.md
cd /d "%~dp0..\.."
python Tools\dev\arrancar_dev.py --web %*
pause
