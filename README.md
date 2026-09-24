# migrate-vivaldi

Arbeitsbereiche, Tabs und Schnellwahlbilder zwischen zwei Vivaldi-Profilen unter
Linux wiederherstellen – beispielsweise beim Wechsel von RPM zu Flatpak.

Das Werkzeug benötigt Python 3.9 oder neuer und keine zusätzlichen Bibliotheken.
Es entstand aus einer erfolgreich überprüften Migration von Vivaldi
8.1.4087.61 (RPM) zu 8.2.4133.76 (Flatpak). Andere Versionen sind nicht im Browser
getestet; Vivaldis interne Dateiformate können sich ändern.

## Was übertragen wird

- Arbeitsbereichsdefinitionen aus `Preferences`; sonstige Einstellungen stammen
  aus dem neuen Profil.
- Der gesamte alte Ordner `Sessions`, einschließlich Tabs und gespeicherter
  Sitzungen. Er ersetzt den neuen Sitzungsstand.
- Thumbnailverweise innerhalb der bestehenden Lesezeichen. Namen, URLs und
  andere Metadaten bleiben aus dem neuen Profil erhalten.
- Lokale Bilder aus `VivaldiThumbnails` und `SyncedFiles`, einschließlich der
  zugehörigen Bildverfügbarkeit in `SyncedFilesData`.

Andere Browserdaten wie Passwörter, Cookies, Verlauf und Erweiterungen werden
nicht übertragen. Ein definierter Arbeitsbereich kann leer bleiben, wenn die
alte Sitzung keine Tabs dafür enthält. Fehlende Bilddateien werden im Bericht
aufgeführt; das Werkzeug erzeugt oder lädt diese nicht nach.

## Vorbereitung

1. Im gewünschten Browserprofil `vivaldi://about` öffnen und den Profilpfad
   notieren. `Default` ist nicht zwingend das aktive Profil!
2. Vivaldi vollständig schließen und die alten sowie neuen Profilordner in ein
   separates Arbeitsverzeichnis kopieren. Alle fünf oben genannten Einträge
   müssen vorhanden sein. Fehlende, tatsächlich ungenutzte Bildordner können in
   der Kopie leer angelegt werden.
3. Die Lesezeichen müssen in beiden Profilkopien dieselben GUIDs haben,
   beispielsweise nach erfolgreichem Vivaldi Sync. Bei abweichenden GUID-Mengen
   bricht das Werkzeug ab; es führt keine Lesezeichenbestände zusammen.

Typische Host-Pfade:

| Installation | Profilpfad |
| --- | --- |
| RPM / native Installation | `~/.config/vivaldi/Default` |
| Flatpak | `~/.var/app/com.vivaldi.Vivaldi/config/vivaldi/Profile 1` |

Profilname und Basispfad immer am eigenen System prüfen. Alle Pfade sind
explizite Parameter; es gibt kein automatisch ausgewähltes Zielprofil.

## 1. Daten aus Offline-Kopien vorbereiten

```bash
python3 migrate_vivaldi.py prepare \
  --old /pfad/zur/alten-kopie/Default \
  --new '/pfad/zur/neuen-kopie/Profile 1' \
  --output /pfad/zum/arbeitsverzeichnis/prepared
```

Das Ausgabeverzeichnis darf noch nicht existieren. Die Eingaben bleiben
unverändert. Das Ergebnis enthält die fünf benötigten Einträge sowie
`migration-report.json` mit Arbeitsbereichszahl, Bildverweisen und fehlenden
Bildern. Quell-, Ziel- und Ausgabeverzeichnisse dürfen sich nicht überlappen.

## 2. Prüfen und installieren

```bash
python3 migrate_vivaldi.py install \
  --source /pfad/zum/arbeitsverzeichnis/prepared \
  --target "$HOME/.var/app/com.vivaldi.Vivaldi/config/vivaldi/Profile 1" \
  --dry-run
```

Der Prüfmodus schreibt nichts. Danach Vivaldi vollständig schließen und
denselben Befehl ohne `--dry-run` ausführen. Als Profilbesitzer ausführen,
**ohne sudo**. Während der Übernahme den Browser nicht starten.

Die Installation:

1. Prüft Dateien, JSON-Daten und laufende Vivaldi-Prozesse des Benutzers.
2. Kopiert und prüft die vorbereiteten Daten mittels SHA-256.
3. Verschiebt die fünf bisherigen Einträge in eine Sicherung unter
   `<Vivaldi-Datenordner>/vivaldi-transfer-backups/profile-…/original`.
4. Ersetzt die fünf Ziele vollständig und prüft das Ergebnis. Bei abgefangenen
   Fehlern während des Austauschs werden die ursprünglichen Einträge
   automatisch zurückgesetzt.

Die Sicherung liegt außerhalb des Zielprofilordners. Ein Systemabsturz oder
`SIGKILL` kann die mehrteilige Übernahme unterbrechen; in diesem Fall die
ausgegebene Sicherung und deren `prepared`-Verzeichnis vor einem Browserstart
prüfen. Die Operation ist keine atomare Transaktion über alle fünf Einträge.

Nach erfolgreicher Übernahme Vivaldi starten und Arbeitsbereiche sowie
Schnellwahlen prüfen. Falls nötig unter Einstellungen → Allgemein → Starten mit
die letzte Sitzung auswählen. Eine Dateiprüfung ersetzt diesen Browsertest nicht.

## Rückgängig machen

Das Werkzeug gibt den passenden Befehl aus. Er entspricht:

```bash
python3 migrate_vivaldi.py install \
  --source /pfad/zur/sicherung/original \
  --target /pfad/zum/installierten/profil
```

Auch beim Wiederherstellen muss Vivaldi geschlossen sein. Der aktuelle Zustand
wird erneut gesichert; vorhandene Sicherungen werden nicht überschrieben.

## Entwicklung

Der Hauptbranch heißt `master`. Code, Kommentare, Bezeichner und CLI-Ausgaben
sind englisch. Profilpfade werden als Parameter übergeben; wiederverwendete
Konstanten sind zentral benannt. Weitere Regeln stehen in `AGENTS.md`.

```bash
python3 -m unittest discover -s tests -v
```

Die Tests verwenden ausschließlich synthetische Daten und prüfen unter anderem
das Zusammenführen der Metadaten, fehlende Bilder, den Prozessschutz, den
Prüfmodus und die Wiederherstellung nach einem simulierten Schreibfehler.

Commits beginnen mit Gitmoji, gefolgt von Conventional Commits mit Scope:

```text
✨ feat(migration): add workspace and thumbnail migration
✅ test(migration): cover backup and rollback
📝 docs(usage): document RPM to Flatpak migration
```

Persönliche Profile, Berichte und Sicherungen gehören nicht ins Repository.
Die `.gitignore` schließt typische Namen und Profilbestandteile aus; vor jedem
Commit zusätzlich die tatsächlich vorgemerkten Dateien prüfen.

Offizielle Hinweise: [Vivaldi – Import and export browser data](https://help.vivaldi.com/desktop/tools/import-and-export-browser-data/).
