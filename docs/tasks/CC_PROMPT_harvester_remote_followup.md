# CC-Auftrag: collect2 — Remote/CI · Follow-up-Retrieval · Harvester-Port

## Arbeitsmodus (verbindlich)

**Zwei Phasen. Erst PLAN, dann STOP.** Du erstellst zuerst einen vollständigen
Umsetzungsplan (siehe §Planformat) und wartest auf mein explizites „go" —
gesamthaft oder pro Baustelle. Vor dem „go" wird **keine Datei geschrieben,
kein Commit gemacht, kein Netzwerk-Setup ausgeführt.** Lesen/Analysieren ist
erlaubt und erwünscht.

Nach „go": Baustellen **einzeln** abarbeiten, in der Reihenfolge A → B → C.
Nach jeder Baustelle: kurzer Ist-Bericht + Stopp-Punkt, ich gebe die nächste frei.

## Kontext

Repo: `~/collect2` (lokal, main, 15 Commits). Multi-Agent-Wissenssystem,
~5.100 LOC in `src/collect/`, 163 Tests grün. Vollständiger Systemstand in
`AUDIT.md` und Design-Anker in `DESIGN.md` — **beide zuerst lesen.**

Geltende Regeln aus DESIGN §5, nicht verhandelbar:
- Module < 400 LOC, Logik von Transport getrennt (alles ohne Redis testbar)
- Kein Blocking-Wait, keine Timeout-Heuristiken für Kontrollfluss
- Ein Embedding-Backend (MiniLM multilingual, CPU)
- Anti-Scaffold: nichts bauen, was keinen existierenden Consumer hat
- Sicherheitsnetz respektieren: `collect-doctor --fast` und Pre-Commit-Guard
  müssen nach jeder Baustelle grün sein; `--no-verify` nur dokumentiert

Referenz-Altsysteme (read-only, niemals verändern): `~/collect` (55k LOC,
läuft parallel auf eigenen Redis-Channels) und `~/vibelike` (27k LOC).

---

## Baustelle A — GitHub-Remote + CI scharf schalten

**Ziel:** Privates Remote `jnrabit/collect2`, bestehender Workflow in
`.github/` läuft tatsächlich, CI ist Gate statt Dekoration.

Anforderungen:
1. Prüfe zuerst `.gitignore`-Hygiene: `data/` (468 MB), `.env`, `logs/`,
   `workspace/`, Caches — nichts davon darf ins Remote. Liste im Plan auf,
   was aktuell committet ist und ob Secrets/Alt-Pfade drinstecken
   (git log nach `.env`-Leaks durchsuchen).
2. Ossifikat ist git-Submodule (`github.com/jnrabit/ossifikat`) — Submodule-
   Handling im CI-Checkout berücksichtigen.
3. Bestehenden Workflow in `.github/` sichten: läuft der so überhaupt?
   Redis/Ollama sind im CI nicht da → CI muss auf `InMemoryBus`-Tests +
   `collect-doctor --fast` beschränkt sein, Integrationstests gegen echte
   Vaults als lokal-only markieren (pytest-Marker, im Plan benennen).
4. Python 3.12 im CI pinnen (3.14 bricht protobuf im Alt-Stack — auch wenn
   collect2 das evtl. nicht betrifft: prüfen, nicht raten).
5. Kein Cutover: Alt-`~/collect` bleibt unberührt, kein Stack wird gestoppt.

**Nicht-Ziele:** Kein öffentliches Repo, kein Release-Prozess, kein Deployment
aus CI.

---

## Baustelle B — Follow-up-Retrieval (Query-Rewrite)

**Ziel:** Kurze referenzielle Folgefragen („und bei X?", „warum ist das so?")
retrieven sinnvoll, statt kontextlos ins Leere zu laufen.

Ansatz: **Query-Rewrite vor der Retrieval-Pipeline.** Letzte Gesprächs-Turns
(bereits vorhanden: 5-Turn-Kontext pro Web-Verbindung, aktuell nur in der
Synthese) → qwen2.5:3b formt daraus eine eigenständige Frage → diese geht in
Translate → Routing → Decompose wie gehabt.

Anforderungen:
1. **Gate davor:** Rewrite nur, wenn die Frage referenziell ist (kurz,
   Pronomen/Deixis, kein eigenständiger Informationsgehalt). Eigenständige
   Fragen laufen unverändert durch — deterministisch entscheidbar bevorzugt
   (Heuristik: Tokenzahl, Pronomen-Liste, fehlende Nomen), LLM-Klassifikation
   nur falls Heuristik nachweislich nicht reicht. Begründe die Wahl im Plan.
2. Rewrite ist **reorder-only in der Wirkung**: er verändert die Query, nie
   die Zonen-Logik, nie die Gewichte. Drei-Zonen-Verdikt bleibt unangetastet.
3. Fehlverhalten muss harmlos sein: wenn 3b Unsinn umformuliert, darf das
   Ergebnis nicht schlechter sein als Status quo → Original-Query als
   Fallback mitführen (z. B. beide suchen, RRF-fusionieren — oder begründet
   dagegen entscheiden).
4. Sichtbarkeit: umgeschriebene Query in der Meta-Zeile des Web-Chats
   anzeigen (Transparenz, Debugging).
5. Messlatte: `scripts/retrieval_benchmark.py` um eine Follow-up-Sektion
   erweitern (mind. 6 Zwei-Turn-Szenarien: Frage → referenzielle Folgefrage,
   erwartete Zone + Trefferthema). Baseline vor der Änderung messen und
   festhalten, danach vergleichen. Bestehende 9 Queries dürfen sich nicht
   verschlechtern.

**Nicht-Ziele:** Kein Langzeitgedächtnis, keine Kontext-Injektion ins
Retrieval selbst, kein Umbau des Gesprächskontexts.

---

## Baustelle C — Harvester-Port (Vault auftauen)

**Ziel:** Vault wächst wieder kontrolliert. Bekannte Lücke als Erstziel:
HTTP/Netzwerk-Grundlagenthema fehlt (Embedder-Verwechslung TLS↔soziales
Handshaking wird durch Korpus, nicht nur Rerank adressiert).

Anforderungen:
1. **Archäologie zuerst:** Harvester-Code in `~/collect` und ggf. Quelibrium-
   Altbeständen sichten (ArXiv, OpenAlex, Semantic Scholar, Wikipedia,
   Gutenberg waren die Quellen). Im Plan: was ist portierbar, was ist
   Neuschreiben, welche Quelle zuerst. **Ein** Quell-Adapter für den ersten
   Schnitt, nicht fünf.
2. Ziel-Format: bestehendes Vault-Format (Chaos-XOR+LZMA, numpy-Cipher,
   byte-kompatibel). Schreiben in den **General-Vault** über einen sauberen
   Ingest-Pfad (dedupe per Doc-Hash, Embedding-Berechnung CPU/MiniLM,
   Cache-Update 384-dim konsistent).
3. **Vault-Writes sind heikel:** Ingest arbeitet auf einer Kopie oder mit
   Backup-Schritt (SHA256 vorher/nachher wie bei der Migration), niemals
   in-place ohne Sicherung. Rollback-Weg im Plan beschreiben.
4. Betrieb: CLI-Einstieg (`collect-harvest <quelle> <query/topic> [--limit]`),
   idempotent wiederholbar, Rate-Limits der Quelle respektieren. Kein Daemon,
   kein Scheduler — manueller Lauf reicht für v1.
5. Qualitätsschranke: eingesammelte Docs durchlaufen Mindestprüfung
   (Sprache, Länge, Duplikat) bevor sie in den Vault sedimentieren —
   Telemetrie-Sink vermeiden.
6. Abnahme: Nach Test-Harvest (z. B. HTTP/TLS-Thema, ~100–500 Docs)
   Retrieval-Benchmark erneut laufen lassen + 2–3 neue HTTP-Queries:
   erreichen sie TRUST? Alte 9 Queries stabil?

**Nicht-Ziele:** Kein Crawler-Dauerbetrieb, keine neuen Quellen-Frameworks,
kein Umbau des Vault-Formats.

---

## Planformat (Phase 1 — davor kein „go")

Pro Baustelle:
1. **Ist-Analyse** — was existiert (Dateien, LOC, Wiederverwendbares aus
   Alt-Repos), was fehlt
2. **Schritte** — konkrete Dateien (neu/geändert) mit geschätzten LOC;
   Module-Limit 400 LOC beachten
3. **Tests** — welche neuen Tests, welche bestehenden betroffen (163 müssen
   grün bleiben)
4. **Risiken & Rollback** — was kann kaputtgehen, wie zurück
5. **Offene Entscheidungen** — als konkrete Fragen an mich, mit deiner
   Empfehlung + Begründung (nicht offenlassen, positionieren)

Dazu gesamt: Reihenfolge-Begründung, geschätzter Umfang pro Baustelle,
und was du bewusst **nicht** anfasst.

Dann: **STOP. Warten auf „go".**
