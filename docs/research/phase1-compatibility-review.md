# Phase 1 – abschließende Kompatibilitätsprüfung

Stand: 25. Oktober 2026

## 1. Prüfumfang und Umgebung

Geprüft wurde der Arbeitsstand auf `feature/os5-monitoring`, ausgehend von
`0314bdc549e734ffa599362861cbbc64d9948258`. Es wurden keine neuen
WD-OS-5-Funktionen ergänzt.

Die Laufzeittests verwendeten:

- Home Assistant `2026.9.4`
- `pytest-homeassistant-custom-component 0.13.367`
- Python `3.14.2`
- den lokalen `wdnas_client`-Stand `4e6389f753ef`
- echte Home-Assistant Entity-, Device- und Area-Registry-Implementierungen
- ausschließlich simulierte NAS-Antworten, keine Verbindung zu einem NAS

## 2. Entity-Registry und Plattformwechsel

### 2.1 Ergebnis der API-Prüfung

Die frühere Integration lud `BinarySensorEntity`-Objekte aus `sensor.py`. Die
Entity-Plattform registrierte diese Einträge deshalb tatsächlich unter
`sensor.*`, unabhängig von ihrer Python-Basisklasse. Die README-Nennung als
`binary_sensor.*` ändert dieses Registry-Verhalten nicht.

Ein atomarer Domainwechsel wird von der eingesetzten Registry-API nicht
unterstützt. Ein Versuch mit
`EntityRegistry.async_update_entity(..., new_entity_id="binary_sensor....")`
endet reproduzierbar mit:

```text
ValueError: New entity ID should be same domain
```

Die zwischenzeitlich untersuchte Alternative „alten Eintrag entfernen und
unter der neuen Domain neu anlegen“ ist für eine automatische Migration nicht
ausreichend sicher:

- die interne Registry-ID ändert sich;
- zwischen Entfernen und Neuanlage besteht ein nicht atomarer
  Unterbrechungszeitraum;
- bei Prozessabbruch kann der alte Eintrag samt Anpassungen verloren sein;
- nicht alle internen und zukünftigen Registry-Metadaten lassen sich zuverlässig
  kopieren;
- Recorder-Historie und externe Referenzen können nicht durch diese Integration
  atomar umgehängt werden;
- ein bereits vorhandener Ziel-Eintrag erzeugt einen Konflikt.

Entsprechend wurde die destruktive Ersatzmigration entfernt. Ein
Config-Entry-Version-Bump ist für die jetzt verwendete Strategie nicht nötig;
die Config-Flow-Version bleibt `1`.

### 2.2 Nicht-destruktiver Kompatibilitätspfad

Bereits vorhandene Binary-Entitäten unter `sensor.*` werden weiterhin über die
Sensorplattform bedient. Dabei bleiben erhalten:

- Entity-ID und Unique-ID;
- interne Registry-ID;
- benutzerdefinierter Name und Icon;
- Bereich;
- Deaktivierungszustand;
- Gerätezuordnung.

Neue Installationen ohne Legacy-Eintrag registrieren dieselben Entitäten korrekt
unter `binary_sensor.*`. Der Legacy-Pfad ist damit eine bewusst begrenzte
Kompatibilitätsausnahme zur sonstigen Plattformtrennung.

Beim erneuten Laden oder Neustart erkennt die Integration den bereits
registrierten Eintrag erneut und legt keine zweite Entität an. Dieses Verhalten
wurde mit Unload und anschließendem Setup gegen die echte Registry getestet.

Existieren für dieselbe Unique-ID bereits ein alter `sensor.*`- und ein neuer
`binary_sensor.*`-Eintrag, werden beide Registry-Datensätze unverändert gelassen
und keine der beiden Entitäten automatisch geladen. Ein generischer Fehler wird
protokolliert, ohne Unique-ID, Seriennummer oder Gerätenamen auszugeben. Diese
konservative Behandlung vermeidet Datenverlust und eine willkürliche Auswahl;
der Konflikt muss vor einer späteren Migration bewusst aufgelöst werden.

### 2.3 Sichere zukünftige Domainmigration

Eine spätere Migration sollte nicht als stiller Config-Entry-Schritt erfolgen.
Empfohlen wird ein benutzergesteuerter Reparatur- oder Options-Flow mit:

1. vollständiger Konfliktprüfung vor jeder Änderung;
2. sichtbarer Zuordnung von Quell- und Ziel-Entity-ID;
3. ausdrücklicher Bestätigung der Domainänderung;
4. Sicherung sämtlicher Registry-Anpassungen;
5. Aktualisierung bekannter Automationsreferenzen über unterstützte HA-APIs;
6. Wiederanlauf- beziehungsweise Rollback-Markern für Prozessabbrüche;
7. abschließender Prüfung, bevor der alte Eintrag entfernt wird.

Bis Home Assistant einen unterstützten atomaren Domainwechsel anbietet oder ein
solcher Ablauf implementiert und freigegeben ist, ist das Beibehalten der
Legacy-Entity die sicherere Variante.

## 3. Bestehende Entitäten

Für einen frischen Config Entry wurden alle bestehenden Entitäten und Zustände
überprüft:

- acht Sensoren: CPU, Speicher, Gesamt-/belegter/freier Speicher,
  Disk-Temperatur, Disk-Größe und Volume-Größe;
- sieben Binary-Sensoren: Disk gesund/schlafend/ausgefallen/überhitzt sowie
  Volume eingehängt/entsperrt/verschlüsselt.

Alle 15 Zustände entsprechen den simulierten NAS-Antworten. Die bisherigen
System-Unique-IDs (`<nas_serial>_<key>`) und Disk-Unique-IDs
(`<disk_serial>_disk_<key>`) bleiben unverändert. Bereits registrierte
Volume-Unique-IDs werden übernommen; neue Volume-IDs werden NAS-weit gescopt.

Die Tests decken außerdem zwei NAS mit derselben lokalen Volume-ID sowie
fehlerhafte optionale Diskwerte ab.

## 4. Coordinator-Lebenszyklus

Die Prüfung bestätigte beziehungsweise korrigierte folgende Punkte:

- pro Setup wird genau eine Client-Session authentifiziert;
- Sensor- und Binary-Sensor-Plattform teilen denselben Coordinator;
- parallele Refresh-Anforderungen erzeugen keine parallelen Logins;
- ein Reload schließt die alte Session genau einmal und erstellt genau eine
  neue Session;
- ein Unload schließt die aktive Session genau einmal;
- nach Reload reagiert nur ein Update-Listener auf Optionsänderungen;
- Verbindungs- und HTTP-Fehler werden als `UpdateFailed` behandelt;
- ein abgelaufener Login schließt nur die Client-Session, authentifiziert einmal
  neu und beendet nicht versehentlich den Coordinator;
- ein endgültiger Shutdown ruft nun auch
  `DataUpdateCoordinator.async_shutdown()` auf und entfernt Timer sowie
  Debouncer.

Neu entdeckt wurde, dass die vorherige Überschreibung von `async_shutdown()`
den Home-Assistant-Teil des Shutdowns nicht aufrief. Dadurch blieb nach
Refresh-Anforderungen ein Timer zurück. Außerdem wurde dieselbe Methode für
Reauthentication verwendet, wodurch der Coordinator intern dauerhaft als
beendet markiert werden konnte. Client-Schließen und endgültiger Shutdown sind
jetzt getrennt.

## 5. Isolierte Testumgebung

### 5.1 WSL2

Die folgenden Befehle erzeugen eine vollständig separate Umgebung. Der Client
wird mit `git archive` exakt aus dem geprüften Commit exportiert; lokale andere
Client-Änderungen gelangen dadurch nicht unbemerkt in den Test.

```bash
set -euo pipefail

TEST_ROOT="$(mktemp -d "$HOME/ha-mycloud-phase1-test.XXXXXX")"
mkdir -p "$TEST_ROOT/wdnas_client" "$TEST_ROOT/config/custom_components"

git -C /mnt/c/Users/pLaCiX/Projects/wdnas_client \
  archive 4e6389f753efdd942a1a919c065861bce18f9ca9 \
  | tar -x -C "$TEST_ROOT/wdnas_client"

cp -a /mnt/c/Users/pLaCiX/Projects/ha-mycloud/custom_components/mycloud \
  "$TEST_ROOT/config/custom_components/"

python3.13 -m venv "$TEST_ROOT/.venv"
source "$TEST_ROOT/.venv/bin/activate"
python -m pip install --upgrade pip
python -m pip install homeassistant
python -m pip install --no-deps -e "$TEST_ROOT/wdnas_client"

python -c 'import wdnas_client; print(wdnas_client.__file__)'
git -C "$TEST_ROOT/wdnas_client" rev-parse HEAD 2>/dev/null || true

hass --skip-pip-packages wdnas-client -c "$TEST_ROOT/config"
```

Da ein `git archive` kein `.git`-Verzeichnis enthält, ist die ausgegebene
Moduldatei die maßgebliche Laufzeitkontrolle. Sie muss unter
`$TEST_ROOT/wdnas_client` liegen. `--skip-pip-packages wdnas-client` verhindert,
dass Home Assistant den lokalen Client beim Start durch PyPI ersetzt.

### 5.2 Docker

Zuerst wird ein isolierter Build-Kontext erzeugt:

```powershell
$testRoot = Join-Path $env:TEMP 'ha-mycloud-phase1-docker'
$clientCheckout = '<wdnas-client-checkout>'
$integrationCheckout = '<ha-mycloud-checkout>'
New-Item -ItemType Directory -Force `
  "$testRoot\wdnas_client", `
  "$testRoot\config\custom_components" | Out-Null

git -C $clientCheckout `
  archive 4e6389f753efdd942a1a919c065861bce18f9ca9 `
  -o "$testRoot\wdnas_client.tar"
tar -xf "$testRoot\wdnas_client.tar" -C "$testRoot\wdnas_client"

Copy-Item -Recurse `
  "$integrationCheckout\custom_components\mycloud" `
  "$testRoot\config\custom_components\mycloud"
```

`Dockerfile` in the isolated test root:

```dockerfile
FROM ghcr.io/home-assistant/home-assistant:stable
COPY wdnas_client /opt/wdnas_client
RUN pip install --no-deps /opt/wdnas_client
ENV PYTHONPATH=/opt/wdnas_client
```

Build, Herkunftskontrolle und Start:

```powershell
docker build -t ha-mycloud-phase1 "$testRoot"

docker run --rm ha-mycloud-phase1 `
  python -c "import wdnas_client; print(wdnas_client.__file__)"

docker run --rm --name ha-mycloud-phase1 `
  -p 8124:8123 `
  -v "${testRoot}\config:/config" `
  ha-mycloud-phase1
```

Die Importkontrolle muss `/opt/wdnas_client/...` ausgeben. Port `8124` und das
separate Config-Verzeichnis verhindern eine Überschneidung mit einer produktiven
Home-Assistant-Instanz. Das Docker-Image enthält ausschließlich den exportierten
Client-Commit; die Manifest-Anforderung `wdnas-client>=0.9.4` ist damit bereits
lokal erfüllt.

## 6. Bewertung

Die getestete Architektur ist für frische Installationen sowie für den
nicht-destruktiven Weiterbetrieb historischer Registrierungen geeignet. Eine
automatische Domainmigration ist mit der geprüften Home-Assistant-Registry nicht
ausreichend sicher und bleibt bewusst ausstehend.

Vor einem Commit sollte entschieden werden, ob der dokumentierte Legacy-Pfad
dauerhaft akzeptiert wird oder ob später ein ausdrücklich bestätigter
Reparatur-Flow entwickelt werden soll. Für Phase 1 ist kein Config-Entry-
Version-Bump erforderlich.
