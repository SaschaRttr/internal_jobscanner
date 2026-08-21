# Stellensuche intern – Anleitung für Kollegen

Dieses Tool durchsucht automatisch das interne Bosch-Stellenportal und zeigt dir
nur die Stellen an, die zu deinen Suchbegriffen, Ausschlussbegriffen und
Wunschorten passen.

## Was du brauchst

- Die Datei `Stellensuche.exe`
- Die Datei `config.txt`
- Beide Dateien müssen **im selben Ordner** liegen (z. B. `C:\Stellensuche\`)

Keine Python-Installation, keine Terminal-Kenntnisse nötig.

## Erster Start

1. Doppelklick auf `Stellensuche.exe`.
2. Beim allerersten Start wird einmalig automatisch der Chromium-Browser
   heruntergeladen ("Chromium-Browser wird für Playwright installiert...").
   Das kann etwas dauern – bitte warten, bis "Chromium-Installation
   abgeschlossen." erscheint. Internetzugang/Firmen-Proxy wird benötigt.
3. Ein Chromium-Browserfenster öffnet sich automatisch. Dort bitte **wie
   gewohnt per SSO im Bosch-Portal einloggen**.
4. Danach im schwarzen Konsolenfenster einmal **Enter** drücken, damit die
   Suche startet.
5. Das Tool lädt die Stellen, filtert sie nach `config.txt` und zeigt am Ende
   an, wie viele Treffer gefunden wurden.
6. Am Ende bitte nochmal **Enter** drücken, um das Fenster zu schließen.

Bei künftigen Starts ist in der Regel **kein erneuter Login** nötig (die
Session wird lokal im Ordner `.browser_profile` gespeichert), solange sie
nicht abgelaufen ist.

## Ergebnisse ansehen

Nach jedem Lauf liegen im selben Ordner zwei neue/aktualisierte Dateien:

- `bosch_jobs_gefiltert.html` – **einfach per Doppelklick öffnen**, übersichtliche
  Ansicht aller Treffer im Browser (inkl. Checkbox "beworben" und Download-Button)
- `bosch_jobs_gefiltert.json` – dieselben Daten im JSON-Format (für Weiterverarbeitung)

## Eigene Filter anpassen (`config.txt`)

Die Datei `config.txt` mit einem Texteditor (z. B. Editor/Notepad) öffnen.
Sie hat drei Abschnitte:

```
[suchbegriffe]
Data Scientist
Software Engineer
...

[ausschlussbegriffe]
PreMaster
Praktikum
Werkstudent
...

[orte]
Stuttgart
Renningen
...
```

- **`[suchbegriffe]`**: Der Jobtitel muss mindestens einen dieser Begriffe enthalten.
- **`[ausschlussbegriffe]`**: Enthält der Jobtitel einen dieser Begriffe, wird die
  Stelle ausgeschlossen (auch wenn ein Suchbegriff passt).
- **`[orte]`**: Nur Stellen an diesen Orten werden angezeigt.

Änderungen speichern und `Stellensuche.exe` erneut starten – die neuen Filter
werden automatisch berücksichtigt. Ein manuelles Setzen von Filtern im
Browserfenster selbst hat **keinen** Einfluss auf die Ergebnisse.

## Optionale Startparameter

Die `.exe` kann auch über die Kommandozeile mit Zusatzoptionen gestartet werden
(für Doppelklick-Nutzung nicht nötig):

- `--max-jobs 20` – nur die 20 neuesten Stellen prüfen (Standard: alle)
- `--headless` – Browserfenster unsichtbar starten (nur sinnvoll, wenn bereits
  eine gültige Session vorhanden ist)

## Häufige Fragen

**"Chromium wurde installiert" kommt bei jedem Start erneut?**
Das sollte nur beim ersten Mal passieren. Falls nicht: Ordner
`%LOCALAPPDATA%\ms-playwright` prüfen, ob er existiert und beschreibbar ist.

**Login-Fenster erscheint jedes Mal neu?**
Der Ordner `.browser_profile` neben der `.exe` darf nicht gelöscht werden –
dort liegt die Session.

**Fehlermeldung "Konfigurationsdatei nicht gefunden"?**
`config.txt` fehlt im selben Ordner wie `Stellensuche.exe` – Datei dorthin
kopieren.
