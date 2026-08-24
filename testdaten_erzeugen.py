"""
Testdaten-Generator für die Stellensuche
=========================================

Erzeugt eine JSON-Datei mit frei erfundenen, aber realistisch aussehenden
Stellen-Datensätzen (deutsche Standorte, Jobtitel, EG/SL/PC-Einstufungen usw.)
im selben Format, das `stellensuche.py` normalerweise per Scraping liefert
(siehe `build_record()` dort).

Damit lässt sich `stellen_verarbeitung.py` (HTML- und Kartenansicht) ohne
Login im Bosch-Portal und ohne Playwright gegen realistische Testdaten prüfen:

    python testdaten_erzeugen.py --anzahl 40 --output testdaten.json
    python stellen_verarbeitung.py --input testdaten.json

Kein Netzwerkzugriff nötig - alle Daten werden lokal zufällig erzeugt.
"""

import argparse
import json
import random
import uuid
from datetime import datetime, timedelta

# (Ort, Region, PLZ-Raum) - reale deutsche Bosch-Standorte, rein zur
# Orientierung für plausible Geocoding-Testdaten in der Kartenansicht.
STANDORTE = [
    ("Stuttgart", "Baden-Württemberg"),
    ("Gerlingen", "Baden-Württemberg"),
    ("Waiblingen", "Baden-Württemberg"),
    ("Reutlingen", "Baden-Württemberg"),
    ("Leinfelden-Echterdingen", "Baden-Württemberg"),
    ("Stuttgart-Feuerbach", "Baden-Württemberg"),
    ("Abstatt", "Baden-Württemberg"),
    ("Immenstaad", "Baden-Württemberg"),
    ("Bühl", "Baden-Württemberg"),
    ("Ulm", "Baden-Württemberg"),
    ("München", "Bayern"),
    ("Nürnberg", "Bayern"),
    ("Erfurt", "Thüringen"),
    ("Hildesheim", "Niedersachsen"),
    ("Salzgitter", "Niedersachsen"),
    ("Wernau", "Baden-Württemberg"),
    ("Homburg", "Saarland"),
    ("Berlin", "Berlin"),
    ("Frankfurt am Main", "Hessen"),
    ("Hamburg", "Hamburg"),
]

UNTERNEHMEN = [
    "Robert Bosch GmbH",
    "Bosch Sensortec GmbH",
    "Bosch Rexroth AG",
    "Bosch Software Innovations GmbH",
    "Bosch Engineering GmbH",
    "BSH Hausgeräte GmbH",
]

ANSTELLUNGSARTEN = ["Vollzeit", "Teilzeit", "Befristet", "Werkstudent", "Praktikum"]

# (Jobtitel-Vorlage, mögliche Einstufungs-Suffixe) - manche Titel tragen die
# EG/SL/PC-Einstufung direkt im Namen, so wie im echten Portal beobachtet.
JOBTITEL_VORLAGEN = [
    "Software-Entwickler (m/w/d) Embedded Systems",
    "Ingenieur (m/w/d) Fahrerassistenzsysteme",
    "Data Scientist (m/w/d) Machine Learning",
    "Teamleiter (m/w/d) Produktion",
    "Projektleiter (m/w/d) Automotive Elektronik",
    "Werkstudent (m/w/d) Data Analytics",
    "Praktikant (m/w/d) Einkauf",
    "Senior Software Engineer (m/w/d) Cloud Plattform",
    "Fachreferent (m/w/d) Qualitätsmanagement",
    "Bereichsleiter (m/w/d) Vertrieb",
    "Systemingenieur (m/w/d) Sensorik, analog upper tariff / PC06",
    "Cloud Architect (m/w/d), analog upper tariff / PC04",
    "Abteilungsleiter (m/w/d) Entwicklung, EG16/SL1",
    "Product Owner (m/w/d) IoT Plattform",
    "Controller (m/w/d) Finanzen",
    "Business Partner (m/w/d) IT-Prozesse, E05",
    "Lagerist (m/w/d) Materialflusslogistik, unterer Tarif",
    "Instandhalter (m/w/d) Fertigungsanlagen, mittlerer Tarif",
    "Fachexperte (m/w/d) Informationsfluss, oberer Tarif",
    "Gruppenleiter (m/w/d) Business Development, Oberer Tarif/SL1",
]

SUCHBEGRIFFE_POOL = [
    "Python", "Softwareentwicklung", "Data Science", "Machine Learning",
    "Cloud", "Projektleitung", "Automotive", "Embedded", "Qualitätsmanagement",
    "Data Analytics",
]

STELLENTEXT_ABSCHNITTE = {
    "Über uns": (
        "Bosch entwickelt zukunftsweisende Technologien und Dienstleistungen "
        "für eine bessere Welt der Mobilität, Konsumgüter, Industrie- und "
        "Gebäudetechnik."
    ),
    "Stellenbeschreibung": (
        "Sie übernehmen Verantwortung für spannende Projekte, arbeiten "
        "eigenständig an neuen Lösungen und bringen sich aktiv in ein "
        "interdisziplinäres Team ein."
    ),
    "Qualifikationen": (
        "Abgeschlossenes Studium im relevanten Bereich, mehrjährige "
        "Berufserfahrung sowie sehr gute Deutsch- und Englischkenntnisse."
    ),
    "Zusätzliche Informationen": (
        "Wir freuen uns auf Ihre Bewerbung - flexible Arbeitszeiten und "
        "mobiles Arbeiten sind bei uns selbstverständlich."
    ),
}


def zufaelliges_update_datum() -> dict:
    """Erzeugt ein Datum-Dict im selben Format wie die SmartRecruiters-API
    (year/month/day/hour/minute), das `_format_date()` in
    stellen_verarbeitung.py erwartet."""
    zeitpunkt = datetime.now() - timedelta(days=random.randint(0, 60), hours=random.randint(0, 23))
    return {
        "year": zeitpunkt.year,
        "month": zeitpunkt.month,
        "day": zeitpunkt.day,
        "hour": zeitpunkt.hour,
        "minute": zeitpunkt.minute,
    }


def zufaelliger_stellentext() -> str:
    return "\n\n".join(f"{titel}:\n{text}" for titel, text in STELLENTEXT_ABSCHNITTE.items())


def erzeuge_testdatensatz(index: int) -> dict:
    jobtitel = random.choice(JOBTITEL_VORLAGEN)
    ort, region = random.choice(STANDORTE)
    stellentext = zufaelliger_stellentext()
    matched_begriffe = random.sample(SUCHBEGRIFFE_POOL, k=random.randint(1, 3))

    return {
        "jobtitel": jobtitel,
        "ort": f"{ort}, {region}, Deutschland",
        "region": region,
        "land": "Deutschland",
        "referenznummer": f"REF-{2024000 + index}",
        "anstellungsart": random.choice(ANSTELLUNGSARTEN),
        "unternehmen": random.choice(UNTERNEHMEN),
        "intern": True,
        "remote": random.choice([True, False]),
        "aktualisiert": zufaelliges_update_datum(),
        "gefundene_suchbegriffe": matched_begriffe,
        "eg_einstufung": None,
        "eg_rang": None,
        "stellentext": stellentext,
        "url": f"https://www.smartrecruiters.com/app/employee-portal/58652035e4b04016904de9fe/jobs/{uuid.uuid4()}",
        "abgerufen_am": datetime.now().isoformat(timespec="seconds"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--anzahl", type=int, default=30,
                         help="Anzahl der zu erzeugenden Test-Stellen (Default: 30)")
    parser.add_argument("--output", type=str, default="testdaten.json",
                         help="Ausgabedatei (Default: testdaten.json)")
    parser.add_argument("--seed", type=int, default=None,
                         help="Zufalls-Seed für reproduzierbare Testdaten (optional)")
    args = parser.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    # extract_eg_einstufung() aus stellensuche.py wird bewusst NICHT importiert
    # (würde Playwright als Abhängigkeit mitziehen) - stattdessen wird hier
    # dieselbe Logik (Regex auf EG/SL/PC/E/TARIF im Jobtitel) minimal
    # nachgebildet, damit die generierten Datensätze exakt dasselbe
    # eg_einstufung/eg_rang-Format wie ein echter Scraper-Lauf haben. MUSS bei
    # Änderungen an _EG_SL_PATTERN/_TARIF_PATTERN/extract_eg_einstufung() in
    # stellensuche.py manuell mit nachgezogen werden.
    import re
    pattern = re.compile(
        r"\bEG\s*-?\s*(?P<eg>\d{1,2})\b|\bSL\s*-?\s*(?P<sl>\d)\b"
        r"|\bPC\s*-?\s*(?P<pc>\d{1,2})\b"
        r"|\bE\s*-?\s*(?P<e>\d{1,2})\b",
        re.IGNORECASE,
    )
    tarif_wort_zu_nummer = {"unterer": 1, "mittlerer": 2, "oberer": 3, "upper": 3}
    tarif_pattern = re.compile(
        r"\b(?P<stufen>(?:unterer|mittlerer|oberer|upper)"
        r"(?:\s*/\s*(?:unterer|mittlerer|oberer|upper))*)\s+tariff?\b",
        re.IGNORECASE,
    )

    def eg_einstufung_aus_titel(jobtitel: str) -> tuple[str | None, int | None]:
        found = []
        seen = set()
        for match in pattern.finditer(jobtitel):
            if match.group("eg"):
                n = int(match.group("eg"))
                label, rang = f"EG{n}", n
            elif match.group("sl"):
                n = int(match.group("sl"))
                label, rang = f"SL{n}", 100 + n
            elif match.group("pc"):
                n = int(match.group("pc"))
                label, rang = f"PC{n:02d}", 300 + n
            elif match.group("e"):
                n = int(match.group("e"))
                label, rang = f"E{n:02d}", 200 + n
            else:
                continue
            if label not in seen:
                seen.add(label)
                found.append((label, rang))
        for match in tarif_pattern.finditer(jobtitel):
            for wort in match.group("stufen").split("/"):
                n = tarif_wort_zu_nummer.get(wort.strip().lower())
                if n is None:
                    continue
                label, rang = f"TARIF{n}", 400 + n
                if label not in seen:
                    seen.add(label)
                    found.append((label, rang))
        if not found:
            return None, None
        return "/".join(label for label, _ in found), max(rang for _, rang in found)

    records = []
    for i in range(args.anzahl):
        rec = erzeuge_testdatensatz(i)
        rec["eg_einstufung"], rec["eg_rang"] = eg_einstufung_aus_titel(rec["jobtitel"])
        records.append(rec)

    output_path = args.output
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(records, f, ensure_ascii=False, indent=2)

    print(f"{len(records)} Testdatensätze erzeugt und gespeichert in: {output_path}")


if __name__ == "__main__":
    main()
