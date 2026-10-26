# Analyse und Implementierungsplan für die ha-mycloud-Erweiterung

Stand: 24. Oktober 2026

## 1. Umfang und geprüfter Stand

Diese Analyse basiert auf folgenden unveränderten Ständen:

- Integration: `placix/ha-mycloud`, Branch `dev`, Commit
  `0314bdc549e734ffa599362861cbbc64d9948258`
- Upstream: `J-shw/ha-mycloud`, Standardbranch `dev`, zum Prüfzeitpunkt
  derselbe Commit `0314bdc549e734ffa599362861cbbc64d9948258`
- Client: `placix/wdnas_client`, Branch `feature/my-cloud-mirror-os5`, Commit
  `4e6389f753efdd942a1a919c065861bce18f9ca9`
- Zielgerät: WD My Cloud Mirror Gen 2, Firmware 5.33.102, RAID 1, zwei
  Festplatten, ext4 und OS-5-Authentifizierung

Das Integrationsrepository wurde in einen lokalen Checkout geklont. `origin`
zeigt auf den Fork,
`upstream` auf `J-shw/ha-mycloud`. Der Arbeitsbaum war vor Erstellung dieses
Berichts sauber. Es wurden keine Integrationsdateien, Versionen, produktiven
Home-Assistant-Installationen oder Remotes verändert.

## 2. Tatsächliche Repository-Struktur

Vorhanden sind:

```text
custom_components/mycloud/
  __init__.py
  config_flow.py
  const.py
  manifest.json
  sensor.py
  translations/en.json
.github/
  ISSUE_TEMPLATE/new_device_request.md
  workflows/hassfest.yml
  workflows/validate.yml
  release.yml
images/
  icon.png
  logo.png
README.md
hacs.json
```

Nicht vorhanden sind insbesondere:

- `coordinator.py`, `entity.py`, `binary_sensor.py` und `diagnostics.py`
- `strings.json` als englische Quellübersetzung
- eine Test-Suite und Test-Konfiguration
- ein Workflow für Python-/Home-Assistant-Tests
- ein Lizenzfile

Die vorhandenen Workflows führen die HACS-Validierung mit `hacs/action@main`
und Hassfest mit `home-assistant/actions/hassfest@master` aus. Das entspricht
den derzeit dokumentierten Actions. Es gibt jedoch keinen Workflow, der die
Integration importiert, Config Flows ausführt oder Entitätsverhalten testet.

## 3. Architektur der bestehenden Integration

### 3.1 Setup und Laufzeitdaten

`custom_components/mycloud/__init__.py` liest Host, Benutzername, Passwort und
API-Version aus dem Config Entry und schreibt sie unter globalen Schlüsseln nach
`hass.data[DOMAIN]`. Anschließend wird ausschließlich die Plattform `sensor`
geladen. Die globalen Werte werden von `sensor.py` nicht benutzt und würden bei
mehreren Config Entries jeweils überschrieben.

`sensor.py` übernimmt derzeit alle Aufgaben:

1. `wdnas_client.client` erzeugen und über `__aenter__()` anmelden,
2. Client-Cleanup registrieren,
3. einen lokalen `DataUpdateCoordinator` definieren,
4. vier API-Aufrufe sequenziell ausführen,
5. Device Registry-Einträge aufbauen,
6. alle Sensor- und Binary-Sensor-Objekte erzeugen.

Der Coordinator fragt bei jedem Zyklus ab:

- `system_info()`
- `system_status()`
- `device_info()`
- `system_version()`

Das Intervall ist als Option konfigurierbar, standardmäßig 600 Sekunden und
mindestens 30 Sekunden. Die vier Requests teilen eine Client-Session und werden
sequenziell ausgeführt. Das vermeidet parallele Anmeldungen, ruft aber auch
nahezu statische Geräte- und Firmwareinformationen in jedem Zyklus erneut ab.

Beim ersten Setup wird `async_refresh()` verwendet. Empfohlen ist
`async_config_entry_first_refresh()`, damit ein wesentlicher Erstfehler als
`ConfigEntryNotReady` behandelt wird und Plattformen erst mit gültigen Daten
starten. `CoordinatorEntity` wird bereits verwendet und liefert grundsätzlich
Coordinator-basierte Verfügbarkeit.

Die Reauthentifizierung erkennt einen abgelaufenen Login durch die Zeichenfolge
`"403"` in einer Exception, schließt die Session, meldet sich neu an und
wiederholt den gesamten Abruf einmal. Dies vermeidet den früheren Session-Leak,
ist aber fragil: `RequestFailedError` besitzt im geprüften Client keinen
allgemein nutzbaren `status_code`, und Stringauswertung kann Fehler falsch
klassifizieren. Eine echte Home-Assistant-Reauthentication existiert nicht.

### 3.2 Plattformaufteilung

Obwohl `sensor.py` mehrere `BinarySensorEntity`-Klassen enthält, wird nur die
Sensorplattform geladen. Für korrekte Plattformzuordnung, Entity Registry,
Validierung und Wartbarkeit gehören diese Klassen nach `binary_sensor.py`; beide
Plattformen müssen über `PLATFORMS` weitergeleitet werden.

`async_add_entities(sensors_to_add, True)` fordert außerdem Updates beim
Hinzufügen an, obwohl der Coordinator bereits aktualisiert wurde. Das kann
unnötige Coordinator-Aktualisierungen auslösen. Coordinator-Entitäten sollten
nach erfolgreichem First Refresh ohne `update_before_add=True` hinzugefügt
werden.

### 3.3 Config Flow und Optionen

Der Config Flow speichert Eingaben ohne Verbindungstest, Loginprüfung oder
Duplikaterkennung. Es gibt keine Config-Entry-`unique_id`, keinen Reauth- und
keinen Reconfigure-Schritt. Ein falsches Passwort oder ein nicht erreichbares
NAS wird somit erst während des Plattform-Setups erkannt.

Die Schemas verwenden die Schlüssel `Host`, `Username`, `Password` und
`Version`, während `translations/en.json` nur kleingeschriebene Schlüssel für
Benutzername und Passwort enthält. Host, API-Version und Optionen sind nicht
vollständig übersetzt. Eine maßgebliche `strings.json` fehlt.

### 3.4 Device und Entity Registry

Das Hauptgerät verwendet `(DOMAIN, serial_number)` als Gerätekennung. Disks
verwenden ihre Seriennummer und sind über `via_device` mit dem NAS verbunden.
Volumes verwenden dagegen nur `(DOMAIN, volume_id)`. `volume_id` ist nicht pro
NAS global eindeutig; bei mehreren Geräten sind Device-Registry-Kollisionen
möglich.

Die bestehenden Unique IDs sind uneinheitlich:

- System: `<nas_serial>_<key>`
- Disk: `<disk_serial>_<key>`
- Volume-Größe: `<volume_id>_volume_size`
- andere Volume-Entitäten: `<device_name> <label>_<key>`

Namensbasierte IDs sind nicht stabil, und die Volume-Größen-ID kollidiert bei
mehreren NAS-Geräten. Diese Schwächen sollten behoben werden, bestehende Registry
IDs dürfen dabei aber nicht still geändert werden. Eine spätere Migration muss
alte Unique IDs gezielt über die Entity Registry auf ein Schema wie
`<nas_serial>_<volume_id>_<key>` abbilden. Bis diese Migration getestet ist,
sollten bestehende IDs erhalten und nur neue Entitäten mit stabilen, NAS-weit
gescopten IDs angelegt werden.

## 4. Aktuelle Entitäten

### Systemweite Sensoren

- CPU-Auslastung
- Speicherauslastung
- Gesamtspeicher
- belegter Speicher
- freier Speicher

### Pro Festplatte

- Temperatur
- Größe
- gesund
- schlafend
- ausgefallen
- Übertemperatur

### Pro Volume

- Größe
- eingehängt
- entsperrt
- verschlüsselt

CPU und Speicher verwenden teilweise die ältere `state`-Property statt
`native_value`. `SensorEntityDescription` und
`BinarySensorEntityDescription` werden nicht verwendet. Binary-Sensoren haben
keine `device_class`. Namen enthalten das Gerät erneut und
`_attr_has_entity_name`/Übersetzungsschlüssel fehlen. Der Basissensor setzt
`state_class=measurement` pauschal; für die aktuellen numerischen Messwerte ist
das meist passend, für zukünftige Text-/Enum-Sensoren jedoch nicht.

Die Speicherprozentberechnung ist historisches Verhalten:
`(mem_total - mem_free) / mem_total`. Die neuen Buffer-/Cachewerte sollten
diese bestehende Entität nicht still semantisch verändern. Stattdessen können
separate diagnostische Datengrößen-Sensoren ergänzt und eine spätere Änderung
gesondert migriert werden.

## 5. Einbindung von wdnas_client

### 5.1 Aktueller Zustand

`manifest.json` verlangt `wdnas-client>=0.9.4`. PyPI bietet aktuell 0.9.4 an.
Der freigegebene Feature-Commit trägt in `setup.py` ebenfalls noch Version
0.9.4, enthält aber zusätzliche APIs, die das veröffentlichte 0.9.4-Paket nicht
enthält. Eine normale Home-Assistant-Installation kann daher scheinbar die
richtige Version besitzen und trotzdem die neuen Methoden nicht haben.

Aktuelle Home-Assistant-Konventionen verlangen für Manifest-Anforderungen eine
exakte Version (`==`), nicht `>=`. Für eine stabile Integration sollte später
eine neue Client-Version veröffentlicht und beispielsweise als
`wdnas-client==<freigegebene-version>` gepinnt werden. Das ist erst nach
separater Freigabe für Versionierung und Veröffentlichung zulässig.

### 5.2 Empfohlener Entwicklungsweg ohne vorzeitige Veröffentlichung

Der sicherste Weg ist eine vollständig isolierte Home-Assistant-
Entwicklungsumgebung, nicht die produktive Installation:

1. Den Client-Checkout exakt auf Commit `4e6389f` prüfen.
2. Den Checkout in der Python-Umgebung dieser Testinstanz editierbar installieren.
3. Home Assistant mit `--skip-pip-packages wdnas-client` starten, damit der
   Resolver das lokale Paket nicht durch PyPI 0.9.4 ersetzt.
4. Vor jedem Hardwaretest Modulpfad und Git-Commit kontrollieren.

Beispielprinzip:

```powershell
git -C <wdnas-client-checkout> rev-parse HEAD
python -m pip install -e <wdnas-client-checkout>
python -c "import wdnas_client; print(wdnas_client.__file__)"
hass --skip-pip-packages wdnas-client -c <isolated-ha-config>
```

Die verwendete `python`- und `hass`-Executable müssen aus derselben isolierten
venv beziehungsweise demselben Container stammen. Bei einem Dev Container muss
der Client-Checkout in den Container gemountet oder innerhalb des
Entwicklungs-Workspaces geklont werden.

Home Assistant unterstützt für reine Entwicklung auch eine Git-Anforderung im
Manifest. Falls dies kurzfristig benötigt wird, müsste sie auf den vollständigen
Commit statt nur auf einen beweglichen Branch zeigen. Sie darf nicht in einen
produktiven Commit, Release oder Pull Request gelangen. Der editierbare Checkout
mit `--skip-pip-packages` ist hier vorzuziehen, weil `manifest.json` unverändert
bleiben kann.

### 5.3 Relevante APIs aus Client-Commit 4e6389f

OS 5 bietet zusätzlich:

- `diagnostics()` für Board-/Lüftercodes, Diskstatus und RAID-Volumes
- `capacity_usage()` für separate rohe Kapazitätsdaten und `used_rate`
- `volume_status()` für RAID-, Dateisystem-, Mount- und Verschlüsselungsdaten
- `system_warning()` für `normal`, `over_temperature`,
  `system_busy_hotplug` oder `unknown`
- `internet_access()` für die vom NAS gemeldete Konnektivität
- `process_info()` für sensible Diagnosedaten
- erweiterte `system_status()`-Daten für Buffer, Cache, Bonding, Prozessanzahl,
  Zeitstempel und rohe LAN-Felder

`process_info()` ist für das regelmäßige Monitoring nicht erforderlich, weil
`system_status()` die Prozessanzahl bereits aus den vorhandenen Datensätzen
ableitet.

## 6. Empfohlene neue Entitäten

### Priorität A: hoher Nutzen, bestätigte Daten, keine Zusatzabfragen pro Ableitung

| Entität | Plattform | Quelle | Modellierung |
| --- | --- | --- | --- |
| RAID-Zustand je RAID-Volume | Sensor | `diagnostics()` | Text/Enum, diagnostisch; Firmwarewert unverändert erhalten |
| RAID-Modus je RAID-Volume | Sensor | `diagnostics()` | Text/Enum, diagnostisch, eher standardmäßig deaktiviert |
| Volume-Auslastung | Sensor | `capacity_usage().used_rate` | `%`, `MEASUREMENT`; niemals `volume_status().percentage` verwenden |
| Systemwarnstatus | Sensor | `system_warning()` | Enum `normal`, `over_temperature`, `system_busy_hotplug`, `unknown` |
| Übertemperaturwarnung | Binary Sensor | dieselbe `system_warning()`-Antwort | `PROBLEM`; aktiv nur bei Code 1, unbekannte Daten nicht als OK melden |
| Internet-Konnektivität | Binary Sensor | `internet_access()` | `CONNECTIVITY`; Bewertung des NAS, kein eigener Internettest |
| Prozessanzahl | Sensor | `system_status().process_count` | Ganzzahl, diagnostisch; kein Aufruf von `process_info()` |
| Buffer-Speicher | Sensor | `system_status().memory.buffers` | `DATA_SIZE`, KiB oder kontrolliert in Bytes umgerechnet |
| Cache-Speicher | Sensor | `system_status().memory.cached` | `DATA_SIZE`, KiB oder kontrolliert in Bytes umgerechnet |

Systemwarnstatus und Übertemperatur-Binary-Sensor sind bewusst zwei Ableitungen
aus einem Request: Der Enum-Sensor bewahrt Code 2, der Binary-Sensor erlaubt
einfache Alarm-Automationen.

### Priorität B: sinnvoll, aber Zuordnung oder Semantik zuerst absichern

| Entität | Plattform | Quelle | Voraussetzung |
| --- | --- | --- | --- |
| Dateisystemtyp je Volume | Sensor | `volume_status().file_type` | stabile Zuordnung zu `system_info()`-Volumes; diagnostisch und standardmäßig deaktiviert |
| RAID-Problem/Gesundheit | Binary Sensor | `diagnostics().raid.volumes[].state` | verbindliche konservative Zustandsmatrix und Verhalten für unbekannte Codes festlegen |

Für RAID-Gesundheit ist ein `PROBLEM`-Sensor semantisch klarer als ein
"Healthy"-Sensor: `degraded` und `damaged` können als Problem gelten; `clean`
als kein Problem. `resync`, `resync5`, `recovery`, `migrate`, `reshape` und
`expansion` sind aktive Vorgänge und nicht automatisch defekt. Unbekannte Werte
sollten die Entität als nicht verfügbar/unknown markieren, nicht als gesund.
Diese Matrix ist vor Implementierung fachlich freizugeben.

### Nicht als neue Entität empfohlen

- `volume_status().percentage`: auf dem nahezu vollen Testvolume trotzdem `0`
- `volume_status().free_size` als Ersatz für `capacity_usage().free_size`: beide
  Endpunkte liefern abweichende Rohwerte
- LAN-Felder: Einheit und Semantik sind nicht abschließend bestätigt
- `now_time`: Unix-Sample-Zeitstempel, keine Uptime
- `fan_rpm` als Fehlerindikator: `0` ist nicht pauschal ein Lüfterfehler
- vollständige Prozesslisten, Kommandos, Benutzer oder PIDs
- RAID-UUIDs, Seriennummern und Geräte-/Mountpfade als Attribute
- USB-Zustand ohne Gerät: `{"success": false}` ist kein Integrationsfehler
- Uptime auf der Mirror: `UptimeUnavailableError` ist erwartetes optionales
  Verhalten und darf nicht den Coordinator blockieren

Bestehende Disk-Health-, Overtemperature-, Mount- und Encryption-Entitäten
sollten nicht aus den neuen Endpunkten dupliziert werden.

## 7. Empfohlener Coordinator und Polling

### 7.1 Zielarchitektur

- Client und Coordinator in `async_setup_entry()` erzeugen.
- Einen Client und genau eine angemeldete Session pro Config Entry verwenden.
- Coordinator als eigene Klasse in `coordinator.py` ablegen.
- Coordinator in einem typisierten `entry.runtime_data` speichern.
- `PLATFORMS = (Platform.SENSOR, Platform.BINARY_SENSOR)` zentral definieren.
- Mit `async_config_entry_first_refresh()` starten und Plattformen erst danach
  weiterleiten.
- Session über `entry.async_on_unload()` genau einmal schließen.
- Keine parallelen Logins; einen Reauth-/Reconnect-Lock verwenden.

### 7.2 Abrufgruppen

Ein einzelner Coordinator kann intern Daten nach Änderungsrate cachen und fällige
Gruppen sequenziell abrufen:

1. **Setup/statisch:** `device_info()`, `system_version()`; einmal beim Setup,
   danach nur nach Reload oder sehr selten.
2. **Basiszyklus:** `system_status()`, `diagnostics()`, `system_warning()`.
3. **Kapazität/Volume:** `system_info()`, `capacity_usage()`, `volume_status()`;
   langsamer, beispielsweise alle 3 bis 6 Basiszyklen.
4. **Internet:** `internet_access()` langsamer oder alle 2 bis 3 Basiszyklen.
5. **Nie regulär:** `process_info()`, `uptime()` und `usb_info()`.

Das vorhandene Benutzerintervall von standardmäßig 600 Sekunden und mindestens
30 Sekunden sollte als Basisintervall erhalten bleiben. Für die leistungsschwache
Mirror ist 300 bis 600 Sekunden ein vernünftiger Standardbereich. Ein Minimum
von 30 Sekunden kann aus Kompatibilitätsgründen bleiben, sollte aber als erhöhte
Last dokumentiert werden. Requests innerhalb eines Zyklus zunächst sequenziell
ausführen; Parallelisierung verkürzt zwar die Laufzeit, erhöht aber Last und kann
Session-/Firmwareprobleme provozieren.

### 7.3 Teilfehler

Coordinator-Daten sollten pro Bereich Wert, Aktualisierungszeit und Fehlerstatus
halten. Ein Fehler eines optionalen OS-5-Endpunkts darf die zuletzt gültigen
Basisdaten nicht verwerfen:

- Authentifizierungsfehler: einmal kontrolliert neu anmelden; bei dauerhaft
  ungültigen Zugangsdaten `ConfigEntryAuthFailed` auslösen.
- Transport-/Kernendpunktfehler: `UpdateFailed`, damit alle wirklich abhängigen
  Entitäten vorübergehend nicht verfügbar sind und HA erneut versucht.
- Optionaler Endpunktfehler: nur den betroffenen Bereich als nicht verfügbar
  markieren, vorige Werte nicht als aktuelle Werte ausgeben.
- `UptimeUnavailableError`, leeres USB-Ergebnis und fehlende OS-5-Funktionen auf
  OS 2 niemals als globalen Fehler behandeln.
- `internet_access()` mit fehlendem/unbekanntem Wert wirft absichtlich
  `ValueError`; die Konnektivitätsentität wird unknown, nicht `off`.

Eine Entity überschreibt bei Bedarf `available`, sodass sowohl
`coordinator.last_update_success` als auch die Verfügbarkeit ihres Datenbereichs
berücksichtigt werden.

## 8. Home-Assistant-Konventionen und notwendige Modernisierung

### Coordinator und Entitäten

- `CoordinatorEntity` beibehalten und `native_value`/`is_on` verwenden.
- Sensoren über `SensorEntityDescription`, Binary-Sensoren über
  `BinarySensorEntityDescription` beschreiben.
- `_attr_has_entity_name = True`, kurze übersetzbare Entitätsnamen und
  `translation_key` einsetzen.
- Nur Messwerte erhalten `SensorStateClass.MEASUREMENT`; Text-/Enum-Sensoren
  erhalten keine State Class.
- `native_unit_of_measurement`, `device_class` und `entity_category` passend
  setzen. RAID-Modus, Dateisystem, Prozesse sowie Cache/Buffer sind diagnostisch.
- Binary-Sensoren nach `binary_sensor.py` verschieben und sinnvolle Device
  Classes (`CONNECTIVITY`, `PROBLEM`) verwenden.

### Config Flow

- Eingabeschlüssel auf HA-Konstanten beziehungsweise konsistente kleingeschriebene
  Konstanten migrieren, ohne bestehende Einträge unkontrolliert zu brechen.
- Im User-Step mit einer kurzlebigen Client-Session Login und Basiszugriff testen
  und die Session in jedem Pfad schließen.
- Nach erfolgreichem Login eine stabile NAS-Kennung als Config-Entry-Unique-ID
  setzen und Duplikate abbrechen.
- `async_step_reauth` und optional `async_step_reconfigure` ergänzen.
- Auth-, Verbindungs- und unbekannte Fehler getrennt und übersetzt anzeigen.
- `strings.json` als Quelle sowie `translations/en.json` synchron pflegen.

### Device Registry und Registry-Migration

- Hauptgerät, Disks und Volumes hierarchisch über `via_device` abbilden.
- Neue Volume-Identifier mit NAS-Kennung scopen.
- Bestehende Unique IDs und Entity IDs zunächst bewahren.
- Falls die kollisionsanfälligen alten Volume-IDs geändert werden, eine
  versionsgesteuerte `async_migrate_entry()`-Migration mit Registry-Tests
  bereitstellen; niemals Entitäten einfach neu erzeugen und Duplikate hinterlassen.
- Dynamisch hinzukommende Disks/Volumes entweder über Coordinator-Listener
  nachregistrieren oder dokumentiert erst nach Reload entdecken.

### Manifest, HACS und Dokumentation

- Später `requirements` exakt auf eine veröffentlichte Client-Version pinnen.
- Veraltete `documentation`- und `issue_tracker`-URLs (`wdnas_hacs`) korrigieren.
- Manifest-Schlüssel in Hassfest-Reihenfolge halten und optional
  `integration_type: device` sowie `loggers: [wdnas_client]` prüfen.
- `hacs.json` besitzt den erforderlichen Namen; HACS- und Hassfest-Workflows
  bleiben erhalten.
- Ein erkennbares OSI-Lizenzfile und aktuelle Brand-Struktur prüfen, falls eine
  Aufnahme als HACS-Standardrepository angestrebt wird.

## 9. Datenschutz und Diagnosedaten

Keine Entity-Attribute oder Logs sollen enthalten:

- Passwort oder Session-Cookies
- Benutzername
- Prozesskommandos oder PIDs
- RAID-UUID
- SCSI-/Festplattenseriennummern als Diagnoseausgabe
- Geräte-, Mount- oder Dateipfade

Seriennummern werden derzeit intern als stabile Device-/Entity-Identifier
verwendet. Das ist von einer Veröffentlichung als Zustandsattribut zu
unterscheiden. Eine spätere vollständige Ablösung bestehender seriennummernbasierter
Unique IDs wäre eine Registry-Migration und darf nicht beiläufig erfolgen.

Für `diagnostics.py` sollte `async_redact_data` auf Config Entry und Coordinator-
Daten angewendet werden. Neben bekannten Schlüsseln müssen auch Werte in
verschachtelten `extra`-Mappings redigiert werden. Empfehlenswert ist eine
Allowlist nützlicher Felder statt nur einer kleinen Denylist. Tests müssen
explizit nach Zugangsdaten, Cookies, `raid_uuid`, `scsi*_serial`, `mount`,
`device`, `used_device`, Prozessfeldern und NAS-Seriennummern suchen.

## 10. Testkonzept

Eine neue Offline-Suite sollte `pytest`, `pytest-homeassistant-custom-component`
und `AsyncMock` verwenden. Echte NAS-Zugriffe gehören in einen separaten,
manuell gestarteten Hardwaretest.

### Pflichtfälle

1. **Config Flow:** Erfolg für OS 2/OS 5, falsche Zugangsdaten, Timeout,
   unbekannter Fehler, Duplikat, Optionsgrenzen, Reauth und Reconfigure.
2. **Setup/Unload:** First Refresh, Retry, exakt eine Session pro Entry,
   Session-Close bei Erfolg und Fehler, Reload nach Optionsänderung.
3. **Mehrere NAS:** getrennte Coordinator-Daten, Device-Identifier und Unique
   IDs ohne Überschreiben/Kollision.
4. **OS 2:** keine OS-5-only-Aufrufe und keine daraus abgeleiteten Entitäten.
5. **OS 5:** alle neuen Rückgabestrukturen vom Client-Commit `4e6389f`.
6. **Optionale Endpunkte:** einzelne 404/500, ungültiges XML, fehlende Felder,
   `UptimeUnavailableError`, USB `success=false`, Internet unknown.
7. **RAID:** `clean`, `degraded`, `damaged`, `recovery`, `resync`, `resync5`
   und unbekannte Zustände; keine falsche Gesundinterpretation.
8. **Volumes:** mehrere Volumes, stabile Zuordnung, `percentage=0` bei voller
   Belegung, unterschiedliche `free_size`-Werte beider Endpunkte.
9. **Entitäten:** Namen, Unique IDs, DeviceInfo, Device Classes, State Classes,
   Units, Entity Categories und Availability.
10. **Registry-Migration:** bestehende Unique IDs bleiben erhalten oder werden
    kontrolliert und idempotent migriert.
11. **Diagnostik:** Snapshot mit vollständiger Redaction sensibler Daten.

### CI

- Bestehende HACS- und Hassfest-Actions weiterführen.
- Einen separaten Testworkflow für Import, pytest und Coverage ergänzen.
- Optional Ruff/Pyright nach einer initialen Bereinigung ergänzen.
- Eine unterstützte Home-Assistant-Version klar definieren und gegen die
  aktuelle stabile Version testen; Versionsmatrix bei HA-Wechsel aktualisieren.
- Hardwaretests nie in öffentliche CI integrieren und keine Zugangsdaten als
  Repository-Secrets für öffentliche Fork-Workflows hinterlegen.

## 11. Priorisierter Implementierungsplan

### Phase 0: Entwicklungsumgebung und Sicherheitsnetz

1. Separaten Feature-Branch der Integration anlegen.
2. Isolierte HA-Testumgebung mit editierbarem Client-Commit `4e6389f` und
   `--skip-pip-packages wdnas-client` aufsetzen.
3. Aktuelles Verhalten und alle vorhandenen Unique IDs in Tests festhalten.
4. Basis-Pytest-, HACS- und Hassfest-Prüfungen lokal reproduzierbar machen.

### Phase 1: Architektur ohne neue Entitäten

1. `MyCloudDataUpdateCoordinator` nach `coordinator.py` verschieben.
2. Client/Coordinator in `__init__.py` erzeugen und in `entry.runtime_data`
   speichern.
3. `async_config_entry_first_refresh()`, typisierte Fehler und sauberes Unload
   einführen.
4. `sensor.py` und neues `binary_sensor.py` korrekt trennen.
5. Bestehende Entitäten und Unique IDs unverändert weiterbetreiben.

### Phase 2: Config Flow und Mehrgerätefähigkeit

1. Loginvalidierung und sichere Sessionbereinigung ergänzen.
2. Config-Entry-Unique-ID, Duplikaterkennung, Reauth und Reconfigure umsetzen.
3. Globale Zugangsdaten in `hass.data` entfernen.
4. Volume-Identifier-Migrationsstrategie implementieren und testen.
5. `strings.json` und Übersetzungen vervollständigen.

### Phase 3: OS-5-Datenmodell und effizientes Polling

1. Coordinator-Datenmodell mit getrennten Basis-/Optionalbereichen definieren.
2. `diagnostics()`, `capacity_usage()`, `volume_status()`, `system_warning()` und
   `internet_access()` gemäß Abrufgruppen einbinden.
3. Teilfehler und letzte gültige Werte je Datenbereich behandeln.
4. `process_count` ausschließlich aus `system_status()` verwenden.
5. OS 2 durch Capability-/Versionsprüfung vollständig kompatibel halten.

### Phase 4: Neue Entitäten

1. Priorität-A-Entitäten über Entity Descriptions hinzufügen.
2. Volume-Zuordnung und Einheiten mit Fixtures und Mirror-Livewerten prüfen.
3. Dateisystem und RAID-Modus als diagnostische, gegebenenfalls standardmäßig
   deaktivierte Entitäten ergänzen.
4. RAID-Problem-Binary-Sensor erst nach Freigabe der Zustandsmatrix ergänzen.
5. Keine sensiblen `extra`-Felder als Attribute übernehmen.

### Phase 5: Diagnostik, Dokumentation und Freigabe

1. Redigierte `diagnostics.py` und Datenschutztests ergänzen.
2. README, Entitätsliste, Pollinglast, OS-2/OS-5-Kompatibilität und bekannte
   Mirror-Einschränkungen dokumentieren.
3. Vollständige Offline-Suite, Hassfest und HACS-Action ausführen.
4. Danach separaten Hardwaretest auf Mirror 5.33.102 durchführen.
5. Erst nach erfolgreichem Client-Release das Manifest auf die neue exakte
   PyPI-Version setzen; keine Git-Abhängigkeit veröffentlichen.

## 12. Risiken und offene Entscheidungen

1. **Client-Version:** Feature-Commit und PyPI-Paket heißen beide 0.9.4. Ohne
   editierbare Installation plus Skip-Option besteht hohes Risiko eines Tests
   gegen den falschen Code.
2. **Fehlerklassen:** `RequestFailedError` braucht für robuste Reauth idealerweise
   einen strukturierten HTTP-Status; Stringprüfung sollte nicht fortgeführt
   werden. Dies kann eine kleine Client-Nachbesserung erfordern.
3. **RAID-Gesundheit:** Zustandsmatrix und unknown-Verhalten müssen fachlich
   freigegeben werden.
4. **Volume-Zuordnung:** IDs zwischen `system_info()`, `capacity_usage()`,
   `volume_status()` und `diagnostics()` müssen für mehrere Volumes bestätigt
   werden.
5. **Registry-Migration:** bestehende kollisionsanfällige Volume-IDs gegen
   Mehrgerätefähigkeit abwägen; keine stille Umbenennung.
6. **Polling:** Basisintervall und langsamere Multiplikatoren auf echter Hardware
   messen, bevor Defaults verändert werden.
7. **Einheiten:** LAN-Rohwerte nicht veröffentlichen; Kapazitäts- und
   Cache-/Buffer-Einheiten durch Integrationstests absichern.
8. **Optionale Verfügbarkeit:** festlegen, ob letzte gültige Werte mit einem
   Altersindikator behalten oder sofort unknown werden. Keine veralteten Werte
   als frisch darstellen.
9. **Dynamische Hardware:** entscheiden, ob neue Disks/Volumes ohne Reload
   automatisch als Entitäten erscheinen müssen.

## 13. Quellen für aktuelle Konventionen

- [Home Assistant: Fetching data und DataUpdateCoordinator](https://contributors.home-assistant.io/core/integrations/fetching-data/)
- [Home Assistant: Config Flow](https://contributors.home-assistant.io/core/integrations/config-flow/)
- [Home Assistant: Integration Manifest und Entwicklungsabhängigkeiten](https://contributors.home-assistant.io/core/integrations/manifest/)
- [Home Assistant: Diagnoseinformationen und Redaction](https://contributors.home-assistant.io/core/quality-scale/diagnostics/)
- [Home Assistant: Entity-Namenskonvention](https://developers.home-assistant.io/blog/2022/07/10/entity_naming/)
- [HACS Action](https://www.hacs.xyz/docs/publish/action/)
- [Home Assistant Hassfest Action](https://github.com/home-assistant/actions#hassfest)
- [PyPI: wdnas-client 0.9.4](https://pypi.org/project/wdnas-client/)

## Ergebnis

Die Integration besitzt bereits eine brauchbare Coordinator-Grundlage und eine
gemeinsame Client-Session, benötigt vor der Erweiterung aber eine strukturelle
Modernisierung. Besonders wichtig sind korrekte Plattformtrennung, robuste
Config-/Reauth-Flows, Mehrgeräte-sichere IDs und Teilfehlerbehandlung. Die neuen
OS-5-Entitäten lassen sich danach mit wenigen gemeinsamen API-Aufrufen
ressourcenschonend ergänzen.

Für die Entwicklung ist keine vorzeitige PyPI-Veröffentlichung nötig: Der auf
Commit `4e6389f` fixierte Client kann editierbar in einer isolierten HA-Umgebung
installiert und mit `--skip-pip-packages wdnas-client` geschützt werden. Für eine
spätere Veröffentlichung bleibt eine neue Client-Version mit exakter
Manifest-Pinnung zwingend.
