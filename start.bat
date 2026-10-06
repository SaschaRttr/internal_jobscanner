@echo off
cd /d "%~dp0"
if exist "packages" (
    set PYTHONPATH=%~dp0packages
)
"C:\Program Files\Miniforge3\python.exe" stellensuche.py
pause
