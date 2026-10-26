# Phase 2 – Reversible HAOS-Testinstallation

Stand: 25. Oktober 2026

## 1. Zweck und Grenzen

Dieses Dokument beschreibt ausschließlich die Vorbereitung einer kontrollierten
Testinstallation unter Home Assistant OS. Es wurde nichts nach HAOS kopiert,
kein Home Assistant neu gestartet und weder `.storage` noch die Entity Registry
verändert.

Verwendete Entwicklungsstände:

- Integration: Branch `feature/os5-monitoring`, Basiscommit
  `6f1658a3212627ddb7efddb520d03bd1914e45f0` plus die uncommitteten
  Phase-2-Änderungen;
- Client: Commit
  `4e6389f753efdd942a1a919c065861bce18f9ca9`;
- lokaler Vergleichstest: Home Assistant Core `2026.10.4`, Python `3.14.4`.

Vor jeder tatsächlichen Installation müssen die produktive HA-Core-Version,
die derzeit installierte Integrationsversion und die Verwaltung durch HACS
bekannt sein. Ohne diese Angaben bleibt die Installation gesperrt.

## 2. Vorbereitetes Testpaket

Paketlayout:

```text
haos-test-package\
  .gitignore
  custom_components\
    mycloud\
      __init__.py
      binary_sensor.py
      config_flow.py
      const.py
      coordinator.py
      entity.py
      manifest.json
      sensor.py
      strings.json
      translations\en.json
```

Die `.gitignore` im Paket ignoriert den gesamten Paketinhalt. `git check-ignore`
muss dies vor jeder weiteren Arbeit bestätigen. Das Paket enthält keine Tests,
virtuelle Umgebung, Git-Metadaten, Zugangsdaten oder aufgezeichnete NAS-Daten.

Beim Kopieren ist der Ordner
`haos-test-package/custom_components/mycloud` direkt als
`/config/custom_components/mycloud` einzusetzen. Ein Ergebnis wie
`/config/custom_components/mycloud/mycloud` ist falsch und ein Abbruchgrund.

## 3. Temporäres Manifest

Nur die Paketkopie verwendet:

```json
"requirements": [
  "wdnas-client@git+https://github.com/placix/wdnas_client.git@4e6389f753efdd942a1a919c065861bce18f9ca9"
]
```

Für das Pre-Release `v1.3.0-beta.1` verwendet nun auch das Release-Manifest die
direkte Commit-Referenz. Die separate Paketkopie bleibt als vorab geprüftes
Rollback-/Vergleichsartefakt erhalten. Die Referenz wurde mit
`packaging.Requirement` geparst. Hassfest verlangt die semantisch gleichwertige
PEP-508-Form ohne Leerzeichen um `@`. Ein realer `pip wheel --no-deps`-Test hat den Fork geklont, den
vollständigen Commit verifiziert und ein Wheel mit dem Distributionsnamen
`wdnas_client-0.9.4-py3-none-any.whl` gebaut. URL und Paketname sind damit
syntaktisch und für eine normale Python-/pip-Umgebung praktisch bestätigt.

Das ist noch kein Nachweis, dass der konkrete HAOS-Core-Container Git enthält
oder direkte Git-Requirements zulässt. Dieser Nachweis kann erst mit der
produktiven HAOS-/Core-Version und deren Logausgabe geführt werden.

Eine Suche in Home Assistant Core `2026.10.4` fand keine weitere Core-
Integration mit `wdnas-client` als Requirement. Vor dem produktiven Test müssen
zusätzlich alle vorhandenen benutzerdefinierten Integrationen und HACS-
Installationen geprüft werden. Python kann pro Umgebung nur eine installierte
Version beziehungsweise Quelle derselben Distribution verwenden.

## 4. Zwingende Vorbedingungen auf HAOS

Vor dem Kopieren:

1. Unter **Einstellungen → System → Sicherungen** ein vollständiges Backup
   erstellen.
2. Backup herunterladen oder auf ein externes, nachweislich erreichbares Ziel
   übertragen und die Wiederherstellbarkeit plausibilisieren.
3. Unter **Einstellungen → System → Reparaturen → Drei-Punkte-Menü →
   Systeminformationen** mindestens Core-, Supervisor- und OS-Version notieren.
4. Unter **HACS → Integrationen** prüfen, ob My Cloud von HACS verwaltet wird,
   und automatische Aktualisierung für den Test ausschließen.
5. Die aktuell angezeigte Integrationsversion und den Zustand der vorhandenen
   My-Cloud-Geräte dokumentieren.
6. Über Samba, Studio Code Server oder SSH-/Terminal-Add-on prüfen, ob
   `/config/custom_components/mycloud` existiert.
7. Den vorhandenen Ordner außerhalb von `custom_components` sichern, zum
   Beispiel nach `/config/ha-mycloud-backup/<zeitstempel>/mycloud`. Dadurch wird
   die Sicherung nicht als zweite Integration erkannt.
8. In allen vorhandenen Custom-Component-Manifests nach `wdnas-client` und
   `wdnas_client` suchen. Jeder weitere Treffer muss vorab bewertet werden.
9. Prüfen, dass mindestens einige hundert MiB freier Speicher vorhanden sind
   und Internetzugriff auf GitHub während des ersten Starts möglich ist.

Bis diese Punkte protokolliert sind, darf keine Installation erfolgen.

## 5. Installation über `/config`

### Samba

1. Die HAOS-Konfigurationsfreigabe öffnen.
2. Den vorhandenen Ordner `custom_components/mycloud` wie oben außerhalb des
   `custom_components`-Ordners sichern.
3. Den vorbereiteten Ordner `haos-test-package/custom_components/mycloud`
   nach `custom_components/mycloud` kopieren.
4. Prüfen, dass `manifest.json` direkt in diesem Zielordner liegt.

### Studio Code Server

1. Im Explorer `/config/custom_components` öffnen.
2. Bestehenden `mycloud`-Ordner außerhalb von `custom_components` sichern.
3. Den Paketordner vollständig hochladen und die Ebenen kontrollieren.
4. `manifest.json` öffnen und mit der erwarteten direkten Commit-URL
   vergleichen.

### SSH-/Terminal-Add-on

Das Add-on läuft nicht notwendigerweise im Core-Container. Befehle dort dürfen
nur Dateien unter `/config` verwalten; es dürfen keine globalen Python-Pakete
installiert werden. Vor einem Ersetzen sind Quelle und Ziel mit `pwd` und
`ls -la` zu kontrollieren. Keine unaufgelösten Variablen oder rekursiven
Löschbefehle verwenden.

Nach dem Kopieren das Manifest mit einem vorhandenen JSON-Werkzeug prüfen, zum
Beispiel `jq empty /config/custom_components/mycloud/manifest.json`. Fehlt
`jq`, die Datei über Studio Code validieren; keine Systempakete nachinstallieren.

Erst nach ausdrücklicher Freigabe Home Assistant über
**Einstellungen → System → Neu starten → Home Assistant neu starten** neu
starten. Kein Host-Neustart und kein automatischer Restart durch dieses Paket.

## 6. Erwartetes Verhalten beim ersten Start

Home Assistant erkennt dieselbe Domain `mycloud` und lädt bestehende Config
Entries weiter. Die Config-Entry-Version bleibt `1`; es findet keine Migration
statt. Bestehende Unique IDs und Legacy-Registry-Einträge werden über die
nicht-destruktive Phase-1-Logik wiederverwendet.

Vor dem Laden versucht Home Assistant, das direkte Client-Requirement zu
installieren. Dieser erste Start kann deutlich länger dauern. Danach sollten
OS-2-Geräte nur die bisherigen Entitäten laden. Für OS 5 kommen Prozessanzahl,
RAM-Cache/-Buffer, Warnung, Internetstatus sowie RAID-/Dateisystementitäten
hinzu. `process_info()` wird nicht regelmäßig abgefragt.

Unmittelbar nach dem Neustart sind die Core-Logs auf folgende Muster zu prüfen:

- `git: not found`, `Cannot find command 'git'` oder Clone-/Checkout-Fehler;
- Fehler beim Installieren von `wdnas-client`;
- `RequirementsNotFound`, `ConfigEntryNotReady` oder Setup-Fehler für
  `mycloud`;
- Authentifizierungs- oder Verbindungsfehler;
- Registry-Konfliktmeldung für gleichzeitig vorhandene Legacy- und neue
  Binary-Sensor-Einträge;
- optionale Endpoint-Warnungen.

Keine Zugangsdaten, Cookies, Seriennummern, UUIDs oder Pfade aus Logs in einen
Bericht kopieren.

## 7. Kontrollschritte für Entitäten

Unter **Einstellungen → Geräte & Dienste → My Cloud → Gerät/Entitäten** prüfen:

- vorhandene CPU-, Speicher-, Disk- und Volume-Entitäten unverändert;
- keine doppelten Entities;
- bestehende benutzerdefinierte Namen, Bereiche und Deaktivierungen erhalten;
- Disk-Health weiter unter ihrer bisherigen Unique-ID;
- RAID-Zustand `clean` und Modus `raid1`, soweit der reale Zustand unverändert
  ist;
- RAID-Problem aus und bei unbekanntem Zustand nicht fälschlich gesund;
- Dateisystem `ext4`;
- Warnstatus `normal` bei Code 0;
- Übertemperaturproblem aus nur bei bestätigtem Code 0 oder 2, unbekannt bei
  unbekanntem Code;
- Internetstatus entsprechend dem NAS-Wert;
- Prozessanzahl numerisch;
- Cache und Buffer als KiB;
- korrekte Gerätezuordnung und Verfügbarkeit.

`volume_status().percentage` ist keine nachgewiesene Speicherauslastung und wird
nicht als neue Auslastungsentität erwartet. Die bestehenden
Kapazitätssensoren bleiben maßgeblich.

## 8. Risiken und Abbruchkriterien

Sofort abbrechen und den Rollback ausführen bei:

- unbekannter produktiver Core-Version oder fehlendem vollständigen Backup;
- nicht eindeutig gesichertem Originalordner;
- zweiter Custom Integration, die eine andere `wdnas-client`-Quelle benötigt;
- fehlendem Git im Core-Container;
- fehlgeschlagenem Clone, Checkout oder Dependency-Build;
- unerwarteter Config-Entry-Migration;
- verschwundenen, duplizierten oder umbenannten Bestandsentitäten;
- wiederholten Logins, anhaltenden Authentifizierungsfehlern oder erkennbarer
  NAS-Last;
- einem Log, das sensible NAS-Daten ausgibt.

Nicht improvisieren: keine Installation mit `pip` im Core-Container, keine
Änderung des Container-Images, keine Bearbeitung von `.storage`, keine manuelle
Registry-Bereinigung und kein Löschen bestehender Entitäten.

## 9. Vollständiger Rollback

### Dateibasierter Rückbau

1. Home Assistant nicht weiter neu starten, solange die Sicherung unklar ist.
2. Den Testordner `/config/custom_components/mycloud` aus dem aktiven Pfad
   entfernen beziehungsweise außerhalb von `custom_components` sichern.
3. Den vorher gesicherten Originalordner exakt als
   `/config/custom_components/mycloud` wiederherstellen.
4. Kontrollieren, dass dessen ursprüngliches `manifest.json`, insbesondere die
   frühere Requirement-Zeile und Integrationsversion, wieder vorhanden ist.
5. Home Assistant erst nach ausdrücklicher Freigabe neu starten.
6. Geräte, Entity-IDs, Namen, Bereiche, Deaktivierungen und Automationen prüfen.
7. Keine Registry-Einträge löschen, auch nicht bei vorübergehend nicht
   verfügbaren Entities.

### Python-Abhängigkeit

Das Wiederherstellen der Integrationsdateien beweist nicht, dass die zuvor aus
Git installierte Distribution aus dem Python-Abhängigkeitspfad entfernt wurde.
Das Originalrequirement `wdnas-client>=0.9.4` kann dieselbe Distributionsversion
weiterhin als erfüllt ansehen, obwohl sie aus dem Testcommit stammt.

Wenn ein exakter Abhängigkeitsrollback erforderlich ist oder der Zustand nicht
zweifelsfrei nachweisbar ist, ist das vollständige, vor dem Test erstellte
HAOS-Backup wiederherzustellen. Alternativ darf nur ein von HAOS offiziell
unterstützter Neuaufbau des Core-Containers zusammen mit der restaurierten
Konfiguration verwendet werden. Kein manuelles globales `pip uninstall` oder
`pip install` im Core-Container.

Schlägt auch der vollständige Restore fehl, Installation nicht weiter
reparieren: Restore-Protokoll sichern, das vorherige Backup unverändert
aufbewahren und Home-Assistant-Support beziehungsweise die dokumentierten
HAOS-Recovery-Verfahren verwenden.

## 10. Alternative ohne Git im Core-Container

Eine unterstützbarere Teststrategie wäre ein separat versioniertes Wheel, zum
Beispiel mit einer eindeutigen lokalen Testversion, das über eine unveränderliche
HTTPS-URL und einen SHA-256-Hash referenziert wird. Eine kontrollierte
Testveröffentlichung wäre eine weitere Möglichkeit. Vor Verwendung muss die
konkrete HAOS-Version eine solche direkte Wheel-Referenz nachweislich
unterstützen.

Es wurde kein Wheel veröffentlicht, kein GitHub Release erzeugt und weder
PyPI noch TestPyPI verändert. Ohne ausdrückliche Freigabe wird diese Alternative
nicht umgesetzt.

## 11. Durchgeführte Prüfungen

- Branch `feature/os5-monitoring` und Basiscommit `6f1658a...` bestätigt;
- Client-Quellcommit `4e6389f...` bestätigt;
- Release- und Testmanifest mit derselben festgeschriebenen Commit-Referenz;
- temporäres Manifest als JSON validiert und Requirement exakt verglichen;
- PEP-508-Parsing erfolgreich;
- realer Clone, Checkout des vollständigen Commits und Wheel-Build erfolgreich;
- Paketstruktur: zehn Integrationsdateien, keine zusätzliche oder fehlende Datei;
- SHA-256-Vergleich: alle Dateien außer dem absichtlich geänderten Manifest
  identisch zur Entwicklungsintegration;
- sieben Python-Dateien der Paketkopie syntaktisch gültig;
- Paketimporte unter Home Assistant Core `2026.10.4` erfolgreich;
- Plattformen `sensor` und `binary_sensor`, Config Flow Version `1` bestätigt;
- Client-Import im isolierten Vergleichstest aus dem exportierten Commitstand;
- keine privaten Schlüssel, Token, URL-Zugangsdaten oder fest codierten
  IP-Adressen im Paket gefunden;
- keine Core-Integration in HA `2026.10.4` mit demselben Distributionsnamen;
- vollständige Offline-Suite: `62 passed`;
- `git diff --check` erfolgreich;
- Paketinhalt durch die lokale Paket-`.gitignore` von Git ausgeschlossen.

## 12. Offene Punkte vor Freigabe

- genaue produktive Home-Assistant-Core-, Supervisor- und HAOS-Version;
- derzeit installierte My-Cloud-Integrationsversion;
- HACS-Verwaltungsstatus;
- Existenz weiterer Custom Integrations mit `wdnas-client`;
- Vorhandensein von Git im konkreten Core-Container;
- vollständiges extern gesichertes und wiederherstellbares Backup;
- ausdrückliche Freigabe für Kopieren und Neustart.

Solange ein Punkt offen ist, bleibt das Paket reine Vorbereitung.
