# CC-Auftrag: collect2 — Cutover (Alt-Stack stilllegen, Neu als Default)

## Arbeitsmodus (verbindlich)

**Zwei Phasen. Erst PLAN, dann STOP.** Erst vollständiger Plan (siehe
§Planformat), dann Warten auf mein explizites „go". Vor dem „go" wird
**kein Dienst gestoppt/gestartet, keine Unit ge-enabled/disabled, keine
Datei geschrieben.** Sichten des Ist-Zustands (welche Units existieren,
was ist enabled, was läuft) ist ausdrücklich erwünscht.

## Was Cutover hier bedeutet (und was NICHT)

**Betriebswechsel, kein Abriss.** Ziel: das alte `~/collect` als *laufendes*
System stilllegen, collect2 wird das einzige, das Anfragen beantwortet und
beim Boot von selbst hochkommt.

**Ausdrücklich NICHT Teil dieses Auftrags:**
- Kein Löschen, kein Deinstallieren, kein Verschieben von `~/collect` oder
  dessen Daten. Das Verzeichnis bleibt **vollständig unangetastet.**
- collect2 liest `~/collect` weiterhin read-only als Nachschlagequelle
  (Executor-Grenze) — das bleibt so.
- Rückweg muss jederzeit trivial bleiben: Alt-Stack per `start.sh` wieder
  hochfahrbar.

Wenn dir im Plan irgendein Schritt begegnet, der Alt-Daten anfasst oder den
Rückweg verbaut — **streichen und als Frage melden.**

## Kontext

Repo: `~/collect2` (Remote grün, 212 Tests). Alt-System `~/collect` (55k LOC)
läuft aktuell **parallel** auf eigenen Redis-Channels (kein Konflikt).
Systemstand `AUDIT.md`, Design `DESIGN.md` — beide lesen. collect2 hat
systemd-User-Units in `deploy/` und Skripte `start.sh`/`stop.sh`/`status.sh`
(Heartbeat-Hash in Redis).

## Die vier Schritte (Reihenfolge einhalten)

1. **Ist-Zustand sichten:** Welche systemd-User-Units existieren für alt und
   neu? Was ist `enabled` (kommt beim Boot hoch), was läuft gerade? Sind die
   collect2-Units aus `deploy/` überhaupt schon **installiert** (nach
   `~/.config/systemd/user/` kopiert) oder lagen sie bisher nur im Repo?
   Wie startet das alte `~/collect` heute — Unit, Skript, manuell?

2. **Alt-Stack stoppen** (nicht deinstallieren): sauberer Stop-Weg des alten
   collect. Danach verifizieren: kein Alt-Heartbeat mehr, Alt-Redis-Channels
   verwaist (erwartet, kein Fehler).

3. **Alt-Autostart deaktivieren:** falls das alte collect enabled ist,
   `disable` — sonst kommt es beim nächsten Reboot wieder hoch und der
   Parallelbetrieb ist unbemerkt zurück. (Falls es nie als Unit lief,
   sondern manuell/über ein Skript: benennen, wie der Autostart-Pfad
   aussieht, nichts raten.)

4. **collect2 als Default scharf schalten:** deploy/-Units installieren
   (falls noch nicht), `enable` (+ `--now` oder einmal `start.sh`), sodass
   collect2 beim Boot allein hochkommt.

## Verifikation (Teil des „go", nicht optional)

- `status.sh`: nur noch collect2-Heartbeat, kein Alt-Heartbeat.
- Ein, zwei echte Queries gegen collect2 (Health + eine Wissensfrage).
- **Reboot-Ehrlichkeit:** beschreibe im Plan, wie wir reboot-sicher prüfen
  (entweder echter Reboot, oder `systemctl --user is-enabled` + ein
  `daemon-reload`-Check, der beweist, dass nach Boot genau collect2 und
  nicht das Alte startet). Kein „läuft schon irgendwie".

## Planformat (Phase 1 — davor kein „go")

1. **Ist-Zustand** — konkrete Unit-Namen, enabled/running-Status für alt und
   neu, Installations-Status der deploy/-Units, wie das Alte heute startet.
2. **Schritte** — exakte Kommandos in Reihenfolge, jeweils mit erwartetem
   Ergebnis. Trenne klar: „stoppen" (reversibel) vs. „disable/enable"
   (Boot-Verhalten).
3. **Rollback** — die genauen Kommandos, um in 30 Sekunden zum Parallel-
   betrieb zurückzukehren.
4. **Risiken** — was könnte schiefgehen (z. B. Units nie installiert,
   Redis-Port-Annahmen, Ollama-Modell-Konkurrenz beim Neustart).
5. **Offene Entscheidungen** — mit Empfehlung. Mindestens: echter Reboot-Test
   jetzt oder is-enabled-Nachweis?

Dann: **STOP. Warten auf „go".**
