# Untis Public – Home Assistant Integration

Stundenplan, Vertretungen und die **Nachrichten des Tages** aus WebUntis – **ohne Login**,
genau wie in der Untis App: Schule suchen → Klasse wählen → fertig.

Funktioniert mit jeder Schule, die in WebUntis den **anonymen/öffentlichen Zugriff** auf den
Klassenstundenplan freigeschaltet hat.

## Installation

**HACS (empfohlen)**

[![In HACS öffnen](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=mcjoin&repository=UntisIntegration&category=integration)

1. HACS öffnen → oben rechts **⋮ → Benutzerdefinierte Repositories**
2. URL `https://github.com/mcjoin/UntisIntegration`, Typ **Integration** → *Hinzufügen*
3. „Untis Public“ suchen → **Herunterladen**
4. Home Assistant neu starten

Updates erscheinen danach automatisch in HACS (sobald ein neues GitHub-Release veröffentlicht wird).

**Manuell**: den Ordner `custom_components/untis_public` nach `<config>/custom_components/`
kopieren und Home Assistant neu starten.

Danach: *Einstellungen → Geräte & Dienste → Integration hinzufügen → „Untis Public“*.

1. Schulname oder Ort eingeben (wie in der App)
2. Schule auswählen
3. Klasse auswählen

Mehrere Kinder / Klassen = Integration mehrfach hinzufügen.

In den **Optionen** (*Konfigurieren* an der Integration) gibt es:

* **Aktualisierung & Vorschau** – Abrufintervall (Standard 15 min) und Vorschau (Standard 14 Tage)
* **Lehrer umbenennen** – für jedes Lehrer-Kürzel im Stundenplan (z. B. `MUE`) ein Feld für den
  Anzeigenamen (z. B. „Frau Müller“). Lehrer, die gerade nicht im Plan stehen, können im Textfeld
  unten mit einer Zeile pro Lehrer ergänzt werden: `MUE = Frau Müller`. Feld leeren = Kürzel wird wieder angezeigt.
* **Fächer umbenennen** – dasselbe für Fach-Kürzel (`AM_G1` → „Mathe“). Die Langnamen, die Untis
  mitliefert, werden als Hilfe angezeigt.

* **Eigene Stunden (AGs, Lerngruppen)** – bis zu 3 Stunden, die nicht in Untis stehen, werden wöchentlich
  in den Stundenplan eingefügt. Pro Stunde: Name, Wochentag, *von/bis Stunde* aus dem Zeitraster der Schule
  (z. B. „7. Stunde (13:30–14:15)“, Doppelstunden möglich), optional Lehrer und Raum, Rhythmus
  (jede Woche / nur gerade / nur ungerade Kalenderwoche) und „nur an Schultagen“ (Standard: an –
  dann entfällt die AG automatisch in den Ferien und an Tagen ohne Unterricht). Name leeren = Eintrag löschen.
  Eigene Stunden haben den Status `custom` und zählen bei Stunden heute/morgen, Schulbeginn/-ende,
  aktueller/nächster Stunde und im Kalender mit.

Die Namen gelten überall: Sensoren, Kalender, Änderungstexte und Events. Die Originalkürzel bleiben
zusätzlich in `teachers_short`, `original_teachers_short` und `subject_short` erhalten. Umbenennen
löst keine erneuten Änderungs-Benachrichtigungen aus.

## Entitäten (pro Klasse)

| Entität | Zustand | Wichtige Attribute |
|---|---|---|
| `sensor.…_stunden_heute` | Anzahl stattfindender Stunden heute | `lessons`, `subjects`, `changes`, `first_start`, `last_end` |
| `sensor.…_stunden_morgen` | dito für morgen | dito |
| `sensor.…_nachster_schultag` | Datum des nächsten Schultags | dito |
| `sensor.…_schulbeginn_heute` / `_morgen` / `_nachster_schultag` | Zeitstempel erste Stunde | – |
| `sensor.…_schulende_heute` / `_morgen` | Zeitstempel letzte Stunde | – |
| `sensor.…_aktuelle_stunde` | Fach der laufenden Stunde | Lehrer, Raum, Status |
| `sensor.…_nachste_stunde` | Fach der nächsten Stunde | Beginn, Raum, Status |
| `sensor.…_vertretungen_und_anderungen` | Anzahl kommender Änderungen | `changes` (Texte), `lessons` |
| `sensor.…_nachrichten_des_tages` | Anzahl Nachrichten | `text`, `subjects`, `messages` |
| `binary_sensor.…_schule_heute` / `_morgen` | an = es findet Unterricht statt | – |
| `binary_sensor.…_anderungen_heute` / `_morgen` | an = Vertretung/Entfall/Raumänderung | – |
| `calendar.…_stundenplan` | Stundenplan als Kalender (Entfall mit ❌, Änderungen mit ⚠️) | – |

Jede Stunde in `lessons` enthält: `start`, `end`, `start_time`, `end_time`, `subject`, `subject_short`,
`subject_long`, `teachers`, `teachers_short`, `rooms`, `original_teachers`, `original_teachers_short`, `original_rooms`, `status`
(`regular`, `cancelled`, `substitution`, `room_substitution`, `additional`, `shift`, `exam`, `event`, `custom`),
`changed`, `substitution_text`, `info`.

## Events (für Benachrichtigungen)

Die Integration merkt sich (persistent, auch über Neustarts), was bereits bekannt ist,
und feuert nur bei **neuen** Einträgen:

* `untis_public_change` – neue Vertretung / neuer Entfall / Raumänderung …
  Daten: alle Stunden-Felder oben + `message` (fertiger Text), `class`, `school`
* `untis_public_message` – neue Nachricht des Tages
  Daten: `subject`, `text` (Klartext), `html`, `class`, `school`

Beim allerersten Abruf werden keine Events gefeuert (sonst käme alles auf einmal).

## Beispiel-Automationen

```yaml
# Push bei jeder neuen Stundenplanänderung
- alias: "Untis: Änderung melden"
  triggers:
    - trigger: event
      event_type: untis_public_change
  actions:
    - action: notify.mobile_app_handy
      data:
        title: "Stundenplan {{ trigger.event.data.class }}"
        message: "{{ trigger.event.data.message }}"

# Push bei neuer Nachricht des Tages
- alias: "Untis: Nachricht des Tages"
  triggers:
    - trigger: event
      event_type: untis_public_message
  actions:
    - action: notify.mobile_app_handy
      data:
        title: "{{ trigger.event.data.subject or 'Nachricht der Schule' }}"
        message: "{{ trigger.event.data.text[:1000] }}"

# Wecker 60 Minuten vor Schulbeginn (nur an Schultagen)
- alias: "Untis: Wecken"
  triggers:
    - trigger: time
      at:
        entity_id: sensor.untis_5a_schulbeginn_heute
        offset: "-01:00:00"
  actions:
    - action: light.turn_on
      target:
        entity_id: light.kinderzimmer

# Abends: Fächer für morgen ansagen
- alias: "Untis: Schultasche packen"
  triggers:
    - trigger: time
      at: "19:00:00"
  conditions:
    - condition: state
      entity_id: binary_sensor.untis_5a_schule_morgen
      state: "on"
  actions:
    - action: notify.mobile_app_handy
      data:
        message: >
          Morgen: {{ state_attr('sensor.untis_5a_stunden_morgen', 'subjects') | unique | join(', ') }}
          {%- set c = state_attr('sensor.untis_5a_stunden_morgen', 'changes') %}
          {%- if c %} | Änderungen: {{ c | join('; ') }}{% endif %}
```

Die Entity-IDs hängen vom Klassennamen und von der Sprache ab, die beim Einrichten in Home Assistant eingestellt war
(bei Englisch z. B. `sensor.untis_5a_school_start_today`). Bitte in *Entwicklerwerkzeuge → Zustände* nachsehen.

## Technik

Die Integration nutzt dieselben Endpunkte wie die Untis App / der WebUntis-Webclient im anonymen Modus:

* Schulsuche: `https://mobile.webuntis.com/ms/schoolquery2` (`searchSchool`)
* Anonymer Login: `jsonrpc_intern.do` → `getUserData2017` mit Benutzer `#anonymous#`
* Klassen: `/WebUntis/api/public/timetable/weekly/pageconfig?type=1`
* Stundenplan: `/WebUntis/api/public/timetable/weekly/data?elementType=1&elementId=…`
* Nachrichten des Tages: `/WebUntis/api/public/news/newsWidgetData?date=YYYYMMDD`
* Zeitraster (für eigene Stunden): `/WebUntis/api/public/timegrid`

Das ist keine offiziell dokumentierte API; Untis kann sie jederzeit ändern.
Zum schnellen Testen ohne Home Assistant: `python tools/untis_cli.py "Schulname"`.
