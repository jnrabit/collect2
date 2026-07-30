# collect2 — Arbeitsauftrag: Trace-Pipeline für WorkflowAgent-Finetune

## Kontext

Qwythos-9B wurde in drei Messläufen als Basis für den collect2-WorkflowAgent
evaluiert. Ergebnis: strukturierte Rolle + natives Tool-Calling funktionieren,
aber drei charakterisierte Schwächen (Rollenkippen unter Rauschen,
Registerinstabilität bei komprimiertem Deutsch inkl. Identity-Leak,
Konfabulation). Die ersten beiden sind Verhaltens-Targets für einen
LoRA-Finetune (via K4N0N3, separater Auftrag), die dritte fängt die
Systemarchitektur ab (Drei-Zonen-Verdikt, Source-Anchoring).

Dieser Auftrag baut die **Datenpipeline** für diesen Finetune: Traces aus dem
echten collect2-Betrieb sammeln, in ein trainierbares Format bringen,
validieren. Er braucht KEINE GPU und keinen neuen RAM — alles läuft auf CPU.

**Vier Design-Entscheidungen sind gesetzt (nicht neu diskutieren):**

1. **Granularität: ein Beispiel = ein Agenten-Schritt** (ein Modellaufruf:
   Kontext → ein Tool-Call ODER eine Antwort). Mehrschritt-Konsistenz kommt
   über den Verlauf im Kontext, nicht über Workflow-große Targets.
2. **Format: Chat-JSONL, identisch zum Inferenz-Rendering.** messages-Array
   mit system/user/assistant/tool-Rollen, Tool-Definitionen im nativen
   tools-Feld, Ziel = das assistant-Turn. Trainings- und Inferenz-Template
   dürfen nie auseinanderlaufen (Lektion aus dem T4-Format-Fehlschlag).
3. **Think in den Targets: kurz, deutsch, synthetisch.** 1–3 Sätze
   Entscheidungsbegründung, Obergrenze ~80 Tokens. Nicht die englischen
   Modell-Monologe einfrieren, nicht Think ganz wegtrainieren.
4. **Negativbeispiel-Quote 25–30 %** gegen die drei dokumentierten
   Kippfehler: eigenständige Frage → UNCHANGED; Konzeptfrage → Antwort ohne
   Tool-Call; komprimiertes Deutsch → deutsches Du-Register.

**Projektregeln gelten:** Anti-Scaffold (der Konsument existiert: der
Finetune), DESIGN §5 (Module < 400 LOC, Logik von Transport getrennt, keine
blockierenden Waits), bestehende Tests bleiben grün, keinerlei Vault-Writes
in diesem Auftrag.

---

## Auftrag 1 — Trace-Schema (`traces/schema.py`)

Ein JSONL-Eintrag pro Agenten-Schritt:

```json
{
  "messages": [
    {"role": "system", "content": "..."},
    {"role": "user", "content": "..."},
    {"role": "assistant", "content": "...", "tool_calls": [...]},
    {"role": "tool", "content": "..."},
    {"role": "assistant", "content": "<think>...</think>\n..."}
  ],
  "tools": [ ...natives Qwen-Tool-Schema... ],
  "meta": {
    "trace_id": "uuid",
    "workflow_id": "uuid",
    "step_index": 3,
    "step_kind": "rewrite | tool_call | answer | no_op",
    "timestamp": "iso8601",
    "collect2_version": "git-hash",
    "tool_schema_hash": "sha256 der tools-Definitionen",
    "outcome": "trust_reached | user_corrected | failed | unknown",
    "curated": false,
    "synthetic": false
  }
}
```

- Das **letzte** assistant-Turn ist das Trainings-Target; alles davor ist
  Kontext. Der Collector schreibt Traces zunächst OHNE Think im Target
  (das echte System hat ja keins) — Think wird in Auftrag 4 ergänzt.
- `tool_schema_hash` ermöglicht später das Aussortieren von Traces, deren
  Tool-Signaturen nicht mehr zum aktuellen Stand passen.
- Schema-Modul enthält Dataclasses + (De-)Serialisierung + einen
  `validate_entry()`-Grundcheck (Pflichtfelder, Rollenfolge plausibel,
  letztes Turn ist assistant). Unit-Tests dafür.

## Auftrag 2 — Collector (`traces/collector.py` + minimale Einhänge-Punkte)

**Prinzip: nur mitschreiben, nie eingreifen.** Der Collector ist eine
Logging-Schicht um die bestehenden Modellaufrufe des Agenten-Stacks
(Rewriter-Aufruf, künftige WorkflowAgent-Aufrufe). Kein Verhalten des
Systems ändert sich; Fehler im Collector dürfen NIE den Agenten-Pfad
brechen (try/except um jeden Write, Fehler nur loggen).

- Append-only JSONL unter `data/traces/YYYY-MM-DD.jsonl` (rotierend pro
  Tag). Kein Redis, keine DB — flache Dateien reichen und sind kuratierbar.
- Pro Aufruf erfasst: der exakte gerenderte Kontext (messages + tools, so
  wie sie ans Modell gingen), die Modellantwort, step_kind.
- `outcome` wird nachgetragen, wenn der Workflow endet (TRUST erreicht /
  Nutzerkorrektur / Fehler) — Implementierung: Workflow-Ende schreibt eine
  kleine Outcome-Zeile mit workflow_id, ein Merge-Schritt im Validator
  (Auftrag 3) joint das. KEIN In-Place-Update der JSONL (append-only).
- Ein/Aus über Config-Flag, Default an im Dev-Betrieb.
- Wo genau eingehängt wird: im Plan auflisten (welche Aufruf-Stellen
  existieren heute), pro Stelle 1–3 Zeilen Einhänge-Code. Wenn eine Stelle
  architektonisch nicht sauber erreichbar ist (Transport/Logik-Trennung
  verletzt würde): benennen statt hacken.

## Auftrag 3 — Validator (`traces/validate.py`, CLI: `collect-traces validate`)

Rendert jeden Trace durch das **echte** Template und prüft maschinell:

1. **Template-Rendering:** `tokenizer.apply_chat_template(messages,
   tools=...)` mit dem Qwythos/Qwen3.5-Tokenizer (nur Tokenizer laden, kein
   Modell — CPU, wenige Sekunden). Rendering darf nicht werfen; Ergebnis-
   Tokenlänge wird erfasst.
2. **Tool-Call-Validität:** jedes tool_call-JSON im Target parst und
   entspricht dem Schema unter `tool_schema_hash` (Name existiert,
   Pflichtparameter da, keine erfundenen Parameter).
3. **Längenverteilung:** Histogramm über Gesamt-Tokenlänge (Kontext+Target).
   Output-Zeile: p50/p90/p99 und Anteil > 1024 bzw. > 2048 Tokens — diese
   Zahl entscheidet die seq_len im Trainings-Auftrag.
4. **Think-Regeln** (für Traces mit Think): ≤ 80 Tokens, Sprache deutsch
   (simple Heuristik reicht: Stoppwort-Quote, kein "The user…"-Prefix).
5. **Register-Heuristik** für answer-Targets: kein "Sie"-Register
   (Wortliste), Sprache deutsch.
6. Report: pro Datei Anzahl valide/invalide mit Fehlergrund, als Tabelle
   und als JSON (`data/traces/validation_report.json`).

## Auftrag 4 — Kurations- & Augmentierungswerkzeug (`traces/curate.py`)

- `collect-traces curate`: interaktives Durchgehen der gesammelten Traces
  (nacheinander anzeigen: Kontext gekürzt, Target voll) mit Tastatur-Verdikt
  gut/schlecht/skip → setzt `curated: true` in eine separate
  Kurations-JSONL (wieder append-only, join über trace_id).
- **Think-Synthese:** für kuratierte Traces ohne Think generiert das Tool
  einen Think-Vorschlag deterministisch aus den Trace-Fakten per Template
  (step_kind + Entscheidungsmerkmale → 1–2 deutsche Sätze, z. B.
  "Frage referenziell auf {X} → Rewrite." / "Konzeptfrage, im Tool-Schema
  beantwortbar → kein Call."). KEIN LLM-Aufruf in v1 — Templates aus
  step_kind reichen für die Ökonomie-Erziehung und sind reproduzierbar.
  Vorschlag wird im Kurations-Schritt mit angezeigt und bestätigt/editiert.
- **Negativ-Synthese:** aus kuratierten Positiv-Traces abgeleitete
  Negativbeispiele (markiert `synthetic: true`): eigenständige Version einer
  referenziellen Frage → Target UNCHANGED; Konzeptfrage zu vorhandenen
  Tools → Target Antwort ohne Call. Quote im Validator-Report ausweisen
  (Ziel 25–30 % am Ende — muss im Erstbatch noch nicht erreicht sein).

## Akzeptanz (Gesamt)

1. Collector läuft im echten Dev-Betrieb mit, mindestens **20 echte Traces**
   aus realen collect2-Nutzungen gesammelt (Rewriter-Aufrufe zählen).
2. Alle 20 durch Kuration gezogen, Think ergänzt, **Validator: 20/20 valide**,
   inklusive Template-Rendering ohne Fehler.
3. Längenverteilungs-Zeile liegt vor (Input für den Trainings-Auftrag).
4. Bestehende collect2-Tests grün; Collector-Ausfall (simuliert: Trace-Dir
   read-only) bricht keinen Agenten-Aufruf (Test dafür).
5. Kein Modul > 400 LOC; keine neuen Pflicht-Dependencies (transformers
   nur für den Validator, als optionale Dependency mit klarer Meldung).

## Nicht-Ziele

Kein Training, kein Modell-Serving, keine Vault-Berührung, keine
LLM-gestützte Synthese, kein Umbau bestehender Agenten-Logik.

---

## Umsetzungsstand (2026-07-19, Branch `feat/trace-pipeline`)

Erste Probe-Umsetzung lag unter `qwythos/` und vermischte den Serving-Agent
mit der Trace-Pipeline. Neu ausgerichtet am Auftrag; 440 collect2-Tests grün.

**Erledigt:**

- **Serving herausgelöst:** `QwythosAgent` hängt nur bei `qwythos_enabled=true`
  in den Bus (Default aus) → die Trace-Sammlung ändert kein Agentenverhalten.
- **Auftrag 1:** `traces/schema.py` mit Dataclasses, `validate_entry()`,
  kanonischem `TOOL_DEFINITIONS` (Single Source; Serving importiert von hier).
  Jeder Trace trägt das `tools`-Feld + Pflicht-Meta (`step_kind`, `workflow_id`,
  `step_index`, `outcome`, `curated`, `synthetic`, git-hash).
- **Auftrag 2:** `traces/collector.py` — rotierend `data/traces/YYYY-MM-DD.jsonl`,
  append-only Outcome-Zeile, try/except um jeden Write (Read-only-Resilienztest
  grün). Einhängepunkte: **Rewriter** (`step_kind=rewrite`, primäres Ziel) und
  finaler LLM-Turn (`step_kind=answer`). Config-Flag `traces_enabled` (Default an).
- **Auftrag 3:** `traces/validate.py` — echtes `apply_chat_template(…, tools=…)`,
  konfigurierbarer Tokenizer, Längenhistogramm (p50/p90/p99, >1024/>2048 →
  seq_len-Empfehlung), Think ≤80 Token (token-basiert), Sie/Du-Register +
  Deutsch-Heuristik, Report als Tabelle + `validation_report.json`.
  CLI `collect-traces validate`. transformers optional (klare Meldung).
- **Auftrag 4:** `traces/curate.py` — interaktive Kuration, deterministische
  Think-Synthese aus `step_kind`, Negativ-Synthese (referenziell→UNCHANGED,
  Konzept→ohne Call, `synthetic:true`). CLI `collect-traces curate --negatives`.
- **Migration:** die 27 realen Alt-Traces via `collect-traces migrate` ins neue
  Schema überführt (echte Daten, keine Fabrikation).
- Alle Module < 400 LOC; keine neue Pflicht-Dependency.

**Offen / für den Menschen zu entscheiden:**

1. **Tokenizer auf Qwen3.5 zeigen.** Default steht auf `Qwen/Qwen2.5-7B-Instruct`
   (erreichbar). Für den *faithful* Template-Check `COLLECT_TRACES_TOKENIZER` auf
   den echten Qwythos/Qwen3.5-Basis-Tokenizer setzen (HF-Repo oder ein aus dem
   GGUF extrahiertes lokales Verzeichnis) — sonst laufen Trainings- und
   Inferenz-Template minimal auseinander (genau die T4-Lektion).
2. **20 saubere Rewriter-Traces sammeln.** Die 27 migrierten stammen aus
   codegen/decision/repair (code-/englischlastig) → 16 fallen im Validator als
   „nicht deutsch" durch. Das ist ein echtes Qualitätssignal, kein Bug: die
   sauberen Positiv-Traces entstehen aus **Rewriter-Aufrufen im echten Betrieb**
   (jetzt eingehängt) + Kuration. Akzeptanz #1/#2 (20/20 valide) wird damit
   erreicht, nicht aus den Migrations-Altdaten.
3. **Serving-Agent:** bewusst behalten und isoliert (gehört zum K4N0N3-Serving-
   Auftrag). Falls er ganz aus diesem Repo-Stand soll: entfernen.

**Commits (feat/trace-pipeline):** `feat(traces): Pipeline` /
`feat(traces): Einhängepunkte` / `refactor(qwythos): Serving isolieren`.
