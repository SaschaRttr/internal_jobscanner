# Baut eine eigenständige Stellensuche.exe (inkl. Playwright), die Kollegen
# ohne eigene Python-Installation per Doppelklick starten können.
#
# Ausführen:
#   & "C:\Program Files\Miniforge\python.exe" -m pip install pyinstaller
#   powershell -File build_exe.ps1
#
# Ergebnis liegt danach in: dist\Stellensuche.exe
# Vor dem Weitergeben zusätzlich config.txt in denselben Ordner wie die .exe legen.

$python = "C:\Program Files\Miniforge\python.exe"

& $python -m PyInstaller `
    --onefile `
    --console `
    --name Stellensuche `
    --collect-all playwright `
    --clean `
    stellensuche.py

Write-Host ""
Write-Host "Fertig. dist\Stellensuche.exe erzeugt."
Write-Host "Bitte config.txt in den 'dist'-Ordner kopieren, bevor die .exe weitergegeben wird."
