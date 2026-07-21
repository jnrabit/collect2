# Trace-Kuration — Rubrik & Baseline

Konsistente Kurations-Linie für Rewrite-Trainingstraces (Auftrag: Trace-Pipeline).
Zweck: **reproduzierbare** Verdikte statt schwankendem Einzelurteil. Diese Datei
ist die maßgebliche Regelquelle — die kuratierte Baseline (`data/traces/curated/`)
ist ihr erster Anwendungsfall, nicht die Definition.

Workflow: **Stichprobe + Override.** Die mechanischen Regeln (unten „auto")
sieben vorab; ein Mensch prüft nur die verbleibenden Grenzfälle („Urteil") und
kann jede Regel-Entscheidung überstimmen. So bleibt die Baseline konstant *und*
auditierbar.

## Annahme (guter Positiv-Trace)

Ein Rewrite-Target wird angenommen, wenn ALLE gelten:

1. **Eigenständig:** ohne den Gesprächsverlauf verständlich.
2. **Antezedent erhalten:** das aufgelöste Thema aus der History steht im Target
   (z. B. „das" → „der Goertzel-Algorithmus").
3. **Deutsch, Du-Register:** kein „Sie/Ihnen/Ihre".
4. **Sauberer Begriff:** kein zerhackter/erfundener Term.
5. **Kein Konjunktions-Anfang:** Target beginnt nicht mit und/aber/oder/also.
6. **Echter Rewrite:** Target ≠ referenzieller Original-Wortlaut.

## Ablehnung (dokumentierte Fehlerklassen)

| Klasse | Regel | Prüfung |
|---|---|---|
| kein Rewrite | Target == Original (Passthrough) | **auto** |
| Konjunktions-Anfang | Target beginnt mit und/aber/oder/also | **auto** |
| Sie-Register | `\b(Sie\|Ihnen\|Ihre\|…)\b` im Target | **auto** |
| zu kurz | < 4 Wörter (kein tragfähiger Standalone) | **auto** |
| Antezedent verloren | wurde zur generischen Definitionsfrage (Thema weg) | Urteil |
| zerhackter/erfundener Begriff | z. B. „Lorenz-Akt attractor" | Urteil |
| Bedeutung verdreht | Thema erhalten, aber die **Frage** ist eine andere geworden | Urteil |
| Grammatik/Satz kaputt | falscher Artikel, abgebrochener Satz | Urteil |

„auto" = mechanisch entscheidbar → Vor-Filter. „Urteil" = semantisch → dem
Menschen als Stichprobe vorlegen.

## Referenz-Baseline (Erst-Batch, 2026-07-20)

22 Rewrite-Traces → **17 angenommen, 5 abgelehnt**; Trainingssatz 24
(17 Positive + 7 Negative, 29 %). Abgelehnt:

- `und wofür das?` → unverändert (kein Rewrite)
- `reicht das kryptografisch?` → „Lorenz-**Akt attractor**" (zerhackt)
- `und was kostet das?` → Target beginnt mit „und"
- `was ist mit Burst-Traffic?` → „Was ist Burst-Traffic?" (Antezedent Rate-Limiting verloren)
- `und worauf achten?` → „…beachten **Sie**…Überlapplen" (Sie + Tippfehler)

Diese 5 sind die Kalibrier-Beispiele der Regeln — bei Unsicherheit hier abgleichen.

## Charge 2 (2026-07-20, `scripts/collect_rewrite_traces.py`)

31 neue Traces, mechanisch 6 abgelehnt, von 26 Urteilsfällen **15 angenommen,
11 abgelehnt**. Positive gesamt damit 32.

Zwei Regeln wurden hier **erst ergänzt**, weil Charge 1 die Fälle nicht hatte
(Reihenfolge laut „Wenn du selbst kurierst": erst Regel, dann Verdikt):

- **Bedeutung verdreht** — das Thema stimmt, die Frage nicht mehr:
  „wann pausiert er die Anwendung?" → „Wann pausiert der Garbage Collector
  **seine Arbeit**?" (Stop-the-World-Frage ist weg);
  „und **zur** Inferenzzeit?" → „Wie **beeinflusst** Dropout **die**
  Inferenzzeit?" (Zeitpunkt → Laufzeit).
- **Grammatik/Satz kaputt** — „Wie funktioniert **der** Konfliktverarbeitung…",
  „Welchem Teil des CAP-Theorems gewähren Datenbanken meist **nur**?"

Weitere Kalibrier-Ablehnungen der bestehenden Regeln: erfundene Begriffe
(„JWT-Gültigkeitseinheit", „Zwischenbotschaft", „ISO-Levelen") und eine
halluzinierte Aufzählung („Btrfs, XFS **und die Linux-Nutzer**").

**Offen, bewusst nicht geregelt:** Kleinschreibung im Target
(„why does the python gil hinder cpu-bound code?") — angenommen, weil die
Rubrik dazu keine Regel hat. Wenn das Modell später inkonsistent
groß-/kleinschreibt, ist das der erste Verdächtige.

## Charge 3 (2026-07-21) — Zielmarke 100 erreicht

47 neue Paare, bewusst breiter als 1+2 (die waren fast reines Infra/ML):
Handwerk, Biologie, Recht, Finanzen, Geschichte, dazu Englisch. Von 42
Urteilsfällen **24 angenommen, 18 abgelehnt** — deutlich härtere Quote als
Charge 2 (15/11). Grund: außerhalb der Technikdomäne bricht qwen2.5:3b
häufiger sprachlich ein („Wo sind Mitochondrien in der Zelle herkommend?",
„Warum hat Chlorophyll im Grünfarben?", „Gebrauchstuffsamenstellungen").

Stand: **100 Rewrite-Traces, 56 kuratierte Positive.**

Häufigste Ablehnungsklasse war **Bedeutung verdreht** (5×), z. B.
„wann schließt er wieder?" → „Wann wird ein Circuit Breaker wieder
**geöffnet**?" (Gegenteil) und „wie erkennt man das?" → „Wie kann man einen
Deadlock **ausnutzen**?".

## Grenze des Gates — behoben (2026-07-21)

12 der 47 Folgefragen kamen zunächst **gar nicht erst zum Rewriter**:
`is_referential()` prüfte gegen eine feste Pronomenliste, Artikel-
Demonstrativa fehlten darin. „wie erkennt man **den**?", „wann ist **die**
bindend?" galten als nicht-referenziell — solche Folgefragen gingen im
Betrieb ungerewritten ins Retrieval.

Behoben über `_DEMONSTRATIVE`: „der/die/den/…" zählt als Rückverweis, wenn
**kein großgeschriebenes Nomen folgt** („wie erkennt man den?" ja, „wie
funktioniert der Cache?" nein). Ergänzt wurden ausserdem „da", „dort",
„denen".

Kosten gemessen statt geschätzt: auf den 98 eigenständigen Basisfragen des
Korpus erzeugt die Regel **genau einen** neuen Fehlalarm („die *degressive*
Abschreibung" — vorangestelltes Adjektiv täuscht die Heuristik). Das ist die
günstige Fehlerrichtung, weil der Rewrite additiv ist (die Originalfrage
läuft per RRF weiter mit).

## Charge 4 (2026-07-21) — gezielt auf die gemessenen Schwächen

Nicht breiter, sondern **schmaler**: der harte Eval zeigte Distraktor 4/6 und
tiefen Antezedenten 3/4. Laut Datenlage kein Wunder — von 100 Traces hatte
**keiner** eine Historie mit zwei Entitäten, fast alle nur einen Turn. Das
Modell hatte die Konstruktion, an der es scheitert, nie gesehen.

36 Paare: 14 Distraktor-Historien, 8 Zwei-Turn-Historien (Bezug im ersten
Turn), 10 Artikel-Demonstrative in natürlicher Form (erst durchs neue Gate
sammelbar), 4 englische mit Distraktor. Entitäten disjunkt zu beiden
Eval-Sätzen.

Von 35 Urteilsfällen **15 angenommen, 20 abgelehnt** — die härteste Quote
bisher, und zwar aus einem Grund, der den Aufwand rechtfertigt:

**Neue Ablehnungsklasse „Hedging".** Bei Distraktor-Historien nennt
qwen2.5:3b überwiegend *beide* Entitäten statt sich zu entscheiden: „Wie
lange speichert Kafka **und RabbitMQ** ihre Nachrichten?", „Wie werden Aktien
**und Anleihen** verzinst?". 8 der 20 Ablehnungen sind dieser Fall. Genau das
Verhalten fällt im harten Eval durch `must_not_include` — deshalb darf es
nicht ins Training.

Ausnahme: bei **vergleichenden** Folgefragen („when does that waste CPU
time?" nach mutex/spinlock) ist die Nennung des Vergleichspartners korrekt,
nicht Hedging. Dieselbe Unterscheidung wie bei `dist-02` im harten Eval.

Stand: **136 Rewrite-Traces, 71 kuratierte Positive**, Trainingssatz 56.

| Klasse | Regel | Prüfung |
|---|---|---|
| Hedging | nennt beide Entitäten der Historie, obwohl eine gemeint ist | Urteil |

## Think-Synthese (deterministisch, kein LLM)

Pro `step_kind` **mehrere** deutsche 1-Satz-Thinks (≤80 Token); ausgewählt per
stabilem Hash über die `trace_id`. Also reproduzierbar (gleicher Trace ⇒
gleicher Think) *und* variiert über den Satz hinweg.

Warum variiert: mit einem wortgleichen Think in jedem Beispiel ist der Großteil
der Target-Tokens ein konstantes Präfix. Die Loss fällt dann, ohne dass die
Fähigkeit besser wird — im 3B-Mechanik-Lauf (2026-07-20) lag sie schon in
Epoche 1 bei 0,0001, während der Adapter die Aufgabe faktisch verschlechterte.

## Trainings-/Eval-Satz bauen

`collect-traces build --eval-n N` baut beide Sätze **neu** aus Traces +
`curation.jsonl` (append-only; späteres Verdikt überstimmt früheres). Keine
Handarbeit, kein Ad-hoc-Skript — derselbe Trace-Bestand ergibt denselben Satz.

- **Eval-Split per Stride**, nicht „die letzten N" — thematisch gestreut.
- **Negative nur aus Train-Positiven.** Ein Negativ aus einem Eval-Trace trägt
  dessen Zielfrage ins Training (Leck); dagegen gibt es einen Test.
- **Eval ohne `<think>`**: dort wird gegen die reine Zielfrage verglichen.

## Negativ-Synthese

Aus angenommenen Positiven: die **eigenständige** Version (das Rewrite-Target)
als Folgefrage OHNE Gespräch → Target `UNCHANGED` (lehrt: schon eigenständige
Frage nicht anfassen). Quote auf 25–30 % des Satzes gedeckelt, per Stride
thematisch gestreut.

## Wenn du selbst kurierst

`collect-traces curate --negatives` (schreibt nach `data/traces/curated/`).
Bei Abweichung von dieser Rubrik: **erst die Regel hier korrigieren**, dann neu
kurieren — nicht 24 Einzelfälle nachziehen. So bleibt die Linie eine Linie.
