"""
Alle Stellen in Deutschland laden (ungefiltert)
=================================================

Lädt - genau wie `stellensuche.py` - echte Stellenausschreibungen aus dem
internen Bosch-Portal (SmartRecruiters Employee Portal, Login per SSO nötig),
aber OHNE die Filterung über Suchbegriffe/Ausschlussbegriffe/Orte aus
config.txt: Es werden einfach ALLE Stellen mit "Working Country: Germany"
geladen und als JSON gespeichert.

Anders als `stellensuche.py` wird dabei NUR die Trefferliste der Such-API
geladen (Jobtitel, Ort, Referenznummer, uuid, ...) - OHNE für jede Stelle
zusätzlich die Detailseite mit dem vollständigen Stellentext/der
Beschreibung abzurufen. Das macht das Laden deutlich schneller (ein
paginierter API-Aufruf statt eines Details-Aufrufs pro Stelle), liefert aber
auch keinen "stellentext" im Ergebnis.

Gedacht als Quelle für realistische Testdaten (echte Jobtitel, Orte usw.)
für `stellen_verarbeitung.py`, ohne dass dafür erst passende Suchbegriffe in
config.txt gepflegt werden müssen.

Nutzt dieselbe Login-Session (.browser_profile) wie stellensuche.py - wurde
dort bereits erfolgreich eingeloggt, ist hier meist kein erneuter Login nötig.

Die Ausgabedatei enthält die rohen Einträge der Such-API (Feldnamen wie
"name", "location", "uuid" - NICHT das von stellen_verarbeitung.py erwartete
Format mit "jobtitel", "ort", "stellentext" usw.). Sie eignet sich daher zum
Sichten der Rohdaten bzw. als Ausgangspunkt für eigene Auswertungen, aber
nicht als direkte Eingabe für stellen_verarbeitung.py.

Nutzung:
    python alle_stellen_de.py
    python alle_stellen_de.py --max-jobs 50 --output alle_stellen.json

Optionen
--------
    --max-jobs N      Anzahl der neuesten Stellen, die geladen werden
                       (Default: alle verfügbaren Stellen)
    --output DATEI    Ausgabedatei (Default: alle_stellen_de.json)
    --headless        Browser unsichtbar starten (nur mit bestehender
                       Session sinnvoll)
"""

import argparse
import json
import sys

from playwright.sync_api import sync_playwright

from stellensuche import (
    PROFILE_DIR,
    SCRIPT_DIR,
    detect_proxy,
    ensure_chromium_installed,
    ensure_logged_in,
    fetch_job_list,
)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-jobs", type=int, default=None,
                         help="Anzahl der neuesten Stellen, die geladen werden (Default: alle verfügbaren Stellen)")
    parser.add_argument("--output", type=str, default="alle_stellen_de.json",
                         help="Ausgabedatei (Default: alle_stellen_de.json)")
    parser.add_argument("--headless", action="store_true",
                         help="Browser unsichtbar starten (nur mit bestehender Session sinnvoll)")
    args = parser.parse_args()

    output_path = SCRIPT_DIR / args.output

    ensure_chromium_installed()

    proxy_server = detect_proxy()
    launch_kwargs = {"user_data_dir": str(PROFILE_DIR), "headless": args.headless}
    if proxy_server:
        print(f"Verwende Proxy: {proxy_server}")
        launch_kwargs["proxy"] = {"server": proxy_server}

    with sync_playwright() as p:
        context = p.chromium.launch_persistent_context(**launch_kwargs)
        page = context.pages[0] if context.pages else context.new_page()

        ensure_logged_in(page)

        if args.max_jobs is None:
            print("Lade alle verfügbaren Stellen in Deutschland (ungefiltert)...")
        else:
            print(f"Lade die neuesten {args.max_jobs} Stellen in Deutschland (ungefiltert)...")
        jobs = fetch_job_list(page, args.max_jobs)
        print(f"{len(jobs)} Stellen geladen.")

        context.close()

    output_path.write_text(
        json.dumps(jobs, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n{len(jobs)} Stellen (nur Listendaten, ohne Beschreibung) gespeichert in: {output_path}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAbgebrochen.")
    except Exception as exc:  # pragma: no cover - Absicherung für die .exe
        print(f"\nFehler: {exc}")
    finally:
        if getattr(sys, "frozen", False):
            input("\nFertig. Enter drücken, um das Fenster zu schließen...")
