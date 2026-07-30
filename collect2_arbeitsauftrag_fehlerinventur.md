# collect2 — Arbeitsauftrag: Fehler-Inventur als Finetune-Gate

## Kontext & Zweck

Die Basismodell-Messung hat qwen3:8b (C) als WorkflowAgent-Basis bestimmt:
40/48 roh, 38/44 bereinigt, beide Gates, ~0,6 s. Damit hat sich die Frage
verschoben, die dieser Auftrag beantwortbar machen soll:

**Es geht nicht mehr darum, genug Traces zu sammeln, sondern nachzuweisen,
dass es überhaupt etwas zu lernen gibt.**

Zwei Befunde aus Umbau 5 begründen das:

1. Der Rewriter-Finetune schlug die unfinetunte 3b-Basis NICHT (17/24 gegen
   17/24 bzw. 16/24). Ein Finetune ohne benannte Ziel-Fehlerklasse tauscht
   Fähigkeiten nur um.
2. Charge 4 (gezielt nachgesammelt) verschlechterte das Ergebnis, weil das
   Lehrer-Modell die Zielfähigkeit selbst nicht beherrschte — von 14
   gesammelten Paaren überlebten 3 die Kuration.

Bei C ist die verbleibende Lücke kleiner als sie bei Qwythos je war. Es ist
daher ein realistischer und ausdrücklich erwünschter Ausgang, dass diese
Inventur ergibt: **kein Finetune nötig.** Anti-Scaffold gilt — gebaut wird
erst, wenn die Daten den Konsumenten belegen.

Dieser Auftrag liefert nur das Instrument dafür. Kein Training, kein
Datensatzbau, keine Änderung an der Agenten-Logik.

**Projektregeln:** Module < 400 LOC, Logik von Transport getrennt,
append-only, keine blockierenden Waits, keine Vault-Writes, bestehende
Tests bleiben grün.

---

## Auftrag 1 — Fehlerklassen-Rubrik (`docs/traces_fehlerklassen.md`)

Vor dem Code: die Klassen festlegen, nach denen markiert wird. Sie leiten
sich aus den gemessenen Schwächen und der Aufgabenstruktur ab, NICHT aus
freier Erfindung. Startsatz (erweiterbar, aber jede Erweiterung braucht
einen Beleg-Trace):

| Klasse | Bedeutung |
|---|---|
| `tool_format` | Tool-Call syntaktisch/schematisch falsch (erfundenes Tool, fehlender Pflichtparameter, kaputtes JSON) |
| `tool_unnoetig` | Call, wo keiner nötig war (T5-Muster) |
| `tool_fehlend` | kein Call, wo einer nötig war |
| `rewrite_falsch` | Rewrite verfehlt/verliert den Antezedenten oder erfindet Kontext |
| `rewrite_unnoetig` | eigenständige Frage umformuliert statt UNCHANGED |
| `rollenbruch` | Modell verlässt die zugewiesene Rolle (antwortet inhaltlich statt zu rewriten o. ä.) |
| `register` | Sprache/Register verfehlt (Englisch/Chinesisch auf deutsche Anfrage, Sie-Form, Identity-Leak) |
| `format` | Output-Format verletzt (mehrzeilig wo einzeilig gefordert, Geschwätz um die Antwort) |
| `multi_step` | Fehler entsteht erst im Zusammenspiel mehrerer Schritte (falsche Reihenfolge, Ergebnis des Vorschritts ignoriert) |
| `konfabulation` | inhaltlich erfundene Aussage mit Sicherheitston |
| `sonstiges` | passt in keine Klasse — Freitext Pflicht, Kandidat für neue Klasse |

Pro Klasse: 1–2 Zeilen Definition, ein Positiv- und ein Abgrenzungsbeispiel
(Kalibrierbeispiele, analog `docs/traces_curation.md`). Wichtig für die
spätere Entscheidung: pro Klasse eine Zeile **"kann C das prinzipiell?"** —
also ob eine Quelle existiert, die die Zielfähigkeit beherrscht (C selbst,
das 30B-MoE im Batch, oder nur Handarbeit). Diese Spalte entscheidet in
Auftrag 4 mit, ob eine Klasse überhaupt antrainierbar ist.

## Auftrag 2 — Flag-Kommando (`collect-traces flag`)

- `collect-traces flag <trace_id> --class <klasse> [--note "…"]`
- Schreibt append-only nach `data/traces/flags.jsonl`:
  `{trace_id, klasse, note, timestamp, flagger}` — kein In-Place-Update
  der Trace-Dateien (bestehendes Muster wie die Outcome-Zeilen).
- Mehrfach-Flags pro Trace erlaubt (ein Schritt kann zwei Fehler haben);
  ein späteres Flag mit `--class none` hebt vorherige auf (Korrekturpfad
  ohne Löschen).
- Validierung: Klasse muss in der Rubrik existieren, sonst Fehler mit
  Liste der gültigen Klassen. `sonstiges` verlangt `--note`.
- Bequemlichkeit zählt: `flag` muss ohne Nachschlagen bedienbar sein.
  Tab-Completion nicht nötig, aber `collect-traces flag --help` listet
  die Klassen mit Einzeiler-Definition.

## Auftrag 3 — Review-Modus (`collect-traces review`)

Damit die Inventur nicht an der Bedienung scheitert:

- Geht ungeflaggte Traces chronologisch durch (Filter: `--since`,
  `--step-kind`, `--model`), zeigt pro Trace kompakt: Kontext gekürzt,
  Target voll, ggf. Tool-Call formatiert.
- Tastatur: `Enter` = korrekt (schreibt Flag `none`, damit der Trace als
  gesichtet gilt), Ziffer/Kürzel = Fehlerklasse, `s` = skip (bleibt
  ungesichtet), `q` = Ende.
- Fortschritt persistent: ein Abbruch verliert nichts, Neustart setzt an
  der ersten ungesichteten Stelle fort.
- Das ist ein CLI-Werkzeug, kein Interface-Projekt — Rohtext reicht,
  keine TUI-Bibliothek, keine neuen Dependencies.

## Auftrag 4 — Statistik (`collect-traces stats`)

Der Blick, der die Entscheidung trägt:

```
Traces gesamt:        412   (davon gesichtet: 380, ungeflaggt/korrekt: 341)
Fehlerquote:          39/380 = 10,3 %

Fehlerklassen (absteigend):
  multi_step        18   4,7 %   [Quelle: nur Handarbeit]
  tool_unnoetig      9   2,4 %   [Quelle: C beherrscht es]
  register           6   1,6 %   [Quelle: C beherrscht es]
  rewrite_falsch     4   1,1 %   [Quelle: 30B-MoE]
  sonstiges          2   0,5 %
```

- Zusätzlich: Aufschlüsselung nach `step_kind` und nach Modell (falls
  Traces mehrerer Modelle im Bestand sind — 3b-Rewrites und C-Schritte
  müssen getrennt ausgewiesen werden, sie sind nicht dieselbe Verteilung).
- `--json` für maschinenlesbare Ausgabe (späterer Bericht).
- Die "Quelle"-Spalte kommt aus der Rubrik (Auftrag 1) — sie macht auf
  einen Blick sichtbar, welche Klassen antrainierbar wären.

## Auftrag 5 — Entscheidungsschwelle dokumentieren (`docs/finetune_gate.md`)

Kurzes Dokument, das die Schwelle VOR der Inventur festschreibt (damit sie
nicht nachträglich passend gemacht wird):

- **Finetune lohnt, wenn:** eine einzelne Fehlerklasse ≥ 5 % der gesichteten
  Schritte ausmacht UND für sie eine Quelle existiert, die die
  Zielfähigkeit beherrscht (Rubrik-Spalte) UND mindestens 30 echte
  Belegfälle im Bestand liegen.
- **Finetune lohnt nicht, wenn:** Fehler unter 5 % gestreut über viele
  Klassen liegen, oder die dominante Klasse nur per Handarbeit lehrbar
  ist und weniger als 30 Fälle hat.
- Zwischenfall (dominante Klasse, aber keine gute Quelle): dokumentierter
  Einzelentscheid, keine Automatik.
- Ausdrücklich festhalten: "kein Finetune" ist ein vollwertiges Ergebnis
  dieses Prozesses, kein Scheitern.

## Akzeptanz

1. Rubrik existiert mit Kalibrierbeispielen und Quellen-Spalte.
2. `flag`, `review`, `stats` funktionieren; Unit-Tests für Flag-Schreiben,
   Aufhebung via `none`, Klassen-Validierung, stats-Aggregation.
3. **Praxisprobe:** mindestens 30 vorhandene Traces per `review` gesichtet,
   `stats` liefert eine plausible erste Verteilung. Diese Zahlen sind noch
   KEINE Entscheidungsgrundlage (Bestand zu klein, Modellmix) — sie belegen
   nur, dass das Instrument trägt.
4. Bestehende Tests grün; Collector-Verhalten unverändert; kein Modul
   > 400 LOC; keine neuen Pflicht-Dependencies.

## Nicht-Ziele

Kein Training, kein Datensatzbau, keine Änderung der Agenten-Logik, keine
automatische Fehlererkennung (die Markierung ist bewusst menschlich —
ein Klassifikator würde genau die blinden Flecken erben, um die es geht).
