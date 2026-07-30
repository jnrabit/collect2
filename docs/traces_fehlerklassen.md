# Fehlerklassen — Rubrik für die Inventur

Maßgebliche Klassenliste für `collect-traces flag` / `review` / `stats`.
Zweck: **nachweisen, ob es überhaupt etwas zu lernen gibt** — nicht Traces
sammeln. Ein Finetune ohne benannte Ziel-Fehlerklasse tauscht Fähigkeiten nur
um (belegt: Rewriter-Finetune 17/24 gegen 17/24 Basis; Charge 4 verschlechterte
das Ergebnis).

Die Klassen leiten sich aus **gemessenen** Schwächen und der Aufgabenstruktur
ab, nicht aus freier Erfindung. Erweiterung ist erlaubt, **aber jede neue
Klasse braucht einen Beleg-Trace** — sonst wächst die Rubrik ins Spekulative.

Schwelle und Entscheidungslogik stehen in [`finetune_gate.md`](finetune_gate.md),
festgeschrieben **vor** der Inventur.

## Die Quellen-Spalte — sie entscheidet mit

Pro Klasse steht, ob eine Quelle existiert, die die Zielfähigkeit **selbst
beherrscht**. Das ist die Lehre aus Charge 4: von 14 gezielt gesammelten
Distraktor-Paaren überlebten 3 die Kuration, weil das Lehrer-Modell
(qwen2.5:3b) selbst hedgte. **Aus einer Quelle, die die Fähigkeit nicht hat,
lässt sie sich nicht destillieren.**

Belegte Quellen-Lage (Stand 2026-07-29, Basismodell-Messung):

- **C = `qwen3:8b`** — die neue Agenten-Basis. 40/48 eval_hard, beide Gates,
  ~0,6 s. Wo C die Fähigkeit hat, ist sie *nicht* antrainierbar-nötig — dann
  ist der Fehler eher Prompt- oder Kontextsache.
- **30B-MoE (`qwen3:30b-a3b-2507`)** — im Batch nutzbar (~5,4 s/Rewrite über
  llama.cpp mit Experten-Offload), nicht resident neben Training.
- **Handarbeit** — teuer, aber die einzige Quelle für alles, was kein
  verfügbares Modell beherrscht.

| Klasse | Bedeutung | Kann C das prinzipiell? |
|---|---|---|
| `tool_format` | Tool-Call syntaktisch/schematisch falsch | **ja** — C bestand T4 (valider Call, Pflichtparameter gesetzt) |
| `tool_unnoetig` | Call, wo keiner nötig war (T5-Muster) | **ja** — C bestand T5 (kein unnötiger Call) |
| `tool_fehlend` | kein Call, wo einer nötig war | **ja** — C bestand T4; D scheiterte genau hier |
| `rewrite_falsch` | Rewrite verfehlt/verliert den Antezedenten oder erfindet Kontext | **ja** — C: `tiefer_antezedent` 4/4 in beiden Sätzen |
| `rewrite_unnoetig` | eigenständige Frage umformuliert statt UNCHANGED | **ja** — C: `unchanged` 5/5 in beiden Sätzen |
| `rollenbruch` | Modell verlässt die zugewiesene Rolle | **ja** — C hielt in T1–T3 die Rewriter-Rolle |
| `register` | Sprache/Register verfehlt (Englisch/Chinesisch auf deutsche Anfrage, Sie-Form, Identity-Leak) | **ja** — C bestand T6 als einziges 8–9B sauber |
| `format` | Output-Format verletzt (mehrzeilig wo einzeilig gefordert, Geschwätz) | **ja** — C: einzeilige Rewrites in T1/T2 |
| `multi_step` | Fehler entsteht erst im Zusammenspiel mehrerer Schritte | **ungeklärt** — nicht gemessen; die Probe-Suite testet Einzelschritte. Bis zum Beleg: **nur Handarbeit** annehmen |
| `konfabulation` | inhaltlich erfundene Aussage mit Sicherheitston | **nein** — modellübergreifend ungelöst; D konfabulierte in T4 einen medizinischen Kontext. Nur Handarbeit |
| `blindflug` | Agent handelt ohne vorherigen Kontext-Check (kein `ls`/`Read` des Zielverzeichnisses) — Dateien am falschen Ort, existierende Konventionen ignoriert | **prozessual** — Workflow-Prompt-Regel adressiert es (Pre-Flight-Check). **KEIN Modellfehler** |
| `kein_trace` | kein echter Modellausgang (Testfixture/Platzhalter im Bestand) — **Korpusdefekt, kein Modellfehler** | — ausschließen, nicht lernen |
| `sonstiges` | passt in keine Klasse — Freitext Pflicht | — Kandidat für neue Klasse |

> **`kein_trace` und `blindflug` zählen NICHT in die Fehlerquote.** Sonst misst man die eigene
> Testdaten-Verschmutzung oder Agent-Prozessfehler als Modellschwäche und entscheidet den
> Finetune auf einer falschen Zahl. Die Klassen werden in `stats` trotzdem ausgewiesen —
> sie sind die Arbeitsliste für Korpus-Bereinigung und Prozess-Verbesserung.

**Lesehilfe zur Spalte:** „ja" heißt *nicht* „ist kein Problem", sondern: eine
Quelle für saubere Zielbeispiele existiert. Eine Klasse mit „ja" und hoher
Frequenz ist antrainierbar. Eine Klasse mit „nur Handarbeit" braucht 30 echte
Fälle, bevor sich der Aufwand lohnt (siehe Gate).

---

## Definitionen mit Kalibrierbeispielen

Format wie in [`traces_curation.md`](traces_curation.md): ein Positivbeispiel
(so sieht der Fehler aus) und eine Abgrenzung (das ist er **nicht**).

### `tool_format`
Der Call existiert, ist aber nicht ausführbar: erfundener Tool-Name, fehlender
Pflichtparameter, kaputtes JSON, Parameter außerhalb der Signatur.

- **Positiv:** `vault_search({"q": "RRF"})` — Pflichtparameter heißt `query`.
- **Abgrenzung:** `retrieve({"query": "RRF", "route": "general"})` mit
  überflüssigem, aber schema-konformem `route` → **kein** Fehler.

### `tool_unnoetig`
Ein Call, dessen Ergebnis die Antwort nicht braucht — im Agenten-Loop ein
bezahlter Leerlauf pro Turn.

- **Positiv:** Frage nach der Definition einer Zone, die in der
  Tool-Beschreibung steht → trotzdem `retrieve`.
- **Abgrenzung:** Retrieval bei einer Wissensfrage, das nichts findet → der
  Call war berechtigt, das Ergebnis leer. Kein Fehler dieser Klasse.

### `tool_fehlend`
Eine Aufgabe verlangt ein Werkzeug, das Modell antwortet stattdessen aus dem
Gedächtnis.

- **Positiv:** „Such im Vault nach X und hol das Dokument" → Fließtext-Antwort
  ohne Call (das gemessene D-Verhalten).
- **Abgrenzung:** Small Talk oder eine Frage über die eigene Funktionsweise →
  kein Tool nötig.

### `rewrite_falsch`
Der Rewrite verliert den Antezedenten, löst den **falschen** auf oder erfindet
Kontext, der im Verlauf nicht steht.

- **Positiv:** Verlauf über LSB-Timing-Jitter, Folgefrage „reicht das für
  Krypto?" → „Ist der Lorenz-Attraktor kryptographisch sicher?" (falsche
  Referenz aufgelöst).
- **Abgrenzung:** Ein Rewrite, der bei zwei Vergleichs-Entitäten **beide**
  nennt („Nginx im Vergleich zu Apache"), ist für RRF-Fusion vertretbar und
  gilt hier **nicht** als Fehler — offene Kategorie-Re-Spezifikation, siehe
  Gate-Dokument.

### `rewrite_unnoetig`
Eine bereits eigenständige Frage wird umformuliert, statt `UNCHANGED` zu
liefern — besonders schädlich, wenn dabei fremder Verlaufskontext anhaftet.

- **Positiv:** „Was ist Batch-Normalisierung?" nach einem Reranker-Verlauf →
  „Was ist Batch-Normalisierung beim Reranking?" (Fremdkontext angehängt).
- **Abgrenzung:** Leichte Umformulierung ohne Bedeutungsverschiebung
  („Wie funktioniert X?" → „Wie funktioniert X genau?") → kein Fehler.

### `rollenbruch`
Das Modell verlässt die zugewiesene Rolle: beantwortet inhaltlich, statt zu
rewriten; kommentiert die Aufgabe; fragt zurück.

- **Positiv:** Rewriter-Prompt → „Der Goertzel-Algorithmus ist robust gegen
  Netzbrummen, weil …" (inhaltliche Antwort statt Suchanfrage).
- **Abgrenzung:** Ein Rewrite, der zufällig wie eine Aussage klingt, aber eine
  Suchanfrage ist → kein Bruch.

### `register`
Sprache oder Register verfehlt. Umfasst ausdrücklich den **Identity-Leak**.

- **Positiv:** Deutsche Anfrage → englische Antwort mit
  „Qwythos here from Empero AI" (gemessenes A-Verhalten); oder chinesische
  Antwort (gemessenes B-Verhalten); oder Sie-Form statt Du.
- **Abgrenzung:** Englische Fachbegriffe im deutschen Satz („Sliding Window") →
  kein Registerfehler.

### `format`
Das Ausgabeformat ist verletzt, obwohl der Inhalt stimmt.

- **Positiv:** Einzeiliger Rewrite gefordert, geliefert werden drei Zeilen mit
  Vorrede („Hier ist die umformulierte Frage:").
- **Abgrenzung:** Eine Zeile, die länger ist als erwartet → kein Formatfehler,
  solange einzeilig und ohne Beiwerk.

### `multi_step`
Der Einzelschritt ist für sich korrekt; der Fehler entsteht erst im
Zusammenspiel: falsche Reihenfolge, Ergebnis des Vorschritts ignoriert,
Endlosschleife, doppelte Arbeit.

- **Positiv:** `retrieve` liefert Treffer, der nächste Schritt ruft erneut
  `retrieve` mit derselben Query, statt zu antworten.
- **Abgrenzung:** Zwei `retrieve`-Calls mit **verschiedenen** Teilfragen
  (Decompose-Muster) → beabsichtigt, kein Fehler.
- **Hinweis:** Diese Klasse ist erst mit dem Serving-Agenten sinnvoll
  markierbar (`step_kind: tool_call`), weil sie mehrere Schritte eines
  `workflow_id` betrifft. Im heutigen deterministischen Bestand wird sie
  selten vorkommen — das ist ein Befund, kein Mangel.

### `konfabulation`
Inhaltlich erfundene Aussage, im Ton einer gesicherten Auskunft.

- **Positiv:** Zum erfundenen Begriff „Ossifikat-Ratifizierung" wird ein
  medizinischer Kontext samt Regelwerk beschrieben (gemessenes D-Verhalten).
- **Abgrenzung:** Explizit markierte Unsicherheit („vermutlich", „ich habe
  dazu nichts gefunden") → kein Fehler dieser Klasse.

### `kein_trace`
Der Eintrag ist gar kein Modellausgang: Testfixture, Platzhalter, Artefakt aus
einem Testlauf. Ein **Korpus**defekt — die Zeile gehört aus jedem Trainingsbau
ausgeschlossen, nicht als Fehlerbild gelernt.

- **Positiv:** Target `"ok"` / `"ungeerdete Antwort"` / `"aaaa…bbbb…cccc"`,
  Frage `FRAGE: q`, Quellen `[1] T: Inhalt`.
- **Abgrenzung:** Eine echte, aber schlechte Modellantwort → gehört in eine
  inhaltliche Klasse, nicht hierher.
- **Beleg (Anlass der Klasse):** 21 solcher Einträge im Bestand vom
  2026-07-29, sämtlich `step_kind=answer`, entstanden beim Bau der
  Trace-Pipeline.

### `blindflug`
Der Agent handelt, ohne vorher die Zielstruktur zu lesen — kein `ls`, kein
`Read` des Zielverzeichnisses oder der existierenden Dateien. Resultat: Code
wird am falschen Ort abgelegt, existierende Konventionen ignoriert, vorhandene
Funktionalität dupliziert. Ein **Prozess**fehler — nicht der Output des Modells
ist falsch, sondern der Workflow hat einen Schritt ausgelassen.

- **Positiv:** Agent legt `tests/test_engine.py` im leeren `~/projekte/quelibrium/`
  ab, ohne vorher `ls` gemacht zu haben — die Python-Testdatei gehört nicht in
  ein C++-Projekt und dupliziert das, was CMake+CTest schon leisten.
- **Positiv:** Agent schreibt `src/engine.cpp`, ohne zu prüfen, ob dort schon
  Code liegt (es lag keiner — aber er hat nicht geschaut).
- **Abgrenzung:** Agent liest die Zielstruktur (`ls`, `Read`) und trifft dann
  eine *fachlich falsche* Entscheidung → das ist ein Modellfehler
  (z.B. `rewrite_falsch`), kein `blindflug`.
- **Abgrenzung:** Agent hat den Pre-Flight-Check gemacht (`ls` zeigt leeres
  Verzeichnis) und entscheidet korrekt, neu anzulegen → kein Fehler.
- **Beleg (Anlass der Klasse):** 2026-07-30: Workflow-Agent verbringt 98 s
  damit, `test_engine.py` in `quelibrium/` zu bauen, ohne je `ls quelibrium/`
  aufgerufen zu haben. Verify (Exit 2) bricht ab, aber erst nach zwei
  Repair-Runden. Ohne `ls` wusste der Agent nicht, dass er in einem leeren
  C++-Projekt operiert.

### `sonstiges`
Auffangklasse. **`--note` ist Pflicht.** Häufen sich hier ähnliche Fälle, ist
das der Beleg für eine neue Klasse — dann Rubrik erweitern und den Beleg-Trace
nennen. (So ist `kein_trace` entstanden.)

---

## Bedienung

```
collect-traces review                 # geführt durchgehen (empfohlen)
collect-traces flag <trace_id> --class register --note "englisch geantwortet"
collect-traces flag <trace_id> --class none        # Markierung zurücknehmen
collect-traces stats                  # Verteilung + Quellen-Spalte
```

Flags liegen append-only in `data/traces/flags.jsonl`; Trace-Dateien werden
**nie** in-place geändert (Muster wie `outcomes.jsonl`). Mehrfach-Flags pro
Trace sind erlaubt — ein Schritt kann zwei Fehler haben. Das jeweils letzte
Flag einer Klasse gilt; `--class none` hebt alle vorherigen auf.
