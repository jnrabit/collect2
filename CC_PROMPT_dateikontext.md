# CC-Auftrag: collect2 — Ad-hoc-Dateikontext + lokaler Vault-Ingest

Zwei Geschwister-Features: **Teil A** „lies und analysiere ~/xyz"
(flüchtiges Arbeitsgedächtnis) und **Teil B** „lade ~/xyz in den Vault"
(bewusste Sedimentierung). Sie teilen Pfad-Erkennung und Lese-Grenzen —
diese Bausteine werden **einmal** implementiert und von beiden genutzt.

## Arbeitsmodus (verbindlich)

**Zwei Phasen. Erst PLAN, dann STOP.** Vollständiger Plan (§Planformat),
dann Warten auf mein explizites „go". Vor dem „go" keine Datei geschrieben,
kein Commit. Lesen/Analysieren erwünscht — insbesondere: wie hat vibelike
(`~/vibelike`, read-only!) Datei-Lesen/Analyse gelöst? Portierbares benennen.

## Ziel

Eine Query kann auf Dateien/Verzeichnisse zeigen — „lies und analysiere
~/projekt/foo.py", „was macht ~/scripts/backup.sh?" — und collect2 erdet
die Antwort **direkt auf dem Dateiinhalt**, ohne dass der Inhalt vorher in
den Vault geladen werden muss.

**Kernprinzip: Vault = Sediment, Dateikontext = Arbeitsgedächtnis.**
Ad-hoc gelesene Inhalte sind flüchtig (nur für diese Antwort), sedimentieren
nie in den Vault und ossifizieren nie.

## Architektur-Vorgaben

1. **Deterministisches Gate, kein LLM-Entscheid:** Pfad-Erkennung im
   Orchestrator regelbasiert (Muster `~/`, `/home/`, `./` + Existenz-Check
   nach Expansion). Kein Pfad in der Query → Verhalten exakt wie heute.
   Kontrollfluss bleibt Zustandsmaschine (DESIGN §5), **kein Agent-Loop,
   kein Tool-Calling durch das Modell.**

2. **Lese-Grenzen (sicherheitskritisch):**
   - Neue `.env`-Allowlist `COLLECT_READ_PATHS` (kommasepariert), Default:
     die bestehenden Executor-Grenzen (`~/collect2`, `~/collect`). Erweiterung
     ist bewusster Opt-in des Operators.
   - Pfad außerhalb der Allowlist → klare Ablehnung in der Antwort („Pfad
     nicht freigegeben"), kein stiller Fallback auf Vault-Suche.
   - Kanonisierung vor der Prüfung (realpath): Symlink-/`..`-Escapes aus der
     Allowlist heraus müssen scheitern. Tests dafür sind Pflicht.
   - Nur Lesen. Niemals Schreiben über diesen Pfad. Keine Ausführung.
   - Zusammenspiel mit API-Härtung bedenken: über die (ggf. exponierte) API
     ist dieses Feature ein Datei-Lese-Endpoint — die Allowlist ist die
     Verteidigungslinie. Im Plan explizit behandeln.

3. **Ephemeres Retrieval statt Volltext-Stopfen:**
   - Datei lesen → chunken → mit dem **bestehenden** MiniLM-Embedder
     (CPU, ein Embedding-Backend — DESIGN §5) in-RAM einbetten → die zur
     Frage relevantesten Chunks in die Synthese. Kein Vault-Write, kein
     Cache auf Platte.
   - Kleine Dateien (unter Schwelle, z. B. passt komplett in Kontextbudget):
     direkt ganz reingeben, Embedding sparen. Schwelle im Plan vorschlagen.
   - Verzeichnis statt Datei: begrenzte Tiefe/Dateizahl, Text-/Code-Dateien
     nach Endung filtern, Binärdateien überspringen. Limits im Plan beziffern
     (max Dateien, max Gesamtbytes) — Schutz vor „lies ~/riesenordner".

4. **Einordnung in die bestehende Pipeline:**
   - Dateikontext ergänzt das normale Retrieval, ersetzt es nicht zwingend:
     Frage kann Datei UND Vault-Wissen brauchen („was macht foo.py und gibt
     es dazu Doku im Vault?"). Vorschlag: Dateichunks als zusätzliche
     Beitragsquelle ins Contribution-Manifest (eigener Beitragstyp), parallel
     zu RetrievalAgent/CodeRetrievalAgent — begründet abweichen erlaubt.
   - **Drei-Zonen-Logik:** direkt gelesener Dateiinhalt ist per Definition
     geerdet (Quelle liegt vor). Vorschlag: eigene Kennzeichnung in der
     Meta-Zeile („📄 Datei: pfad") statt Distanz-Zone auf Dateichunks
     anzuwenden; Zonen-Verdikt weiter nur für Vault-Anteile. Im Plan
     positionieren.
   - Meta-Zeile im Web-Chat zeigt gelesene Pfade + Chunk-Anzahl (Transparenz,
     wie beim ↻-Rewrite).

5. **Nicht ossifizieren:** LearningAgent ignoriert Antworten mit
   Dateikontext-Anteil für die Fakt-Extraktion — oder extrahiert nur aus dem
   Vault-Anteil. Begründete Empfehlung im Plan; im Zweifel konservativ
   (gar nicht ossifizieren).

---

## Teil B — Lokaler Vault-Ingest („lade ~/xyz in den Vault")

**Ziel:** Lokale Dateien/Verzeichnisse bewusst in den General-Vault
sedimentieren — als zweiter Quell-Adapter des bestehenden Harvesters.

1. **Maximale Wiederverwendung:** Der Ingest-Pfad aus Baustelle C
   (`harvest/ingest.py`: Qualität/Dedupe → Backup → tmp → Reload-Verify →
   atomarer Move, nie in-place) wird **unverändert** genutzt. Neu ist nur
   der Quell-Adapter `harvest/local.py` (Dateien lesen statt MediaWiki),
   analog zu `harvest/wikipedia.py`. Gleiche Qualitätsschranke (Sprache,
   Länge, Duplikat), gleiche Limits-Logik.
2. **CLI zuerst:** `collect-harvest local <pfad> [--limit]` — derselbe
   Einstieg wie bei Wikipedia, idempotent wiederholbar. Lese-Grenzen:
   dieselbe `COLLECT_READ_PATHS`-Allowlist + realpath-Kanonisierung wie
   Teil A — **eine** Implementierung, beide Consumer.
3. **Chat-Befehl nur mit Bestätigung:** Deterministische Erkennung
   („lade … in den Vault"), aber der Ingest läuft NIE direkt los.
   Stattdessen: Vorschau-Antwort (N Dateien gefunden, M nach Qualitätsfilter,
   Gesamtgröße) → explizites „ja"/„bestätigen" im Folge-Turn startet den
   Ingest → Ergebnis-Report (Vault-Count vorher/nachher, Backup-Pfad).
   Kein beiläufiges Sedimentieren durch einen dahingetippten Satz —
   Vault-Writes sind die heikelste Operation im System. Wie der
   Bestätigungs-Zustand über zwei Turns gehalten wird (Gesprächskontext
   existiert bereits), im Plan positionieren — ohne Blocking-Wait.
4. **Abgrenzung zu Teil A klar in der Antwort:** „lies X" erzeugt nie
   Vault-Writes, „lade X" erzeugt nie eine Analyse-Antwort. Zwei Verben,
   zwei Wirkungen, keine stille Vermischung. Bei ambiger Formulierung:
   nachfragen statt raten.

## Nicht-Ziele (bewusst)

Agent-Loop / freies Tool-Calling · Sandbox / Code-Ausführung ·
Datei-Schreiben außerhalb des Harvester-Ingest-Pfads · Watching/
automatische Indizierung von Verzeichnissen · Sync (geänderte Datei
re-ingesten) · Idiom-System.
Erscheint dir davon etwas nötig: als Frage benennen, nicht bauen.

## Abnahme

- Bestehende Tests grün (212), doctor grün, CI grün.
- Neue Tests: Gate (Pfad erkannt / nicht erkannt), Allowlist-Rejection,
  Symlink-/`..`-Escape scheitert, kleine Datei direkt, große Datei via
  Chunk-Auswahl, Verzeichnis-Limits, Manifest-Integration ohne Redis
  (InMemoryBus).
- Realer Smoke-Test A: „lies und analysiere <echte Datei>" im Web-Chat →
  korrekte Analyse, Meta-Zeile zeigt Datei, keine Vault-Verschmutzung
  (Vault-Doc-Count unverändert).
- Realer Smoke-Test B: `collect-harvest local` auf ein kleines Test-
  verzeichnis → Safe-Write verifiziert (Backup existiert, Count korrekt
  gestiegen, Reload-Verify grün); danach Chat-Weg mit Vorschau →
  Bestätigung → Ingest. Abbruch-Fall testen (Vorschau, dann „nein" →
  kein Write).
- Teil-B-Tests: Adapter ohne echten Vault (InMemory/Fixture), Bestätigungs-
  Zustandsmaschine über zwei Turns, Allowlist-Rejection auch im Ingest-Weg.
- Retrieval-Benchmark unverändert (Features dürfen pfadlose Queries nicht
  beeinflussen — 0-Diff nachweisen); nach Test-Ingest alte Queries stabil.

## Planformat (Phase 1 — davor kein „go")

1. **Archäologie** — was hatte vibelike dafür, was ist portierbar
2. **Ist-Analyse** — Orchestrator-Routing heute, Manifest-Beitragstypen,
   Executor-Lese-Grenzen (wo implementiert, wiederverwendbar?)
3. **Schritte** — Dateien (neu/geändert) mit LOC-Schätzung; wo eigenes Modul
   (z. B. `retrieval/filecontext.py`) statt Orchestrator aufblähen
4. **Tests** — siehe Abnahme, konkret benennen
5. **Risiken & Rollback** — größtes Risiko vermutlich: Security der
   Lese-Grenze und Kontextbudget-Sprengung bei großen Dateien
6. **Offene Entscheidungen** — mit Empfehlung + Begründung. Mindestens:
   Zonen-Behandlung von Dateichunks, Ossifikation, Direktgabe-Schwelle,
   Verzeichnis-Limits, Bestätigungs-Mechanik über zwei Turns (Teil B),
   welche Dateitypen der local-Adapter akzeptiert (Endungs-Allowlist?)

Empfohlene Bau-Reihenfolge: gemeinsame Bausteine (Pfad-Gate + Lese-Grenzen)
→ Teil A → Teil B. Nach Teil A kurzer Ist-Bericht + Stopp-Punkt; Teil B
erst nach meiner Freigabe (Vault-Write-Risiko rechtfertigt Einzelfreigabe).

Dann: **STOP. Warten auf „go".**
