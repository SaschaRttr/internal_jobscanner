"""
Stellensuche intern (Bosch Employee Portal / SmartRecruiters)
==============================================================

Durchsucht das interne Bosch-Stellenportal (SmartRecruiters Employee Portal)
nach den neuesten Stellenausschreibungen, filtert sie anhand der Suchbegriffe
aus config.txt und speichert die passenden Treffer (inkl. vollständigem
Stellentext und Link) als JSON-Datei.

Zwei Nutzungsarten
------------------
1) Als Python-Skript (Entwicklung):
       pip install -r requirements.txt
       playwright install chromium
       python stellensuche.py

2) Als eigenständige Stellensuche.exe (für Kollegen ohne Python):
       Stellensuche.exe muss zusammen mit config.txt im selben Ordner liegen.
       Einfach per Doppelklick starten. Chromium wird beim allerersten Start
       automatisch heruntergeladen (einmalig, Internet/Proxy nötig) und
       dauerhaft in "%LOCALAPPDATA%\\ms-playwright" abgelegt, sodass künftige
       Starts das nicht wiederholen müssen.
       Siehe build_exe.ps1, um die .exe selbst neu zu bauen (PyInstaller).

Beim ersten Start öffnet sich ein Chromium-Fenster. Bitte dort manuell im
Bosch-Portal einloggen (SSO). Danach im Terminal Enter drücken, um
fortzufahren. Die Login-Session wird in einem lokalen Browser-Profil-Ordner
(".browser_profile") gespeichert, sodass bei künftigen Läufen in der Regel
kein erneuter Login nötig ist (bis die Session abläuft). Bei der .exe bleibt
das Konsolenfenster nach dem Lauf offen (auch bei Fehlern), bis Enter
gedrückt wird.

Optionen
--------
    --max-jobs N      Anzahl der neuesten Stellen, die geprüft werden
                       (Default: alle verfügbaren Stellen)
    --output DATEI    Ausgabedatei (Default: bosch_jobs_gefiltert.json)
    --headless        Browser unsichtbar starten (nur sinnvoll, wenn bereits
                       eine gültige Session im Profil-Ordner vorhanden ist)
"""

import argparse
import html
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

# Als gebündelte .exe (PyInstaller) extrahiert Playwright seinen Treiber in
# einen temporären "_MEI..."-Ordner, der nach jedem Lauf gelöscht wird. Ohne
# explizite PLAYWRIGHT_BROWSERS_PATH würde Chromium dort hinein installiert
# und wäre beim nächsten Start wieder weg. Deshalb VOR dem Playwright-Import
# einen dauerhaften, nutzerspezifischen Ordner festlegen.
if getattr(sys, "frozen", False):
    # Der PyInstaller-Runtime-Hook von Playwright setzt PLAYWRIGHT_BROWSERS_PATH
    # bereits VOR dem Start dieses Skripts auf "0" (lokal, relativ zum
    # temporären _MEI-Ordner). Das muss hier hart überschrieben werden.
    _browsers_dir = Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "ms-playwright"
    os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(_browsers_dir)

from playwright.sync_api import sync_playwright

# Läuft das Skript als gebündelte .exe (PyInstaller), liegen config.txt,
# Browser-Profil und Ausgabedateien neben der .exe. Im normalen Python-Betrieb
# liegen sie neben stellensuche.py.
if getattr(sys, "frozen", False):
    SCRIPT_DIR = Path(sys.executable).resolve().parent
else:
    SCRIPT_DIR = Path(__file__).resolve().parent

CONFIG_FILE = SCRIPT_DIR / "config.txt"
PROFILE_DIR = SCRIPT_DIR / ".browser_profile"
GEOCODE_CACHE_FILE = SCRIPT_DIR / "geocode_cache.json"
NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

PORTAL_BASE = "https://www.smartrecruiters.com"
JOBS_URL = f"{PORTAL_BASE}/app/employee-portal/58652035e4b04016904de9fe/jobs"
SEARCH_API = f"{PORTAL_BASE}/employee-portal/api/job/search"
DETAIL_API = f"{PORTAL_BASE}/employee-portal/api/job"

# Entspricht dem in der Portal-UI aktivierten Filter "Working Country: Germany"
COUNTRY_FILTER = {
    "custom_field_value_id_586a996ce4b05fd937b86097": [
        "34525254-83b2-42bc-9932-cf9f16db5281"
    ]
}

PAGE_SIZE = 100

# Fallback-Proxy für das Bosch-Firmennetz (falls nicht per Env-Variable/PAC ermittelbar)
FALLBACK_PROXY = "http://rb-proxy-de.bosch.com:8080"
PAC_URL = "http://rbins.bosch.com/fe.pac"


def ensure_chromium_installed() -> None:
    """Installiert den Chromium-Browser für Playwright, falls noch nicht vorhanden.

    Wichtig für Kollegen, die nur die gebündelte .exe erhalten haben und nie
    manuell "playwright install chromium" ausgeführt haben. Der Download läuft
    beim allerersten Start automatisch (Internet/Proxy nötig).
    """
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            browser.close()
        return
    except Exception:
        pass

    print("Chromium-Browser wird für Playwright installiert (einmalig, bitte warten)...")
    from playwright.__main__ import main as playwright_cli_main

    old_argv = sys.argv
    sys.argv = ["playwright", "install", "chromium"]
    try:
        playwright_cli_main()
    except SystemExit:
        pass
    finally:
        sys.argv = old_argv
    print("Chromium-Installation abgeschlossen.")


def detect_proxy() -> str | None:
    """Ermittelt den zu verwendenden Proxy-Server (Env-Variable > PAC-Datei > Fallback)."""
    for var in ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy"):
        if os.environ.get(var):
            return os.environ[var]

    try:
        with urllib.request.urlopen(PAC_URL, timeout=5) as resp:
            pac_text = resp.read().decode("utf-8", errors="ignore")
        match = re.search(r'Pri_Proxy\s*=\s*"([^"]+)"', pac_text)
        if match:
            return f"http://{match.group(1)}"
    except Exception:
        pass

    return FALLBACK_PROXY


def load_search_terms(config_path: Path) -> list[str]:
    """Liest die Suchbegriffe aus dem Abschnitt [suchbegriffe] in config.txt."""
    if not config_path.exists():
        raise FileNotFoundError(f"Konfigurationsdatei nicht gefunden: {config_path}")

    lines = config_path.read_text(encoding="utf-8").splitlines()
    terms: list[str] = []
    in_section = False
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if line.lower() == "[suchbegriffe]":
            in_section = True
            continue
        if line.lower() == "[\\suchbegriffe]":
            in_section = False
            continue
        if in_section:
            terms.append(line)

    # Duplikate entfernen, Reihenfolge beibehalten
    seen = set()
    unique_terms = []
    for term in terms:
        key = term.lower()
        if key not in seen:
            seen.add(key)
            unique_terms.append(term)
    return unique_terms


def load_blacklist(config_path: Path) -> list[str]:
    """Liest die Ausschlussbegriffe aus dem Abschnitt [ausschlussbegriffe] in config.txt.

    Stellen, deren Titel einen dieser Begriffe enthält, werden auch dann
    ausgeschlossen, wenn sie einen Suchbegriff treffen.
    """
    if not config_path.exists():
        raise FileNotFoundError(f"Konfigurationsdatei nicht gefunden: {config_path}")

    lines = config_path.read_text(encoding="utf-8").splitlines()
    terms: list[str] = []
    in_section = False
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if line.lower() == "[ausschlussbegriffe]":
            in_section = True
            continue
        if line.lower() == "[\\ausschlussbegriffe]":
            in_section = False
            continue
        if not in_section:
            continue
        if line.startswith("#"):
            continue
        terms.append(line)

    seen = set()
    unique_terms = []
    for term in terms:
        key = term.lower()
        if key not in seen:
            seen.add(key)
            unique_terms.append(term)
    return unique_terms


def load_locations(config_path: Path) -> list[str]:
    """Liest die Orte aus dem Abschnitt [orte] in config.txt.

    Format: ein Ortsname pro Zeile. Zeilen, die mit "#" beginnen, sind
    Kommentare und werden ignoriert.

    Gibt die Orts-Liste ohne Duplikate zurück.
    """
    if not config_path.exists():
        raise FileNotFoundError(f"Konfigurationsdatei nicht gefunden: {config_path}")

    lines = config_path.read_text(encoding="utf-8").splitlines()
    cities: list[str] = []
    in_section = False
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue
        if line.lower() == "[orte]":
            in_section = True
            continue
        if line.lower() == "[\\orte]":
            in_section = False
            continue
        if not in_section:
            continue
        if line.startswith("#"):
            continue
        cities.append(line)

    seen = set()
    unique_cities = []
    for city in cities:
        key = city.lower()
        if key not in seen:
            seen.add(key)
            unique_cities.append(city)
    return unique_cities


def location_matches(detail: dict, cities: list[str]) -> bool:
    """Prüft, ob ein Job-Detail zu einem der konfigurierten Orte passt.

    Es zählt ausschließlich die Orts-Liste (cities); die Zeile
    "Working Country: Germany" dient nur der Dokumentation und wird hier
    nicht als eigenständiges Kriterium gewertet, damit z.B. Jobs in Bonn
    (Deutschland, aber nicht in der Orts-Liste) korrekt ausgeschlossen werden.
    """
    if not cities:
        return True

    location = (detail.get("location") or "").strip().lower()
    region = (detail.get("regionName") or "").strip().lower()

    return any(c.lower() in location or c.lower() in region for c in cities)


def load_geocode_cache() -> dict:
    """Lädt den lokalen Geocoding-Cache (geocode_cache.json), falls vorhanden.

    Der Cache bildet Adressen (lowercased) auf [lat, lon] oder null (nicht
    gefunden) ab, damit dieselbe Adresse nicht bei jedem Lauf erneut bei
    Nominatim angefragt werden muss.
    """
    if not GEOCODE_CACHE_FILE.exists():
        return {}
    try:
        return json.loads(GEOCODE_CACHE_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_geocode_cache(cache: dict) -> None:
    GEOCODE_CACHE_FILE.write_text(
        json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def geocode_address(address: str, cache: dict, proxy_server: str | None = None) -> tuple[float, float] | None:
    """Ermittelt Breiten-/Längengrad für eine Adresse via OpenStreetMap Nominatim.

    Ergebnisse werden im übergebenen Cache-Dict gespeichert (Aufrufer ist für
    das Speichern via save_geocode_cache verantwortlich). Laut Nominatim-
    Nutzungsrichtlinie ist maximal eine Anfrage pro Sekunde erlaubt, daher wird
    nach jeder tatsächlichen Netzwerkanfrage kurz gewartet. Ist proxy_server
    gesetzt (z. B. im Firmennetz erforderlich), wird er für die Anfrage genutzt.
    """
    if not address:
        return None
    key = address.strip().lower()
    if key in cache:
        value = cache[key]
        return (value[0], value[1]) if value else None

    query = urllib.parse.urlencode({"format": "json", "q": address, "limit": 1})
    request = urllib.request.Request(
        f"{NOMINATIM_URL}?{query}",
        headers={"User-Agent": "Stellensuche-intern/1.0 (internes Tool)"},
    )
    try:
        if proxy_server:
            opener = urllib.request.build_opener(
                urllib.request.ProxyHandler({"http": proxy_server, "https": proxy_server})
            )
            resp = opener.open(request, timeout=10)
        else:
            resp = urllib.request.urlopen(request, timeout=10)
        with resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError):
        cache[key] = None
        return None
    finally:
        time.sleep(1)

    if not data:
        cache[key] = None
        return None

    try:
        lat = float(data[0]["lat"])
        lon = float(data[0]["lon"])
    except (KeyError, ValueError, TypeError):
        cache[key] = None
        return None

    cache[key] = [lat, lon]
    return lat, lon


def html_to_text(fragment: str) -> str:
    """Wandelt einen HTML-Textblock in reinen, lesbaren Text um."""
    if not fragment:
        return ""
    text = re.sub(r"<[^>]+>", " ", fragment)
    text = html.unescape(text)
    return re.sub(r"\s+", " ", text).strip()


def _api_reachable(page) -> bool:
    """Testet, ob die Such-API JSON statt einer Login-/Challenge-Seite liefert."""
    try:
        _page_fetch_json(
            page,
            SEARCH_API,
            method="POST",
            body={"query": "", "sorts": {"released_date": "desc"}, "offset": 0,
                  "limit": 1, "filters": COUNTRY_FILTER},
        )
        return True
    except Exception:
        return False


def _safe_goto(page, url: str) -> None:
    """page.goto, das eine durch parallele SSO-Redirects ausgelöste
    'Navigation is interrupted by another navigation' ignoriert."""
    try:
        page.goto(url, wait_until="domcontentloaded")
        page.wait_for_load_state("networkidle", timeout=10000)
    except Exception:
        time.sleep(1.5)


def ensure_logged_in(page) -> None:
    """Navigiert zur Job-Liste und wartet ggf. auf manuellen Login.

    Die URL allein verrät nicht, ob man eingeloggt ist (SPA behält die URL bei
    einem Login-Overlay bei), daher wird zusätzlich ein echter Testaufruf der
    Such-API gemacht.
    """
    _safe_goto(page, JOBS_URL)

    while not _api_reachable(page):
        print("\nBitte im geöffneten Browserfenster einloggen (SSO).")
        input("Danach hier im Terminal Enter drücken, um fortzufahren...")
        _safe_goto(page, JOBS_URL)


def _page_fetch_json(page, url: str, method: str = "GET", body: dict | None = None) -> dict:
    """Führt einen fetch()-Aufruf im Kontext der echten Browserseite aus
    (umgeht Bot-Schutzmechanismen wie Datadome, die reine HTTP-Clients blockieren)."""
    result = page.evaluate(
        """async ({ url, method, body }) => {
            function readCookie(name) {
                const match = document.cookie.match(new RegExp('(?:^|; )' + name + '=([^;]*)'));
                return match ? decodeURIComponent(match[1]) : null;
            }
            const options = {
                method,
                credentials: 'include',
                headers: { 'Accept': 'application/json' },
            };
            const csrf = readCookie('_csrf') || readCookie('XSRF-TOKEN');
            if (csrf) {
                options.headers['X-CSRF-Token'] = csrf;
                options.headers['X-XSRF-TOKEN'] = csrf;
            }
            if (body) {
                options.headers['Content-Type'] = 'application/json';
                options.body = JSON.stringify(body);
            }
            const resp = await fetch(url, options);
            const text = await resp.text();
            return { ok: resp.ok, status: resp.status, text };
        }""",
        {"url": url, "method": method, "body": body},
    )
    if not result["ok"]:
        raise RuntimeError(f"Anfrage fehlgeschlagen ({result['status']}): {url}\n{result['text'][:500]}")
    try:
        return json.loads(result["text"])
    except json.JSONDecodeError as exc:
        raise RuntimeError(
            f"Antwort von {url} war kein gültiges JSON (Status {result['status']}):\n{result['text'][:500]}"
        ) from exc


def fetch_job_list(page, max_jobs: int | None = None) -> list[dict]:
    """Holt Stellen über die Such-API (Pagination), neueste zuerst.

    HINWEIS: Ein Versuch, die Suchbegriffe serverseitig als OR-Query an die
    API zu übergeben, wurde getestet, führte aber mit dem vollen
    Begriffs-Set (103 Begriffe) zu einem 500-Fehler der API. Deshalb wird
    weiterhin mit leerer Query geladen und ausschließlich lokal gefiltert
    (`filter_matching_jobs`).

    Wenn `max_jobs` None ist, werden ALLE verfügbaren Stellen geholt.
    """
    jobs: list[dict] = []
    offset = 0

    while max_jobs is None or len(jobs) < max_jobs:
        limit = PAGE_SIZE if max_jobs is None else min(PAGE_SIZE, max_jobs - len(jobs))
        body = {
            "query": "",
            "sorts": {"released_date": "desc"},
            "offset": offset,
            "limit": limit,
            "filters": COUNTRY_FILTER,
        }
        data = _page_fetch_json(page, SEARCH_API, method="POST", body=body)
        results = data.get("results", [])
        if not results:
            break

        jobs.extend(results)
        offset += len(results)

        if offset >= data.get("numFound", 0):
            break

    return jobs if max_jobs is None else jobs[:max_jobs]


def filter_matching_jobs(jobs: list[dict], terms: list[str], blacklist: list[str] | None = None) -> list[dict]:
    """Filtert Jobs, deren Titel einen der Suchbegriffe enthält (case-insensitive).

    Jobs, deren Titel einen Begriff aus `blacklist` enthält, werden ausgeschlossen,
    auch wenn sie einen Suchbegriff treffen.
    """
    blacklist = blacklist or []
    matches = []
    for job in jobs:
        name = job.get("name", "")
        name_lower = name.lower()
        if any(b.lower() in name_lower for b in blacklist):
            continue
        hit_terms = [t for t in terms if t.lower() in name_lower]
        if hit_terms:
            job["_matched_begriffe"] = hit_terms
            matches.append(job)
    return matches


def fetch_job_detail(page, uuid: str) -> dict:
    return _page_fetch_json(page, f"{DETAIL_API}/{uuid}")


# EG-Einstufungen (tarifliche Entgeltgruppen) erhalten als Rang direkt ihre Zahl
# (EG12 -> 12, EG16 -> 16, ...). Die außertariflichen Führungsstufen SL1/SL2/SL3
# liegen darüber, mit SL1 < SL2 < SL3 (Rang 101-103), unabhängig von der
# höchsten vorkommenden EG-Zahl. Die Einstufung steht i. d. R. im Jobtitel
# (z. B. "... (EG16, w/m/div.)" oder "..., SL1"), daher wird sowohl der Titel
# als auch der Stellentext durchsucht.
_EG_SL_PATTERN = re.compile(
    r"\bEG\s*-?\s*(?P<eg>\d{1,2})\b|\bSL\s*-?\s*(?P<sl>\d)\b",
    re.IGNORECASE,
)


def extract_eg_einstufung(*texts: str) -> tuple[str | None, int | None]:
    """Sucht in den übergebenen Texten (z. B. Jobtitel, Stellentext) nach einer
    EG- oder SL-Einstufung.

    Gibt ein Label ("EG13", "SL2") und einen numerischen Rang für Vergleiche
    zurück (EG-Rang = EG-Zahl, SL1=101, SL2=102, SL3=103 - immer höher als
    jede EG-Zahl). Wird nichts gefunden, wird (None, None) zurückgegeben.
    """
    for text in texts:
        if not text:
            continue
        match = _EG_SL_PATTERN.search(text)
        if not match:
            continue
        if match.group("eg"):
            n = int(match.group("eg"))
            return f"EG{n}", n
        n = int(match.group("sl"))
        return f"SL{n}", 100 + n
    return None, None


def build_record(detail: dict, matched_begriffe: list[str]) -> dict:
    sections = detail.get("sections", {}) or {}
    text_parts = []
    for key in ("companyDescription", "jobDescription", "qualifications", "additionalInformation"):
        section = sections.get(key)
        if section and section.get("text"):
            title = section.get("title") or key
            text_parts.append(f"{title}:\n{html_to_text(section['text'])}")

    stellentext = "\n\n".join(text_parts)
    jobtitel = detail.get("name") or ""
    eg_einstufung, eg_rang = extract_eg_einstufung(jobtitel, stellentext)

    uuid = detail.get("uuid")
    return {
        "jobtitel": detail.get("name"),
        "ort": detail.get("location"),
        "region": detail.get("regionName"),
        "land": detail.get("countryName"),
        "referenznummer": detail.get("refNumber"),
        "anstellungsart": detail.get("typeOfEmployment"),
        "unternehmen": detail.get("companyName"),
        "intern": detail.get("isInternal"),
        "remote": detail.get("locationRemote"),
        "aktualisiert": detail.get("updateDate"),
        "gefundene_suchbegriffe": matched_begriffe,
        "eg_einstufung": eg_einstufung,
        "eg_rang": eg_rang,
        "stellentext": stellentext,
        "url": f"{JOBS_URL}/{uuid}",
        "abgerufen_am": datetime.now().isoformat(timespec="seconds"),
    }


def _format_date(value) -> str:
    """Formatiert ein Datumsfeld (dict mit year/month/day[/hour/minute] oder String) zu DD.MM.YYYY [HH:MM]."""
    if not value:
        return ""
    if isinstance(value, dict):
        try:
            date_str = f"{value['day']:02d}.{value['month']:02d}.{value['year']}"
            if "hour" in value and "minute" in value:
                date_str += f" {value['hour']:02d}:{value['minute']:02d}"
            return date_str
        except (KeyError, TypeError, ValueError):
            return str(value)
    return str(value)


def build_html_page(records: list[dict], map_filename: str | None = None) -> str:
    """Erzeugt eine übersichtliche, eigenständige HTML-Seite aus den Job-Records.

    Enthält eine "Beworben"-Checkbox pro Stelle (Status wird im Browser via
    localStorage gespeichert und bleibt so auch nach erneutem Erzeugen der
    Seite erhalten) sowie einen Button zum Herunterladen der Stellenanzeige
    als .txt-Datei.
    """
    generated_at = datetime.now().strftime("%d.%m.%Y %H:%M")

    cards = []
    jobs_data: dict[str, dict[str, str]] = {}
    for idx, rec in enumerate(records):
        title = html.escape(rec.get("jobtitel") or "")
        url = html.escape(rec.get("url") or "#")
        ort = html.escape(rec.get("ort") or "")
        region = html.escape(rec.get("region") or "")
        land = html.escape(rec.get("land") or "")
        anstellungsart = html.escape(rec.get("anstellungsart") or "")
        unternehmen = html.escape(rec.get("unternehmen") or "")
        aktualisiert = html.escape(_format_date(rec.get("aktualisiert")))
        begriffe = rec.get("gefundene_suchbegriffe") or []
        tags = "".join(f'<span class="tag">{html.escape(b)}</span>' for b in begriffe)
        stellentext_raw = rec.get("stellentext") or ""
        stellentext = html.escape(stellentext_raw).replace("\n", "<br>")
        eg_label = rec.get("eg_einstufung")
        eg_rang = rec.get("eg_rang")
        eg_rang_attr = str(eg_rang) if eg_rang is not None else ""
        eg_tag = f'<span class="tag eg-tag">{html.escape(eg_label)}</span>' if eg_label else ""

        job_id = html.escape((rec.get("url") or f"job-{idx}").rsplit("/", 1)[-1] or f"job-{idx}")
        jobs_data[job_id] = {
            "title": rec.get("jobtitel") or "",
            "text": stellentext_raw,
        }

        meta_parts = [p for p in (ort, region, land) if p]
        meta_line = ", ".join(meta_parts)

        cards.append(f"""
        <article class="card" id="card_{job_id}" data-eg-rang="{eg_rang_attr}">
            <h2><a href="{url}" target="_blank" rel="noopener">{title}</a></h2>
            <div class="meta">
                {f'<span>📍 {meta_line}</span>' if meta_line else ''}
                {f'<span>🏢 {unternehmen}</span>' if unternehmen else ''}
                {f'<span>💼 {anstellungsart}</span>' if anstellungsart else ''}
                {f'<span>🕒 Aktualisiert: {aktualisiert}</span>' if aktualisiert else ''}
            </div>
            <div class="tags">{eg_tag}{tags}</div>
            <div class="actions">
                <label class="beworben-label">
                    <input type="checkbox" class="beworben-checkbox" data-id="{job_id}">
                    Beworben
                </label>
                <button type="button" class="download-btn" data-id="{job_id}">⬇️ Als TXT herunterladen</button>
            </div>
            <details>
                <summary>Stellenbeschreibung anzeigen</summary>
                <div class="stellentext">{stellentext}</div>
            </details>
        </article>""")

    cards_html = "\n".join(cards) if cards else '<p class="empty">Keine passenden Stellen gefunden.</p>'
    jobs_data_json = json.dumps(jobs_data, ensure_ascii=False).replace("</", "<\\/")

    eg_level_options = "".join(f'<option value="{n}">EG{n}</option>' for n in range(1, 19))
    eg_level_options += "".join(f'<option value="{100 + n}">SL{n}</option>' for n in range(1, 4))


    return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Bosch Stellensuche – Ergebnisse</title>
<style>
    :root {{ color-scheme: light dark; }}
    body {{
        font-family: "Segoe UI", Arial, sans-serif;
        max-width: 900px;
        margin: 0 auto;
        padding: 24px;
        background: #f4f5f7;
        color: #1a1a1a;
    }}
    header {{ margin-bottom: 24px; }}
    header h1 {{ margin-bottom: 4px; }}
    header p {{ color: #555; margin: 0; }}
    .card {{
        background: #fff;
        border: 1px solid #e0e0e0;
        border-radius: 8px;
        padding: 16px 20px;
        margin-bottom: 16px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }}
    .card.applied {{
        opacity: 0.6;
        border-left: 4px solid #2e7d32;
    }}
    .card h2 {{ margin: 0 0 8px 0; font-size: 1.15rem; }}
    .card h2 a {{ color: #005691; text-decoration: none; }}
    .card h2 a:hover {{ text-decoration: underline; }}
    .meta {{
        display: flex;
        flex-wrap: wrap;
        gap: 12px;
        color: #444;
        font-size: 0.9rem;
        margin-bottom: 8px;
    }}
    .tags {{ margin-bottom: 8px; }}
    .tag {{
        display: inline-block;
        background: #e8f1f8;
        color: #005691;
        border-radius: 12px;
        padding: 2px 10px;
        font-size: 0.8rem;
        margin: 2px 4px 2px 0;
    }}
    .actions {{
        display: flex;
        align-items: center;
        gap: 16px;
        margin-bottom: 8px;
        font-size: 0.9rem;
    }}
    .beworben-label {{ display: flex; align-items: center; gap: 6px; cursor: pointer; }}
    .download-btn {{
        background: #005691;
        color: #fff;
        border: none;
        border-radius: 6px;
        padding: 6px 12px;
        font-size: 0.85rem;
        cursor: pointer;
    }}
    .download-btn:hover {{ background: #00426d; }}
    details summary {{
        cursor: pointer;
        color: #005691;
        font-size: 0.9rem;
    }}
    .stellentext {{
        margin-top: 10px;
        font-size: 0.9rem;
        line-height: 1.5;
        color: #333;
    }}
    .empty {{ color: #777; }}
    .eg-tag {{ background: #fdecea; color: #a13a2a; }}
    .filterbar {{
        display: flex;
        flex-wrap: wrap;
        align-items: center;
        gap: 10px;
        background: #fff;
        border: 1px solid #e0e0e0;
        border-radius: 8px;
        padding: 10px 14px;
        margin-bottom: 16px;
        font-size: 0.9rem;
    }}
    .filterbar label {{ display: flex; align-items: center; gap: 6px; }}
    .filterbar select {{ padding: 4px 6px; }}
    .filterbar .reset-btn {{
        background: none;
        border: 1px solid #005691;
        color: #005691;
        border-radius: 6px;
        padding: 4px 10px;
        cursor: pointer;
        font-size: 0.85rem;
    }}
    .filterbar .reset-btn:hover {{ background: #e8f1f8; }}
    .filter-count {{ color: #555; }}
</style>
</head>
<body>
<header>
    <h1>Bosch Stellensuche – Ergebnisse</h1>
    <p>{len(records)} passende Stelle(n) &middot; erzeugt am {generated_at}{f' &middot; <a href="{html.escape(map_filename)}">🗺️ Karte anzeigen</a>' if map_filename else ''}</p>
</header>
<div class="filterbar">
    <label>EG/SL-Einstufung
        <select id="eg-operator">
            <option value="=">=</option>
            <option value="<">&lt;</option>
            <option value="<=">&le;</option>
            <option value=">">&gt;</option>
            <option value=">=">&ge;</option>
            <option value="between">zwischen</option>
        </select>
    </label>
    <label>
        <select id="eg-level">
            <option value="">Alle Stufen</option>
            {eg_level_options}
        </select>
    </label>
    <label id="eg-level-2-label" style="display:none;">und
        <select id="eg-level-2">
            <option value="">...</option>
            {eg_level_options}
        </select>
    </label>
    <button type="button" class="reset-btn" id="eg-reset">Filter zurücksetzen</button>
    <span class="filter-count" id="eg-filter-count"></span>
</div>
<main>
{cards_html}
</main>
<script>
const JOBS_DATA = {jobs_data_json};

function sanitizeFilename(name) {{
    return name.replace(/[\\\\/:*?"<>|]/g, "_").trim() || "stellenanzeige";
}}

function downloadTxt(id) {{
    const job = JOBS_DATA[id];
    if (!job) return;
    const content = job.title + "\\n\\n" + job.text;
    const blob = new Blob([content], {{ type: "text/plain;charset=utf-8" }});
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = sanitizeFilename(job.title) + ".txt";
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(a.href);
}}

document.addEventListener("DOMContentLoaded", () => {{
    document.querySelectorAll(".beworben-checkbox").forEach((cb) => {{
        const id = cb.dataset.id;
        const card = document.getElementById("card_" + id);
        if (localStorage.getItem("beworben_" + id) === "1") {{
            cb.checked = true;
            if (card) card.classList.add("applied");
        }}
        cb.addEventListener("change", () => {{
            localStorage.setItem("beworben_" + id, cb.checked ? "1" : "0");
            if (card) card.classList.toggle("applied", cb.checked);
        }});
    }});

    document.querySelectorAll(".download-btn").forEach((btn) => {{
        btn.addEventListener("click", () => downloadTxt(btn.dataset.id));
    }});

    const opSelect = document.getElementById("eg-operator");
    const levelSelect = document.getElementById("eg-level");
    const level2Select = document.getElementById("eg-level-2");
    const level2Label = document.getElementById("eg-level-2-label");
    const resetBtn = document.getElementById("eg-reset");
    const countLabel = document.getElementById("eg-filter-count");
    const allCards = Array.from(document.querySelectorAll(".card"));

    function compare(rang, op, target, target2) {{
        switch (op) {{
            case "=": return rang === target;
            case "<": return rang < target;
            case "<=": return rang <= target;
            case ">": return rang > target;
            case ">=": return rang >= target;
            case "between": {{
                const lo = Math.min(target, target2);
                const hi = Math.max(target, target2);
                return rang >= lo && rang <= hi;
            }}
            default: return true;
        }}
    }}

    function applyEgFilter() {{
        const op = opSelect.value;
        const target = levelSelect.value;
        const target2 = level2Select.value;
        level2Label.style.display = op === "between" ? "" : "none";

        const filterActive = op === "between" ? (target !== "" && target2 !== "") : target !== "";
        let visible = 0;
        allCards.forEach((card) => {{
            let show = true;
            if (filterActive) {{
                const rawRang = card.dataset.egRang;
                if (rawRang === "") {{
                    show = false;
                }} else if (op === "between") {{
                    show = compare(parseInt(rawRang, 10), op, parseInt(target, 10), parseInt(target2, 10));
                }} else {{
                    show = compare(parseInt(rawRang, 10), op, parseInt(target, 10));
                }}
            }}
            card.style.display = show ? "" : "none";
            if (show) visible++;
        }});
        countLabel.textContent = filterActive
            ? `${{visible}} von ${{allCards.length}} Stellen passen zum Filter`
            : "";
    }}

    opSelect.addEventListener("change", applyEgFilter);
    levelSelect.addEventListener("change", applyEgFilter);
    level2Select.addEventListener("change", applyEgFilter);
    resetBtn.addEventListener("click", () => {{
        levelSelect.value = "";
        level2Select.value = "";
        opSelect.value = "=";
        applyEgFilter();
    }});
}});
</script>
</body>
</html>
"""


def build_map_page(records: list[dict], geocode_cache: dict, proxy_server: str | None = None) -> str:
    """Erzeugt eine eigenständige HTML-Seite mit einer Karte (Leaflet/OSM),
    auf der jede Stelle als Punkt an ihrem Standort eingeblendet wird.

    Adressen werden über den übergebenen Geocoding-Cache aufgelöst (siehe
    geocode_address). Mehrere Stellen am selben Ort werden zu einem
    gemeinsamen Marker zusammengefasst.
    """
    generated_at = datetime.now().strftime("%d.%m.%Y %H:%M")

    points: dict[str, dict] = {}
    missing = 0
    for rec in records:
        ort = (rec.get("ort") or "").strip()
        if not ort:
            missing += 1
            continue
        coords = geocode_address(ort, geocode_cache, proxy_server=proxy_server)
        if not coords:
            missing += 1
            continue
        lat, lon = coords
        key = f"{lat:.5f},{lon:.5f}"
        entry = points.setdefault(key, {"lat": lat, "lon": lon, "ort": ort, "jobs": []})
        entry["jobs"].append({
            "title": rec.get("jobtitel") or "",
            "url": rec.get("url") or "#",
        })

    markers = list(points.values())
    markers_json = json.dumps(markers, ensure_ascii=False).replace("</", "<\\/")

    return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Bosch Stellensuche \u2013 Karte</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<style>
    :root {{ color-scheme: light dark; }}
    body {{
        font-family: "Segoe UI", Arial, sans-serif;
        margin: 0;
        padding: 0;
        color: #1a1a1a;
    }}
    header {{
        padding: 16px 24px;
        background: #fff;
        border-bottom: 1px solid #e0e0e0;
    }}
    header h1 {{ margin: 0 0 4px 0; font-size: 1.3rem; }}
    header p {{ color: #555; margin: 0; font-size: 0.9rem; }}
    header a {{ color: #005691; text-decoration: none; }}
    header a:hover {{ text-decoration: underline; }}
    #map {{ height: calc(100vh - 78px); width: 100%; }}
    .popup-jobs {{ margin: 6px 0 0 0; padding-left: 18px; }}
    .popup-jobs li {{ margin-bottom: 4px; }}
</style>
</head>
<body>
<header>
    <h1>Bosch Stellensuche \u2013 Karte</h1>
    <p>{len(markers)} Standort(e) &middot; {len(records)} passende Stelle(n)
        {f' &middot; {missing} ohne ermittelbaren Standort' if missing else ''}
        &middot; erzeugt am {generated_at} &middot; <a href="javascript:history.back()">\u2190 Zur\u00fcck zur Liste</a></p>
</header>
<div id="map"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const MARKERS = {markers_json};

const map = L.map('map').setView([51.1657, 10.4515], 6);
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>-Mitwirkende'
}}).addTo(map);

const bounds = [];
MARKERS.forEach((pt) => {{
    bounds.push([pt.lat, pt.lon]);
    const jobsHtml = pt.jobs.map((j) => {{
        const safeTitle = j.title.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        return `<li><a href="${{j.url}}" target="_blank" rel="noopener">${{safeTitle}}</a></li>`;
    }}).join("");
    const safeOrt = pt.ort.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    const popupHtml = `<strong>${{safeOrt}}</strong><ul class="popup-jobs">${{jobsHtml}}</ul>`;
    L.marker([pt.lat, pt.lon]).addTo(map).bindPopup(popupHtml);
}});

if (bounds.length) {{
    map.fitBounds(bounds, {{ padding: [30, 30] }});
}}
</script>
</body>
</html>
"""


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--max-jobs", type=int, default=None,
                         help="Anzahl der neuesten Stellen, die geprüft werden (Default: alle verfügbaren Stellen)")
    parser.add_argument("--output", type=str, default="bosch_jobs_gefiltert.json",
                         help="Ausgabedatei (Default: bosch_jobs_gefiltert.json)")
    parser.add_argument("--html-output", type=str, default=None,
                         help="HTML-Ausgabedatei (Default: gleicher Name wie --output mit .html-Endung)")
    parser.add_argument("--map-output", type=str, default=None,
                         help="HTML-Kartenansicht (Default: gleicher Name wie --output mit _karte.html-Endung)")
    parser.add_argument("--no-map", action="store_true",
                         help="Keine Kartenansicht erzeugen (spart Geocoding-Anfragen)")
    parser.add_argument("--headless", action="store_true",
                         help="Browser unsichtbar starten (nur mit bestehender Session sinnvoll)")
    args = parser.parse_args()

    output_path = SCRIPT_DIR / args.output

    terms = load_search_terms(CONFIG_FILE)
    print(f"{len(terms)} Suchbegriffe aus config.txt geladen.")

    blacklist = load_blacklist(CONFIG_FILE)
    print(f"{len(blacklist)} Ausschlussbegriffe aus config.txt geladen.")

    cities = load_locations(CONFIG_FILE)
    print(f"{len(cities)} Orts-Filter aus config.txt geladen.")

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
            print("Lade alle verfügbaren Stellen...")
        else:
            print(f"Lade die neuesten {args.max_jobs} Stellen...")
        jobs = fetch_job_list(page, args.max_jobs)
        print(f"{len(jobs)} Stellen geladen.")

        matches = filter_matching_jobs(jobs, terms, blacklist)
        print(f"{len(matches)} Treffer gegen die Suchbegriffe gefunden.")

        records = []
        for job in matches:
            uuid = job.get("uuid")
            detail = fetch_job_detail(page, uuid)
            if not location_matches(detail, cities):
                print(f"  -> Übersprungen (Ort passt nicht): {job.get('name')} "
                      f"({detail.get('location')}, {detail.get('countryName')})")
                continue
            print(f"  -> Lade Details: {job.get('name')}")
            records.append(build_record(detail, job.get("_matched_begriffe", [])))

        context.close()

    output_path.write_text(
        json.dumps(records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\n{len(records)} passende Stellen gespeichert in: {output_path}")

    html_output_path = (
        SCRIPT_DIR / args.html_output if args.html_output else output_path.with_suffix(".html")
    )
    map_output_path = (
        SCRIPT_DIR / args.map_output if args.map_output
        else output_path.parent / f"{output_path.stem}_karte.html"
    )

    map_filename = None
    if not args.no_map:
        print("\nErmittle Standorte für die Kartenansicht (Geocoding via OpenStreetMap)...")
        geocode_cache = load_geocode_cache()
        try:
            map_html = build_map_page(records, geocode_cache, proxy_server=proxy_server)
        finally:
            save_geocode_cache(geocode_cache)
        map_output_path.write_text(map_html, encoding="utf-8")
        print(f"Kartenansicht gespeichert in: {map_output_path}")
        map_filename = map_output_path.name

    html_output_path.write_text(build_html_page(records, map_filename=map_filename), encoding="utf-8")
    print(f"HTML-Übersicht gespeichert in: {html_output_path}")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nAbgebrochen.")
    except Exception as exc:  # pragma: no cover - Absicherung für die .exe
        print(f"\nFehler: {exc}")
    finally:
        # Läuft das Skript als gebündelte .exe (Doppelklick), würde sich das
        # Konsolenfenster sonst sofort schließen. So kann man die Ausgabe lesen.
        if getattr(sys, "frozen", False):
            input("\nFertig. Enter drücken, um das Fenster zu schließen...")
