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
