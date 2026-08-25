"""
Stellensuche intern – JSON-Verarbeitung (HTML- und Kartenansicht)
==================================================================

Liest eine JSON-Datei mit Stellen-Datensätzen (wie sie `stellensuche.py`
erzeugt, z. B. bosch_jobs_gefiltert.json) und baut daraus:

    - eine übersichtliche HTML-Listenansicht (inkl. EG/SL-Filter und einem
      Status-Dropdown "Kein Status / Möchte mich bewerben / Beworben" pro
      Stelle, gespeichert im Browser via localStorage)
    - eine Kartenansicht (Leaflet/OpenStreetMap) mit demselben Status-Dropdown
      in den Marker-Popups

Diese Datei braucht keinen Login und kein Playwright – sie lässt sich direkt
gegen eine bereits vorhandene JSON-Datei testen:

    python stellen_verarbeitung.py --input bosch_jobs_gefiltert.json

Das Erzeugen der JSON-Datei selbst übernimmt weiterhin `stellensuche.py`
(dort ist ein Login im Bosch-Portal nötig).
"""

import argparse
import html
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

# Fallback-Proxy für das Bosch-Firmennetz (falls nicht per Env-Variable/PAC ermittelbar)
FALLBACK_PROXY = "http://rb-proxy-de.bosch.com:8080"
PAC_URL = "http://rbins.bosch.com/fe.pac"


def detect_proxy() -> str | None:
    """Ermittelt den zu verwendenden Proxy-Server (Env-Variable > PAC-Datei > Fallback).

    Wird fürs Geocoding (Nominatim) gebraucht, falls das Firmennetz einen
    Proxy verlangt. Identisch zur Logik in stellensuche.py.
    """
    import os

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


# --- TEMPORÄRER Workaround (siehe TODO.md, Punkt "Geocoding schlägt außerhalb
# des Bosch-Netzes fehl") ------------------------------------------------
# detect_proxy() liefert außerhalb des Bosch-Netzes/VPN trotzdem den fest
# hinterlegten Firmenproxy zurück, der dort per DNS nicht auflösbar ist
# (getaddrinfo failed). Dieser Helper versucht deshalb zuerst OHNE Proxy und
# fällt erst bei einem Fehler auf `proxy_server` zurück - funktioniert so
# sowohl im Firmennetz als auch ohne Proxy/VPN. Kann komplett entfernt werden
# (Aufruf in geocode_address() durch ein einfaches urlopen()/opener.open()
# ersetzen), sobald detect_proxy() das sauberer selbst erkennt.
def _open_with_proxy_fallback(request: urllib.request.Request, proxy_server: str | None):
    try:
        return urllib.request.urlopen(request, timeout=3)
    except (urllib.error.URLError, TimeoutError):
        if not proxy_server:
            raise
        opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({"http": proxy_server, "https": proxy_server})
        )
        return opener.open(request, timeout=10)
# --- Ende TEMPORÄRER Workaround ------------------------------------------


def load_geocode_cache(path: Path) -> dict:
    """Lädt den lokalen Geocoding-Cache, falls vorhanden.

    Der Cache bildet Adressen (lowercased) auf [lat, lon] oder null (nicht
    gefunden) ab, damit dieselbe Adresse nicht bei jedem Lauf erneut bei
    Nominatim angefragt werden muss.
    """
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_geocode_cache(cache: dict, path: Path) -> None:
    path.write_text(json.dumps(cache, ensure_ascii=False, indent=2), encoding="utf-8")


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
        with _open_with_proxy_fallback(request, proxy_server) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, ValueError) as exc:
        # Netzwerk-/Proxy-Fehler oder eine kaputte Antwort bedeuten nicht, dass die
        # Adresse nicht existiert - hier NICHT cachen, sonst "vergiftet" ein
        # vorübergehender Ausfall (z. B. kein Proxy-Zugriff) den Cache dauerhaft
        # und spätere, funktionierende Läufe finden den Ort nie mehr.
        print(f"  -> Geocoding fehlgeschlagen für '{address}': {exc}")
        return None
    finally:
        time.sleep(1)

    if not data:
        print(f"  -> Geocoding: kein Treffer bei Nominatim für '{address}'")
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


def _date_to_iso(value) -> str:
    """Wandelt ein Datumsfeld (dict mit year/month/day[/hour/minute] oder String,
    siehe _format_date()) in einen von JS parsbaren ISO-String um, für den
    Zeitraum-Filter (Vergleich mit new Date(...) im Browser)."""
    if not value:
        return ""
    if isinstance(value, dict):
        try:
            return (
                f"{value['year']:04d}-{value['month']:02d}-{value['day']:02d}"
                f"T{value.get('hour', 0):02d}:{value.get('minute', 0):02d}:00"
            )
        except (KeyError, TypeError, ValueError):
            return ""
    if isinstance(value, str):
        return value
    return ""


def _load_scan_state(path: Path) -> dict:
    """Lädt den Zustand des vorherigen Laufs für diese Eingabedatei (Zeitpunkt
    für den Zeitraum-Filter "Neu seit letztem Scan" + Job-Store für den
    Verfügbarkeits-Abgleich, siehe _merge_job_store). Gibt {} zurück, wenn noch
    kein vorheriger Lauf bekannt ist (z. B. beim allerersten Aufruf)."""
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def _save_scan_state(path: Path, state: dict) -> None:
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


# Wie viele Tage eine verschwundene Stelle noch (ausgegraut/eingeklappt) in der
# Ansicht auftaucht, bevor sie endgültig aus dem Job-Store entfernt wird -
# ansonsten würde der Store (und damit die _scan_state.json) unbegrenzt wachsen.
UNAVAILABLE_RETENTION_DAYS = 30


def _days_since(iso_ts: str | None, now_iso: str) -> float:
    if not iso_ts:
        return 0.0
    try:
        return (datetime.fromisoformat(now_iso) - datetime.fromisoformat(iso_ts)).total_seconds() / 86400
    except (ValueError, TypeError):
        return 0.0


def _merge_job_store(
    current_records: list[dict], previous_jobs: dict, current_scan_at: str
) -> tuple[list[dict], dict]:
    """Reichert die aktuell gescrapten Stellen um solche an, die im letzten
    Lauf noch da waren, jetzt aber aus dem Scan verschwunden sind (= vermutlich
    vergeben oder offline genommen).

    Verschwundene Stellen bekommen `_verfuegbar=False` und werden mit ihren
    zuletzt bekannten Daten weitergereicht, damit build_html_page/
    build_map_page sie ausgegraut/eingeklappt statt einfach unsichtbar
    anzeigen können. Nach UNAVAILABLE_RETENTION_DAYS werden sie endgültig aus
    dem zurückgegebenen Store entfernt.

    Gibt (alle_records, neuer_job_store) zurück; alle_records enthält zuerst
    die aktuell verfügbaren, danach die (noch nicht abgelaufenen) nicht mehr
    verfügbaren Stellen.
    """
    new_store: dict[str, dict] = {}
    all_records: list[dict] = []
    current_ids: set[str] = set()

    for idx, rec in enumerate(current_records):
        job_id = _job_id(rec, idx)
        current_ids.add(job_id)
        new_store[job_id] = {"record": rec, "zuletzt_gesehen": current_scan_at}
        merged = dict(rec)
        merged["_verfuegbar"] = True
        all_records.append(merged)

    for job_id, entry in previous_jobs.items():
        if job_id in current_ids:
            continue
        seit_wann_weg = entry.get("seit_wann_nicht_verfuegbar") or entry.get("zuletzt_gesehen") or current_scan_at
        if _days_since(seit_wann_weg, current_scan_at) > UNAVAILABLE_RETENTION_DAYS:
            continue
        new_store[job_id] = {
            "record": entry.get("record", {}),
            "zuletzt_gesehen": entry.get("zuletzt_gesehen"),
            "seit_wann_nicht_verfuegbar": seit_wann_weg,
        }
        merged = dict(entry.get("record", {}))
        merged["_verfuegbar"] = False
        all_records.append(merged)

    return all_records, new_store


def _job_id(rec: dict, idx: int) -> str:
    """Leitet eine stabile Kennung für eine Stelle aus ihrer URL ab (für localStorage-Keys).

    Muss in HTML- und Kartenansicht identisch berechnet werden, damit der in
    einer Ansicht gesetzte Status in der anderen Ansicht wiedergefunden wird.
    """
    return html.escape((rec.get("url") or f"job-{idx}").rsplit("/", 1)[-1] or f"job-{idx}")


# Gemeinsame Status-Optionen für das Bewerbungsstatus-Dropdown (Liste + Karte).
# Werte werden 1:1 als localStorage-Wert unter dem Key "status_<job_id>" gespeichert.
STATUS_OPTIONS = [
    ("", "Kein Status"),
    ("will_bewerben", "Möchte mich bewerben"),
    ("beworben", "Beworben"),
]


def _status_options_html(selected: str = "") -> str:
    return "".join(
        f'<option value="{value}"{" selected" if value == selected else ""}>{label}</option>'
        for value, label in STATUS_OPTIONS
    )


# Gemeinsames JavaScript für das Status-Dropdown, in Listen- und Kartenansicht
# identisch eingebettet (kein externes JS, damit beide Seiten weiterhin
# eigenständige, per Doppelklick öffnenbare Dateien bleiben).
_STATUS_SCRIPT = """
const STATUS_CLASS = { "": "", "will_bewerben": "status-will", "beworben": "status-beworben" };

function migrateOldBewerbenKey(id) {
    const oldKey = "beworben_" + id;
    const newKey = "status_" + id;
    if (localStorage.getItem(newKey) === null && localStorage.getItem(oldKey) === "1") {
        localStorage.setItem(newKey, "beworben");
    }
}

function getJobStatus(id) {
    migrateOldBewerbenKey(id);
    return localStorage.getItem("status_" + id) || "";
}

function setJobStatus(id, value) {
    localStorage.setItem("status_" + id, value);
}
"""


# Die Einstufungs-Kategorien sind unabhängige Schienen (siehe
# extract_eg_einstufung in stellensuche.py) - der Filter vergleicht deshalb
# immer NUR innerhalb der gewählten Kategorie. Beispiel: ">" PC04 zeigt nur
# PC05, PC06, ... - keine EG- oder SL-Stellen, selbst wenn deren (inzwischen
# nicht mehr genutzter) kombinierter Rang zufällig höher/niedriger wäre.
#
# "E" und "TARIF" wurden anhand einer Analyse von alle_stellen_de.json
# (bundesweite Stellenliste, 1320 Stellen) ergänzt/reaktiviert - siehe
# TODO.md für Details zu den zuvor offenen Fragen.
_EG_KATEGORIEN: dict[str, list[int]] = {
    "EG": list(range(1, 19)),
    "SL": list(range(1, 4)),
    "E": list(range(1, 17)),
    # Außertarifliche Fachlaufbahn bei englischsprachigen Stellen
    # ("analog upper tariff / PC06").
    "PC": list(range(1, 11)),
    # Gewerbliche Tarifstufen ohne EG-Nummer ("unterer"/"mittlerer"/"oberer
    # Tarif", engl. "upper tariff") - 1=unterer, 2=mittlerer, 3=oberer.
    "TARIF": [1, 2, 3],
}

_EG_KATEGORIE_TITEL = {
    "EG": "EG (tariflich)",
    "SL": "SL (außertarifliche Führung)",
    "E": "E (außertarifliche Fachlaufbahn)",
    "PC": "PC (außertarifliche Fachlaufbahn, englisch)",
    "TARIF": "Tarifstufe (gewerblich, ohne EG-Nummer)",
}

# Kategorien mit zweistelliger, führender Null (PC06, E05, ...).
_ZERO_PADDED_KATEGORIEN = {"E", "PC"}

_TARIF_LABELS = {1: "Unterer Tarif", 2: "Mittlerer Tarif", 3: "Oberer Tarif"}


def _eg_label(kategorie: str, nummer: int) -> str:
    if kategorie == "TARIF":
        return _TARIF_LABELS[nummer]
    if kategorie in _ZERO_PADDED_KATEGORIEN:
        return f"{kategorie}{nummer:02d}"
    return f"{kategorie}{nummer}"


def _eg_level_options_html() -> str:
    groups = []
    for kategorie, nummern in _EG_KATEGORIEN.items():
        opts = "".join(
            f'<option value="{kategorie}:{n}">{_eg_label(kategorie, n)}</option>' for n in nummern
        )
        groups.append(f'<optgroup label="{_EG_KATEGORIE_TITEL[kategorie]}">{opts}</optgroup>')
    return "".join(groups)


def _eg_levels_by_kategorie_json() -> str:
    data = {
        kategorie: [[n, _eg_label(kategorie, n)] for n in nummern]
        for kategorie, nummern in _EG_KATEGORIEN.items()
    }
    return json.dumps(data, ensure_ascii=False)


_EG_LABEL_PART = re.compile(r"^(EG|SL|PC|E|TARIF)(\d{1,2})$")


def _parse_eg_label(label: str | None) -> list[tuple[str, int]]:
    """Zerlegt ein (ggf. kombiniertes) Einstufungs-Label wie "EG16/SL1" oder
    "E05" wieder in einzelne (Kategorie, Nummer)-Paare, damit der Filter jede
    Kategorie isoliert vergleichen kann."""
    if not label:
        return []
    result = []
    for part in label.split("/"):
        match = _EG_LABEL_PART.match(part.strip())
        if match:
            result.append((match.group(1), int(match.group(2))))
    return result


# Ortsfilter - filtert nach der Stadt statt nach der vollen Adresse im
# "ort"-Feld (z. B. "Gerhard-Kindler-Str. 9, 72770 Reutlingen, Deutschland").
# So werden mehrere Standorte mit unterschiedlicher Adresse in derselben
# Stadt (z. B. zwei Standorte in Stuttgart) unter einem Filterwert
# zusammengefasst. Sammel-Angaben mit mehreren möglichen Einsatzorten
# (" / "-getrennt, siehe PREFERRED_MULTI_LOCATION_CITY weiter unten) liefern
# alle genannten Städte, damit die Stelle unter jeder von ihnen auffindbar ist.
_PLZ_ORT_RE = re.compile(r"^\d{4,6}\s+(.+)$")


def _extract_ort_cities(ort: str) -> list[str]:
    parts = [p.strip() for p in (ort or "").split(",") if p.strip()]
    if not parts:
        return []
    kandidat = parts[1] if len(parts) > 1 else parts[0]
    match = _PLZ_ORT_RE.match(kandidat)
    if match:
        kandidat = match.group(1)
    if " / " in kandidat:
        return [c.strip() for c in kandidat.split(" / ") if c.strip()]
    return [kandidat] if kandidat else []


def _collect_ort_options(records: list[dict]) -> list[str]:
    cities: set[str] = set()
    for rec in records:
        cities.update(_extract_ort_cities(rec.get("ort") or ""))
    return sorted(cities, key=str.casefold)


# Filter für den Bewerbungsstatus (siehe STATUS_OPTIONS) - "any" fasst
# "möchte mich bewerben" und "beworben" zusammen ("gemerkt oder beworben").
STATUS_FILTER_OPTIONS = [
    ("", "Alle"),
    ("any", "Gemerkt oder beworben"),
    ("will_bewerben", "Nur gemerkt"),
    ("beworben", "Nur beworben"),
]


def _eg_filterbar_html(ort_options: list[str] | None = None) -> str:
    eg_level_options = _eg_level_options_html()
    status_filter_options = "".join(
        f'<option value="{value}">{label}</option>' for value, label in STATUS_FILTER_OPTIONS
    )
    ort_filter_options = "".join(
        f'<option value="{html.escape(ort)}">{html.escape(ort)}</option>' for ort in (ort_options or [])
    )
    return f"""<div class="filterbar">
    <label>Einstufung
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
    <label>Status
        <select id="status-filter">
            {status_filter_options}
        </select>
    </label>
    <label>Ort
        <select id="ort-filter">
            <option value="">Alle Orte</option>
            {ort_filter_options}
        </select>
    </label>
    <label>Zeitraum
        <select id="zeit-filter">
            <option value="">Alle</option>
            <option value="scan">Neu seit letztem Scan</option>
            <option value="tage">Letzte ... Tage</option>
            <option value="wochen">Letzte ... Wochen</option>
        </select>
    </label>
    <label id="zeit-anzahl-label" style="display:none;">
        <input type="number" id="zeit-anzahl" min="1" value="7">
    </label>
    <button type="button" class="reset-btn" id="eg-reset">Filter zurücksetzen</button>
    <span class="filter-count" id="eg-filter-count"></span>
</div>"""


# Gemeinsame CSS-Regeln für die Filterleiste (EG/SL), in Listen- und
# Kartenansicht identisch eingebettet.
_FILTERBAR_CSS = """
    .filterbar {
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
    }
    .filterbar label { display: flex; align-items: center; gap: 6px; }
    .filterbar select { padding: 4px 6px; }
    .filterbar input[type="number"] { width: 60px; padding: 4px 6px; }
    .filterbar .reset-btn {
        background: none;
        border: 1px solid #005691;
        color: #005691;
        border-radius: 6px;
        padding: 4px 10px;
        cursor: pointer;
        font-size: 0.85rem;
    }
    .filterbar .reset-btn:hover { background: #e8f1f8; }
    .filter-count { color: #555; }
"""

# Gemeinsame JS-Logik für den Einstufungs- und Status-Filter (Liste + Karte).
# `egRangMatches` nimmt sowohl den kommagetrennten String aus einem
# data-Attribut ("EG:16,SL:1") als auch das rohe JSON-Array ([["EG",16],...])
# entgegen, damit dieselbe Funktion in beiden Ansichten funktioniert. Die
# Kategorie (EG/SL/E) wird dabei bewusst NIE gemischt verglichen.
def _eg_filter_script() -> str:
    levels_by_kategorie = _eg_levels_by_kategorie_json()
    return f"""
const EG_LEVELS_BY_KATEGORIE = {levels_by_kategorie};

function compare(nummer, op, target, target2) {{
    switch (op) {{
        case "=": return nummer === target;
        case "<": return nummer < target;
        case "<=": return nummer <= target;
        case ">": return nummer > target;
        case ">=": return nummer >= target;
        case "between": {{
            const lo = Math.min(target, target2);
            const hi = Math.max(target, target2);
            return nummer >= lo && nummer <= hi;
        }}
        default: return true;
    }}
}}

function populateLevel2Options() {{
    const levelSelect = document.getElementById("eg-level");
    const level2Select = document.getElementById("eg-level-2");
    const kategorie = levelSelect.value ? levelSelect.value.split(":")[0] : null;
    const previous = level2Select.value;
    let optionsHtml = '<option value="">...</option>';
    if (kategorie && EG_LEVELS_BY_KATEGORIE[kategorie]) {{
        optionsHtml += EG_LEVELS_BY_KATEGORIE[kategorie]
            .map(([nummer, label]) => `<option value="${{kategorie}}:${{nummer}}">${{label}}</option>`)
            .join("");
    }}
    level2Select.innerHTML = optionsHtml;
    if (Array.from(level2Select.options).some((o) => o.value === previous)) {{
        level2Select.value = previous;
    }}
}}

function readEgFilterState() {{
    const opSelect = document.getElementById("eg-operator");
    const levelSelect = document.getElementById("eg-level");
    const level2Select = document.getElementById("eg-level-2");
    const level2Label = document.getElementById("eg-level-2-label");
    const op = opSelect.value;
    const rawTarget = levelSelect.value;
    const rawTarget2 = level2Select.value;
    level2Label.style.display = op === "between" ? "" : "none";
    const filterActive = op === "between" ? (rawTarget !== "" && rawTarget2 !== "") : rawTarget !== "";
    let kategorie = null, target = null, target2 = null;
    if (rawTarget) {{
        const [kat, num] = rawTarget.split(":");
        kategorie = kat;
        target = parseInt(num, 10);
    }}
    if (rawTarget2) {{
        target2 = parseInt(rawTarget2.split(":")[1], 10);
    }}
    return {{ op, kategorie, target, target2, filterActive }};
}}

function parseKlassifikationen(raw) {{
    if (!raw) return [];
    if (Array.isArray(raw)) {{
        return raw.map(([kategorie, nummer]) => ({{ kategorie, nummer }}));
    }}
    return raw.split(",").filter(Boolean).map((part) => {{
        const [kategorie, nummer] = part.split(":");
        return {{ kategorie, nummer: parseInt(nummer, 10) }};
    }});
}}

function egRangMatches(rawKlass, filterState) {{
    if (!filterState.filterActive) return true;
    const klass = parseKlassifikationen(rawKlass);
    return klass.some((k) => {{
        if (k.kategorie !== filterState.kategorie) return false;
        if (filterState.op === "between") {{
            return compare(k.nummer, filterState.op, filterState.target, filterState.target2);
        }}
        return compare(k.nummer, filterState.op, filterState.target);
    }});
}}

function statusMatches(status, filterValue) {{
    if (!filterValue) return true;
    if (filterValue === "any") return status === "will_bewerben" || status === "beworben";
    return status === filterValue;
}}

function parseOrtCities(raw) {{
    if (!raw) return [];
    if (Array.isArray(raw)) return raw;
    return raw.split("|").filter(Boolean);
}}

function ortMatches(rawOrtCities, filterValue) {{
    if (!filterValue) return true;
    return parseOrtCities(rawOrtCities).includes(filterValue);
}}

function wireEgFilterControls(onChange) {{
    const levelSelect = document.getElementById("eg-level");
    levelSelect.addEventListener("change", () => {{
        populateLevel2Options();
        onChange();
    }});
    document.getElementById("eg-operator").addEventListener("change", onChange);
    document.getElementById("eg-level-2").addEventListener("change", onChange);
    document.getElementById("status-filter").addEventListener("change", onChange);
    document.getElementById("ort-filter").addEventListener("change", onChange);
    document.getElementById("eg-reset").addEventListener("click", () => {{
        levelSelect.value = "";
        populateLevel2Options();
        document.getElementById("eg-operator").value = "=";
        document.getElementById("status-filter").value = "";
        document.getElementById("ort-filter").value = "";
        document.getElementById("zeit-filter").value = "";
        document.getElementById("zeit-anzahl").value = "7";
        readZeitFilterState();
        onChange();
    }});
    populateLevel2Options();
}}
"""


# Zeitraum-Filter (Liste + Karte) - vergleicht das "aktualisiert"-Datum jeder
# Stelle entweder gegen den Zeitpunkt des vorherigen Laufs ("Neu seit letztem
# Scan", siehe _load_scan_state/_save_scan_state) oder gegen "jetzt minus
# X Tage/Wochen". PREVIOUS_SCAN_AT wird als Konstante eingebettet, weil der
# Vergleichszeitpunkt (Zeitpunkt des vorherigen Skript-Laufs) sich nicht aus
# den Job-Daten selbst ableiten lässt.
def _zeit_filter_script(previous_scan_at: str | None) -> str:
    previous_scan_json = json.dumps(previous_scan_at)
    return f"""
const PREVIOUS_SCAN_AT = {previous_scan_json};

function readZeitFilterState() {{
    const modeSelect = document.getElementById("zeit-filter");
    const anzahlLabel = document.getElementById("zeit-anzahl-label");
    const anzahlInput = document.getElementById("zeit-anzahl");
    const mode = modeSelect.value;
    anzahlLabel.style.display = (mode === "tage" || mode === "wochen") ? "" : "none";

    if (mode === "scan") {{
        if (!PREVIOUS_SCAN_AT) return {{ active: false, cutoff: null }};
        return {{ active: true, cutoff: new Date(PREVIOUS_SCAN_AT) }};
    }}
    if (mode === "tage" || mode === "wochen") {{
        const anzahl = parseInt(anzahlInput.value, 10);
        if (!anzahl || anzahl < 1) return {{ active: false, cutoff: null }};
        const tage = mode === "wochen" ? anzahl * 7 : anzahl;
        const cutoff = new Date();
        cutoff.setDate(cutoff.getDate() - tage);
        return {{ active: true, cutoff }};
    }}
    return {{ active: false, cutoff: null }};
}}

function zeitMatches(rawDate, zeitFilterState) {{
    if (!zeitFilterState.active) return true;
    if (!rawDate) return false;
    const parsed = new Date(rawDate);
    if (isNaN(parsed.getTime())) return false;
    return parsed >= zeitFilterState.cutoff;
}}

function wireZeitFilterControls(onChange) {{
    document.getElementById("zeit-filter").addEventListener("change", () => {{
        readZeitFilterState();
        onChange();
    }});
    document.getElementById("zeit-anzahl").addEventListener("input", onChange);
}}
"""


def build_html_page(
    records: list[dict],
    map_filename: str | None = None,
    previous_scan_at: str | None = None,
) -> str:
    """Erzeugt eine übersichtliche, eigenständige HTML-Seite aus den Job-Records.

    Enthält ein Status-Dropdown (Kein Status / Möchte mich bewerben / Beworben)
    pro Stelle. Der Status wird im Browser via localStorage gespeichert und
    bleibt so auch nach erneutem Erzeugen der Seite erhalten (solange dieselbe
    Datei am selben Pfad geöffnet wird). Zusätzlich ein Button zum
    Herunterladen der Stellenanzeige als .txt-Datei.
    """
    generated_at = datetime.now().strftime("%d.%m.%Y %H:%M")

    cards = []
    jobs_data: dict[str, dict[str, str]] = {}
    for idx, rec in enumerate(records):
        title = html.escape(rec.get("jobtitel") or "")
        url = html.escape(rec.get("url") or "#")
        ort_raw = rec.get("ort") or ""
        ort = html.escape(ort_raw)
        ort_city_attr = html.escape("|".join(_extract_ort_cities(ort_raw)))
        region = html.escape(rec.get("region") or "")
        land = html.escape(rec.get("land") or "")
        anstellungsart = html.escape(rec.get("anstellungsart") or "")
        unternehmen = html.escape(rec.get("unternehmen") or "")
        aktualisiert = html.escape(_format_date(rec.get("aktualisiert")))
        aktualisiert_iso = html.escape(_date_to_iso(rec.get("aktualisiert")))
        begriffe = rec.get("gefundene_suchbegriffe") or []
        tags = "".join(f'<span class="tag">{html.escape(b)}</span>' for b in begriffe)
        stellentext_raw = rec.get("stellentext") or ""
        stellentext = html.escape(stellentext_raw).replace("\n", "<br>")
        eg_label = rec.get("eg_einstufung")
        eg_klass = _parse_eg_label(eg_label)
        eg_klass_attr = html.escape(",".join(f"{kategorie}:{nummer}" for kategorie, nummer in eg_klass))
        # Anzeige nutzt _eg_label() statt des rohen Labels, damit z. B.
        # "TARIF3" lesbar als "Oberer Tarif" erscheint (EG/SL/PC/E bleiben
        # dabei wie zuvor, da _eg_label() dort denselben Text liefert).
        eg_display = "/".join(_eg_label(kategorie, nummer) for kategorie, nummer in eg_klass)
        eg_tag = f'<span class="tag eg-tag">{html.escape(eg_display)}</span>' if eg_display else ""

        job_id = _job_id(rec, idx)
        jobs_data[job_id] = {
            "title": rec.get("jobtitel") or "",
            "text": stellentext_raw,
        }

        meta_parts = [p for p in (ort, region, land) if p]
        meta_line = ", ".join(meta_parts)

        # Stellen, die im aktuellen Scan nicht mehr auftauchten (siehe
        # _merge_job_store), werden ausgegraut, mit "Vergeben"-Badge markiert
        # und eingeklappt dargestellt, statt einfach aus der Liste zu
        # verschwinden.
        verfuegbar = rec.get("_verfuegbar", True)
        vergeben_badge = '<span class="vergeben-badge">🚫 Vergeben / nicht mehr verfügbar</span>' if not verfuegbar else ""

        body_html = f"""
            <div class="meta">
                {f'<span>📍 {meta_line}</span>' if meta_line else ''}
                {f'<span>🏢 {unternehmen}</span>' if unternehmen else ''}
                {f'<span>💼 {anstellungsart}</span>' if anstellungsart else ''}
                {f'<span>🕒 Aktualisiert: {aktualisiert}</span>' if aktualisiert else ''}
            </div>
            <div class="tags">{eg_tag}{tags}</div>
            <div class="actions">
                <label class="status-label">
                    Status:
                    <select class="status-select" data-id="{job_id}">
                        {_status_options_html()}
                    </select>
                </label>
                <button type="button" class="download-btn" data-id="{job_id}">⬇️ Als TXT herunterladen</button>
                <button type="button" class="copy-btn" data-id="{job_id}">📋 Text kopieren</button>
            </div>
            <details>
                <summary>Stellenbeschreibung anzeigen</summary>
                <div class="stellentext">{stellentext}</div>
            </details>"""

        if verfuegbar:
            card_inner = body_html
        else:
            card_inner = f"""
            <details>
                <summary>Details anzeigen (eingeklappt, vermutlich vergeben)</summary>
                {body_html}
            </details>"""

        cards.append(f"""
        <article class="card{'' if verfuegbar else ' unavailable'}" id="card_{job_id}" data-eg-klass="{eg_klass_attr}" data-ort-city="{ort_city_attr}" data-aktualisiert="{aktualisiert_iso}" data-verfuegbar="{'1' if verfuegbar else '0'}">
            <h2><a href="{url}" target="_blank" rel="noopener">{title}</a>{vergeben_badge}</h2>{card_inner}
        </article>""")

    cards_html = "\n".join(cards) if cards else '<p class="empty">Keine passenden Stellen gefunden.</p>'
    jobs_data_json = json.dumps(jobs_data, ensure_ascii=False).replace("</", "<\\/")

    verfuegbar_count = sum(1 for rec in records if rec.get("_verfuegbar", True))
    vergeben_count = len(records) - verfuegbar_count
    ort_options = _collect_ort_options(records)

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
    .card.status-will {{ border-left: 4px solid #f9a825; }}
    .card.status-beworben {{
        opacity: 0.6;
        border-left: 4px solid #2e7d32;
    }}
    .card.unavailable {{
        opacity: 0.55;
        filter: grayscale(70%);
        background: #ececec;
        border-left: 4px solid #888;
    }}
    .card.unavailable h2 a {{ color: #555; }}
    .card h2 {{ margin: 0 0 8px 0; font-size: 1.15rem; }}
    .card h2 a {{ color: #005691; text-decoration: none; }}
    .card h2 a:hover {{ text-decoration: underline; }}
    .vergeben-badge {{
        display: inline-block;
        background: #555;
        color: #fff;
        border-radius: 10px;
        padding: 2px 8px;
        font-size: 0.75rem;
        font-weight: normal;
        margin-left: 8px;
        vertical-align: middle;
    }}
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
    .status-label {{ display: flex; align-items: center; gap: 6px; }}
    .status-select {{ padding: 3px 6px; }}
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
    .copy-btn {{
        background: #fff;
        color: #005691;
        border: 1px solid #005691;
        border-radius: 6px;
        padding: 6px 12px;
        font-size: 0.85rem;
        cursor: pointer;
    }}
    .copy-btn:hover {{ background: #e8f1f8; }}
    .copy-btn.copied {{ background: #2e7d32; border-color: #2e7d32; color: #fff; }}
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
    {_FILTERBAR_CSS}
</style>
</head>
<body>
<header>
    <h1>Bosch Stellensuche – Ergebnisse</h1>
    <p>{verfuegbar_count} passende Stelle(n){f' &middot; {vergeben_count} davon nicht mehr verfügbar' if vergeben_count else ''} &middot; erzeugt am {generated_at}{f' &middot; <a href="{html.escape(map_filename)}">🗺️ Karte anzeigen</a>' if map_filename else ''}</p>
</header>
{_eg_filterbar_html(ort_options)}
<main>
{cards_html}
</main>
<script>
const JOBS_DATA = {jobs_data_json};
{_STATUS_SCRIPT}
{_eg_filter_script()}
{_zeit_filter_script(previous_scan_at)}
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

function copyText(id, btn) {{
    const job = JOBS_DATA[id];
    if (!job) return;
    const content = job.title + "\\n\\n" + job.text;
    const showCopied = () => {{
        const original = btn.textContent;
        btn.textContent = "✅ Kopiert!";
        btn.classList.add("copied");
        setTimeout(() => {{
            btn.textContent = original;
            btn.classList.remove("copied");
        }}, 1500);
    }};
    if (navigator.clipboard && window.isSecureContext) {{
        navigator.clipboard.writeText(content).then(showCopied).catch(() => fallbackCopy(content, showCopied));
    }} else {{
        fallbackCopy(content, showCopied);
    }}
}}

function fallbackCopy(text, onSuccess) {{
    const textarea = document.createElement("textarea");
    textarea.value = text;
    textarea.style.position = "fixed";
    textarea.style.opacity = "0";
    document.body.appendChild(textarea);
    textarea.select();
    try {{
        document.execCommand("copy");
        onSuccess();
    }} catch (err) {{
        alert("Kopieren fehlgeschlagen. Bitte Text manuell markieren.");
    }}
    document.body.removeChild(textarea);
}}

function applyStatusToCard(select) {{
    const id = select.dataset.id;
    const card = document.getElementById("card_" + id);
    if (!card) return;
    card.classList.remove("status-will", "status-beworben");
    card.dataset.status = select.value;
    const cls = STATUS_CLASS[select.value];
    if (cls) card.classList.add(cls);
}}

document.addEventListener("DOMContentLoaded", () => {{
    const countLabel = document.getElementById("eg-filter-count");
    const allCards = Array.from(document.querySelectorAll(".card"));

    function applyFilters() {{
        const filterState = readEgFilterState();
        const statusFilterValue = document.getElementById("status-filter").value;
        const ortFilterValue = document.getElementById("ort-filter").value;
        const zeitFilterState = readZeitFilterState();
        let visible = 0;
        allCards.forEach((card) => {{
            const show = egRangMatches(card.dataset.egKlass, filterState)
                && statusMatches(card.dataset.status, statusFilterValue)
                && ortMatches(card.dataset.ortCity, ortFilterValue)
                && zeitMatches(card.dataset.aktualisiert, zeitFilterState);
            card.style.display = show ? "" : "none";
            if (show) visible++;
        }});
        countLabel.textContent = (filterState.filterActive || statusFilterValue !== "" || ortFilterValue !== "" || zeitFilterState.active)
            ? `${{visible}} von ${{allCards.length}} Stellen passen zum Filter`
            : "";
    }}

    document.querySelectorAll(".status-select").forEach((select) => {{
        const id = select.dataset.id;
        select.value = getJobStatus(id);
        applyStatusToCard(select);
        select.addEventListener("change", () => {{
            setJobStatus(id, select.value);
            applyStatusToCard(select);
            applyFilters();
        }});
    }});

    document.querySelectorAll(".download-btn").forEach((btn) => {{
        btn.addEventListener("click", () => downloadTxt(btn.dataset.id));
    }});

    document.querySelectorAll(".copy-btn").forEach((btn) => {{
        btn.addEventListener("click", () => copyText(btn.dataset.id, btn));
    }});

    wireEgFilterControls(applyFilters);
    wireZeitFilterControls(applyFilters);
}});
</script>
</body>
</html>
"""


# Manche Stellen (z. B. bei ITK) listen mehrere mögliche Einsatzorte in einem
# Feld, z. B. "Im Speyerer Tal 6, Rülzheim / Holzkirchen / ... / Stuttgart,
# Deutschland". Die volle Adresse (Straße vom erstgenannten Ort + mehrere
# Ortsnamen) lässt sich bei Nominatim nicht auflösen. Ist einer der
# genannten Orte PREFERRED_MULTI_LOCATION_CITY, wird stattdessen nur dieser
# Ort (+ Land) fürs Geocoding verwendet, damit die Stelle trotzdem sinnvoll
# auf der Karte auftaucht.
PREFERRED_MULTI_LOCATION_CITY = "Stuttgart"


def _geocode_query_for_ort(ort: str) -> str:
    parts = [p.strip() for p in ort.split(",") if p.strip()]
    for part in parts:
        if " / " not in part:
            continue
        cities = [c.strip() for c in part.split(" / ")]
        if any(c.lower() == PREFERRED_MULTI_LOCATION_CITY.lower() for c in cities):
            land = parts[-1] if len(parts) > 1 and parts[-1] != part else ""
            return f"{PREFERRED_MULTI_LOCATION_CITY}, {land}" if land else PREFERRED_MULTI_LOCATION_CITY
        break
    return ort


def build_map_page(
    records: list[dict],
    geocode_cache: dict,
    proxy_server: str | None = None,
    previous_scan_at: str | None = None,
) -> str:
    """Erzeugt eine eigenständige HTML-Seite mit einer Karte (Leaflet/OSM),
    auf der jede Stelle als Punkt an ihrem Standort eingeblendet wird.

    Jede Stelle im Popup hat dasselbe Status-Dropdown wie die Listenansicht
    (gleicher localStorage-Key "status_<job_id>", da _job_id() identisch
    berechnet wird).

    Adressen werden über den übergebenen Geocoding-Cache aufgelöst (siehe
    geocode_address). Mehrere Stellen am selben Ort werden zu einem
    gemeinsamen Marker zusammengefasst.
    """
    generated_at = datetime.now().strftime("%d.%m.%Y %H:%M")

    points: dict[str, dict] = {}
    missing = 0
    for idx, rec in enumerate(records):
        ort = (rec.get("ort") or "").strip()
        if not ort:
            missing += 1
            continue
        geocode_query = _geocode_query_for_ort(ort)
        coords = geocode_address(geocode_query, geocode_cache, proxy_server=proxy_server)
        if not coords:
            missing += 1
            continue
        lat, lon = coords
        key = f"{lat:.5f},{lon:.5f}"
        entry = points.setdefault(key, {"lat": lat, "lon": lon, "ort": ort, "jobs": []})
        entry["jobs"].append({
            "id": _job_id(rec, idx),
            "title": rec.get("jobtitel") or "",
            "url": rec.get("url") or "#",
            "eg_klass": _parse_eg_label(rec.get("eg_einstufung")),
            "ort_city": _extract_ort_cities(ort),
            "aktualisiert": _date_to_iso(rec.get("aktualisiert")),
            "verfuegbar": rec.get("_verfuegbar", True),
        })

    markers = list(points.values())
    markers_json = json.dumps(markers, ensure_ascii=False).replace("</", "<\\/")
    verfuegbar_count = sum(1 for rec in records if rec.get("_verfuegbar", True))
    vergeben_count = len(records) - verfuegbar_count
    ort_options = _collect_ort_options(records)

    return f"""<!DOCTYPE html>
<html lang="de">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Bosch Stellensuche – Karte</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" />
<style>
    :root {{ color-scheme: light dark; }}
    body {{
        font-family: "Segoe UI", Arial, sans-serif;
        margin: 0;
        padding: 0;
        color: #1a1a1a;
        display: flex;
        flex-direction: column;
        height: 100vh;
    }}
    header {{
        padding: 16px 24px;
        background: #fff;
        border-bottom: 1px solid #e0e0e0;
        flex: none;
    }}
    header h1 {{ margin: 0 0 4px 0; font-size: 1.3rem; }}
    header p {{ color: #555; margin: 0; font-size: 0.9rem; }}
    header a {{ color: #005691; text-decoration: none; }}
    header a:hover {{ text-decoration: underline; }}
    #map {{ flex: 1 1 auto; width: 100%; }}
    .popup-jobs {{ margin: 6px 0 0 0; padding-left: 18px; }}
    .popup-jobs li {{ margin-bottom: 8px; }}
    .popup-jobs li.unavailable {{ opacity: 0.55; filter: grayscale(70%); }}
    .popup-jobs select {{ display: block; margin-top: 4px; padding: 2px 4px; font-size: 0.85rem; }}
    .vergeben-badge {{
        display: inline-block;
        background: #555;
        color: #fff;
        border-radius: 10px;
        padding: 1px 7px;
        font-size: 0.72rem;
        margin-left: 6px;
        vertical-align: middle;
    }}
    {_FILTERBAR_CSS}
    .filterbar {{ margin: 12px 24px 0; flex: none; }}
</style>
</head>
<body>
<header>
    <h1>Bosch Stellensuche – Karte</h1>
    <p>{len(markers)} Standort(e) &middot; {verfuegbar_count} passende Stelle(n)
        {f' &middot; {vergeben_count} davon nicht mehr verfügbar' if vergeben_count else ''}
        {f' &middot; {missing} ohne ermittelbaren Standort' if missing else ''}
        &middot; erzeugt am {generated_at} &middot; <a href="javascript:history.back()">← Zurück zur Liste</a></p>
</header>
{_eg_filterbar_html(ort_options)}
<div id="map"></div>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<script>
const MARKERS = {markers_json};
{_STATUS_SCRIPT}
{_eg_filter_script()}
{_zeit_filter_script(previous_scan_at)}
function updateJobStatus(select) {{
    setJobStatus(select.dataset.id, select.value);
    renderMarkers();
}}

const map = L.map('map').setView([51.1657, 10.4515], 6);
L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>-Mitwirkende'
}}).addTo(map);

const STATUS_OPTIONS = {json.dumps(STATUS_OPTIONS, ensure_ascii=False)};
const markerLayer = L.layerGroup().addTo(map);
const countLabel = document.getElementById("eg-filter-count");

const allBounds = MARKERS.map((pt) => [pt.lat, pt.lon]);
if (allBounds.length) {{
    map.fitBounds(allBounds, {{ padding: [30, 30] }});
}}

function jobsHtmlFor(jobs) {{
    return jobs.map((j) => {{
        const safeTitle = j.title.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        const stored = getJobStatus(j.id);
        const optionsHtml = STATUS_OPTIONS.map(([value, label]) =>
            `<option value="${{value}}"${{value === stored ? " selected" : ""}}>${{label}}</option>`
        ).join("");
        const unavailable = j.verfuegbar === false;
        const badge = unavailable ? '<span class="vergeben-badge">🚫 Vergeben</span>' : "";
        return `<li${{unavailable ? ' class="unavailable"' : ""}}><a href="${{j.url}}" target="_blank" rel="noopener">${{safeTitle}}</a>${{badge}}
            <select data-id="${{j.id}}" onchange="updateJobStatus(this)">${{optionsHtml}}</select>
        </li>`;
    }}).join("");
}}

function renderMarkers() {{
    const filterState = readEgFilterState();
    const statusFilterValue = document.getElementById("status-filter").value;
    const ortFilterValue = document.getElementById("ort-filter").value;
    const zeitFilterState = readZeitFilterState();
    markerLayer.clearLayers();
    let visibleMarkers = 0, visibleJobs = 0, totalJobs = 0;
    MARKERS.forEach((pt) => {{
        totalJobs += pt.jobs.length;
        const matching = pt.jobs.filter((j) =>
            egRangMatches(j.eg_klass, filterState) && statusMatches(getJobStatus(j.id), statusFilterValue)
            && ortMatches(j.ort_city, ortFilterValue)
            && zeitMatches(j.aktualisiert, zeitFilterState)
        );
        if (matching.length === 0) return;
        visibleMarkers++;
        visibleJobs += matching.length;
        const safeOrt = pt.ort.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
        const popupHtml = `<strong>${{safeOrt}}</strong><ul class="popup-jobs">${{jobsHtmlFor(matching)}}</ul>`;
        L.marker([pt.lat, pt.lon]).bindPopup(popupHtml).addTo(markerLayer);
    }});
    countLabel.textContent = (filterState.filterActive || statusFilterValue !== "" || ortFilterValue !== "" || zeitFilterState.active)
        ? `${{visibleJobs}} von ${{totalJobs}} Stellen an ${{visibleMarkers}} von ${{MARKERS.length}} Standort(en) passen zum Filter`
        : "";
}}

renderMarkers();
wireEgFilterControls(renderMarkers);
wireZeitFilterControls(renderMarkers);
</script>
</body>
</html>
"""


def process_records(
    records: list[dict],
    base_path: Path,
    html_output: str | None = None,
    map_output: str | None = None,
    no_map: bool = False,
    proxy_server: str | None = None,
) -> None:
    """Baut HTML- und Kartenansicht aus `records` und schreibt sie neben `base_path`.

    `base_path` ist die (fiktive oder echte) JSON-Datei, aus der die Records
    stammen - Ausgabedateien landen standardmäßig im selben Ordner mit
    abgeleitetem Namen (z. B. bosch_jobs_gefiltert.html / _karte.html).
    """
    out_dir = base_path.parent
    html_output_path = out_dir / html_output if html_output else base_path.with_suffix(".html")
    map_output_path = (
        out_dir / map_output if map_output else out_dir / f"{base_path.stem}_karte.html"
    )
    cache_path = out_dir / "geocode_cache.json"
    scan_state_path = out_dir / f"{base_path.stem}_scan_state.json"

    # Zeitpunkt und Job-Store des VORHERIGEN Laufs für diese Eingabedatei -
    # müssen vor dem Überschreiben der Scan-State-Datei ausgelesen werden.
    scan_state = _load_scan_state(scan_state_path)
    previous_scan_at = scan_state.get("last_scan_at")
    current_scan_at = datetime.now().isoformat(timespec="seconds")

    # Stellen, die im vorherigen Lauf noch da waren, jetzt aber aus dem Scan
    # verschwunden sind, werden als "vergeben"/nicht mehr verfügbar
    # weitergereicht (ausgegraut + eingeklappt in der Ansicht), statt einfach
    # zu verschwinden - siehe _merge_job_store.
    all_records, job_store = _merge_job_store(records, scan_state.get("jobs", {}), current_scan_at)

    map_filename = None
    if not no_map:
        print("\nErmittle Standorte für die Kartenansicht (Geocoding via OpenStreetMap)...")
        geocode_cache = load_geocode_cache(cache_path)
        try:
            map_html = build_map_page(
                all_records, geocode_cache, proxy_server=proxy_server, previous_scan_at=previous_scan_at
            )
        finally:
            save_geocode_cache(geocode_cache, cache_path)
        map_output_path.write_text(map_html, encoding="utf-8")
        print(f"Kartenansicht gespeichert in: {map_output_path}")
        map_filename = map_output_path.name

    html_output_path.write_text(
        build_html_page(all_records, map_filename=map_filename, previous_scan_at=previous_scan_at),
        encoding="utf-8",
    )
    print(f"HTML-Übersicht gespeichert in: {html_output_path}")

    _save_scan_state(scan_state_path, {"last_scan_at": current_scan_at, "jobs": job_store})


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=str, default="bosch_jobs_gefiltert.json",
                         help="JSON-Datei mit den Stellen-Datensätzen (Default: bosch_jobs_gefiltert.json)")
    parser.add_argument("--html-output", type=str, default=None,
                         help="HTML-Ausgabedatei (Default: gleicher Name wie --input mit .html-Endung)")
    parser.add_argument("--map-output", type=str, default=None,
                         help="HTML-Kartenansicht (Default: gleicher Name wie --input mit _karte.html-Endung)")
    parser.add_argument("--no-map", action="store_true",
                         help="Keine Kartenansicht erzeugen (spart Geocoding-Anfragen)")
    args = parser.parse_args()

    input_path = Path(args.input)
    records = json.loads(input_path.read_text(encoding="utf-8"))
    print(f"{len(records)} Stellen aus {input_path} geladen.")

    proxy_server = None if args.no_map else detect_proxy()
    process_records(
        records, input_path,
        html_output=args.html_output, map_output=args.map_output,
        no_map=args.no_map, proxy_server=proxy_server,
    )


if __name__ == "__main__":
    main()
