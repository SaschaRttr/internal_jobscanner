# TODO

- **EG/SL/PC-Einstufungs-Parsing** (`extract_eg_einstufung` /
  `_EG_SL_PATTERN` in `stellensuche.py`) - **umgesetzt**:
  - `PC01`, ... `PC06`, ... (bei englischsprachigen Stellen, im Titel oft
    "analog upper tariff / PC06") sind eine eigene, außertarifliche Schiene
    für englische Stellenausschreibungen. Rang = 300 + Zahl - **vorläufige
    Platzhalter-Einordnung** (offene Frage, wie sich PC-Stufen zahlenmäßig zu
    EG/SL wirklich verhalten sollen, laut Rücksprache "noch unklar / später
    klären") - einfach anpassbar an einer Stelle, sobald geklärt.
  - Kombinierte Angaben wie `EG16/SL1` (außertarifliche Verträge) werden jetzt
    beide erfasst: Label wird zu `"EG16/SL1"` kombiniert, als `eg_rang` zählt
    der höhere der beiden Werte (hier SL1 = 101) - dieser kombinierte Rang
    dient nur noch der Anzeige/Sortierung, NICHT mehr dem Filter (siehe
    unten).
  - **`E01`...`E16` ist jetzt REAKTIVIERT** (Regex-Alternative in
    `_EG_SL_PATTERN` + Auswertung in `extract_eg_einstufung`, außerdem die
    `"E"`-Kategorie in `_EG_KATEGORIEN`/`_EG_KATEGORIE_TITEL` in
    `stellen_verarbeitung.py`) - Analyse von `alle_stellen_de.json` (1320
    bundesweite Bosch-Stellen, Stand 24.08.2026) zeigt 53 echte Titel mit
    E01, E03, E05, E06, E07, E08, E10, E16 (u. a. "Safety Expert (E06,
    w/m/div.)", "HR Expert Talent Management (E16, f/m/div.)"). Rang =
    200 + Zahl. Kategorie-Bereich in `_EG_KATEGORIEN` auf `range(1, 17)`
    gesetzt (höchster gefundener Wert: E16).
  - **"oberer Tarif" umgesetzt** - anhand derselben Analyse gefunden: 86
    echte Titel mit "unterer"/"mittlerer"/"oberer Tarif" (bzw. engl.
    "upper tariff"), auch als Bereich ("unterer/mittlerer Tarif") oder
    kombiniert mit SL ("Oberer Tarif/SL1") bzw. PC ("analog upper tariff /
    PC05"). Neue eigene Schiene `TARIF` (1=unterer, 2=mittlerer, 3=oberer,
    Rang = 400 + Zahl) in `_TARIF_PATTERN`/`_TARIF_WORT_ZU_NUMMER`
    (`stellensuche.py`) sowie `_EG_KATEGORIEN`/`_EG_KATEGORIE_TITEL` in
    `stellen_verarbeitung.py`. Anzeige auf der Karte nutzt jetzt `_eg_label()`
    statt des rohen Labels, damit "TARIF3" lesbar als "Oberer Tarif"
    erscheint.
  - **Nicht umgesetzt: "AT" ohne Nummer** - bei der Analyse zusätzlich
    gefunden: 6 Buderus-Außendienst-Titel mit `(AT, w/m/div.)` (AT =
    außertariflich) OHNE Zahl dahinter. Passt nicht ins numerische
    Kategorie-Schema (Operatoren `<`/`>`/"zwischen" ergäben keinen Sinn) und
    betrifft nur eine Handvoll Stellen einer einzigen Marke - bewusst nicht
    in den Filter aufgenommen. Bei Bedarf leicht als eigene Kategorie mit
    einer einzigen Stufe ergänzbar.
  - Getestet gegen alle 30 echten Datensätze aus `bosch_jobs_gefiltert.json`
    (in-memory neu extrahiert, ohne die Datei zu verändern). **Hinweis**:
    `bosch_jobs_gefiltert.json` selbst enthält noch die alten
    `eg_einstufung`-Werte aus einem Lauf vor diesen Änderungen (u. a. noch
    `E05`/`E06` bei den ITK-Stellen) - die neue Logik wirkt erst ab dem
    nächsten echten Scraper-Lauf (`stellensuche.py`), der die Records neu
    baut.
  - Weiterhin offen: genauer anschauen, welche anderen Schreibweisen im
    Portal vorkommen (das Regex ist noch nicht an echten Beispielen für alle
    Varianten geprüft).

- **EG/SL/PC-Filter kategorie-isoliert umgebaut** (`stellen_verarbeitung.py`)
  - **umgesetzt**: Der alte Filter verglich alle Kategorien über einen
    einzigen kombinierten `eg_rang`-Wert (SL=100+n, PC=300+n, früher auch
    E=200+n) - dadurch konnte z. B. "> SL1" fälschlich auch andere Stufen
    zeigen, deren kombinierter Rang zufällig höher war. Jetzt trägt jede
    Stelle ihre Einstufungen strukturiert als (Kategorie, Nummer)
    (`data-eg-klass="EG:16,SL:1"` in der Liste, `eg_klass` im Karten-JSON,
    beide aus `_parse_eg_label()` abgeleitet). Operator/Vergleich
    (`egRangMatches` in `_eg_filter_script()`) wirkt jetzt NUR innerhalb der
    im Dropdown gewählten Kategorie - EG, SL und PC werden nie gemischt.
    Das Level-Dropdown ist per `<optgroup>` nach Kategorie gruppiert; beim
    "zwischen"-Filter engt `populateLevel2Options()` die zweite Auswahl
    automatisch auf dieselbe Kategorie ein. Getestet u. a. mit einem
    synthetischen `EG16/SL1`-Datensatz (matcht sowohl auf `EG >= 14` als
    auch auf `SL = 1`).

- **Status-Filter ergänzt** (Liste + Karte) - **umgesetzt**: Neues
  Dropdown "Status" in der Filterleiste (Alle / Gemerkt oder beworben / Nur
  gemerkt / Nur beworben), kombinierbar (UND) mit dem Einstufungs-Filter.
  Liste nutzt `card.dataset.status` (wird beim Setzen des Status live
  aktualisiert), Karte liest den Status direkt aus localStorage
  (`getJobStatus`) beim Neu-Rendern der Marker.

- **Geocoding schlägt außerhalb des Bosch-Netzes fehl (DNS-Fehler)** - Ursache
  gefunden, Fix noch nicht eingebaut: `detect_proxy()` in
  `stellen_verarbeitung.py` fällt IMMER auf den fest hinterlegten
  Bosch-Firmenproxy (`rb-proxy-de.bosch.com`) zurück, sobald keine
  Proxy-Env-Variable gesetzt ist und die PAC-Datei nicht erreichbar ist - auch
  wenn man gar nicht im Firmennetz/VPN ist. Der Proxy-Hostname lässt sich dann
  per DNS nicht auflösen (`getaddrinfo failed`), jede NEUE (noch nicht
  gecachte) Geocoding-Anfrage schlägt fehl. Bereits gecachte Orte sind davon
  nicht betroffen (kein Netzwerk-Request nötig). Genau daran ist auch der
  Stuttgart-Fallback für ITK-Stellen (`_geocode_query_for_ort`) beim Testen
  gescheitert - die Erkennungslogik selbst ist korrekt (isoliert ohne
  Netzwerk getestet), nur die Netzwerkanfrage kam nicht durch.
  - **Umgesetzt** (als TEMPORÄRER, klar abgegrenzter Workaround): Neue
    Funktion `_open_with_proxy_fallback()` in `stellen_verarbeitung.py`
    (direkt vor `load_geocode_cache`) - versucht zuerst OHNE Proxy, fällt
    erst bei Fehler auf `proxy_server` zurück. Bewusst NICHT fest mit
    `geocode_address` verwoben, sondern als eigene Funktion mit
    Kommentar-Markierung "TEMPORÄRER Workaround" - kann später einfach
    wieder entfernt werden (Aufruf in `geocode_address` durch normales
    `urlopen`/`opener.open` ersetzen), sobald `detect_proxy()` das sauberer
    selbst erkennt.
