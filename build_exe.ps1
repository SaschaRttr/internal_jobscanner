# Baut eine eigenständige Stellensuche.exe (inkl. Playwright), die Kollegen
# ohne eigene Python-Installation per Doppelklick starten können.
#
# Ausführen:
#   & "C:\Program Files\Miniforge\python.exe" -m pip install pyinstaller
#   powershell -File build_exe.ps1
#
# Ergebnis liegt danach in: dist\Stellensuche\Stellensuche.exe (Ordner-Build, siehe unten)
# Vor dem Weitergeben zusätzlich config.txt in denselben Ordner wie die .exe legen.
#
# Hinweis: Wir nutzen bewusst --onedir statt --onefile. Bei --onefile entpackt
# sich die exe bei jedem Start in %TEMP%\_MEIxxxxx und lädt python*.dll von dort.
# Viele Firmen-Gruppenrichtlinien (Software Restriction Policy/AppLocker)
# blockieren genau das ("This program is blocked by group policy" beim Laden
# der Python-DLL aus dem Temp-Ordner). Mit --onedir liegt die DLL direkt im
# Installationsordner und wird nicht aus Temp geladen.

$python = "C:\Program Files\Miniforge\python.exe"

& $python -m PyInstaller `
    --onedir `
    --console `
    --name Stellensuche `
    --collect-all playwright `
    --clean `
    stellensuche.py

Write-Host ""
Write-Host "Fertig. dist\Stellensuche\ enthaelt Stellensuche.exe + benoetigte DLLs/Dateien."
Write-Host "Bitte den kompletten Ordner 'dist\Stellensuche' weitergeben (nicht nur die exe)."
Write-Host "Bitte config.txt zusaetzlich in den Ordner 'dist\Stellensuche' kopieren."
