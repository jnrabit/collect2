# Finetune-Gate — die Schwelle, festgeschrieben VOR der Inventur

Dieses Dokument entsteht **bevor** gezählt wurde. Das ist der ganze Zweck:
eine Schwelle, die man erst nach den Zahlen formuliert, ist keine Schwelle,
sondern eine Rechtfertigung.

Kontext: die Basismodell-Messung hat `qwen3:8b` als Agenten-Basis bestimmt
(40/48 roh, 38/44 bereinigt, beide Gates, ~0,6 s). Die verbleibende Lücke ist
damit kleiner als sie bei Qwythos je war.

## Die Entscheidungsregel

**Ein Finetune lohnt, wenn ALLE drei Bedingungen gelten:**

1. Eine **einzelne** Fehlerklasse macht **≥ 5 %** der gesichteten Schritte aus.
2. Für diese Klasse existiert eine **Quelle, die die Zielfähigkeit selbst
   beherrscht** (Spalte „Kann C das prinzipiell?" in
   [`traces_fehlerklassen.md`](traces_fehlerklassen.md)).
3. Mindestens **30 echte Belegfälle** dieser Klasse liegen im Bestand —
   echte, keine synthetisierten.

**Ein Finetune lohnt nicht, wenn:**

- die Fehler **unter 5 %** über viele Klassen gestreut liegen (dann gibt es
  kein benennbares Lernziel, und ein Finetune tauscht nur Fähigkeiten um), oder
- die dominante Klasse **nur per Handarbeit** lehrbar ist **und** weniger als
  30 Fälle hat.

**Zwischenfall** — dominante Klasse vorhanden, aber keine gute Quelle:
**dokumentierter Einzelentscheid, keine Automatik.** Festzuhalten sind dann:
die Klasse, die Fallzahl, warum keine Quelle taugt, und was stattdessen
versucht wird (Prompt-Änderung, Gate-Regel, Kontextaufbereitung).

## Warum genau diese drei Bedingungen

Jede ist eine Lehre aus einem bezahlten Fehlschlag, nicht aus Theorie:

- **≥ 5 % einer einzelnen Klasse** — der Rewriter-Finetune erreichte 17/24
  gegen 17/24 der unfinetunten Basis: er tauschte `+Distraktor` (1/6 → 3/6)
  gegen `−UNCHANGED` (5/5 → 3/5). Ohne dominante Zielklasse verschiebt
  Training nur Gewichte zwischen Fähigkeiten.
- **Quelle muss die Fähigkeit beherrschen** — Charge 4 sammelte gezielt gegen
  die gemessene Schwäche; von 14 Distraktor-Paaren überlebten **3** die
  Kuration, weil das Lehrer-Modell selbst hedgte. Das Ergebnis (v3, 20/24) lag
  **unter** dem ungezielten v2 (21/24).
- **≥ 30 echte Fälle** — die wirksamen Adapter entstanden aus 41 Beispielen.
  Darunter ist die Stichprobe kleiner als das Rauschen der Evaluation
  (24 Fälle, ±2 sind Zufall).

## „Kein Finetune" ist ein vollwertiges Ergebnis

Ausdrücklich festgehalten: Wenn die Inventur zeigt, dass die Fehler dünn
gestreut sind, ist der richtige Abschluss **„kein Finetune nötig"** — und das
ist ein Ergebnis dieses Prozesses, **kein Scheitern**. Es spart genau die
Arbeit, deren Nutzlosigkeit zweimal gemessen wurde.

Der Bestand liefert dann trotzdem etwas: eine benannte, belegte Liste der
tatsächlichen Fehlerbilder — die Grundlage für Prompt- und Gate-Arbeit, die
billiger ist als Training.

## Was NICHT als Beleg zählt

- Zahlen aus einem zu kleinen oder gemischten Bestand. Traces verschiedener
  Modelle (3b-Rewrites, C-Schritte) sind **nicht dieselbe Verteilung** und
  werden in `stats` getrennt ausgewiesen; eine gemeinsame Quote über beide ist
  keine Entscheidungsgrundlage.
- Die erste Praxisprobe (~30 gesichtete Traces). Sie belegt nur, dass das
  Instrument trägt — nicht, wie die Verteilung aussieht.
- Selbst erzeugte synthetische Negativbeispiele. Zweimal gemessen: sie
  sabotieren den Finetune (9B: 6/10 → 9/10 nach Entfernen; 3B: zerstörten den
  Distraktor-Gewinn).

## Bekannte Mängel des Bestands (aus der ersten Praxisprobe, 2026-07-29)

Zwei Befunde, die **vor** einer echten Inventur behoben oder mitgedacht werden
müssen — sonst entscheidet man auf einer verschmutzten Zahl:

1. **Testfixtures im Korpus.** Mindestens 21 der 203 Einträge sind keine
   Modellausgaben, sondern Platzhalter aus dem Bau der Trace-Pipeline
   (Targets `ok`, `ungeerdete Antwort`, `aaaa…`, Frage `q`), sämtlich
   `step_kind=answer`. Sie sind als `kein_trace` markierbar und zählen
   **nicht** in die Fehlerquote. **Jeder Trainingsbau muss sie ausschließen.**
2. **Das Modell wurde nicht mitgeschrieben — behoben, wirkt aber erst ab
   jetzt.** Neue Traces tragen `model`, `model_digest`, `quant` und `driver`;
   `stats` schlüsselt danach auf. **Der Digest ist das harte Merkmal** (ein Tag
   kann auf neue Gewichte umgebogen werden), **der Treiber ist gleichrangig
   nötig**: dasselbe Modell lieferte über verschiedene Treiber 17/24
   (transformers-fp16) gegen 13/24 (ollama-q4). Der **Altbestand bleibt
   „unbekannt"** — es wird nichts nachträglich geraten.

3. **Die Testfixtures kamen aus einem Leck, nicht aus Zufall.**
   `traces_enabled` ist per Default an und `traces_dir` zeigt auf das
   Produktivverzeichnis — jeder Test, der den Agenten-Pfad berührte, schrieb
   dorthin (reproduziert: zwei Testdateien erzeugten 5 neue Zeilen).
   Behoben durch eine `autouse`-Fixture in `tests/conftest.py`, die den
   Collector für **jeden** Test in ein temporäres Verzeichnis umleitet.
   Nachweislich testerzeugt sind **18 eindeutige trace_ids**; davon waren
   mehrere **nicht** von echten Traces zu unterscheiden (realistische
   TLS-/Spark-Rewrites) — genau deshalb sind die Provenienz-Felder nötig und
   nicht bloß nützlich.

## Offener Punkt, der die Zahlen berührt

Die Distraktor-Kategorie im harten Eval ist **unterspezifiziert**: bei
Vergleichs-Folgefragen beide Entitäten zu nennen (`Nginx im Vergleich zu
Apache`) ist für einen RRF-fusionierenden Retriever vertretbar. `qwen2.5:3b`
und `qwen3:30b-a3b` taten das identisch, `qwen3:8b` dagegen wählt meist eine
Entität. Für die Inventur gilt deshalb: **„beide Entitäten genannt" ist kein
`rewrite_falsch`** (so in der Rubrik verankert). Wird die Kategorie später
re-spezifiziert, ändert das die Zählung — dann ist die Inventur erneut
auszuwerten, nicht die Schwelle anzupassen.
