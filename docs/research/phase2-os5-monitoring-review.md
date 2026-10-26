# Phase 2 – WD OS 5 Monitoring Review

Stand: 25. Oktober 2026

## Umfang und geprüfte Stände

Die Erweiterung wurde auf `feature/os5-monitoring` auf Basis von
`6f1658a3212627ddb7efddb520d03bd1914e45f0` implementiert. Für Tests wurde
ausschließlich der lokale `wdnas_client`-Checkout auf
`4e6389f753efdd942a1a919c065861bce18f9ca9` über `PYTHONPATH` verwendet. Das
Für das Pre-Release `v1.3.0-beta.1` verweist das Manifest vorübergehend direkt
auf den unveränderlichen Client-Commit `4e6389f...`. Diese Git-Abhängigkeit ist
ausdrücklich experimentell und muss vor einem stabilen Release durch eine
veröffentlichte Client-Version ersetzt werden.

Phase 1 bleibt kompatibel: bestehende `sensor.*`-Legacy-Einträge werden nicht
destruktiv migriert, neue Installationen verwenden `binary_sensor.*`, Konflikte
werden nicht automatisch bereinigt und die Config-Entry-Version bleibt `1`.

## Coordinator und Polling

Alle Plattformen teilen genau einen authentifizierten Client und einen
`DataUpdateCoordinator`. Die vier bisherigen Kernaufrufe laufen bei jedem
konfigurierten Basiszyklus. Nur bei API-Version 5 werden zusätzlich folgende
optionale Aufrufe ausgeführt:

| Bereich | Client-Methode | Mindestabstand |
| --- | --- | ---: |
| RAID/Diagnose | `diagnostics()` | 600 Sekunden |
| Systemwarnung | `system_warning()` | 600 Sekunden |
| Internetstatus | `internet_access()` | 600 Sekunden |
| Volume-Metadaten | `volume_status()` | 3600 Sekunden |

Die Abfragen erfolgen sequenziell über dieselbe Session. Auch bei einem auf 30
Sekunden gesetzten Basisintervall werden die optionalen Endpunkte nicht ebenso
häufig belastet. `process_info()` wird nie periodisch aufgerufen; die
Prozessanzahl stammt aus `system_status()`.

Jeder optionale Bereich besitzt einen eigenen Cache und ein eigenes
Verfügbarkeitsflag. HTTP-, Parsing-, Timeout- oder Unsupported-Fehler markieren
nur die abhängigen Entitäten als nicht verfügbar. Zuletzt bekannte Daten können
intern für die Topologie erhalten bleiben, werden bei gesetztem Fehlerflag aber
nicht als aktuell verfügbar präsentiert. Authentifizierungsfehler durchlaufen
den zentralen einmaligen Reauthentifizierungspfad. Kernfehler bleiben ein
`UpdateFailed`. OS-2-Einträge führen keinen OS-5-Aufruf aus.

## Neue Entitäten und Semantik

Alle neuen Entitäten nutzen `SensorEntityDescription` beziehungsweise
`BinarySensorEntityDescription`, stabile seriennummerngescopte Unique IDs und
die vorhandene Device-Hierarchie.

### System

- Prozessanzahl: diagnostischer Messsensor aus `process_count`.
- Memory Cache und Buffers: diagnostische Datengrößen, unverändert in KiB. Die
  Einheit ist durch die Client-Struktur und Mirror-Livewerte bestätigt.
- Systemwarnung: Enum `normal`, `over_temperature`,
  `system_busy_hotplug`, `unknown`.
- Übertemperatur: `PROBLEM`; nur Warncode `1` ist aktiv. Code `0` und `2` sind
  kein Übertemperaturproblem, unbekannte Codes bleiben `unknown`.
- Internetzugang: `CONNECTIVITY` mit dem NAS-Wert; fehlende Daten werden nicht
  als `off` interpretiert.

### RAID und Volumes

Pro RAID-Volume entstehen RAID-Zustand, RAID-Modus und RAID-Problem. `clean` ist
normal. `degraded` und `damaged` setzen den Problemsensor. `resync`, `resync5`,
`recovery`, `migrate`, `reshape` und `expansion` sind laufende Vorgänge und
werden nicht als Defekt bewertet. Unbekannte Zustände ergeben bei Zustand und
Problemsensor `unknown`.

Mehrere RAID-Volumes werden über die normalisierte Volume-ID, danach
Volume-Nummer und nur als letzter Fallback über ihre Position unterschieden.
Die NAS-Seriennummer scope't alle neuen Unique IDs, sodass gleiche lokale
Volume-Nummern auf mehreren NAS nicht kollidieren.

`volume_status().file_type` wird als diagnostischer Textsensor je Volume
ausgegeben. Nicht veröffentlicht werden:

- `percentage`, weil der Live-Test trotz nahezu voller Belegung `0` lieferte;
- `free_size`, weil die Quelle von `capacity_usage().free_size` abweicht;
- RAID-UUIDs, SCSI-Seriennummern, Device-, Mount- und sonstige Pfade;
- rohe `extra`-Mappings.

Bestehende Disk-Health-, Disk-Overtemperature-, Mount- und Encryption-Entitäten
werden nicht dupliziert. Ebenfalls nicht umgesetzt wurden unbestätigte
LAN-Einheiten, `now_time` als Uptime, Boardtemperatur `0`, Lüfterwert `0` oder
ein Uptime-Aufruf.

## Fehler, Datenschutz und Topologie

Fehlende Felder ergeben `unknown`; ein fehlgeschlagener optionaler Endpunkt
ergibt `unavailable`. Logs enthalten nur Bereich und Exception-Typ. Antworten,
Pfade, Seriennummern, UUIDs, Nutzer, Prozessinformationen, Cookies und
Zugangsdaten werden nicht protokolliert.

Die Entity-Anzahl hängt von der beim ersten Setup bekannten Topologie ab. Ist
`diagnostics()` oder `volume_status()` zunächst nicht verfügbar, bleiben die
Kernintegration und statischen OS-5-Entitäten aktiv; für später erkannte
RAID-/Volume-Topologie ist ein Reload nötig. Gleiches gilt für neue Volumes.

## Automatisierte Prüfung

Die Offline-Suite deckt insbesondere ab:

- alle Phase-1-Fälle und die 15 bisherigen Entitäten;
- OS 2 ohne OS-5-Aufrufe oder neue Entitäten;
- OS-5-Entitäten, Einheiten und Unique IDs;
- Warncodes `0`, `1`, `2` und unbekannt;
- RAID `clean`, `degraded`, `damaged`, `resync`, `resync5`, `recovery`,
  `migrate`, `reshape`, `expansion` und unbekannt;
- mehrere RAID-Volumes und mehrere NAS mit gleichen lokalen Volume-IDs;
- optionale HTTP-Fehler, Timeouts, Cache/Availability und zentrale Reauth;
- fehlende optionale Felder und ausbleibenden `process_info()`-Aufruf;
- keine RAID-UUID oder Gerätepfade im HA-State.

Die Tests nutzen nur `AsyncMock`-Antworten und keine NAS-Verbindung.

## Isolierter Hardwaretest

Es gilt die WSL2-/Docker-Anleitung aus
`phase1-compatibility-review.md`. Der Client muss exakt aus Commit
`4e6389f753efdd942a1a919c065861bce18f9ca9` exportiert oder installiert werden.
Der Start erfolgt mit `hass --skip-pip-packages wdnas-client`. Mit
`python -c "import wdnas_client; print(wdnas_client.__file__)"` muss geprüft
werden, dass der Import aus dem lokalen Stand kommt. Ein eigener Port und ein
separates HA-Config-Verzeichnis schützen die Produktivinstallation.

## Veröffentlichungsgrenze

Das öffentliche Client-Paket enthält die neuen Methoden noch nicht. Das
Pre-Release verwendet deshalb ausschließlich für den kontrollierten HAOS-Test
den festgeschriebenen Git-Commit. Fehlende Methoden werden weiterhin als nicht
verfügbare optionale Endpunkte behandelt. Vor einem stabilen Release ist eine
regulär veröffentlichte Client-Version erforderlich; die Git-Abhängigkeit darf
nicht unverändert in ein Stable-Release übernommen werden.
